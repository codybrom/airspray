"""The shared config file contract: atomic, merge-on-save, never clobbering."""

# Tests exercise private helpers directly. A file-level setting survives the
# formatter, which moves per-line ignore comments off the line they cover.
# pyright: reportPrivateUsage=false

import json
import os
from pathlib import Path
from typing import Any

import pytest

from airspray import config as app_config
from airspray.config import AppConfig, modify_config, update_config


def _raw(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(path.read_text())
    return loaded


def test_missing_file_is_a_fresh_config(tmp_path: Path) -> None:
    assert AppConfig.load(tmp_path / "nope.json") == AppConfig()


def test_update_touches_only_the_named_keys(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"swiftOnlyKey": 1, "lastAddress": "AA", "lastRoomId": 8})
    )
    update_config(path, default_intensity=3)
    assert _raw(path) == {
        "swiftOnlyKey": 1,
        "lastAddress": "AA",
        "lastRoomId": 8,
        "defaultIntensity": 3,
    }


def test_stale_object_does_not_revert_other_writers(tmp_path: Path) -> None:
    """The tray used to hold a config from startup and write every field back."""
    path = tmp_path / "config.json"
    update_config(path, refill_percentage=100.0, device_address="OLD")
    stale = AppConfig.load(path)

    # Another process changes things after `stale` was loaded.
    update_config(path, refill_percentage=55.0, device_address="NEW")

    stale.presence_enabled = False
    stale.save(path)

    raw = _raw(path)
    assert raw["isDeskPresenceEnabled"] is False
    assert raw["refillPercentage"] == 55.0
    assert raw["lastAddress"] == "NEW"


def test_modify_applies_a_delta_to_the_current_value(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    update_config(path, total_puffs=4)
    modify_config(lambda current: {"total_puffs": current.total_puffs + 1}, path)
    assert _raw(path)["totalPuffsCount"] == 5


def test_cleared_optional_removes_the_key(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    update_config(path, location_name="Studio", last_active_timestamp=12.5)
    update_config(path, location_name=None, last_active_timestamp=None)
    raw = _raw(path)
    assert "customLocationName" not in raw
    assert "lastActiveTimestamp" not in raw


def test_explicit_null_loads_like_an_absent_key(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"customLocationName": None, "lastRoomId": None}))
    loaded = AppConfig.load(path)
    assert loaded.location_name is None
    assert loaded.room_id == AppConfig().room_id


def test_unparseable_file_is_moved_aside_not_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    truncated = '{"lastAddress": "AA", "swiftOnlyKey": 1, "refillPercentage": 37.5'
    path.write_text(truncated)

    update_config(path, default_intensity=4)

    corrupt = tmp_path / "config.json.corrupt"
    assert corrupt.read_text() == truncated  # nothing was silently destroyed
    assert _raw(path) == {"defaultIntensity": 4}


def test_torn_read_is_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A reader that catches a writer mid-write must wait, not see 'empty'."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"lastAddress": "AA"}))

    real = app_config._parse_file
    calls = {"n": 0}

    def flaky(p: Path) -> Any:
        calls["n"] += 1
        return None if calls["n"] < 3 else real(p)

    monkeypatch.setattr(app_config, "_parse_file", flaky)
    assert AppConfig.load(path).device_address == "AA"
    assert not (tmp_path / "config.json.corrupt").exists()


def test_write_is_atomic_and_leaves_no_temp_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    update_config(path, device_address="AA")
    before = path.read_text()

    def boom(_src: Any, _dst: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        update_config(path, device_address="BB")

    assert path.read_text() == before  # the old file is intact, not truncated
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]


def test_write_errors_propagate(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        AppConfig(default_intensity=4).save(tmp_path / "missing-dir" / "config.json")


def test_string_false_is_not_true(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"isDeskPresenceEnabled": "false", "activeDays": "12345"})
    )
    loaded = AppConfig.load(path)
    assert loaded.presence_enabled is False
    assert loaded.active_days == [1, 2, 3, 4, 5]  # malformed list -> default


def test_bad_value_keeps_the_default_for_that_field_only(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"defaultIntensity": "loud", "lastRoomId": 8}))
    loaded = AppConfig.load(path)
    assert loaded.default_intensity == 2
    assert loaded.room_id == 8
