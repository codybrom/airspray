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
    """The menu bar app picks fragrances by name; the CLI must speak the same
    vocabulary rather than making the user look up an ID."""
    args = build_parser().parse_args(["config-device", "--fragrance", "Wild Mint"])
    assert resolve_fragrance_id(args.fragrance) == 15


def test_fragrance_accepts_an_id() -> None:
    args = build_parser().parse_args(["config-device", "--fragrance", "15"])
    assert resolve_fragrance_id(args.fragrance) == 15


def test_fragrance_name_is_case_insensitive() -> None:
    assert resolve_fragrance_id("wild MINT") == 15


def test_fragrance_accepts_the_ids_the_mac_app_offers() -> None:
    """The Swift picker lists 33; rejecting 32 or 33 here is a divergence."""
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
