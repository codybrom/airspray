"""
Air Wick Essential Mist Bluetooth Diffuser - Protocol Implementation
Reverse engineered from Air Wick Android APK (version 3.2.1, project codename 'Reese').
"""

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Union

# BLE UUIDs
SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
WRITE_CHAR_UUID = "6e400007-b5a3-f393-e0a9-e50e24dcca9e"
NOTIFY_CHAR_UUID = "6e400008-b5a3-f393-e0a9-e50e24dcca9e"

# Advertised device names
DEVICE_NAME_PATTERNS: List[str] = ["Air Wick Mist", "AIRWICK REESES"]

# Room Preset Mappings
ROOM_PRESETS: Dict[str, int] = {
    "bathroom": 1,
    "bath": 1,
    "bedroom": 2,
    "dining room": 3,
    "diningroom": 3,
    "dining": 3,
    "foyer": 4,
    "entryway": 4,
    "hallway": 4,
    "hall": 4,
    "kitchen": 5,
    "living room": 6,
    "livingroom": 6,
    "living": 6,
    "other": 7,
    "office": 8,
    "desk": 8,
    "work": 8,
}

# Room IDs are fixed by the diffuser firmware; do not renumber.
ROOM_NAMES: Dict[int, str] = {
    1: "Bathroom",
    2: "Bedroom",
    3: "Dining Room",
    4: "Foyer",
    5: "Kitchen",
    6: "Living Room",
    7: "Other",
    8: "Office",
}


# Fragrance names as shipped in the vendor app.
#
# All 33 are listed and all transmit -- the encoder does not validate. Note the
# vendor's own BLE decoder only acknowledges 1-31, so for 32 and 33 the device
# will not echo the value back; they are stored locally either way.
FRAGRANCE_NAMES: Dict[int, str] = {
    1: "Aroma Unwind",
    2: "Aroma Happiness",
    3: "Aroma Refresh",
    4: "Aroma Sleep",
    5: "Apple & Cinnamon",
    6: "Eucalyptus",
    7: "Fresh Cucumber",
    8: "Fresh Waters",
    9: "Lavender & Almond Blossom",
    10: "Lemon Thyme",
    11: "Mandarin & Sweet Tangerine",
    12: "Peony & Jasmine",
    13: "Sweet Fruit & Nutmeg",
    14: "Sweet Melon & Vanilla",
    15: "Wild Mint",
    16: "Woodland Pine",
    17: "Coconut and Pineapple",
    18: "Sandalwood",
    19: "Pumpkin Spice",
    20: "Fresh Pine & Juniper",
    21: "Apple Cinnamon Medley",
    22: "Cinnamon & Crisp Apple",
    23: "Brown Sugar & Vanilla",
    24: "Vanilla & Caramel",
    25: "Aroma Rejuvenate",
    26: "Blue Agave",
    27: "Vanilla Pink Papaya",
    28: "Cotton & Spring Lilac",
    29: "Lush Honeysuckle & Raspberry",
    30: "Hibiscus & Bloom Orchids",
    31: "Bonfire & Crisp Fall Air",
    32: "Aroma Relax",
    33: "Linen Petals",
}

MAX_FRAGRANCE_ID: int = max(FRAGRANCE_NAMES)

# Level 0 pauses spraying; 1-4 are the fragrance strengths.
INTENSITY_NAMES: Dict[int, str] = {
    0: "Paused",
    1: "Low",
    2: "Medium",
    3: "High",
    4: "Max",
}


def fragrances_sorted_by_name() -> List[Tuple[int, str]]:
    """The catalogue ordered by name, for pickers. A 33-item list in ID order is
    effectively unscannable; the IDs carry no meaning to the reader."""
    return sorted(FRAGRANCE_NAMES.items(), key=lambda item: item[1].casefold())


def is_recognised_device_name(name: str) -> bool:
    """Whether an advertised BLE name belongs to a diffuser we control.

    Anchored like the vendor app (^(Air Wick Mist|AIRWICK REESES)$), but
    case-insensitive for tolerance. A substring match would happily pair with an
    unrelated humidifier called "Air Wick Mister".
    """
    trimmed = name.strip()
    if not trimmed:
        return False
    return any(p.casefold() == trimmed.casefold() for p in DEVICE_NAME_PATTERNS)


def intensity_label(level: int) -> str:
    return INTENSITY_NAMES.get(level, f"Level {level}")


def resolve_room_id(location: str) -> int:
    """Resolve custom location name or preset name/number to 1-byte room ID (1-8)."""
    loc_clean = location.strip().lower()
    if loc_clean.isdigit():
        val = int(loc_clean)
        if 1 <= val <= 8:
            return val
    return ROOM_PRESETS.get(loc_clean, 7)  # 7 = "Other"


