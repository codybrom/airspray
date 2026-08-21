"""
AirSpray Device Data Schema & Model Specification.
Provides structured JSON Schema and serialization models for Bluetooth diffusers.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
import json

from airspray.protocol import MAX_FRAGRANCE_ID, intensity_label


@dataclass
class DeviceInfoSchema:
    model: str = "Smart BLE Diffuser"
    project_codename: str = "Reese"
    mcu_firmware: Optional[str] = None
    ble_firmware: Optional[str] = None
    bluetooth_address: Optional[str] = None
    rssi: Optional[int] = None


@dataclass
class DeviceStateSchema:
    power: bool = False
    spraying: bool = False
    intensity: int = 2
    intensity_label: str = "Medium"
    battery_level: int = 0
    battery_percentage: Optional[int] = None
    refill_percentage: float = 100.0
    refill_installed_date: Optional[str] = None
    schedule_active: bool = False
    current_schedule_id: int = 0


@dataclass
class LocationProfileSchema:
    # 8 is Office; 7 is "Other". Keep the pair consistent -- see
    # test_schema_default_room_name_matches_its_id.
    room_id: int = 8
    room_name: str = "Office"
    fragrance_id: int = 1
    mood_id: int = 1


@dataclass
class ScheduleItemSchema:
    id: int
    name_id: int
    name: str
    enabled: bool
    start_time: str
    end_time: str
    intensity: int
    days: List[str]
    repeat_enabled: bool


@dataclass
class DiffuserDeviceSchema:
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    connected: bool = False
    device_info: DeviceInfoSchema = field(default_factory=DeviceInfoSchema)
    state: DeviceStateSchema = field(default_factory=DeviceStateSchema)
    location: LocationProfileSchema = field(default_factory=LocationProfileSchema)
    schedules: List[ScheduleItemSchema] = field(
        default_factory=lambda: []  # noqa: C408 -- typed factory keeps pyright strict happy
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def get_json_schema(cls) -> Dict[str, Any]:
        """Generate JSON Schema Draft-07 specification."""
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "AirSprayDiffuser",
            "type": "object",
            "required": [
                "timestamp",
                "connected",
                "device_info",
                "state",
                "location",
                "schedules",
            ],
            "properties": {
                "timestamp": {"type": "string", "format": "date-time"},
                "connected": {"type": "boolean"},
                "device_info": {
                    "type": "object",
                    "properties": {
                        "model": {"type": "string"},
                        "project_codename": {"type": "string"},
                        "mcu_firmware": {"type": ["string", "null"]},
                        "ble_firmware": {"type": ["string", "null"]},
                        "bluetooth_address": {"type": ["string", "null"]},
                        "rssi": {"type": ["integer", "null"]},
                    },
                    "required": ["model", "project_codename"],
                },
                "state": {
                    "type": "object",
                    "properties": {
                        "power": {"type": "boolean"},
                        "spraying": {"type": "boolean"},
                        "intensity": {"type": "integer", "minimum": 0, "maximum": 4},
                        "intensity_label": {"type": "string"},
                        "battery_level": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 4,
                        },
                        "battery_percentage": {"type": ["integer", "null"]},
                        "refill_percentage": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 100.0,
                        },
                        "refill_installed_date": {"type": ["string", "null"]},
                        "schedule_active": {"type": "boolean"},
                        "current_schedule_id": {"type": "integer"},
                    },
                    "required": ["power", "spraying", "intensity", "refill_percentage"],
                },
                "location": {
                    "type": "object",
                    "properties": {
                        "room_id": {"type": "integer", "minimum": 1, "maximum": 8},
                        "room_name": {"type": "string"},
                        "fragrance_id": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_FRAGRANCE_ID,
                        },
                        "mood_id": {"type": "integer"},
                    },
                    "required": ["room_id", "room_name"],
                },
                "schedules": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer", "minimum": 1, "maximum": 10},
                            "name_id": {"type": "integer"},
                            "name": {"type": "string"},
                            "enabled": {"type": "boolean"},
                            "start_time": {
                                "type": "string",
                                "pattern": "^[0-2][0-9]:[0-5][0-9]$",
                            },
                            "end_time": {
                                "type": "string",
                                "pattern": "^[0-2][0-9]:[0-5][0-9]$",
                            },
                            "intensity": {
                                "type": "integer",
                                "minimum": 0,
                                "maximum": 4,
                            },
                            "days": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "enum": [
                                        "Mon",
                                        "Tue",
                                        "Wed",
                                        "Thu",
                                        "Fri",
                                        "Sat",
                                        "Sun",
                                    ],
                                },
                            },
                            "repeat_enabled": {"type": "boolean"},
                        },
                        "required": [
                            "id",
                            "enabled",
                            "start_time",
                            "end_time",
                            "intensity",
                            "days",
                        ],
                    },
                },
            },
        }


def intensity_to_label(level: int) -> str:
    """Level 0 is Paused, not Off -- the device stays powered and scheduled."""
    return intensity_label(level)


def schedule_name_from_id(name_id: int) -> str:
    names = {
        1: "Start the Day",
        2: "Midday Boost",
        3: "Wake Up",
        4: "Morning",
        5: "Midday",
        6: "Afternoon",
        7: "Evening",
        8: "Custom",
    }
    return names.get(name_id, f"Schedule {name_id}")
