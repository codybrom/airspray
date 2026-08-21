"""Tests for refill tracking and liquid capacity calculations."""

import time
from pathlib import Path

import pytest

from airspray.refill import BOOST_CONSUMPTION_PERCENT, RefillTracker


@pytest.fixture
def tracker(tmp_path: Path) -> RefillTracker:
    """A tracker backed by a scratch copy of the shared config file.

    legacy_path is pointed at a file that does not exist so the migration branch
    cannot reach into the real ~/.airspray/state.json.
    """
    return RefillTracker(
        config_path=tmp_path / "config.json",
        legacy_path=tmp_path / "absent-state.json",
    )


def test_refill_initial_state(tracker: RefillTracker) -> None:
    assert tracker.state.refill_percent == 100.0
    assert tracker.state.total_boosts == 0


def test_refill_boost_deduction(tracker: RefillTracker) -> None:
    tracker.record_boost()
    assert tracker.state.total_boosts == 1
    assert (
        abs(tracker.state.refill_percent - (100.0 - BOOST_CONSUMPTION_PERCENT)) < 1e-5
    )


def test_refill_usage_tracking(tracker: RefillTracker) -> None:
    tracker.state.last_updated = time.time() - 3600  # 1 hour ago
    tracker.state.last_power = True
    tracker.state.last_intensity = 2

    tracker.update_usage(power=True, intensity=2)
    assert tracker.state.refill_percent < 100.0
    assert tracker.state.refill_percent > 90.0


def test_refill_reset_and_adjust(tracker: RefillTracker) -> None:
    tracker.adjust_level(75.5)
    assert tracker.state.refill_percent == 75.5

    tracker.reset_refill(100.0)
    assert tracker.state.refill_percent == 100.0
    assert tracker.state.total_boosts == 0


def test_refill_custom_location_name(tracker: RefillTracker) -> None:
    assert tracker.state.location_name == "Office"
    tracker.set_location_name("Studio Desk")
    assert tracker.state.location_name == "Studio Desk"

    # Reload from disk -- and from the same file the Swift app reads.
    tracker2 = RefillTracker(
        config_path=tracker.config_path, legacy_path=tracker.legacy_path
    )
    assert tracker2.state.location_name == "Studio Desk"