# Command Codes
CMD_QUERY_MCU_FIRMWARE = 0x00
CMD_QUERY_STATUS = 0x01
CMD_QUERY_SCHEDULE = 0x02
CMD_SET_SCHEDULE = 0x03
CMD_CLEAR_SCHEDULE = 0x04
CMD_SET_TIME = 0x05
CMD_SET_POWER = 0x06
CMD_SET_INTENSITY = 0x07
CMD_SET_ROOM_FRAGRANCE = 0x08
CMD_BOOST = 0x09
CMD_SET_DEBUG = 0x0A
CMD_RESET = 0x0B
CMD_HARDWARE_SWITCH_REPORT = 0x10
CMD_SPRAY_REPORT = 0x11
CMD_BATTERY_REPORT = 0x12
CMD_SCHEDULE_STATUS_REPORT = 0x13
CMD_REQUEST_IDENTITY = 0x20
CMD_IDENTITY_AUTH = 0x21
CMD_QUERY_BLE_FIRMWARE = 0x22
CMD_SET_TIMEOUT = 0x40
CMD_SET_SPRAY_PARAMETER = 0x41


@dataclass
class DiffuserStatus:
    power: bool = False
    spraying: bool = False
    intensity: int = 0  # 0 to 4 (0=paused, 1=low, 2=med, 3=high, 4=max)
    battery_level: int = 0  # 0 to 4 (bars / raw level)
    schedule_info: int = 0
    mcu_firmware: Optional[str] = None
    ble_firmware: Optional[str] = None
    room_id: Optional[int] = None
    fragrance_id: Optional[int] = None
    raw_status_bytes: Optional[bytes] = None


@dataclass
class Schedule:
    id: int  # 1 to 10
    name_id: int = 1
    enabled: bool = True
    start_hour: int = 8
    start_min: int = 0
    end_hour: int = 17
    end_min: int = 0
    intensity: int = 2
    monday: bool = True
    tuesday: bool = True
    wednesday: bool = True
    thursday: bool = True
    friday: bool = True
    saturday: bool = False
    sunday: bool = False

    @property
    def is_set(self) -> bool:
        """Whether the slot is occupied, matching the vendor's Schedule.isSet():
        decided purely by the window, ignoring `enabled`. A slot switched off
        still holds its times, and a cleared slot reads back all zeros."""
        return (
            self.start_hour != 0
            or self.start_min != 0
            or self.end_hour != 0
            or self.end_min != 0
        )

    def normalized_for_device(self) -> "Schedule":
        """An all-zero window is how the diffuser marks a slot free, so a
        midnight-to-midnight "all day" schedule would read back as an empty slot
        and vanish from the list. Widen it to 00:00-23:59, which is what the user
        meant anyway. (The vendor dodged this by storing 00:01 as the start and
        hiding it on read.)"""
        if self.is_set:
            return self
        return replace(self, end_hour=23, end_min=59)

    @property
    def days_str(self) -> str:
        days: List[str] = []
        if self.monday:
            days.append("Mon")
        if self.tuesday:
            days.append("Tue")
        if self.wednesday:
            days.append("Wed")
        if self.thursday:
            days.append("Thu")
        if self.friday:
            days.append("Fri")
        if self.saturday:
            days.append("Sat")
        if self.sunday:
            days.append("Sun")
        return ",".join(days) if days else "None"


def calculate_checksum(cmd: int, data: bytes) -> int:
    """
    Checksum algorithm from ReeseBluetoothEncoder:
    checksum = cmd ^ 0xAA ^ len(data) ^ (XOR of all data bytes)
    """
    chk = cmd ^ 0xAA ^ len(data)
    for b in data:
        chk ^= b
    return chk & 0xFF


def construct_command(
    cmd: int, data: Union[bytes, bytearray, List[int]] = b""
) -> bytes:
    """
    Packet structure:
    [0xFF, 0x55, CMD, DATA_LENGTH, ...DATA..., CHECKSUM]
    """
    if isinstance(data, list):
        payload = bytes(data)
    elif isinstance(data, bytearray):
        payload = bytes(data)
    else:
        payload = data

    chk = calculate_checksum(cmd, payload)
    return bytes([0xFF, 0x55, cmd, len(payload)]) + payload + bytes([chk])


def encode_set_power(on: bool) -> bytes:
    """Turn device power ON (1) or OFF (0)."""
    return construct_command(CMD_SET_POWER, [0x01 if on else 0x00])


