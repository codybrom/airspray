"""Guards that the Swift menu bar app speaks the same wire protocol as the Python client.

The Swift app duplicates the protocol constants in AirsprayMac/Sources/Protocol.swift.
When those drift from airspray/protocol.py the app connects fine but every command is
silently interpreted as a different command by the diffuser, so nothing appears to happen.
"""

import dataclasses
import json
import re
from typing import Any
from pathlib import Path

import pytest

from airspray import config as app_config
from airspray import protocol
from airspray import refill as protocol_refill
from airspray import schema

SWIFT_PROTOCOL = (
    Path(__file__).resolve().parent.parent
    / "AirsprayMac"
    / "Sources"
    / "Protocol.swift"
)

# Swift constant name -> Python constant name
COMMAND_PARITY = {
    "cmdQueryMcuFirmware": "CMD_QUERY_MCU_FIRMWARE",
    "cmdQueryStatus": "CMD_QUERY_STATUS",
    "cmdQuerySchedule": "CMD_QUERY_SCHEDULE",
    "cmdSetSchedule": "CMD_SET_SCHEDULE",
    "cmdClearSchedule": "CMD_CLEAR_SCHEDULE",
    "cmdSetTime": "CMD_SET_TIME",
    "cmdSetPower": "CMD_SET_POWER",
    "cmdSetIntensity": "CMD_SET_INTENSITY",
    "cmdSetRoomFragrance": "CMD_SET_ROOM_FRAGRANCE",
    "cmdBoost": "CMD_BOOST",
    "cmdReset": "CMD_RESET",
    "cmdSprayReport": "CMD_SPRAY_REPORT",
    "cmdBatteryReport": "CMD_BATTERY_REPORT",
    "cmdQueryBleFirmware": "CMD_QUERY_BLE_FIRMWARE",
}

UUID_PARITY = {
    "serviceUUID": "SERVICE_UUID",
    "commandCharUUID": "WRITE_CHAR_UUID",
    "notifyCharUUID": "NOTIFY_CHAR_UUID",
}


@pytest.fixture(scope="module")
def swift_source() -> str:
    assert SWIFT_PROTOCOL.exists(), f"missing {SWIFT_PROTOCOL}"
    return SWIFT_PROTOCOL.read_text()


def _swift_uint8(source: str, name: str) -> int:
    match = re.search(
        rf"static let {name}\s*:\s*UInt8\s*=\s*(0x[0-9A-Fa-f]+|\d+)", source
    )
    assert match, f"could not find Swift constant {name}"
    return int(match.group(1), 0)


def _swift_string(source: str, name: str) -> str:
    match = re.search(rf'static let {name}\s*=\s*"([^"]+)"', source)
    assert match, f"could not find Swift constant {name}"
    return match.group(1)


@pytest.mark.parametrize("swift_name,python_name", sorted(COMMAND_PARITY.items()))
def test_command_code_matches_python(
    swift_source: str, swift_name: str, python_name: str
) -> None:
    expected: int = getattr(protocol, python_name)
    actual = _swift_uint8(swift_source, swift_name)
    assert (
        actual == expected
    ), f"Swift {swift_name}=0x{actual:02X} but Python {python_name}=0x{expected:02X}"


@pytest.mark.parametrize("swift_name,python_name", sorted(UUID_PARITY.items()))
def test_uuid_matches_python(
    swift_source: str, swift_name: str, python_name: str
) -> None:
    expected: str = getattr(protocol, python_name)
    assert _swift_string(swift_source, swift_name).lower() == expected.lower()


def test_command_codes_are_unique(swift_source: str) -> None:
    codes = {name: _swift_uint8(swift_source, name) for name in COMMAND_PARITY}
    seen: dict[int, str] = {}
    for name, code in codes.items():
        assert code not in seen, f"{name} and {seen[code]} both use 0x{code:02X}"
        seen[code] = name


