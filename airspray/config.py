"""
Persistent Application Configuration for Airspray.
Stores device settings, schedule preferences, and launch-at-login state.

This file is shared with the Swift menu bar app, which reads and writes the same
~/.airspray_config.json. The two ports keep their own idiomatic attribute names
-- snake_case here, camelCase there -- but must agree on the JSON keys, and must
leave each other's keys alone. Before this was shared, whichever app saved last
silently discarded everything the other had stored. See
tests/test_swift_protocol_parity.py::test_config_keys_match_python.
"""

import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

CONFIG_FILE: Path = Path.home() / ".airspray_config.json"

# Python attribute -> JSON key, matching AppConfig.CodingKeys in
# AirsprayMac/Sources/Config.swift.
JSON_KEYS: Dict[str, str] = {
    "device_address": "lastAddress",
    "room_id": "lastRoomId",
    "fragrance_id": "lastFragranceId",
    "location_name": "customLocationName",
    "default_intensity": "defaultIntensity",
    "presence_enabled": "isDeskPresenceEnabled",
    "schedule_always": "isDeskScheduleAlways",
    "start_time": "deskScheduleStart",
    "end_time": "deskScheduleEnd",
    "launch_at_login": "isLaunchAtLogin",
    "refill_percentage": "refillPercentage",
    "refill_installed_date": "refillInstalledDate",
    "total_puffs": "totalPuffsCount",
    "last_active_timestamp": "lastActiveTimestamp",
    # Python-only, but namespaced the same way so the Swift app preserves them.
    "active_days": "activeDays",
    "max_idle_seconds": "maxIdleSeconds",
    "last_power_on": "lastPowerOn",
    "last_intensity": "lastIntensity",
}


def _read_raw(path: Path) -> Dict[str, Any]:
    # Declared as the union json can actually produce, so narrowing to a dict
    # yields a fully known type under both pyright --strict and mypy --strict.
    loaded: Union[Dict[str, Any], List[Any], str, float, bool, None]
    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


@dataclass
class AppConfig:
    device_address: Optional[str] = None
    room_id: int = 1
    fragrance_id: int = 1
    location_name: Optional[str] = None
    default_intensity: int = 2
    presence_enabled: bool = True
    schedule_always: bool = False
    start_time: str = "09:00"
    end_time: str = "17:30"
    launch_at_login: bool = False
    refill_percentage: float = 100.0
    refill_installed_date: Optional[str] = None
    total_puffs: int = 0
    last_active_timestamp: Optional[float] = None
    active_days: List[int] = field(default_factory=lambda: [1, 2, 3, 4, 5])
    max_idle_seconds: int = 300
    last_power_on: bool = False
    last_intensity: int = 2

    @classmethod
    def load(cls, path: Path = CONFIG_FILE) -> "AppConfig":
        raw = _read_raw(path)
        if not raw:
            return cls()

        defaults = cls()
        kwargs: Dict[str, Any] = {}
        for f in fields(cls):
            key = JSON_KEYS[f.name]
            if key not in raw:
                continue
            value = raw[key]
            fallback = getattr(defaults, f.name)
            try:
                if value is None:
                    kwargs[f.name] = None if fallback is None else fallback
                elif isinstance(fallback, bool) or f.name in (
                    "presence_enabled",
                    "schedule_always",
                    "launch_at_login",
                    "last_power_on",
                ):
                    kwargs[f.name] = bool(value)
                elif isinstance(fallback, list):
                    kwargs[f.name] = list(value)
                elif f.name in ("refill_percentage", "last_active_timestamp"):
                    kwargs[f.name] = float(value)
                elif isinstance(fallback, int):
                    kwargs[f.name] = int(value)
                else:
                    kwargs[f.name] = value
            except TypeError, ValueError:
                continue  # keep the default rather than discarding the whole file
        return cls(**kwargs)

    def save(self, path: Path = CONFIG_FILE) -> None:
        """Merge over whatever is already on disk.

        A plain overwrite would drop every key the Swift app owns but this port
        does not model, so the menu bar app would lose its paired device and
        refill level the first time the CLI touched a setting.
        """
        raw = _read_raw(path)
        for f in fields(self):
            raw[JSON_KEYS[f.name]] = getattr(self, f.name)
        try:
            with open(path, "w", encoding="utf-8") as f_out:
                json.dump(raw, f_out, indent=2, sort_keys=True)
        except Exception:
            pass
