"""Tests for AppConfig, autostart, and self-managing app components."""

from pathlib import Path
from airspray.config import AppConfig
from airspray.autostart import is_launch_at_login_enabled, set_launch_at_login


def test_app_config_defaults() -> None:
    config = AppConfig()
    # None, not a literal, so the UI can fall back to the room the device reports.
    assert config.location_name is None
    assert config.default_intensity == 2
    assert config.presence_enabled is True
    assert config.start_time == "09:00"
    assert config.end_time == "17:30"
    assert config.active_days == [1, 2, 3, 4, 5]


def test_app_config_save_and_load(tmp_path: Path) -> None:
    cfg_file = tmp_path / "test_config.json"
    config = AppConfig(
        device_address="AA:BB:CC:DD:EE:FF",
        location_name="Living Room",
        default_intensity=3,
        presence_enabled=False,
    )
    config.save(cfg_file)
    assert cfg_file.exists()

    loaded = AppConfig.load(cfg_file)
    assert loaded.device_address == "AA:BB:CC:DD:EE:FF"
    assert loaded.location_name == "Living Room"
    assert loaded.default_intensity == 3
    assert loaded.presence_enabled is False


def test_autostart_toggle() -> None:
    # Test setting autostart off without raising errors
    res = set_launch_at_login(False)
    assert isinstance(res, bool)
    status = is_launch_at_login_enabled()
    assert isinstance(status, bool)