def test_set_time_weekday_matches_python(swift_source: str) -> None:
    """Swift Calendar weekday is 1=Sun..7=Sat; the wire format is 0=Sun..6=Sat."""
    match = re.search(r"let weekday = UInt8\((.+?)\)\s*//", swift_source)
    assert match, "could not find the weekday expression in encodeSetTime"

    # Turn the Swift expression into an evaluable Python one.
    expr = match.group(1)
    expr = expr.replace("calendar.component(.weekday, from: date)", "w")
    assert re.fullmatch(
        r"[\sw0-9+\-*%()]+", expr
    ), f"unexpected weekday expression: {expr}"

    # Calendar.weekday value -> expected wire byte (0=Sun .. 6=Sat)
    for cal_weekday in range(1, 8):
        expected = cal_weekday - 1
        actual: int = eval(expr, {"__builtins__": {}}, {"w": cal_weekday})  # noqa: S307
        assert (
            actual == expected
        ), f"Calendar.weekday={cal_weekday} encodes as {actual}, expected {expected}"


def test_room_table_matches_python(swift_source: str) -> None:
    """Room IDs are firmware-defined; the two ports must agree on them."""
    block = re.search(
        r"roomNames:\s*\[Int:\s*String\]\s*=\s*\[(.*?)\]", swift_source, re.S
    )
    assert block, "could not find roomNames in Protocol.swift"
    swift_rooms = {
        int(rid): name
        for rid, name in re.findall(r'(\d+)\s*:\s*"([^"]+)"', block.group(1))
    }
    assert swift_rooms == protocol.ROOM_NAMES


def test_device_name_patterns_match_python(swift_source: str) -> None:
    block = re.search(r"deviceNamePatterns\s*=\s*\[(.*?)\]", swift_source, re.S)
    assert block, "could not find deviceNamePatterns in Protocol.swift"
    swift_names = re.findall(r'"([^"]+)"', block.group(1))
    assert swift_names == protocol.DEVICE_NAME_PATTERNS


# ---------------------------------------------------------------------------
# Tables that both ports render to the user. A mismatch here is not a wire
# fault -- the device does the right thing and the two UIs disagree about what
# they just did, which is harder to notice and harder to trust.
# ---------------------------------------------------------------------------

SWIFT_REFILL = (
    Path(__file__).resolve().parent.parent
    / "AirsprayMac"
    / "Sources"
    / "RefillModel.swift"
)

SWIFT_CONFIG = (
    Path(__file__).resolve().parent.parent / "AirsprayMac" / "Sources" / "Config.swift"
)


@pytest.fixture(scope="module")
def swift_refill() -> str:
    assert SWIFT_REFILL.exists(), f"missing {SWIFT_REFILL}"
    return SWIFT_REFILL.read_text()


@pytest.fixture(scope="module")
def swift_config() -> str:
    assert SWIFT_CONFIG.exists(), f"missing {SWIFT_CONFIG}"
    return SWIFT_CONFIG.read_text()


def _swift_int_string_table(source: str, name: str) -> dict[int, str]:
    block = re.search(rf"{name}[^=]*=\s*\[(.*?)\n\s*\]", source, re.S)
    assert block, f"could not find Swift table {name}"
    return {
        int(key): value
        for key, value in re.findall(r'(\d+)\s*:\s*"([^"]*)"', block.group(1))
    }


def test_fragrance_table_matches_python(swift_source: str) -> None:
    """The refill modal and the CLI must name the same bottle the same way."""
    swift_fragrances = _swift_int_string_table(swift_source, "fragranceNames")
    assert swift_fragrances == protocol.FRAGRANCE_NAMES


def test_intensity_labels_match_python(swift_source: str) -> None:
    """Level 0 pauses spraying; neither port may call it "Off" or "Lowest"."""
    block = re.search(r"intensityNames\s*=\s*\[(.*?)\]", swift_source, re.S)
    assert block, "could not find intensityNames in Protocol.swift"
    swift_levels = {
        int(key): value
        for key, value in re.findall(r'(\d+)\s*:\s*"([^"]*)"', block.group(1))
    }
    assert swift_levels == protocol.INTENSITY_NAMES


