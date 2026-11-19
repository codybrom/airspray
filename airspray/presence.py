"""
Cross-Platform Office Presence Automation Daemon for Air Wick Diffuser.
Automatically controls the diffuser based on your presence at your workstation.
Supports macOS, Linux, and Windows.
"""

import asyncio
import ctypes
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from airspray.client import AirWickDiffuser
from airspray.config import AppConfig
from airspray.refill import RefillTracker

logger = logging.getLogger(__name__)

# Native CoreGraphics / CoreFoundation bindings for macOS via standard library ctypes
_kCFStringEncodingUTF8: int = 0x08000100
_darwin_ctypes_available: bool = False

if sys.platform == "darwin":
    try:
        _cg = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        _cg.CGSessionCopyCurrentDictionary.restype = ctypes.c_void_p
        _cg.CGSessionCopyCurrentDictionary.argtypes = []

        _cf = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        _cf.CFDictionaryGetValue.restype = ctypes.c_void_p
        _cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        _cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        _cf.CFStringCreateWithCString.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_uint32,
        ]

        _cf.CFBooleanGetValue.restype = ctypes.c_bool
        _cf.CFBooleanGetValue.argtypes = [ctypes.c_void_p]

        _cf.CFRelease.restype = None
        _cf.CFRelease.argtypes = [ctypes.c_void_p]
        _darwin_ctypes_available = True
    except Exception as e:
        logger.debug(f"Could not load CoreGraphics / CoreFoundation via ctypes: {e}")
        _darwin_ctypes_available = False


def _is_darwin_locked() -> bool:
    if not _darwin_ctypes_available:
        return False
    session_ref = _cg.CGSessionCopyCurrentDictionary()
    if not session_ref:
        return False
    try:
        key_ref = _cf.CFStringCreateWithCString(
            None, b"CGSSessionScreenIsLocked", _kCFStringEncodingUTF8
        )
        if not key_ref:
            return False
        try:
            val_ref = _cf.CFDictionaryGetValue(session_ref, key_ref)
            if val_ref:
                return bool(_cf.CFBooleanGetValue(val_ref))
            return False
        finally:
            _cf.CFRelease(key_ref)
    finally:
        _cf.CFRelease(session_ref)


def _is_windows_locked() -> bool:
    if sys.platform != "win32":
        return False
    try:
        windll: object = getattr(ctypes, "windll", None)
        if windll is not None:
            user32: object = getattr(windll, "user32", None)
            if user32 is not None:
                open_desktop = getattr(user32, "OpenInputDesktop", None)
                close_desktop = getattr(user32, "CloseDesktop", None)
                if callable(open_desktop) and callable(close_desktop):
                    # DESKTOP_SWITCHDESKTOP = 0x0100
                    hdesk: object = open_desktop(0, False, 0x0100)
                    if not hdesk:
                        return True
                    close_desktop(hdesk)
                    return False
    except Exception as e:
        logger.debug(f"Error checking Windows lock state: {e}")
    return False


def _is_linux_locked() -> bool:
    return False


def is_screen_locked() -> bool:
    """
    Check if the user screen/session is currently locked.
    Cross-platform support for macOS, Windows, and Linux.
    """
    if sys.platform == "darwin":
        return _is_darwin_locked()
    elif sys.platform == "win32":
        return _is_windows_locked()
    else:
        return _is_linux_locked()


def _now() -> float:
    """Monotonic clock for retry timing (a seam for tests)."""
    return time.monotonic()


def parse_clock(text: str, allow_24: bool = False) -> int:
    """Strict HH:MM -> minutes since midnight. Raises ValueError when invalid.

    "24:00" is accepted only where it means end-of-day (allow_24).
    """
    parts = text.strip().split(":")
    if len(parts) != 2 or not all(p.isdigit() and 1 <= len(p) <= 2 for p in parts):
        raise ValueError(f"Invalid time {text!r}. Expected HH:MM (e.g. 09:00)")
    hour, minute = int(parts[0]), int(parts[1])
    if allow_24 and hour == 24 and minute == 0:
        return 1440
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(
            f"Invalid time {text!r}. Expected HH:MM between 00:00 and 23:59"
        )
    return hour * 60 + minute


def _parse_time_to_minutes(time_str: str, default: int) -> int:
    try:
        return parse_clock(time_str, allow_24=True)
    except ValueError:
        return default


def is_within_window(current_min: int, start_min: int, end_min: int) -> bool:
    """Whether a minute-of-day falls in [start, end), wrapping past midnight.

    22:00-06:00 covers the evening and the following early morning. A window
    whose start equals its end is empty.
    """
    if start_min == end_min:
        return False
    if start_min < end_min:
        return start_min <= current_min < end_min
    return current_min >= start_min or current_min < end_min


def presence_target(config: AppConfig, now: datetime, locked: bool) -> Optional[bool]:
    """What desk presence wants the diffuser to be, per the shared config.

    None means presence tracking is switched off and has no opinion.
    """
    if not config.presence_enabled:
        return None
    if config.schedule_always:
        in_schedule = True
    else:
        end_min = _parse_time_to_minutes(config.end_time, 1440)
        if end_min == 1439:
            # "23:59" is the last minute a time picker can express. Read it as
            # end-of-day so an all-day window does not switch off at 23:59.
            end_min = 1440
        in_schedule = (now.weekday() + 1) in config.active_days and is_within_window(
            now.hour * 60 + now.minute,
            _parse_time_to_minutes(config.start_time, 0),
            end_min,
        )
    return in_schedule and not locked