def encode_set_intensity(intensity: int) -> bytes:
    """
    Set fragrance intensity level (0 to 4).
    0 = Paused, 1 = Low, 2 = Medium, 3 = High, 4 = Max.
    """
    if not (0 <= intensity <= 4):
        raise ValueError("Intensity must be between 0 and 4")
    return construct_command(CMD_SET_INTENSITY, [intensity])


def encode_boost() -> bytes:
    """Trigger immediate spray boost."""
    return construct_command(CMD_BOOST, [])


def encode_query_status() -> bytes:
    """Query current device state (power, spraying, intensity, battery)."""
    return construct_command(CMD_QUERY_STATUS, [])


def encode_query_mcu_firmware() -> bytes:
    """Query MCU firmware version."""
    return construct_command(CMD_QUERY_MCU_FIRMWARE, [])


def encode_query_ble_firmware() -> bytes:
    """Query BLE firmware version."""
    return construct_command(CMD_QUERY_BLE_FIRMWARE, [])


def encode_set_time(dt: Optional[datetime] = None) -> bytes:
    """
    Sync RTC clock on diffuser.
    Data: [hour, min, sec, day_of_week]
    day_of_week: 0 = Sun, 1 = Mon, ..., 6 = Sat (matching Java Calendar: cal.get(7) - 1)
    """
    if dt is None:
        dt = datetime.now()

    dow = (dt.weekday() + 1) % 7
    return construct_command(CMD_SET_TIME, [dt.hour, dt.minute, dt.second, dow])


def encode_set_room_and_fragrance(room_id: int, fragrance_id: int) -> bytes:
    """Set room ID (1..8) and fragrance ID (1..33).

    The catalogue runs to 33 even though the vendor's own BLE decoder only
    acknowledges 1-31; 32 and 33 transmit but are not echoed back.
    """
    if not (1 <= room_id <= 8):
        raise ValueError("Room ID must be between 1 and 8")
    if not (1 <= fragrance_id <= MAX_FRAGRANCE_ID):
        raise ValueError(f"Fragrance ID must be between 1 and {MAX_FRAGRANCE_ID}")
    return construct_command(CMD_SET_ROOM_FRAGRANCE, [room_id, fragrance_id])


def encode_query_schedule(schedule_id: int) -> bytes:
    """Query schedule by ID (1..10)."""
    if not (1 <= schedule_id <= 10):
        raise ValueError("Schedule ID must be between 1 and 10")
    return construct_command(CMD_QUERY_SCHEDULE, [schedule_id])


def encode_delete_schedule(schedule_id: int) -> bytes:
    """Delete schedule by ID."""
    if not (1 <= schedule_id <= 10):
        raise ValueError("Schedule ID must be between 1 and 10")
    return construct_command(
        CMD_SET_SCHEDULE, [schedule_id & 0x0F, 0, 0, 0, 0, 0, 0, 0]
    )


def encode_clear_schedule() -> bytes:
    """Clear all schedules."""
    return construct_command(CMD_CLEAR_SCHEDULE, [])


def encode_reset() -> bytes:
    """Reset device to factory settings."""
    return construct_command(CMD_RESET, [])


def encode_set_schedule(s: Schedule) -> bytes:
    s = s.normalized_for_device()
    if not (1 <= s.id <= 10):
        raise ValueError("Schedule ID must be between 1 and 10")
    if not (0 <= s.intensity <= 4):
        raise ValueError("Intensity must be between 0 and 4")
    if not (0 <= s.start_hour <= 23 and 0 <= s.start_min <= 59):
        raise ValueError("Invalid start time")
    if not (0 <= s.end_hour <= 23 and 0 <= s.end_min <= 59):
        raise ValueError("Invalid end time")

    b0 = ((s.name_id & 0x0F) << 4) | (s.id & 0x0F)
    b1 = 1 if s.enabled else 0
    repeat_byte = 0x80  # Repeat enable flag
    if s.monday:
        repeat_byte |= 0x40
    if s.tuesday:
        repeat_byte |= 0x20
    if s.wednesday:
        repeat_byte |= 0x10
    if s.thursday:
        repeat_byte |= 0x08
    if s.friday:
        repeat_byte |= 0x04
    if s.saturday:
        repeat_byte |= 0x02
    if s.sunday:
        repeat_byte |= 0x01

    return construct_command(
        CMD_SET_SCHEDULE,
        [
            b0,
            b1,
            s.start_hour,
            s.start_min,
            s.end_hour,
            s.end_min,
            repeat_byte,
            s.intensity,
        ],
    )


