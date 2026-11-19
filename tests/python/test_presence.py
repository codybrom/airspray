"""Tests for presence daemon configuration and schedule logic."""

# Tests exercise private helpers directly. A file-level setting survives the
# formatter, which moves per-line ignore comments off the line they cover.
# pyright: reportPrivateUsage=false

import asyncio
from datetime import datetime
from typing import List, Optional

import pytest

from airspray import presence
from airspray.config import AppConfig
from airspray.presence import (
    Backoff,
    PresenceConfig,
    PresenceDaemon,
    is_within_window,
    parse_clock,
    presence_target,
)


def test_presence_config_defaults() -> None:
    config = PresenceConfig()
    assert config.intensity == 2
    assert config.boost_on_arrival is True
    assert config.check_interval_seconds == 20.0
    assert config.start_time == "00:00"
    assert config.end_time == "24:00"


def test_presence_schedule_always_open() -> None:
    daemon = PresenceDaemon(PresenceConfig(start_time="00:00", end_time="24:00"))
    assert daemon.is_within_schedule() is True


def test_presence_schedule_closed() -> None:
    daemon = PresenceDaemon(PresenceConfig(start_time="00:00", end_time="00:00"))
    assert daemon.is_within_schedule() is False


def _m(text: str) -> int:
    return parse_clock(text, allow_24=True)


@pytest.mark.parametrize(
    "now,start,end,expected",
    [
        ("09:00", "09:00", "17:30", True),  # start is inclusive
        ("17:29", "09:00", "17:30", True),
        ("17:30", "09:00", "17:30", False),  # end is exclusive
        ("08:59", "09:00", "17:30", False),
        ("23:59", "00:00", "24:00", True),  # the last minute of the day
        # Overnight: 22:00 -> 06:00 the next morning.
        ("22:00", "22:00", "06:00", True),
        ("23:30", "22:00", "06:00", True),
        ("00:00", "22:00", "06:00", True),
        ("05:59", "22:00", "06:00", True),
        ("06:00", "22:00", "06:00", False),
        ("12:00", "22:00", "06:00", False),
        ("21:59", "22:00", "06:00", False),
    ],
)
def test_window_including_overnight(
    now: str, start: str, end: str, expected: bool
) -> None:
    assert is_within_window(_m(now), _m(start), _m(end)) is expected


def test_daemon_supports_overnight_window() -> None:
    daemon = PresenceDaemon(PresenceConfig(start_time="22:00", end_time="06:00"))
    assert daemon.is_within_schedule(datetime(2026, 11, 18, 23, 15)) is True
    assert daemon.is_within_schedule(datetime(2026, 11, 19, 3, 0)) is True
    assert daemon.is_within_schedule(datetime(2026, 11, 19, 12, 0)) is False