class Backoff:
    """Exponential retry gate: 10 s, 20 s, 40 s ... capped at 5 minutes.

    Every failed attempt against an unpaired diffuser raises an OS pairing
    dialog, so failures must get rarer, not repeat on every tick.
    """

    def __init__(self, initial: float = 10.0, cap: float = 300.0) -> None:
        self.initial: float = initial
        self.cap: float = cap
        self._delay: float = initial
        self._not_before: float = 0.0

    @property
    def next_delay(self) -> float:
        return self._delay

    def ready(self, now: float) -> bool:
        return now >= self._not_before

    def fail(self, now: float) -> float:
        """Record a failure. Returns how long until the next attempt is allowed."""
        waited = self._delay
        self._not_before = now + waited
        self._delay = min(self._delay * 2, self.cap)
        return waited

    def reset(self) -> None:
        self._delay = self.initial
        self._not_before = 0.0


@dataclass
class PresenceConfig:
    diffuser_address: Optional[str] = None
    intensity: int = 2  # 1=Low, 2=Medium, 3=High, 4=Max
    boost_on_arrival: bool = True
    check_interval_seconds: float = 20.0
    start_time: str = "00:00"  # e.g. "09:00". May be later than end_time (overnight)
    end_time: str = "24:00"  # e.g. "17:30"
    workdays_only: bool = False  # Monday-Friday


# How often the daemon folds elapsed runtime into the shared refill level.
USAGE_TICK_SECONDS: float = 60.0


class PresenceDaemon:
    def __init__(self, config: Optional[PresenceConfig] = None) -> None:
        self.config: PresenceConfig = config or PresenceConfig()
        self.client: Optional[AirWickDiffuser] = None
        self.tracker: RefillTracker = RefillTracker()
        self.is_present: bool = False
        # What the diffuser was last *successfully* told. None = not yet known.
        self.applied: Optional[bool] = None
        self.backoff: Backoff = Backoff()
        self._running: bool = False
        self._last_usage_tick: float = 0.0

    def is_within_schedule(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now()
        if self.config.workdays_only and now.weekday() >= 5:
            return False
        return is_within_window(
            now.hour * 60 + now.minute,
            _parse_time_to_minutes(self.config.start_time, 0),
            _parse_time_to_minutes(self.config.end_time, 1440),
        )

    def check_presence(self) -> bool:
        if not self.is_within_schedule():
            return False
        if is_screen_locked():
            return False
        return True

    async def apply_state(self, present: bool) -> bool:
        """Drive the diffuser to the given state. Returns whether it worked.

        Usage is recorded only after the command succeeded, so a diffuser that
        could not be reached is never accounted as running (or as stopped).
        """
        diffuser = AirWickDiffuser(self.config.diffuser_address)
        boosted = False
        try:
            logger.info(
                f"Applying state: {'PRESENT (Turning ON)' if present else 'AWAY (Turning OFF)'}..."
            )
            await diffuser.connect(timeout=10.0)
            if present:
                await diffuser.turn_on()
                await diffuser.set_intensity(self.config.intensity)
                if self.config.boost_on_arrival:
                    logger.info("Triggering arrival burst spray...")
                    await diffuser.boost()
                    boosted = True
            else:
                await diffuser.turn_off()

            if not self.config.diffuser_address and diffuser.target_address:
                self.config.diffuser_address = diffuser.target_address
        except Exception as e:
            logger.error(f"Failed to update diffuser state: {e}")
            return False
        finally:
            await diffuser.disconnect()

        try:
            if present:
                self.tracker.update_usage(True, self.config.intensity)
                if boosted:
                    self.tracker.record_boost()
            else:
                self.tracker.update_usage(False, None)
            self._last_usage_tick = _now()
        except Exception as e:
            logger.error(f"Could not record refill usage: {e}")
        return True

    async def _step(self) -> None:
        """One pass of the loop: reconcile the diffuser with desk presence."""
        desired = self.check_presence()
        if desired != self.is_present:
            logger.info(f"Presence changed: {self.is_present} -> {desired}")
            self.is_present = desired
            self.backoff.reset()  # a real transition deserves a prompt attempt

        now = _now()
        if desired != self.applied:
            if self.backoff.ready(now):
                if await self.apply_state(desired):
                    self.applied = desired
                    self.backoff.reset()
                else:
                    wait = self.backoff.fail(_now())
                    logger.warning(f"Will retry in {wait:.0f}s.")
        elif self.applied and now - self._last_usage_tick >= USAGE_TICK_SECONDS:
            self._last_usage_tick = now
            self.tracker.update_usage(True, self.config.intensity)

    async def run(self) -> None:
        self._running = True
        logger.info("Starting Air Wick Office Presence Daemon...")
        logger.info(
            f"Configuration: interval={self.config.check_interval_seconds}s, "
            f"schedule={self.config.start_time}-{self.config.end_time} "
            f"({'Mon-Fri' if self.config.workdays_only else 'Everyday'}), "
            f"intensity={self.config.intensity}, boost_on_arrival={self.config.boost_on_arrival}"
        )

        self.is_present = self.check_presence()
        first = True
        while self._running:
            try:
                if not first:
                    await asyncio.sleep(self.config.check_interval_seconds)
                first = False
                await self._step()
            except asyncio.CancelledError:
                # Ctrl-C and SIGTERM both arrive as a cancellation. Fall through
                # to the shutdown below instead of dying with the diffuser on.
                task = asyncio.current_task()
                if task is not None:
                    task.uncancel()
                break
            except Exception as e:
                logger.error(f"Unexpected error in presence loop: {e}")

        self._running = False
        if self.applied is not False:
            logger.info("Daemon stopping. Turning off diffuser...")
            if await self.apply_state(False):
                self.applied = False

    def stop(self) -> None:
        self._running = False
