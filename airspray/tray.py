"""
Cross-Platform Self-Managing System Tray / Menu Bar App for Air Wick Diffuser.
Supports macOS, Windows, and Linux with native vector SVG assets, automatic presence
tracking, schedule presets, and launch-at-login settings.
"""

import asyncio
import logging
import threading
import time
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
from airspray.config import AppConfig
from airspray.presence import is_screen_locked
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


class AirSprayTrayApp:
    def __init__(self, address: Optional[str] = None) -> None:
        self.config: AppConfig = AppConfig.load()
        if address:
            self.config.device_address = address
            self.config.save()

        self.tracker: RefillTracker = RefillTracker()
        self.is_on: bool = False
        self.intensity: int = self.config.default_intensity
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._async_thread: Optional[threading.Thread] = None
        self._presence_thread: Optional[threading.Thread] = None
        self._icon: object = None
        self._running: bool = False
        self._status_message: str = "Ready"

    def _run_async_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run_coro(self, coro: Coroutine[object, object, None]) -> None:
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _run_presence_loop(self) -> None:
        """Background thread monitoring desk presence and schedule."""
        from datetime import datetime

        def parse_minutes(t_str: str, default: int) -> int:
            try:
                parts = [int(p.strip()) for p in t_str.split(":")]
                return parts[0] * 60 + (parts[1] if len(parts) > 1 else 0)
            except Exception:
                return default

        while self._running:
            try:
                if self.config.presence_enabled:
                    now = datetime.now()
                    day_1_to_7 = now.weekday() + 1
                    in_days = day_1_to_7 in self.config.active_days
                    curr_min = now.hour * 60 + now.minute
                    start_min = parse_minutes(self.config.start_time, 0)
                    end_min = parse_minutes(self.config.end_time, 1440)
                    in_time = start_min <= curr_min < end_min

                    in_schedule = in_days and in_time
                    locked = is_screen_locked()
                    is_present = in_schedule and not locked

                    should_be_on = is_present
                    if should_be_on != self.is_on:
                        if should_be_on:
                            self._status_message = "Desk Active • Auto ON"
                            self.run_coro(self._async_turn_on())
                        else:
                            reason = (
                                "Screen Locked"
                                if (in_schedule and locked)
                                else "Off Hours"
                            )
                            self._status_message = f"{reason} • Auto OFF"
                            self.run_coro(self._async_turn_off())
            except Exception as e:
                logger.error(f"Error in presence loop: {e}")
            time.sleep(10)

    async def _async_turn_on(self) -> None:
        try:
            diffuser = AirWickDiffuser(self.config.device_address)
            await diffuser.connect()
            await diffuser.turn_on()
            await diffuser.set_intensity(self.intensity)
            self.tracker.update_usage(True, self.intensity)
            await diffuser.disconnect()
            self.is_on = True
            self._update_icon()
        except Exception as e:
            logger.error(f"Error turning on diffuser: {e}")

    async def _async_turn_off(self) -> None:
        try:
            diffuser = AirWickDiffuser(self.config.device_address)
            await diffuser.connect()
            await diffuser.turn_off()
            self.tracker.update_usage(False, 0)
            await diffuser.disconnect()
            self.is_on = False
            self._update_icon()
        except Exception as e:
            logger.error(f"Error turning off diffuser: {e}")

    async def _async_boost(self) -> None:
        try:
            diffuser = AirWickDiffuser(self.config.device_address)
            await diffuser.connect()
            await diffuser.boost()
            self.tracker.record_boost()
            await diffuser.disconnect()
        except Exception as e:
            logger.error(f"Error triggering boost: {e}")

    async def _async_set_intensity(self, level: int) -> None:
        try:
            diffuser = AirWickDiffuser(self.config.device_address)
            await diffuser.connect()
            await diffuser.set_intensity(level)
            self.intensity = level
            self.config.default_intensity = level
            self.config.save()
            self.tracker.update_usage(self.is_on, level)
            await diffuser.disconnect()
            self._update_icon()
        except Exception as e:
            logger.error(f"Error setting intensity: {e}")

    async def _async_scan_and_pair(self) -> None:
        try:
            self._status_message = "Scanning for Diffusers..."
            self._update_icon()
            found: List[BLEDevice] = await scan_for_diffusers(timeout=5.0)
            if found:
                dev = found[0]
                self.config.device_address = dev.address
                self.config.save()
                self._status_message = f"Connected: {dev.name or dev.address}"
            else:
                self._status_message = "No Diffusers Found"
            self._update_icon()
        except Exception as e:
            self._status_message = f"Scan Failed: {e}"
            self._update_icon()

    def toggle_power(self, _icon: object, _item: object) -> None:
        if self.is_on:
            self.run_coro(self._async_turn_off())
        else:
            self.run_coro(self._async_turn_on())

    def trigger_boost(self, _icon: object, _item: object) -> None:
        self.run_coro(self._async_boost())

    def set_level(self, level: int) -> None:
        self.run_coro(self._async_set_intensity(level))

    def reset_refill(self, _icon: object, _item: object) -> None:
        self.tracker.reset_refill()
        self._update_icon()

    def toggle_presence(self, _icon: object, _item: object) -> None:
        self.config.presence_enabled = not self.config.presence_enabled
        self.config.save()
        self._update_icon()

    def set_schedule_office(self, _icon: object, _item: object) -> None:
        self.config.start_time = "09:00"
        self.config.end_time = "17:30"
        self.config.active_days = [1, 2, 3, 4, 5]
        self.config.presence_enabled = True
        self.config.save()
        self._update_icon()

    def set_schedule_always(self, _icon: object, _item: object) -> None:
        self.config.start_time = "00:00"
        self.config.end_time = "23:59"
        self.config.active_days = [1, 2, 3, 4, 5, 6, 7]
        self.config.presence_enabled = True
        self.config.save()
        self._update_icon()

    def toggle_launch_at_login(self, _icon: object, _item: object) -> None:
        current = is_launch_at_login_enabled()
        target = not current
        if set_launch_at_login(target):
            self.config.launch_at_login = target
            self.config.save()
        self._update_icon()

    def trigger_scan(self, _icon: object, _item: object) -> None:
        self.run_coro(self._async_scan_and_pair())

    def _update_icon(self) -> None:
        if self._icon is not None:
            setattr(self._icon, "icon", get_tray_icon(self.is_on, self.intensity))
            setattr(self._icon, "menu", self._build_menu())

    def _build_menu(self) -> Menu:
        refill_pct = self.tracker.state.refill_percent
        filled = int(refill_pct / 10)
        bar = "█" * filled + "░" * (10 - filled)
        loc = self.tracker.state.location_name

        refill_text = f"Refill: [{bar}] {refill_pct:.1f}%"

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
            return self.config.presence_enabled

        def _check_schedule_office(_item: object) -> bool:
            return (
                self.config.presence_enabled
                and self.config.start_time == "09:00"
                and self.config.end_time == "17:30"
                and len(self.config.active_days) == 5
            )

        def _check_schedule_always(_item: object) -> bool:
            return (
                self.config.presence_enabled
                and self.config.start_time == "00:00"
                and self.config.end_time == "23:59"
                and len(self.config.active_days) == 7
            )

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

        self._presence_thread = threading.Thread(
            target=self._run_presence_loop, daemon=True
        )
        self._presence_thread.start()

        time.sleep(0.1)
        self._icon = Icon(
            "airspray",
            icon=get_tray_icon(self.is_on, self.intensity),
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
