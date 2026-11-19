"""Tests for refill tracking and liquid capacity calculations."""

import json
import time
from pathlib import Path

import pytest

from airspray.config import AppConfig, update_config
from airspray.refill import (
    BOOST_CONSUMPTION_PERCENT,
    CONSUMPTION_RATE_PER_SEC,
    RefillTracker,
)


@pytest.fixture
def tracker(tmp_path: Path) -> RefillTracker:
    """A tracker backed by a scratch copy of the shared config file."""
    return RefillTracker(
        config_path=tmp_path / "config.json",
        legacy_path=tmp_path / "absent-state.json",
    )


def _raw(tracker: RefillTracker) -> dict[str, object]:
    loaded: dict[str, object] = json.loads(tracker.config_path.read_text())
    return loaded


def test_refill_initial_state(tracker: RefillTracker) -> None:
    assert tracker.state.refill_percent == 100.0
    assert tracker.state.total_boosts == 0


def test_constructing_a_tracker_writes_nothing(tracker: RefillTracker) -> None:
    assert not tracker.config_path.exists()


def test_refill_boost_deduction(tracker: RefillTracker) -> None:
    tracker.record_boost()
    assert tracker.state.total_boosts == 1
    assert (
        abs(tracker.state.refill_percent - (100.0 - BOOST_CONSUMPTION_PERCENT)) < 1e-5
    )


def test_refill_usage_tracking(tracker: RefillTracker) -> None:
    update_config(
        tracker.config_path,
        last_active_timestamp=time.time() - 3600,  # 1 hour ago
        last_power_on=True,
        last_intensity=2,
    )
    tracker.update_usage(power=True, intensity=2)
    expected = 100.0 - CONSUMPTION_RATE_PER_SEC[2] * 3600
    assert abs(tracker.state.refill_percent - expected) < 0.01


def test_usage_is_a_delta_on_the_level_currently_on_disk(
    tracker: RefillTracker,
) -> None:
    """A long-running tracker must not put back a level another process reset."""
    tracker.adjust_level(40.0)
    tracker.update_usage(True, 2)  # the daemon is now "running" at 40%

    # Meanwhile the Mac app (or `airspray refill reset`) installs a new bottle.
    update_config(tracker.config_path, refill_percentage=100.0)

    tracker.update_usage(True, 2)  # the daemon's next tick
    assert tracker.state.refill_percent > 99.9


def test_intensity_only_update_keeps_recorded_power(tracker: RefillTracker) -> None:
    tracker.update_usage(True, 2)
    tracker.update_usage(None, 4)
    assert tracker.state.last_power is True
    assert tracker.state.last_intensity == 4

    tracker.update_usage(False, None)
    assert tracker.state.last_power is False
    assert tracker.state.last_intensity == 4


def test_refill_reset_and_adjust(tracker: RefillTracker) -> None:
    tracker.adjust_level(75.5)
    assert tracker.state.refill_percent == 75.5

    tracker.reset_refill(100.0)
    assert tracker.state.refill_percent == 100.0
    assert tracker.state.total_boosts == 0


@pytest.mark.parametrize("given,expected", [(250.0, 100.0), (-5.0, 0.0), (42.0, 42.0)])
def test_refill_reset_is_clamped(
    tracker: RefillTracker, given: float, expected: float
) -> None:
    assert tracker.reset_refill(given) == expected
    assert _raw(tracker)["refillPercentage"] == expected


def test_no_location_name_is_forced_into_the_shared_file(
    tracker: RefillTracker,
) -> None:
    """customLocationName is written only when the user sets it. Until then the
    label falls back to the room preset, in both apps."""
    tracker.record_boost()
    tracker.update_usage(True, 2)
    assert "customLocationName" not in _raw(tracker)
    assert tracker.state.location_name is None

    update_config(tracker.config_path, room_id=8)
    assert tracker.state.location_label == "Office"


def test_refill_custom_location_name(tracker: RefillTracker) -> None:
    tracker.set_location_name("Studio Desk")
    assert tracker.state.location_name == "Studio Desk"

    # Reload from disk -- and from the same file the Swift app reads.
    tracker2 = RefillTracker(
        config_path=tracker.config_path, legacy_path=tracker.legacy_path
    )
    assert tracker2.state.location_name == "Studio Desk"
    assert tracker2.state.location_label == "Studio Desk"

    # Clearing removes the key rather than writing a null or a default.
    tracker.set_location_name("")
    assert "customLocationName" not in _raw(tracker)


def _write_legacy(path: Path, percent: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "refill_percent": percent,
                "total_boosts": 7,
                "installed_date": "2026-01-02",
                "location_name": "Office",
            }
        )
    )


def test_legacy_state_is_migrated_once(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy" / "state.json"
    config_path = tmp_path / "config.json"
    _write_legacy(legacy, 61.5)

    tracker = RefillTracker(config_path=config_path, legacy_path=legacy)
    assert tracker.state.refill_percent == 61.5
    assert tracker.state.total_boosts == 7
    assert tracker.state.location_name is None  # the old forced default
    assert not legacy.exists()  # retired, cannot be read again

    # Even if the old file reappears, a level that is already there wins --
    # including a level of exactly 100%.
    tracker.reset_refill(100.0)
    _write_legacy(legacy, 61.5)
    again = RefillTracker(config_path=config_path, legacy_path=legacy)
    assert again.state.refill_percent == 100.0
    assert AppConfig.load(config_path).total_puffs == 0
