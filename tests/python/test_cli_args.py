"""Argument-parsing tests for the CLI.

The intensity byte accepts 0-4. Level 0 pauses spraying without powering the
diffuser down, so the CLI must accept it rather than rejecting it as invalid.
"""

import argparse

import pytest

from airspray.cli import build_parser, resolve_fragrance_id


@pytest.mark.parametrize("level", [0, 1, 2, 3, 4])
def test_intensity_command_accepts_every_valid_level(level: int) -> None:
    args = build_parser().parse_args(["intensity", str(level)])
    assert args.level == level


@pytest.mark.parametrize("level", [0, 4])
def test_on_command_accepts_every_valid_level(level: int) -> None:
    args = build_parser().parse_args(["on", "--intensity", str(level)])
    assert args.intensity == level


@pytest.mark.parametrize("level", [0, 4])
def test_daemon_accepts_every_valid_level(level: int) -> None:
    args = build_parser().parse_args(["daemon", "--intensity", str(level)])
    assert args.intensity == level


def test_intensity_command_rejects_out_of_range() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["intensity", "5"])


def test_fragrance_accepts_a_name() -> None:
    """The menu bar app picks fragrances by name. The CLI must speak the same
    vocabulary rather than making the user look up an ID."""
    args = build_parser().parse_args(["config-device", "--fragrance", "Wild Mint"])
    assert resolve_fragrance_id(args.fragrance) == 15


def test_fragrance_accepts_an_id() -> None:
    args = build_parser().parse_args(["config-device", "--fragrance", "15"])
    assert resolve_fragrance_id(args.fragrance) == 15


def test_fragrance_name_is_case_insensitive() -> None:
    assert resolve_fragrance_id("wild MINT") == 15


def test_fragrance_accepts_the_ids_the_mac_app_offers() -> None:
    """The Swift picker lists 33, so rejecting 32 or 33 here is a divergence."""
    assert resolve_fragrance_id("33") == 33


def test_fragrance_rejects_unknown() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        resolve_fragrance_id("Unobtainium Breeze")
    with pytest.raises(argparse.ArgumentTypeError):
        resolve_fragrance_id("99")


def test_fragrances_command_exists() -> None:
    """The error text and --fragrance help both point at this command."""
    args = build_parser().parse_args(["fragrances"])
    assert args.command == "fragrances"


# ---------------------------------------------------------------------------
# Validation happens at parse time, before any Bluetooth connection.
# ---------------------------------------------------------------------------


def test_on_without_intensity_keeps_current_intensity() -> None:
    assert build_parser().parse_args(["on"]).intensity is None


def test_days_are_validated() -> None:
    from airspray.cli import parse_days

    assert parse_days("Mon, tue") == frozenset({"mon", "tue"})
    assert len(parse_days("all")) == 7
    for bad in ("monday", "mon,funday", "", " , "):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_days(bad)


