"""Tests for system tray icon generator and SVG asset availability."""

from pathlib import Path
from airspray.tray import AirSprayTrayApp, get_tray_icon, render_svg_icon


def test_tray_app_is_importable() -> None:
    """The tray entry point imports pystray and PIL at module scope; a missing
    optional dependency shows up here rather than at launch."""
    assert callable(AirSprayTrayApp)


def test_svg_asset_exists() -> None:
    asset_path = (
        Path(__file__).resolve().parent.parent / "airspray" / "assets" / "icon.svg"
    )
    assert asset_path.exists()
    content = asset_path.read_text()
    assert "<svg" in content
    assert "</svg>" in content


def test_get_tray_icon() -> None:
    img_active = get_tray_icon(is_active=True, intensity=2)
    assert img_active.size == (128, 128)
    assert img_active.mode == "RGBA"

    img_standby = get_tray_icon(is_active=False, intensity=2)
    assert img_standby.size == (128, 128)
    assert img_standby.mode == "RGBA"

    custom_size = render_svg_icon(is_active=True, intensity=3, size=(128, 128))
    assert custom_size.size == (128, 128)