def test_no_unknown_swift_command_constants(swift_source: str) -> None:
    """Every command Swift defines must exist in Python at the same value.

    The name-keyed map above proves the commands we listed agree. This catches
    the other direction: a command added to Swift alone, or renamed so the map
    silently stops covering it.
    """
    declared = re.findall(r"static let (cmd\w+)\s*:\s*UInt8\s*=", swift_source)
    unmapped = sorted(set(declared) - set(COMMAND_PARITY))
    assert not unmapped, f"Swift commands missing from COMMAND_PARITY: {unmapped}"


def test_full_bottle_hours_match_python(swift_refill: str) -> None:
    """Swift forecasts from hours-per-bottle, Python depletes from percent-per-
    second. They are the same number expressed two ways, so derive and compare."""
    block = re.search(
        r"fullBottleOperatingHours:\s*\[Int:\s*Double\]\s*=\s*\[(.*?)\n\s*\]",
        swift_refill,
        re.S,
    )
    assert block, "could not find fullBottleOperatingHours in RefillModel.swift"
    swift_hours = {
        int(level): float(hours)
        for level, hours in re.findall(r"(\d+)\s*:\s*([\d.]+)", block.group(1))
    }

    python_hours = {
        level: 100.0 / (rate * 3600.0)
        for level, rate in protocol_refill.CONSUMPTION_RATE_PER_SEC.items()
        if rate > 0
    }

    assert set(swift_hours) == set(python_hours), (
        f"Swift covers intensities {sorted(swift_hours)}, "
        f"Python covers {sorted(python_hours)}"
    )
    for level, hours in python_hours.items():
        assert (
            abs(swift_hours[level] - hours) < 0.5
        ), f"intensity {level}: Swift {swift_hours[level]}h vs Python {hours}h"


def test_paused_intensity_is_absent_from_swift_table(swift_refill: str) -> None:
    """Level 0 is paused. Python rates it at 0.0/sec; the Swift table must leave
    it out so consumptionPercentPerSecond returns zero rather than falling
    through to Medium and draining the bottle while nothing is spraying."""
    assert protocol_refill.CONSUMPTION_RATE_PER_SEC[0] == 0.0
    block = re.search(
        r"fullBottleOperatingHours:\s*\[Int:\s*Double\]\s*=\s*\[(.*?)\n\s*\]",
        swift_refill,
        re.S,
    )
    assert block, "could not find fullBottleOperatingHours in RefillModel.swift"
    assert not re.search(r"\b0\s*:", block.group(1)), "intensity 0 must not be rated"


def test_boost_consumption_matches_python(swift_refill: str) -> None:
    match = re.search(
        r"boostConsumptionPercent\s*:?\s*\w*\s*=\s*([\d.]+)", swift_refill
    )
    assert match, "could not find boostConsumptionPercent in RefillModel.swift"
    assert abs(float(match.group(1)) - protocol_refill.BOOST_CONSUMPTION_PERCENT) < 1e-9


def _swift_coding_keys(source: str) -> set[str]:
    block = re.search(r"private enum CodingKeys.*?\n(.*?)\n\s*\}", source, re.S)
    assert block, "could not find CodingKeys in Config.swift"
    keys: set[str] = set()
    for line in block.group(1).splitlines():
        line = line.strip()
        if not line.startswith("case "):
            continue
        keys.update(k.strip() for k in line[len("case ") :].split(",") if k.strip())
    return keys


def test_config_keys_match_python(swift_config: str) -> None:
    """Both apps read and write ~/.airspray_config.json. The attribute names differ
    by language convention, but every key the Swift app models must be a key this
    port writes under the same name -- otherwise each save silently discards the
    other's settings."""
    swift_keys = _swift_coding_keys(swift_config)
    python_keys = set(app_config.JSON_KEYS.values())

    missing = sorted(swift_keys - python_keys)
    assert not missing, f"Swift config keys the Python port does not know: {missing}"


