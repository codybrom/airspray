"""
Cross-Platform Self-Managing System Tray / Menu Bar App for Air Wick Diffuser.
Supports macOS, Windows, and Linux with native vector SVG assets, automatic presence
tracking, schedule presets, and launch-at-login settings.
"""

import asyncio
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Coroutine, List, Optional, Tuple
from bleak.backends.device import BLEDevice
from PIL import Image, ImageDraw
from pystray import Icon, Menu, MenuItem  # pyright: ignore[reportMissingTypeStubs]

# Patch pystray macOS backend for crisp Retina displays and native template rendering
try:
    import importlib
    import io
    from typing import Any

    appkit: Any = importlib.import_module("AppKit")
    foundation: Any = importlib.import_module("Foundation")
    pystray_darwin: Any = importlib.import_module("pystray._darwin")

    def _retina_assert_image(self: Any) -> None:
        status_bar: Any = getattr(self, "_status_bar")
        thickness: float = float(status_bar.thickness())
        icon_img: Any = getattr(self, "_icon")

        # 4x high-resolution supersampling for crisp Retina rendering
        retina_px = int(thickness * 4)
        resized = icon_img.resize((retina_px, retina_px), Image.Resampling.LANCZOS)

        b = io.BytesIO()
        resized.save(b, "png")
        png_bytes = b.getvalue()
        data = foundation.NSData.dataWithBytes_length_(png_bytes, len(png_bytes))

        ns_image = appkit.NSImage.alloc().initWithData_(data)
        # Set point size to menu bar thickness so macOS renders as a crisp Retina asset
        ns_image.setSize_(foundation.NSSize(thickness, thickness))
        ns_image.setTemplate_(True)

        setattr(self, "_icon_image", ns_image)
        status_item: Any = getattr(self, "_status_item")
        status_item.button().setImage_(ns_image)

    setattr(pystray_darwin.Icon, "_assert_image", _retina_assert_image)
except Exception:
    pass

from airspray.autostart import is_launch_at_login_enabled, set_launch_at_login
from airspray.client import AirWickDiffuser, scan_for_diffusers
from airspray.config import AppConfig, update_config
from airspray.presence import Backoff, is_screen_locked, presence_target
from airspray.refill import RefillTracker

logger = logging.getLogger("airspray.tray")

ASSETS_DIR: Path = Path(__file__).resolve().parent / "assets"