def decode_packet(data: bytes) -> Optional[Tuple[int, bytes]]:
    """
    Find and decode packet from raw bytes.
    Returns (cmd, payload) or None if invalid.
    """
    if len(data) < 5:
        return None

    idx = -1
    for i in range(len(data) - 2):
        if data[i] == 0xFF and data[i + 1] == 0x55:
            idx = i
            break

    if idx == -1:
        return None

    if len(data) < idx + 5:
        return None

    cmd = data[idx + 2]
    length = data[idx + 3]

    if len(data) < idx + 5 + length:
        return None

    payload = data[idx + 4 : idx + 4 + length]
    expected_chk = data[idx + 4 + length]

    calc_chk = calculate_checksum(cmd, payload)
    if calc_chk != expected_chk:
        return None

    return (cmd, payload)


def parse_schedule_payload(payload: bytes) -> Optional[Schedule]:
    """Decode 8-byte schedule payload into Schedule dataclass."""
    if len(payload) < 8:
        return None

    sched_id = payload[0] & 0x0F
    if sched_id == 0:
        return None

    name_id = (payload[0] & 0xF0) >> 4
    enabled = bool(payload[1] == 1)
    start_hour = payload[2]
    start_min = payload[3]
    end_hour = payload[4]
    end_min = payload[5]
    repeat_byte = payload[6]
    intensity = payload[7]

    return Schedule(
        id=sched_id,
        name_id=name_id,
        enabled=enabled,
        start_hour=start_hour,
        start_min=start_min,
        end_hour=end_hour,
        end_min=end_min,
        intensity=intensity,
        monday=bool(repeat_byte & 0x40),
        tuesday=bool(repeat_byte & 0x20),
        wednesday=bool(repeat_byte & 0x10),
        thursday=bool(repeat_byte & 0x08),
        friday=bool(repeat_byte & 0x04),
        saturday=bool(repeat_byte & 0x02),
        sunday=bool(repeat_byte & 0x01),
    )


def parse_notification(
    data: bytes, status: Optional[DiffuserStatus] = None
) -> Tuple[Optional[str], DiffuserStatus, Optional[Schedule]]:
    """
    Parse a BLE notification packet into event name, updated DiffuserStatus, and optional Schedule.
    """
    current_status = status if status is not None else DiffuserStatus()
    parsed_schedule: Optional[Schedule] = None

    decoded = decode_packet(data)
    if not decoded:
        return (None, current_status, None)

    cmd, payload = decoded

    if cmd == CMD_QUERY_MCU_FIRMWARE and len(payload) >= 4:
        current_status.mcu_firmware = (
            f"{payload[0]}.{payload[1]:02d},{payload[2]}.{payload[3]:02d}"
        )
        return ("mcu_firmware", current_status, None)

    elif cmd == CMD_QUERY_BLE_FIRMWARE and len(payload) >= 4:
        current_status.ble_firmware = (
            f"{payload[0]}.{payload[1]:02d},{payload[2]}.{payload[3]:02d}"
        )
        return ("ble_firmware", current_status, None)

    elif cmd == CMD_QUERY_STATUS and len(payload) >= 4:
        current_status.power = bool(payload[0] == 1)
        current_status.spraying = bool(payload[1] == 1)
        current_status.intensity = payload[2]
        current_status.battery_level = payload[3]
        if len(payload) >= 6:
            current_status.schedule_info = payload[5]
        current_status.raw_status_bytes = payload
        return ("status", current_status, None)

    elif cmd in (CMD_QUERY_SCHEDULE, CMD_SET_SCHEDULE):
        parsed_schedule = parse_schedule_payload(payload)
        return ("schedule", current_status, parsed_schedule)

    elif cmd == CMD_SET_POWER and len(payload) >= 1:
        current_status.power = bool(payload[0] == 1)
        return ("power", current_status, None)

    elif cmd == CMD_SET_INTENSITY and len(payload) >= 1:
        current_status.intensity = payload[0]
        return ("intensity", current_status, None)

    elif cmd == CMD_SET_ROOM_FRAGRANCE and len(payload) >= 2:
        current_status.room_id = payload[0]
        current_status.fragrance_id = payload[1]
        return ("room_fragrance", current_status, None)

    elif cmd == CMD_SPRAY_REPORT and len(payload) >= 1:
        current_status.spraying = bool(payload[0] == 1)
        return ("spraying", current_status, None)

    elif cmd == CMD_BATTERY_REPORT and len(payload) >= 1:
        level = payload[0]
        # The diffuser intermittently reports 0 bars while spraying. The vendor app
        # only believes a 0 when the level was already nearly empty, so mirror that
        # rather than flashing a false "empty battery" at the user.
        if 0 <= level <= 4 and (level != 0 or abs(current_status.battery_level) <= 1):
            current_status.battery_level = level
        return ("battery", current_status, None)

    elif cmd == CMD_BOOST:
        current_status.spraying = True
        return ("boost", current_status, None)

    return (f"cmd_{cmd}", current_status, None)