def test_config_key_map_covers_every_python_field() -> None:
    """A field added here without a JSON key would raise on save, or worse, be
    written under its snake_case attribute name and never seen by the Swift app."""
    assert _python_field_names(app_config.AppConfig) == set(app_config.JSON_KEYS)


def test_python_config_save_preserves_unknown_keys(tmp_path: Path) -> None:
    """The Swift app owns keys this port does not model; save() must merge, not
    overwrite."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"someFutureSwiftKey": "keep me", "lastRoomId": 8}))

    config = app_config.AppConfig.load(path)
    assert config.room_id == 8
    config.default_intensity = 3
    config.save(path)

    written = json.loads(path.read_text())
    assert written["someFutureSwiftKey"] == "keep me"
    assert written["defaultIntensity"] == 3
    assert written["lastRoomId"] == 8


# ---------------------------------------------------------------------------
# Diagnostics schema. Both ports export the same document; the Swift app's copy
# is pasted into bug reports and has to validate against the schema Python
# publishes, which means matching key names, not just field meanings.
# ---------------------------------------------------------------------------

SWIFT_SCHEMA = (
    Path(__file__).resolve().parent.parent / "AirsprayMac" / "Sources" / "Schema.swift"
)

# Swift nested struct -> Python dataclass
SCHEMA_STRUCT_PARITY = {
    "DeviceInfo": schema.DeviceInfoSchema,
    "DeviceState": schema.DeviceStateSchema,
    "LocationProfile": schema.LocationProfileSchema,
    "ScheduleItem": schema.ScheduleItemSchema,
}


@pytest.fixture(scope="module")
def swift_schema() -> str:
    assert SWIFT_SCHEMA.exists(), f"missing {SWIFT_SCHEMA}"
    return SWIFT_SCHEMA.read_text()


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _swift_struct_properties(source: str, struct: str) -> set[str]:
    block = re.search(
        rf"public struct {struct}: Codable \{{(.*?)\n\s*\}}", source, re.S
    )
    assert block, f"could not find Swift struct {struct}"
    return set(re.findall(r"public var (\w+)\s*:", block.group(1)))


def _python_field_names(dataclass_type: type[Any]) -> set[str]:
    return {f.name for f in dataclasses.fields(dataclass_type)}


def test_swift_schema_encodes_snake_case(swift_schema: str) -> None:
    """Swift property names are camelCase; the published schema is snake_case."""
    assert (
        "keyEncodingStrategy = .convertToSnakeCase" in swift_schema
    ), "DiffuserSchema must encode snake_case keys to match airspray/schema.py"


@pytest.mark.parametrize("struct,dataclass_type", sorted(SCHEMA_STRUCT_PARITY.items()))
def test_schema_fields_match_python(
    swift_schema: str, struct: str, dataclass_type: type[Any]
) -> None:
    swift_fields = {
        _snake(name) for name in _swift_struct_properties(swift_schema, struct)
    }
    assert swift_fields == _python_field_names(dataclass_type)


def test_schema_top_level_fields_match_python(swift_schema: str) -> None:
    block = re.search(
        r"public struct DiffuserSchema: Codable \{(.*?)\n\s*public struct",
        swift_schema,
        re.S,
    )
    assert block, "could not find DiffuserSchema properties"
    swift_fields = {
        _snake(n) for n in re.findall(r"public var (\w+)\s*:", block.group(1))
    }
    assert swift_fields == _python_field_names(schema.DiffuserDeviceSchema)


def test_schema_fragrance_range_covers_catalogue() -> None:
    """The published schema must not reject an ID the pickers offer."""
    location = schema.DiffuserDeviceSchema.get_json_schema()["properties"]["location"]
    assert (
        location["properties"]["fragrance_id"]["maximum"] == protocol.MAX_FRAGRANCE_ID
    )


def test_schema_default_room_name_matches_its_id() -> None:
    location = schema.LocationProfileSchema()
    assert protocol.ROOM_NAMES[location.room_id] == location.room_name


def test_intensity_label_calls_zero_paused() -> None:
    assert schema.intensity_to_label(0) == "Paused"
    assert schema.intensity_to_label(2) == "Medium"
