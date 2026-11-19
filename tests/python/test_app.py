"""Tests for AppConfig, autostart, and self-managing app components."""

from pathlib import Path
from typing import Any, List, Tuple

import pytest

from airspray import autostart
from airspray.config import AppConfig


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


class FakeLoginService:
    """Stands in for SMAppService. Returns PyObjC-style (ok, error) tuples."""

    def __init__(self, ok: bool, enabled: bool = False) -> None:
        self.ok = ok
        self.enabled = enabled
        self.calls: List[str] = []

    def registerAndReturnError_(self, _error: Any) -> Tuple[bool, Any]:  # noqa: N802
        self.calls.append("register")
        return (self.ok, None if self.ok else "Operation not permitted")

    def unregisterAndReturnError_(self, _error: Any) -> Tuple[bool, Any]:  # noqa: N802
        self.calls.append("unregister")
        return (self.ok, None if self.ok else "Operation not permitted")

    def status(self) -> int:
        return 1 if self.enabled else 0


def test_autostart_reports_failure_from_pyobjc_tuple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(False, error) is a non-empty tuple and therefore truthy. It must still
    be reported as a failure."""
    service = FakeLoginService(ok=False)
    monkeypatch.setattr(autostart, "_get_sm_app_service", lambda: service)
    assert autostart.set_launch_at_login(True) is False
    assert autostart.set_launch_at_login(False) is False
    assert service.calls == ["register", "unregister"]


def test_autostart_reports_success(monkeypatch: pytest.MonkeyPatch) -> None:
    service = FakeLoginService(ok=True, enabled=True)
    monkeypatch.setattr(autostart, "_get_sm_app_service", lambda: service)
    assert autostart.set_launch_at_login(True) is True
    assert autostart.is_launch_at_login_enabled() is True


def test_autostart_unavailable_without_app_bundle() -> None:
    # conftest makes the service unavailable, as it is for a plain interpreter.
    assert autostart.set_launch_at_login(True) is False
    assert autostart.is_launch_at_login_enabled() is False
