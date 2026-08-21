"""Tests for Air Wick protocol encoding, decoding, and checksum calculation."""

import pytest
from datetime import datetime
from airspray.protocol import (
    Schedule,
    calculate_checksum,
    construct_command,
    decode_packet,
    encode_boost,
    encode_clear_schedule,
    encode_delete_schedule,
    encode_query_ble_firmware,
    encode_query_mcu_firmware,
    encode_query_schedule,
    encode_query_status,
    encode_reset,
    encode_set_intensity,
    encode_set_power,
    encode_set_room_and_fragrance,
    encode_set_schedule,
    encode_set_time,
    parse_notification,
    parse_schedule_payload,
    CMD_QUERY_MCU_FIRMWARE,
    CMD_QUERY_STATUS,
    CMD_SET_POWER,
    CMD_SET_INTENSITY,
    CMD_BOOST,
    CMD_SPRAY_REPORT,
    CMD_BATTERY_REPORT,
    CMD_QUERY_BLE_FIRMWARE,
    CMD_CLEAR_SCHEDULE,
    CMD_RESET,
    CMD_QUERY_SCHEDULE,
    CMD_SET_ROOM_FRAGRANCE,
)


def test_checksum_calculation() -> None:
    cmd = 0x06
    data = bytes([0x01])
    expected = 0x06 ^ 0xAA ^ 0x01 ^ 0x01
    assert calculate_checksum(cmd, data) == expected


def test_construct_command() -> None:
    packet = construct_command(0x06, [0x01])
    assert packet[0] == 0xFF
    assert packet[1] == 0x55
    assert packet[2] == 0x06
    assert packet[3] == 0x01
    assert packet[4] == 0x01
    assert packet[5] == calculate_checksum(0x06, bytes([0x01]))


def test_encode_set_power() -> None:
    on_pkt = encode_set_power(True)
    decoded = decode_packet(on_pkt)
    assert decoded is not None
    cmd, payload = decoded
    assert cmd == CMD_SET_POWER
    assert payload == b"\x01"

    off_pkt = encode_set_power(False)
    decoded_off = decode_packet(off_pkt)
    assert decoded_off is not None
    cmd_off, payload_off = decoded_off
    assert cmd_off == CMD_SET_POWER
    assert payload_off == b"\x00"


def test_encode_set_intensity() -> None:
    for i in range(5):
        pkt = encode_set_intensity(i)
        decoded = decode_packet(pkt)
        assert decoded is not None
        cmd, payload = decoded
        assert cmd == CMD_SET_INTENSITY
        assert payload == bytes([i])

    with pytest.raises(ValueError):
        encode_set_intensity(5)

    with pytest.raises(ValueError):
        encode_set_intensity(-1)


def test_encode_boost() -> None:
    pkt = encode_boost()
    decoded = decode_packet(pkt)
    assert decoded is not None
    cmd, payload = decoded
    assert cmd == CMD_BOOST
    assert payload == b""


def test_encode_queries() -> None:
    st_pkt = encode_query_status()
    decoded_st = decode_packet(st_pkt)
    assert decoded_st is not None
    assert decoded_st[0] == CMD_QUERY_STATUS

    mcu_pkt = encode_query_mcu_firmware()
    decoded_mcu = decode_packet(mcu_pkt)
    assert decoded_mcu is not None
    assert decoded_mcu[0] == CMD_QUERY_MCU_FIRMWARE

    ble_pkt = encode_query_ble_firmware()
    decoded_ble = decode_packet(ble_pkt)
    assert decoded_ble is not None
    assert decoded_ble[0] == CMD_QUERY_BLE_FIRMWARE

    sched_pkt = encode_query_schedule(1)
    decoded_sched = decode_packet(sched_pkt)
    assert decoded_sched is not None
    assert decoded_sched[0] == CMD_QUERY_SCHEDULE

    clear_pkt = encode_clear_schedule()
    decoded_clear = decode_packet(clear_pkt)
    assert decoded_clear is not None
    assert decoded_clear[0] == CMD_CLEAR_SCHEDULE

    reset_pkt = encode_reset()
    decoded_reset = decode_packet(reset_pkt)
    assert decoded_reset is not None
    assert decoded_reset[0] == CMD_RESET

    rf_pkt = encode_set_room_and_fragrance(1, 2)
    decoded_rf = decode_packet(rf_pkt)
    assert decoded_rf is not None
    assert decoded_rf[0] == CMD_SET_ROOM_FRAGRANCE


def test_encode_set_time() -> None:
    dt = datetime(2026, 8, 20, 14, 30, 45)  # Thursday
    pkt = encode_set_time(dt)
    decoded = decode_packet(pkt)
    assert decoded is not None
    _cmd, payload = decoded
    assert payload[0] == 14
    assert payload[1] == 30
    assert payload[2] == 45
    assert payload[3] == 4


