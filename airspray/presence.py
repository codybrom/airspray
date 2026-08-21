"""
Cross-Platform Office Presence Automation Daemon for Air Wick Diffuser.
Automatically controls the diffuser based on your presence at your workstation.
Supports macOS, Linux, and Windows.
"""

import asyncio
import ctypes
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from airspray.client import AirWickDiffuser
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


def _parse_time_to_minutes(time_str: str, default: int) -> int:
    try:
        parts = [int(p.strip()) for p in time_str.split(":")]
        hour = parts[0]
        minute = parts[1] if len(parts) > 1 else 0
        return hour * 60 + minute
    except Exception:
        return default


@dataclass
class PresenceConfig:
    diffuser_address: Optional[str] = None
    intensity: int = 2  # 1=Low, 2=Medium, 3=High, 4=Max
    boost_on_arrival: bool = True
    check_interval_seconds: float = 20.0
    start_time: str = "00:00"  # e.g. "09:00"
    end_time: str = "24:00"  # e.g. "17:30"
    workdays_only: bool = False  # Monday-Friday


class PresenceDaemon:
    def __init__(self, config: Optional[PresenceConfig] = None) -> None:
        self.config: PresenceConfig = config or PresenceConfig()
        self.client: Optional[AirWickDiffuser] = None
        self.tracker: RefillTracker = RefillTracker()
        self.is_present: bool = False
        self._running: bool = False

    def is_within_schedule(self) -> bool:
        now = datetime.now()
        if self.config.workdays_only and now.weekday() >= 5:
            return False

        current_min = now.hour * 60 + now.minute
        start_min = _parse_time_to_minutes(self.config.start_time, 0)
        end_min = _parse_time_to_minutes(self.config.end_time, 1440)

        if not (start_min <= current_min < end_min):
            return False
        return True

    def check_presence(self) -> bool:
        if not self.is_within_schedule():
            return False
        if is_screen_locked():
            return False
        return True

    async def apply_state(self, present: bool) -> None:
        diffuser = AirWickDiffuser(self.config.diffuser_address)
        try:
            logger.info(
                f"Applying state: {'PRESENT (Turning ON)' if present else 'AWAY (Turning OFF)'}..."
            )
            await diffuser.connect(timeout=10.0)
            if present:
                await diffuser.turn_on()
                await diffuser.set_intensity(self.config.intensity)
                self.tracker.update_usage(True, self.config.intensity)
                if self.config.boost_on_arrival:
                    logger.info("Triggering arrival burst spray...")
                    await diffuser.boost()
                    self.tracker.record_boost()
            else:
                await diffuser.turn_off()
                self.tracker.update_usage(False, 0)

            if not self.config.diffuser_address and diffuser.target_address:
                self.config.diffuser_address = diffuser.target_address
        except Exception as e:
            logger.error(f"Failed to update diffuser state: {e}")
        finally:
            await diffuser.disconnect()

    async def run(self) -> None:
        self._running = True
        logger.info("Starting Air Wick Office Presence Daemon...")
        logger.info(
            f"Configuration: interval={self.config.check_interval_seconds}s, "
            f"schedule={self.config.start_time}-{self.config.end_time} "
            f"({'Mon-Fri' if self.config.workdays_only else 'Everyday'}), "
            f"intensity={self.config.intensity}, boost_on_arrival={self.config.boost_on_arrival}"
        )

        initial_presence = self.check_presence()
        self.is_present = initial_presence
        await self.apply_state(self.is_present)

        while self._running:
            try:
                await asyncio.sleep(self.config.check_interval_seconds)
                current_presence = self.check_presence()

                if self.is_present:
                    self.tracker.update_usage(True, self.config.intensity)

                if current_presence != self.is_present:
                    logger.info(
                        f"Presence changed: {self.is_present} -> {current_presence}"
                    )
                    self.is_present = current_presence
                    await self.apply_state(self.is_present)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected error in presence loop: {e}")

        try:
            logger.info("Daemon stopping. Turning off diffuser...")
            await self.apply_state(False)
        except Exception:
            pass

    def stop(self) -> None:
        self._running = False
