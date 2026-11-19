"""Request/response matching in the BLE client, against a fake GATT client."""

# Tests exercise private helpers directly. A file-level setting survives the
# formatter, which moves per-line ignore comments off the line they cover.
# pyright: reportPrivateUsage=false

import asyncio
from typing import List

import pytest

from airspray.client import AirWickDiffuser, DiffuserError, ResponseTimeout
from airspray.protocol import (
    CMD_BATTERY_REPORT,
    CMD_QUERY_SCHEDULE,
    CMD_QUERY_STATUS,
    CMD_SET_TIME,
    CMD_SPRAY_REPORT,
    Schedule,
    construct_command,
    decode_packet,
    encode_set_schedule,
)


class FakeGatt:
    """Answers each write with the queued notification packets."""

    is_connected = True

    def __init__(self, diffuser: AirWickDiffuser, replies: List[bytes]) -> None:
        self.diffuser = diffuser
        self.replies = replies
        self.written: List[bytes] = []

    async def write_gatt_char(
        self, _uuid: str, data: bytes, response: bool = True
    ) -> None:
        self.written.append(bytes(data))
        loop = asyncio.get_running_loop()
        for packet in self.replies:
            loop.call_soon(
                self.diffuser._on_notify,
                None,
                bytearray(packet),
            )


def _diffuser(replies: List[bytes]) -> AirWickDiffuser:
    diffuser = AirWickDiffuser("fake-address")
    setattr(diffuser, "_client", FakeGatt(diffuser, replies))
    return diffuser


STATUS_ON = construct_command(CMD_QUERY_STATUS, [1, 0, 3, 4, 0, 0])
SPRAY = construct_command(CMD_SPRAY_REPORT, [1])
BATTERY = construct_command(CMD_BATTERY_REPORT, [3])
TIME_ECHO = construct_command(CMD_SET_TIME, [1, 2, 3, 4])


def test_status_waits_past_unrelated_notifications() -> None:
    """Spray/battery reports and echoes used to satisfy the wait, so `status`
    printed the defaults (OFF, intensity 0) as if they were real."""
    diffuser = _diffuser([SPRAY, BATTERY, TIME_ECHO, STATUS_ON])
    status = asyncio.run(diffuser.get_status(timeout=1.0))
    assert status.power is True
    assert status.intensity == 3
    assert status.battery_level == 4


def test_status_timeout_raises_instead_of_returning_defaults() -> None:
    diffuser = _diffuser([SPRAY, BATTERY])  # chatter, but never the answer
    with pytest.raises(ResponseTimeout):
        asyncio.run(diffuser.get_status(timeout=0.05))


def test_silent_diffuser_raises() -> None:
    diffuser = _diffuser([])
    with pytest.raises(ResponseTimeout):
        asyncio.run(diffuser.get_schedule(1, timeout=0.05))


def _schedule_reply(schedule: Schedule) -> bytes:
    decoded = decode_packet(encode_set_schedule(schedule))
    assert decoded is not None
    return construct_command(CMD_QUERY_SCHEDULE, decoded[1])


def test_schedule_query_ignores_other_slots_and_reports() -> None:
    wanted = Schedule(id=3, start_hour=7, end_hour=9, intensity=1)
    other = Schedule(id=5, start_hour=1, end_hour=2)
    diffuser = _diffuser([SPRAY, _schedule_reply(other), _schedule_reply(wanted)])
    got = asyncio.run(diffuser.get_schedule(3, timeout=1.0))
    assert got is not None
    assert (got.id, got.start_hour, got.end_hour) == (3, 7, 9)


def test_list_includes_disabled_slots_and_skips_empty_ones() -> None:
    class PerSlot(FakeGatt):
        async def write_gatt_char(
            self, _uuid: str, data: bytes, response: bool = True
        ) -> None:
            decoded = decode_packet(bytes(data))
            assert decoded is not None
            slot = decoded[1][0]
            if slot == 2:
                sched = Schedule(id=2, enabled=False, start_hour=6, end_hour=7)
                self.replies = [_schedule_reply(sched)]
            else:  # a cleared slot reads back all zeros
                self.replies = [
                    construct_command(CMD_QUERY_SCHEDULE, [slot, 0, 0, 0, 0, 0, 0, 0])
                ]
            await super().write_gatt_char(_uuid, data, response)

    diffuser = AirWickDiffuser("fake-address")
    setattr(diffuser, "_client", PerSlot(diffuser, []))
    schedules = asyncio.run(diffuser.get_all_schedules(timeout=1.0))
    assert [(s.id, s.enabled) for s in schedules] == [(2, False)]


def test_command_when_not_connected_raises() -> None:
    with pytest.raises(DiffuserError):
        asyncio.run(AirWickDiffuser("fake-address").turn_on())


def test_failed_subscribe_disconnects_and_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What an unpaired computer sees: connect works, the first protected call
    is refused. The connection must be closed, not leaked."""
    events: List[str] = []

    class RefusingClient:
        def __init__(self, _device: object, timeout: float = 0.0) -> None:
            self.is_connected = False

        async def connect(self) -> None:
            self.is_connected = True
            events.append("connect")

        async def start_notify(self, _uuid: str, _cb: object) -> None:
            raise RuntimeError("Authentication is insufficient")

        async def stop_notify(self, _uuid: str) -> None:
            events.append("stop_notify")

        async def disconnect(self) -> None:
            self.is_connected = False
            events.append("disconnect")

    monkeypatch.setattr("airspray.client.BleakClient", RefusingClient)
    diffuser = AirWickDiffuser("fake-address")
    setattr(diffuser, "_device", object())

    async def use() -> None:
        async with diffuser:
            events.append("body ran")

    with pytest.raises(DiffuserError, match="paired"):
        asyncio.run(use())
    assert "body ran" not in events
    assert events[0] == "connect" and events[-1] == "disconnect"
    assert diffuser.is_connected is False
