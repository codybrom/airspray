"""Tests for presence daemon configuration and schedule logic."""

from airspray.presence import PresenceConfig, PresenceDaemon


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