@pytest.mark.parametrize("bad", ["9", "25:00", "09:60", "nine", "", "9:5:1", "24:00"])
def test_parse_clock_rejects_invalid(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_clock(bad)


def test_parse_clock_accepts_24_only_as_end_of_day() -> None:
    assert parse_clock("24:00", allow_24=True) == 1440
    assert parse_clock("9:05") == 545


WEDNESDAY_NOON = datetime(2026, 11, 18, 12, 0)
WEDNESDAY_LAST_MINUTE = datetime(2026, 11, 18, 23, 59)
SATURDAY_NOON = datetime(2026, 11, 21, 12, 0)


def test_presence_target_follows_shared_config() -> None:
    office = AppConfig()
    assert presence_target(office, WEDNESDAY_NOON, locked=False) is True
    assert presence_target(office, WEDNESDAY_NOON, locked=True) is False
    assert presence_target(office, SATURDAY_NOON, locked=False) is False
    assert (
        presence_target(AppConfig(presence_enabled=False), WEDNESDAY_NOON, False)
        is None
    )


def test_all_day_does_not_switch_off_at_2359() -> None:
    always = AppConfig(schedule_always=True)
    assert presence_target(always, WEDNESDAY_LAST_MINUTE, locked=False) is True
    assert presence_target(always, SATURDAY_NOON, locked=False) is True
    # A config written by the old preset (00:00-23:59, every day).
    old_preset = AppConfig(
        start_time="00:00", end_time="23:59", active_days=[1, 2, 3, 4, 5, 6, 7]
    )
    assert presence_target(old_preset, WEDNESDAY_LAST_MINUTE, locked=False) is True


def test_backoff_doubles_to_a_cap_and_resets() -> None:
    backoff = Backoff()
    assert backoff.ready(0.0)
    waits = [backoff.fail(0.0) for _ in range(7)]
    assert waits == [10.0, 20.0, 40.0, 80.0, 160.0, 300.0, 300.0]
    assert not backoff.ready(299.0)
    assert backoff.ready(300.0)
    backoff.reset()
    assert backoff.ready(0.0)
    assert backoff.fail(0.0) == 10.0


class FakeDiffuser:
    """Replaces AirWickDiffuser inside the daemon."""

    fail_connect = False
    fail_command = False
    log: List[str] = []

    def __init__(self, address: Optional[str] = None) -> None:
        self.target_address = address

    async def connect(self, timeout: float = 15.0) -> None:
        FakeDiffuser.log.append("connect")
        if FakeDiffuser.fail_connect:
            raise RuntimeError("not found")

    async def disconnect(self) -> None:
        FakeDiffuser.log.append("disconnect")

    async def turn_on(self) -> None:
        if FakeDiffuser.fail_command:
            raise RuntimeError("write refused: not paired")
        FakeDiffuser.log.append("on")

    async def turn_off(self) -> None:
        if FakeDiffuser.fail_command:
            raise RuntimeError("write refused: not paired")
        FakeDiffuser.log.append("off")

    async def set_intensity(self, level: int) -> None:
        FakeDiffuser.log.append(f"intensity {level}")

    async def boost(self) -> None:
        FakeDiffuser.log.append("boost")


@pytest.fixture
def fake_diffuser(monkeypatch: pytest.MonkeyPatch) -> type[FakeDiffuser]:
    FakeDiffuser.fail_connect = False
    FakeDiffuser.fail_command = False
    FakeDiffuser.log = []
    monkeypatch.setattr(presence, "AirWickDiffuser", FakeDiffuser)
    return FakeDiffuser


def test_failed_apply_records_no_usage_and_disconnects(
    fake_diffuser: type[FakeDiffuser],
) -> None:
    """An unpaired diffuser refuses the write. It must not be accounted as
    running, and the connection must still be closed."""
    fake_diffuser.fail_command = True
    daemon = PresenceDaemon(PresenceConfig())
    assert asyncio.run(daemon.apply_state(True)) is False
    assert fake_diffuser.log == ["connect", "disconnect"]
    assert daemon.tracker.state.last_power is False
    assert daemon.tracker.state.total_boosts == 0


def test_successful_apply_records_usage(fake_diffuser: type[FakeDiffuser]) -> None:
    daemon = PresenceDaemon(PresenceConfig(intensity=3))
    assert asyncio.run(daemon.apply_state(True)) is True
    assert fake_diffuser.log == ["connect", "on", "intensity 3", "boost", "disconnect"]
    state = daemon.tracker.state
    assert (state.last_power, state.last_intensity, state.total_boosts) == (True, 3, 1)


def test_daemon_retries_a_failed_apply_with_backoff(
    fake_diffuser: type[FakeDiffuser], monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = {"t": 1000.0}
    monkeypatch.setattr(presence, "_now", lambda: clock["t"])
    daemon = PresenceDaemon(PresenceConfig())
    monkeypatch.setattr(daemon, "check_presence", lambda: True)
    daemon.is_present = True

    def attempts() -> int:
        return fake_diffuser.log.count("connect")

    fake_diffuser.fail_connect = True
    asyncio.run(daemon._step())
    assert attempts() == 1 and daemon.applied is None

    clock["t"] += 5  # the next tick is inside the 10 s backoff: no attempt
    asyncio.run(daemon._step())
    assert attempts() == 1

    clock["t"] += 6  # past it: retried (this used to never happen)
    asyncio.run(daemon._step())
    assert attempts() == 2

    clock["t"] += 15  # second failure doubled the wait to 20 s
    asyncio.run(daemon._step())
    assert attempts() == 2

    fake_diffuser.fail_connect = False
    clock["t"] += 6
    asyncio.run(daemon._step())
    assert attempts() == 3 and daemon.applied is True

    clock["t"] += 20  # in sync: nothing more to do
    asyncio.run(daemon._step())
    assert attempts() == 3


def test_cancellation_turns_the_diffuser_off(
    fake_diffuser: type[FakeDiffuser], monkeypatch: pytest.MonkeyPatch
) -> None:
    """SIGTERM and Ctrl-C both cancel the daemon task."""
    daemon = PresenceDaemon(PresenceConfig(check_interval_seconds=30.0))
    monkeypatch.setattr(daemon, "check_presence", lambda: True)

    async def scenario() -> None:
        task = asyncio.ensure_future(daemon.run())
        await asyncio.sleep(0.05)  # first apply done. Now sleeping
        task.cancel()
        await task

    asyncio.run(scenario())
    assert fake_diffuser.log[-3:] == ["connect", "off", "disconnect"]
    assert daemon.applied is False
    assert daemon.tracker.state.last_power is False