def render_svg_icon(
    is_active: bool = False,
    intensity: int = 2,
    size: Tuple[int, int] = (128, 128),
) -> Image.Image:
    """
    Renders clean, high-DPI vector diffuser icon matching the user's custom SVG design:
    - Active / Running: Solid filled dome with transparent cutout nozzle aperture + filled base
    - Standby / Off: 50% opacity group with solid base, 8px stroke dome outline, and solid nozzle ellipse
    - Perfectly uniform parallel seam gap
    - Centered aspect ratio optimized for menu bar heights
    """
    render_size = (size[0] * 4, size[1] * 4)
    image = Image.new("RGBA", render_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    scale_x = render_size[0] / 512.0
    scale_y = render_size[1] / 512.0

    def sx(x: float) -> float:
        return x * scale_x

    def sy(y: float) -> float:
        return y * scale_y

    # Base Bowl Geometry
    base_poly: List[Tuple[float, float]] = []
    for x in range(90, 423, 4):
        t_x = (x - 256.0) / 166.0
        seam_y = 296.0 + 12.0 * (1.0 - t_x**2)
        base_poly.append((sx(float(x)), sy(seam_y)))

    for i in range(31):
        t = i / 30.0
        bx = (
            (1 - t) ** 3 * 422
            + 3 * (1 - t) ** 2 * t * 422
            + 3 * (1 - t) * t**2 * 360
            + t**3 * 256
        )
        by = (
            (1 - t) ** 3 * 296
            + 3 * (1 - t) ** 2 * t * 370
            + 3 * (1 - t) * t**2 * 415
            + t**3 * 415
        )
        base_poly.append((sx(bx), sy(by)))
    for i in range(31):
        t = i / 30.0
        bx = (
            (1 - t) ** 3 * 256
            + 3 * (1 - t) ** 2 * t * 152
            + 3 * (1 - t) * t**2 * 90
            + t**3 * 90
        )
        by = (
            (1 - t) ** 3 * 415
            + 3 * (1 - t) ** 2 * t * 415
            + 3 * (1 - t) * t**2 * 370
            + t**3 * 296
        )
        base_poly.append((sx(bx), sy(by)))

    # Active Outer Dome Geometry
    active_dome_poly: List[Tuple[float, float]] = []
    for i in range(21):
        t = i / 20.0
        bx = (
            (1 - t) ** 3 * 90
            + 3 * (1 - t) ** 2 * t * 90
            + 3 * (1 - t) * t**2 * 130
            + t**3 * 205
        )
        by = (
            (1 - t) ** 3 * 280
            + 3 * (1 - t) ** 2 * t * 150
            + 3 * (1 - t) * t**2 * 95
            + t**3 * 92
        )
        active_dome_poly.append((sx(bx), sy(by)))
    for i in range(21):
        t = i / 20.0
        bx = (
            (1 - t) ** 3 * 205
            + 3 * (1 - t) ** 2 * t * 256
            + 3 * (1 - t) * t**2 * 307
            + t**3 * 307
        )
        by = (
            (1 - t) ** 3 * 92
            + 3 * (1 - t) ** 2 * t * 90
            + 3 * (1 - t) * t**2 * 92
            + t**3 * 92
        )
        active_dome_poly.append((sx(bx), sy(by)))
    for i in range(21):
        t = i / 20.0
        bx = (
            (1 - t) ** 3 * 307
            + 3 * (1 - t) ** 2 * t * 382
            + 3 * (1 - t) * t**2 * 422
            + t**3 * 422
        )
        by = (
            (1 - t) ** 3 * 92
            + 3 * (1 - t) ** 2 * t * 95
            + 3 * (1 - t) * t**2 * 150
            + t**3 * 280
        )
        active_dome_poly.append((sx(bx), sy(by)))
    for x in range(422, 89, -4):
        t_x = (x - 256.0) / 166.0
        dy = 280.0 + 12.0 * (1.0 - t_x**2)
        active_dome_poly.append((sx(float(x)), sy(dy)))

    # Standby Dome Stroke Path
    standby_dome_path: List[Tuple[float, float]] = []
    for i in range(21):
        t = i / 20.0
        bx = (
            (1 - t) ** 3 * 96
            + 3 * (1 - t) ** 2 * t * 96
            + 3 * (1 - t) * t**2 * 133
            + t**3 * 206
        )
        by = (
            (1 - t) ** 3 * 280
            + 3 * (1 - t) ** 2 * t * 155
            + 3 * (1 - t) * t**2 * 101
            + t**3 * 98
        )
        standby_dome_path.append((sx(bx), sy(by)))
    for i in range(21):
        t = i / 20.0
        bx = (
            (1 - t) ** 3 * 206
            + 3 * (1 - t) ** 2 * t * 256
            + 3 * (1 - t) * t**2 * 306
            + t**3 * 306
        )
        by = (
            (1 - t) ** 3 * 98
            + 3 * (1 - t) ** 2 * t * 96
            + 3 * (1 - t) * t**2 * 98
            + t**3 * 98
        )
        standby_dome_path.append((sx(bx), sy(by)))
    for i in range(21):
        t = i / 20.0
        bx = (
            (1 - t) ** 3 * 306
            + 3 * (1 - t) ** 2 * t * 379
            + 3 * (1 - t) * t**2 * 416
            + t**3 * 416
        )
        by = (
            (1 - t) ** 3 * 98
            + 3 * (1 - t) ** 2 * t * 101
            + 3 * (1 - t) * t**2 * 155
            + t**3 * 280
        )
        standby_dome_path.append((sx(bx), sy(by)))
    for x in range(416, 95, -4):
        t_x = (x - 256.0) / 160.0
        dy = 280.0 + 10.0 * (1.0 - t_x**2)
        standby_dome_path.append((sx(float(x)), sy(dy)))

    if is_active:
        # Active: Solid 100% white fill with transparent cutout aperture
        draw.polygon(base_poly, fill=(255, 255, 255, 255))
        draw.polygon(active_dome_poly, fill=(255, 255, 255, 255))
        draw.ellipse(
            [sx(220), sy(96), sx(292), sy(114)],
            fill=(0, 0, 0, 0),
        )
    else:
        # Standby: 50% opacity (128 alpha) unified group
        dim_color = (255, 255, 255, 128)
        draw.polygon(base_poly, fill=dim_color)
        stroke_w = max(2, int(8 * scale_x))
        draw.polygon(standby_dome_path, outline=dim_color, width=stroke_w)
        draw.ellipse(
            [sx(220), sy(96), sx(292), sy(114)],
            fill=dim_color,
        )

    return image.resize(size, Image.Resampling.LANCZOS)


def get_tray_icon(is_active: bool = False, intensity: int = 2) -> Image.Image:
    """Retrieve rendered SVG tray icon image."""
    return render_svg_icon(is_active=is_active, intensity=intensity, size=(128, 128))


PRESENCE_TICK_SECONDS: float = 10.0


def _brief(error: BaseException) -> str:
    """An error short enough to sit in a menu row."""
    text = str(error).strip() or type(error).__name__
    return text if len(text) <= 70 else text[:67] + "..."


class AirSprayTrayApp:
    def __init__(self, address: Optional[str] = None) -> None:
        if address:
            update_config(device_address=address)

        self.tracker: RefillTracker = RefillTracker()
        # A read-only snapshot for drawing the menu. It is refreshed from disk
        # and never written back. Every change goes through update_config so a
        # stale copy cannot revert what the CLI or the Mac app wrote meanwhile.
        self._view: AppConfig = AppConfig.load()
        # None until the diffuser has told us. The tray does not assume "off".
        self.is_on: Optional[bool] = None
        self.intensity: int = self._view.default_intensity
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._async_thread: Optional[threading.Thread] = None
        self._presence_thread: Optional[threading.Thread] = None
        self._icon: object = None
        self._running: bool = False
        self._status_message: str = "Ready"

        # One BLE conversation at a time, manual or automatic.
        self._ble_lock: asyncio.Lock = asyncio.Lock()
        # Automatic attempts: at most one in flight, spaced out after failures.
        self._auto_in_flight: bool = False
        self._backoff: Backoff = Backoff()
        # The last thing presence asked for, to spot transitions.
        self._last_target: Optional[bool] = None
        # Set by a manual on/off. Presence leaves the diffuser alone until its
        # own next transition (lock, unlock, schedule edge).
        self._manual_hold: bool = False
        self._startup_sync_done: bool = False

    # -- plumbing ----------------------------------------------------------

    def _run_async_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run_coro(self, coro: Coroutine[object, object, None]) -> bool:
        """Schedule a coroutine on the BLE loop. False if it could not be."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._loop)
            return True
        coro.close()  # never leave an un-awaited coroutine behind
        return False

    def _refresh_view(self) -> None:
        try:
            self._view = AppConfig.load()
        except Exception as e:
            logger.error(f"Could not read config: {e}")

    def _save(self, **changes: object) -> bool:
        """Write just these settings to the shared config."""
        try:
            self._view = update_config(None, **changes)
            return True
        except Exception as e:
            logger.error(f"Could not save settings: {e}")
            self._status_message = f"Could not save settings: {_brief(e)}"
            return False

    def _record(self, action: Callable[[], object]) -> None:
        """Run a refill-tracker update. A failed write must not undo a command."""
        try:
            action()
        except Exception as e:
            logger.error(f"Could not record refill usage: {e}")

    def _address(self) -> Optional[str]:
        self._refresh_view()
        return self._view.device_address

    # -- presence ----------------------------------------------------------

    def _presence_decision(self, target: Optional[bool], now: float) -> Optional[bool]:
        """Whether presence should act right now, and to which state.

        `target` is what presence wants (None = tracking off). `Now` is a
        monotonic clock reading. Returns the state to apply, or None to wait.
        """
        if target is None:
            self._last_target = None
            return None
        if target != self._last_target:
            self._last_target = target
            self._manual_hold = False
            self._backoff.reset()
        if self._manual_hold or self._auto_in_flight:
            return None
        if self.is_on is not None and self.is_on == target:
            return None
        if not self._backoff.ready(now):
            return None
        return target

    def _presence_tick(self) -> None:
        self._refresh_view()
        locked = is_screen_locked()
        target = presence_target(self._view, datetime.now(), locked)
        action = self._presence_decision(target, time.monotonic())
        if action is not None:
            self._auto_in_flight = True
            if not self.run_coro(self._auto_apply(action, locked)):
                self._auto_in_flight = False
        elif (
            target is None
            and self.is_on is None
            and not self._startup_sync_done
            and not self._auto_in_flight
        ):
            # Presence is off, so nothing else will ask: learn the real power
            # state once instead of presenting a guess.
            self._auto_in_flight = True
            if self.run_coro(self._auto_apply(None, locked)):
                self._startup_sync_done = True
            else:
                self._auto_in_flight = False

    def _run_presence_loop(self) -> None:
        """Background thread monitoring desk presence and schedule."""
        while self._running:
            try:
                self._presence_tick()
            except Exception as e:
                logger.error(f"Error in presence loop: {e}")
            time.sleep(PRESENCE_TICK_SECONDS)

    async def _auto_apply(self, target: Optional[bool], locked: bool) -> None:
        """One automatic attempt: learn the power state, then match `target`.

        With target None it only reads the state. Failures back off
        exponentially. They are never retried on the next tick.
        """
        ok = False
        try:
            async with self._ble_lock:
                async with AirWickDiffuser(self._address()) as diffuser:
                    if self.is_on is None:
                        status = await diffuser.get_status()
                        self.is_on = status.power
                    if target is not None and self.is_on != target:
                        if target:
                            await diffuser.turn_on()
                            await diffuser.set_intensity(self.intensity)
                        else:
                            await diffuser.turn_off()
                        changed = True
                    else:
                        changed = False
            if target is not None:
                self.is_on = target
                if changed:
                    if target:
                        self._record(
                            lambda: self.tracker.update_usage(True, self.intensity)
                        )
                        self._status_message = "Desk Active • Auto ON"
                    else:
                        self._record(lambda: self.tracker.update_usage(False, None))
                        reason = "Screen Locked" if locked else "Off Hours"
                        self._status_message = f"{reason} • Auto OFF"
            ok = True
        except Exception as e:
            logger.error(f"Automatic diffuser update failed: {e}")
            wait = self._backoff.fail(time.monotonic())
            self._status_message = f"Diffuser unreachable • retry in {wait:.0f}s"
        finally:
            if ok:
                self._backoff.reset()
            self._auto_in_flight = False
            self._update_icon()

    # -- manual actions ----------------------------------------------------

    async def _async_set_power(self, on: bool) -> None:
        try:
            async with self._ble_lock:
                async with AirWickDiffuser(self._address()) as diffuser:
                    if on:
                        await diffuser.turn_on()
                        await diffuser.set_intensity(self.intensity)
                    else:
                        await diffuser.turn_off()
            self.is_on = on
            if on:
                self._record(lambda: self.tracker.update_usage(True, self.intensity))
            else:
                self._record(lambda: self.tracker.update_usage(False, None))
            self._status_message = "Turned ON" if on else "Turned OFF"
        except Exception as e:
            logger.error(f"Error turning {'on' if on else 'off'} diffuser: {e}")
            self._status_message = f"Failed: {_brief(e)}"
        finally:
            self._update_icon()

    async def _async_turn_on(self) -> None:
        await self._async_set_power(True)

    async def _async_turn_off(self) -> None:
        await self._async_set_power(False)

    async def _async_boost(self) -> None:
        try:
            async with self._ble_lock:
                async with AirWickDiffuser(self._address()) as diffuser:
                    await diffuser.boost()
            self._record(self.tracker.record_boost)
            self._status_message = "Boost sprayed"
        except Exception as e:
            logger.error(f"Error triggering boost: {e}")
            self._status_message = f"Failed: {_brief(e)}"
        finally:
            self._update_icon()

    async def _async_set_intensity(self, level: int) -> None:
        try:
            async with self._ble_lock:
                async with AirWickDiffuser(self._address()) as diffuser:
                    await diffuser.set_intensity(level)
            self.intensity = level
            # Power is untouched by an intensity change. Keep what is recorded.
            self._record(lambda: self.tracker.update_usage(None, level))
            self._save(default_intensity=level)
        except Exception as e:
            logger.error(f"Error setting intensity: {e}")
            self._status_message = f"Failed: {_brief(e)}"
        finally:
            self._update_icon()

    async def _async_scan_and_pair(self) -> None:
        try:
            self._status_message = "Scanning for Diffusers..."
            self._update_icon()
            async with self._ble_lock:
                found: List[BLEDevice] = await scan_for_diffusers(timeout=5.0)
            if found:
                dev = found[0]
                if self._save(device_address=dev.address):
                    self._status_message = f"Selected: {dev.name or dev.address}"
                    self.is_on = None  # a different diffuser. State unknown again
            else:
                self._status_message = "No Diffusers Found"
        except Exception as e:
            self._status_message = f"Scan Failed: {_brief(e)}"
        finally:
            self._update_icon()

    def _manual_action(self) -> None:
        """A person asked for something: stop holding back after old failures."""
        self._backoff.reset()

    def toggle_power(self, _icon: object, _item: object) -> None:
        self._manual_action()
        # Respected until presence next changes its mind (lock/unlock/schedule).
        self._manual_hold = True
        self.run_coro(self._async_set_power(not self.is_on))

    def trigger_boost(self, _icon: object, _item: object) -> None:
        self._manual_action()
        self.run_coro(self._async_boost())

    def set_level(self, level: int) -> None:
        self._manual_action()
        self.run_coro(self._async_set_intensity(level))

    def reset_refill(self, _icon: object, _item: object) -> None:
        try:
            self.tracker.reset_refill()
        except Exception as e:
            logger.error(f"Could not reset refill: {e}")
            self._status_message = f"Could not save settings: {_brief(e)}"
        self._update_icon()

    def toggle_presence(self, _icon: object, _item: object) -> None:
        self._refresh_view()
        self._save(presence_enabled=not self._view.presence_enabled)
        self._update_icon()

    def set_schedule_office(self, _icon: object, _item: object) -> None:
        self._save(
            start_time="09:00",
            end_time="17:30",
            active_days=[1, 2, 3, 4, 5],
            schedule_always=False,
            presence_enabled=True,
        )
        self._update_icon()

    def set_schedule_always(self, _icon: object, _item: object) -> None:
        # A flag, not a 00:00-23:59 window: a window has an end, and the
        # diffuser used to switch off for the last minute of every day.
        self._save(
            schedule_always=True,
            active_days=[1, 2, 3, 4, 5, 6, 7],
            presence_enabled=True,
        )
        self._update_icon()

    def toggle_launch_at_login(self, _icon: object, _item: object) -> None:
        target = not is_launch_at_login_enabled()
        if set_launch_at_login(target):
            self._save(launch_at_login=target)
        else:
            self._status_message = "Launch at Login unavailable (not an app bundle?)"
        self._update_icon()

    def trigger_scan(self, _icon: object, _item: object) -> None:
        self._manual_action()
        self.run_coro(self._async_scan_and_pair())

    # -- menu --------------------------------------------------------------

    def _update_icon(self) -> None:
        if self._icon is not None:
            setattr(self._icon, "icon", get_tray_icon(bool(self.is_on), self.intensity))
            setattr(self._icon, "menu", self._build_menu())

    def _build_menu(self) -> Menu:
        try:
            refill_pct = max(0.0, min(100.0, self.tracker.state.refill_percent))
            filled = int(refill_pct / 10)
            bar = "█" * filled + "░" * (10 - filled)
            refill_text = f"Refill: [{bar}] {refill_pct:.1f}%"
        except Exception as e:
            logger.error(f"Could not read refill level: {e}")
            refill_text = "Refill: unknown"
        loc = self._view.location_label

        def _noop(_icon: object, _item: object) -> None:
            pass

        def _make_lvl_action(lvl: int) -> Callable[[object, object], None]:
            def _action(_icon: object, _item: object) -> None:
                self.set_level(lvl)

            return _action

        def _make_lvl_check(lvl: int) -> Callable[[object], bool]:
            def _check(_item: object) -> bool:
                return self.intensity == lvl

            return _check

        intensity_items: List[MenuItem] = [
            MenuItem("Level 1 (Low)", _make_lvl_action(1), checked=_make_lvl_check(1)),
            MenuItem(
                "Level 2 (Medium)", _make_lvl_action(2), checked=_make_lvl_check(2)
            ),
            MenuItem("Level 3 (High)", _make_lvl_action(3), checked=_make_lvl_check(3)),
            MenuItem("Level 4 (Max)", _make_lvl_action(4), checked=_make_lvl_check(4)),
        ]

        refill_items: List[MenuItem] = [
            MenuItem(refill_text, _noop, enabled=False),
            MenuItem("🔄 Reset Refill Bottle (100%)", self.reset_refill),
        ]

        def _check_presence_enabled(_item: object) -> bool:
            return self._view.presence_enabled

        def _check_schedule_office(_item: object) -> bool:
            return (
                self._view.presence_enabled
                and not self._view.schedule_always
                and self._view.start_time == "09:00"
                and self._view.end_time == "17:30"
                and sorted(self._view.active_days) == [1, 2, 3, 4, 5]
            )

        def _check_schedule_always(_item: object) -> bool:
            return self._view.presence_enabled and self._view.schedule_always

        def _check_launch_login(_item: object) -> bool:
            return is_launch_at_login_enabled()

        schedule_items: List[MenuItem] = [
            MenuItem(
                "Enable Desk Presence Tracking",
                self.toggle_presence,
                checked=_check_presence_enabled,
            ),
            Menu.SEPARATOR,
            MenuItem(
                "Office (M–F 9:00 AM – 5:30 PM)",
                self.set_schedule_office,
                checked=_check_schedule_office,
            ),
            MenuItem(
                "All Days (Whenever at Desk)",
                self.set_schedule_always,
                checked=_check_schedule_always,
            ),
        ]

        menu_items: List[MenuItem] = [
            MenuItem(f"Diffuser: {loc}", _noop, enabled=False),
            MenuItem(self._status_message, _noop, enabled=False),
            MenuItem("Refill Bottle", Menu(*refill_items)),
            Menu.SEPARATOR,
            MenuItem("Turn OFF" if self.is_on else "Turn ON", self.toggle_power),
            MenuItem("💨 Trigger Boost Spray", self.trigger_boost),
            MenuItem("Intensity Level", Menu(*intensity_items)),
            Menu.SEPARATOR,
            MenuItem("🕒 Presence Schedule", Menu(*schedule_items)),
            MenuItem(
                "🚀 Launch at Login",
                self.toggle_launch_at_login,
                checked=_check_launch_login,
            ),
            MenuItem("🔍 Scan & Pair Diffuser...", self.trigger_scan),
            Menu.SEPARATOR,
            MenuItem("Quit Airspray", self.quit),
        ]

        return Menu(*menu_items)

    def quit(self, _icon: object, _item: object) -> None:
        self._running = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._icon is not None:
            stop_fn: object = getattr(self._icon, "stop", None)
            if callable(stop_fn):
                stop_fn()

    def run(self) -> None:
        self._running = True
        self._async_thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self._async_thread.start()
        time.sleep(0.1)  # let the BLE loop start before the first presence tick

        self._presence_thread = threading.Thread(
            target=self._run_presence_loop, daemon=True
        )
        self._presence_thread.start()

        self._icon = Icon(
            "airspray",
            icon=get_tray_icon(bool(self.is_on), self.intensity),
            title="AirSpray Diffuser Controller",
            menu=self._build_menu(),
        )
        run_fn: object = getattr(self._icon, "run", None)
        if callable(run_fn):
            run_fn()


def main() -> None:
    app = AirSprayTrayApp()
    app.run()


if __name__ == "__main__":
    main()
