"""Tests for the tray icon generator and the tray's presence decisions."""

# Tests exercise private helpers directly. A file-level setting survives the
# formatter, which moves per-line ignore comments off the line they cover.
# pyright: reportPrivateUsage=false

import asyncio
from pathlib import Path
from typing import List, Optional

import pytest

from airspray import tray
from airspray.config import AppConfig, update_config
from airspray.tray import AirSprayTrayApp, get_tray_icon, render_svg_icon


def test_tray_app_is_importable() -> None:
    """The tray entry point imports pystray and PIL at module scope. A missing
    optional dependency shows up here rather than at launch."""
    assert callable(AirSprayTrayApp)


def test_svg_asset_exists() -> None:
    asset_path = (
        Path(__file__).resolve().parents[2] / "airspray" / "assets" / "icon.svg"
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


def test_power_state_is_unknown_at_startup() -> None:
    assert AirSprayTrayApp().is_on is None


def test_presence_does_not_act_while_an_attempt_is_in_flight() -> None:
    app = AirSprayTrayApp()
    assert app._presence_decision(True, 0.0) is True
    app._auto_in_flight = True
    assert app._presence_decision(True, 10.0) is None


def test_failures_back_off_instead_of_retrying_every_tick() -> None:
    """An unpaired diffuser raised a pairing dialog every 10 s."""
    app = AirSprayTrayApp()
    decide = app._presence_decision
    backoff = app._backoff

    attempts: List[float] = []
    now = 0.0
    while now <= 1200.0:  # twenty minutes of 10 s ticks, every attempt failing
        if decide(True, now) is not None:
            attempts.append(now)
            backoff.fail(now)
        now += 10.0
    assert attempts == [0.0, 10.0, 30.0, 70.0, 150.0, 310.0, 610.0, 910.0]

    # A success resets it.
    backoff.reset()
    assert decide(True, now) is True


def test_manual_toggle_is_respected_until_the_next_presence_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AirSprayTrayApp()
    decide = app._presence_decision
    scheduled: List[str] = []

    def fake_run_coro(coro: "asyncio.Future[None]") -> bool:
        scheduled.append(getattr(coro, "__name__", "coro"))
        coro.close()  # type: ignore[attr-defined]
        return True

    monkeypatch.setattr(app, "run_coro", fake_run_coro)

    assert decide(True, 0.0) is True  # at the desk: presence turns it on
    app.is_on = True

    app.toggle_power(None, None)  # the user turns it off by hand
    app.is_on = False
    assert len(scheduled) == 1
    for tick in (10.0, 20.0, 600.0):
        assert decide(True, tick) is None  # presence leaves it alone

    assert decide(False, 610.0) is None  # screen locks: already off
    assert decide(True, 620.0) is True  # unlock: a new transition, presence resumes


def test_presence_disabled_takes_no_action() -> None:
    app = AirSprayTrayApp()
    assert app._presence_decision(None, 0.0) is None


class FakeDiffuser:
    log: List[str] = []
    refuse_writes = False
    power = True

    def __init__(self, address: Optional[str] = None) -> None:
        self.address = address

    async def __aenter__(self) -> "FakeDiffuser":
        FakeDiffuser.log.append("connect")
        return self

    async def __aexit__(self, *_exc: object) -> None:
        FakeDiffuser.log.append("disconnect")

    async def get_status(self) -> "FakeDiffuser":
        FakeDiffuser.log.append("status")
        return self

    async def turn_on(self) -> None:
        if FakeDiffuser.refuse_writes:
            raise RuntimeError("not paired")
        FakeDiffuser.log.append("on")

    async def turn_off(self) -> None:
        if FakeDiffuser.refuse_writes:
            raise RuntimeError("not paired")
        FakeDiffuser.log.append("off")

    async def set_intensity(self, level: int) -> None:
        FakeDiffuser.log.append(f"intensity {level}")


@pytest.fixture
def fake_diffuser(monkeypatch: pytest.MonkeyPatch) -> type[FakeDiffuser]:
    FakeDiffuser.log = []
    FakeDiffuser.refuse_writes = False
    FakeDiffuser.power = True
    monkeypatch.setattr(tray, "AirWickDiffuser", FakeDiffuser)
    return FakeDiffuser


def test_auto_apply_learns_state_before_acting(
    fake_diffuser: type[FakeDiffuser],
) -> None:
    """The device is already on and presence wants it on: nothing is sent."""
    app = AirSprayTrayApp()
    asyncio.run(app._auto_apply(True, False))
    assert fake_diffuser.log == ["connect", "status", "disconnect"]
    assert app.is_on is True


def test_failed_auto_apply_closes_the_connection_and_backs_off(
    fake_diffuser: type[FakeDiffuser],
) -> None:
    fake_diffuser.refuse_writes = True
    fake_diffuser.power = False
    app = AirSprayTrayApp()
    app._auto_in_flight = True
    asyncio.run(app._auto_apply(True, False))

    assert fake_diffuser.log == ["connect", "status", "disconnect"]
    assert app.is_on is False
    assert app._auto_in_flight is False
    assert "retry in 10s" in app._status_message
    assert AppConfig.load().last_power_on is False  # never recorded as running


def test_tray_settings_do_not_revert_other_writers() -> None:
    """The tray used to save a config it loaded at startup, undoing changes the
    CLI or the Mac app had made since."""
    update_config(refill_percentage=100.0)
    app = AirSprayTrayApp()

    update_config(refill_percentage=55.0, device_address="NEW")  # another process
    app.toggle_presence(None, None)
    app.set_schedule_always(None, None)

    saved = AppConfig.load()
    assert saved.refill_percentage == 55.0
    assert saved.device_address == "NEW"
    assert saved.schedule_always is True
    assert saved.start_time == "09:00"  # the window itself is left alone


def _refuse_launch_at_login(_enabled: bool) -> bool:
    return False


def _accept_launch_at_login(_enabled: bool) -> bool:
    return True


def test_launch_at_login_is_only_saved_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = AirSprayTrayApp()
    monkeypatch.setattr(tray, "is_launch_at_login_enabled", lambda: False)

    monkeypatch.setattr(tray, "set_launch_at_login", _refuse_launch_at_login)
    app.toggle_launch_at_login(None, None)
    assert AppConfig.load().launch_at_login is False

    monkeypatch.setattr(tray, "set_launch_at_login", _accept_launch_at_login)
    app.toggle_launch_at_login(None, None)
    assert AppConfig.load().launch_at_login is True