@pytest.mark.parametrize(
    "argv",
    [
        ["schedule", "set", "1", "--days", "monday"],
        ["schedule", "set", "1", "--start", "9"],
        ["schedule", "set", "1", "--end", "25:00"],
        ["schedule", "set", "1", "--end", "24:00"],
        ["daemon", "--start", "9am"],
        ["service", "install", "--end", "17:3o"],
        ["refill", "adjust", "lots"],
    ],
)
def test_invalid_values_are_rejected_by_the_parser(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args(argv)
    assert exit_info.value.code == 2


def test_daemon_accepts_overnight_and_end_of_day() -> None:
    args = build_parser().parse_args(["daemon", "--start", "22:00", "--end", "6:00"])
    assert (args.start, args.end) == ("22:00", "06:00")
    assert build_parser().parse_args(["daemon"]).end == "24:00"


def test_build_schedule_from_validated_arguments() -> None:
    from airspray.cli import build_schedule, describe_schedule, parse_days

    args = build_parser().parse_args(
        [
            "schedule",
            "set",
            "3",
            "--start",
            "9:00",
            "--end",
            "17:30",
            "--days",
            "sat,sun",
            "--disable",
        ]
    )
    sched = build_schedule(
        args.id,
        args.start,
        args.end,
        args.days,
        args.intensity,
        enabled=not args.disable,
    )
    assert (sched.start_hour, sched.start_min, sched.end_hour, sched.end_min) == (
        9,
        0,
        17,
        30,
    )
    assert sched.days_str == "Sat,Sun"
    assert "DISABLED" in describe_schedule(sched)
    assert parse_days("all") == frozenset(
        {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
    )


def test_refill_percent_is_clamped() -> None:
    args = build_parser().parse_args(["refill", "reset", "--percent", "250"])
    assert args.percent == 100.0
    assert build_parser().parse_args(["refill", "adjust", "-5"]).percent == 0.0


def test_refill_reset_command_clamps_and_reports(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from airspray import cli
    from airspray.config import AppConfig

    assert cli.run(["refill", "reset", "--percent", "250"]) == 0
    assert "100.0%" in capsys.readouterr().out
    assert AppConfig.load().refill_percentage == 100.0


def test_config_write_failure_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A save that fails must not print success."""
    from airspray import cli, config

    def boom(_path: object, _raw: object) -> None:
        raise OSError("Read-only file system")

    monkeypatch.setattr(config, "_write_raw", boom)
    assert cli.run(["refill", "adjust", "50"]) == 1
    captured = capsys.readouterr()
    assert "Read-only file system" in captured.err
    assert "✅" not in captured.out


# ---------------------------------------------------------------------------
# config-device changes only what was given.
# ---------------------------------------------------------------------------


def test_config_device_keeps_the_field_not_given() -> None:
    from airspray.cli import plan_config_device
    from airspray.config import AppConfig

    current = AppConfig(room_id=2, fragrance_id=15, location_name="Nook")

    room, fragrance, changes = plan_config_device(current, None, 9)
    assert (room, fragrance) == (2, 9)
    assert changes == {"room_id": 2, "fragrance_id": 9}  # location untouched

    room, fragrance, changes = plan_config_device(current, "Kitchen", None)
    assert (room, fragrance) == (5, 15)
    assert changes == {"room_id": 5, "fragrance_id": 15, "location_name": None}

    room, fragrance, changes = plan_config_device(current, "Corner Studio", None)
    assert (room, fragrance) == (7, 15)
    assert changes["location_name"] == "Corner Studio"


def test_config_device_requires_something_to_change(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from airspray import cli

    assert cli.run(["config-device"]) == 2
    assert "--location" in capsys.readouterr().err


def test_config_device_sends_and_saves_both_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airspray import cli
    from airspray.config import AppConfig, update_config

    sent: list[tuple[int, int]] = []

    class FakeDiffuser:
        def __init__(self, _address: object = None) -> None:
            pass

        async def __aenter__(self) -> "FakeDiffuser":
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def set_room_and_fragrance(self, room: int, fragrance: int) -> None:
            sent.append((room, fragrance))

    monkeypatch.setattr(cli, "AirWickDiffuser", FakeDiffuser)
    update_config(room_id=2, fragrance_id=15)

    assert cli.run(["config-device", "--fragrance", "Sandalwood"]) == 0
    assert sent == [(2, 18)]
    saved = AppConfig.load()
    assert (saved.room_id, saved.fragrance_id) == (2, 18)


def test_intensity_command_does_not_mark_the_diffuser_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airspray import cli
    from airspray.config import AppConfig, update_config

    class FakeDiffuser:
        def __init__(self, _address: object = None) -> None:
            pass

        async def __aenter__(self) -> "FakeDiffuser":
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def set_intensity(self, _level: int) -> None:
            return None

    monkeypatch.setattr(cli, "AirWickDiffuser", FakeDiffuser)
    update_config(last_power_on=True, last_intensity=2)

    assert cli.run(["intensity", "4"]) == 0
    saved = AppConfig.load()
    assert saved.last_power_on is True
    assert saved.last_intensity == 4


def test_failed_command_reports_and_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from airspray import cli
    from airspray.client import ResponseTimeout

    class SilentDiffuser:
        def __init__(self, _address: object = None) -> None:
            pass

        async def __aenter__(self) -> "SilentDiffuser":
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def get_status(self) -> None:
            raise ResponseTimeout("The diffuser did not answer command 0x01 within 3s.")

    monkeypatch.setattr(cli, "AirWickDiffuser", SilentDiffuser)
    assert cli.run(["status"]) == 1
    captured = capsys.readouterr()
    assert "did not answer" in captured.err
    assert "Power:" not in captured.out


# ---------------------------------------------------------------------------
# Background service definitions.
# ---------------------------------------------------------------------------


def test_daemon_command_for_interpreter_and_frozen_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    from airspray.cli import daemon_command

    monkeypatch.setattr(sys, "executable", "/opt/venv/bin/python")
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert daemon_command(2)[:4] == [
        "/opt/venv/bin/python",
        "-m",
        "airspray.cli",
        "daemon",
    ]

    monkeypatch.setattr(sys, "executable", "/Users/me/dist/airspray")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    command = daemon_command(3, "22:00", "06:00", True, True, "AA:BB")
    assert command == [
        "/Users/me/dist/airspray",
        "daemon",
        "--intensity",
        "3",
        "--start",
        "22:00",
        "--end",
        "06:00",
        "--workdays-only",
        "--no-boost",
        "--address",
        "AA:BB",
    ]
    # ...and that is a command line the CLI itself accepts.
    assert build_parser().parse_args(command[1:]).command == "daemon"


def test_plist_escapes_values() -> None:
    import plistlib

    from airspray.cli import get_plist_content

    command = ["/Users/a&b/<py>", "daemon", "--address", 'x"</string><string>y']
    content = get_plist_content(command, "/Users/a&b", "/Users/a&b/.airspray")
    parsed = plistlib.loads(content.encode())
    assert parsed["ProgramArguments"] == command
    assert parsed["WorkingDirectory"] == "/Users/a&b"
    assert parsed["StandardOutPath"] == "/Users/a&b/.airspray/airspray.log"


def test_systemd_unit_quotes_arguments() -> None:
    import shlex

    from airspray.cli import get_systemd_service_content

    command = ["/home/me/My Projects/.venv/bin/python", "-m", "airspray.cli", "daemon"]
    content = get_systemd_service_content(command, "/home/me/My Projects")
    line = next(l for l in content.splitlines() if l.startswith("ExecStart="))
    assert shlex.split(line[len("ExecStart=") :]) == command
    assert "TimeoutStopSec=" in content


def test_service_install_fails_when_launchctl_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import subprocess
    import sys

    from airspray import cli

    calls: list[list[str]] = []

    def fake_run(
        argv: list[str], **_kwargs: object
    ) -> "subprocess.CompletedProcess[bytes]":
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1 if argv[1] == "load" else 0)

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert cli.run(["service", "install"]) == 1
    assert [c[1] for c in calls] == ["unload", "load"]
    assert "Failed to load" in capsys.readouterr().err
    assert cli.SERVICE_LOG_DIR.is_dir()  # created for launchd (in the sandbox)
    assert cli.LAUNCHD_PLIST_PATH.exists()