def test_encode_schedule() -> None:
    s = Schedule(id=1, start_hour=9, start_min=0, end_hour=17, end_min=0, intensity=3)
    assert s.days_str == "Mon,Tue,Wed,Thu,Fri"

    pkt = encode_set_schedule(s)
    decoded = decode_packet(pkt)
    assert decoded is not None
    assert decoded[0] == 0x03
    assert len(decoded[1]) == 8

    # Test parse_schedule_payload
    parsed = parse_schedule_payload(decoded[1])
    assert parsed is not None
    assert parsed.id == 1
    assert parsed.start_hour == 9
    assert parsed.end_hour == 17
    assert parsed.intensity == 3
    assert parsed.monday is True
    assert parsed.saturday is False

    del_pkt = encode_delete_schedule(1)
    decoded_del = decode_packet(del_pkt)
    assert decoded_del is not None
    assert decoded_del[0] == 0x03


def test_decode_invalid_packets() -> None:
    assert decode_packet(b"\x00\x00\x01\x00\x00") is None
    assert decode_packet(b"\xff\x55") is None
    corrupt = bytes([0xFF, 0x55, 0x06, 0x01, 0x01, 0x99])
    assert decode_packet(corrupt) is None


def test_parse_notifications() -> None:
    status_data = construct_command(0x01, [0x01, 0x00, 0x03, 0x04, 0x00, 0x11])
    event, status, _ = parse_notification(status_data)
    assert event == "status"
    assert status.power is True
    assert status.spraying is False
    assert status.intensity == 3
    assert status.battery_level == 4
    assert status.schedule_info == 17

    spray_data = construct_command(CMD_SPRAY_REPORT, [0x01])
    event_sp, status_sp, _ = parse_notification(spray_data, status)
    assert event_sp == "spraying"
    assert status_sp.spraying is True

    bat_data = construct_command(CMD_BATTERY_REPORT, [0x02])
    event_bat, status_bat, _ = parse_notification(bat_data, status)
    assert event_bat == "battery"
    assert status_bat.battery_level == 2

    mcu_data = construct_command(CMD_QUERY_MCU_FIRMWARE, [0x01, 0x00, 0x01, 0x08])
    event_mcu, status_mcu, _ = parse_notification(mcu_data)
    assert event_mcu == "mcu_firmware"
    assert status_mcu.mcu_firmware == "1.00,1.08"


# Room IDs as defined by the diffuser firmware. Source of truth is the Air Wick
# Android app's ViewUtils.getRoomTypeName(), recovered by decompilation.
FIRMWARE_ROOM_NAMES = {
    1: "Bathroom",
    2: "Bedroom",
    3: "Dining Room",
    4: "Foyer",
    5: "Kitchen",
    6: "Living Room",
    7: "Other",
    8: "Office",
}


def test_room_names_match_firmware() -> None:
    from airspray.protocol import ROOM_NAMES

    assert ROOM_NAMES == FIRMWARE_ROOM_NAMES


def test_resolve_room_id() -> None:
    from airspray.protocol import resolve_room_id

    assert resolve_room_id("Office") == 8
    assert resolve_room_id("desk") == 8
    assert resolve_room_id("Bedroom") == 2
    assert resolve_room_id("Living Room") == 6
    assert resolve_room_id("Bathroom") == 1
    assert resolve_room_id("Foyer") == 4
    assert resolve_room_id("3") == 3
    # Custom unknown string defaults to 7 (Other)
    assert resolve_room_id("Executive Corner Studio") == 7


def test_every_room_preset_resolves_to_its_own_name() -> None:
    from airspray.protocol import ROOM_NAMES, resolve_room_id

    for room_id, name in ROOM_NAMES.items():
        assert resolve_room_id(name) == room_id


def test_battery_report_ignores_spurious_drop_to_zero() -> None:
    """The vendor app rejects a battery report of 0 unless the level was already low."""
    from airspray.protocol import CMD_BATTERY_REPORT, DiffuserStatus

    status = DiffuserStatus(battery_level=4)
    _, updated, _ = parse_notification(
        construct_command(CMD_BATTERY_REPORT, [0]), status
    )
    assert updated.battery_level == 4


def test_battery_report_accepts_zero_when_already_low() -> None:
    from airspray.protocol import CMD_BATTERY_REPORT, DiffuserStatus

    status = DiffuserStatus(battery_level=1)
    _, updated, _ = parse_notification(
        construct_command(CMD_BATTERY_REPORT, [0]), status
    )
    assert updated.battery_level == 0


def test_battery_report_ignores_out_of_range_level() -> None:
    from airspray.protocol import CMD_BATTERY_REPORT, DiffuserStatus

    status = DiffuserStatus(battery_level=3)
    _, updated, _ = parse_notification(
        construct_command(CMD_BATTERY_REPORT, [9]), status
    )
    assert updated.battery_level == 3
