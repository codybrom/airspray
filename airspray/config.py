"""
Persistent Application Configuration for Airspray.
Stores device settings, schedule preferences, and launch-at-login state.

This file is shared with the Swift menu bar app, which reads and writes the same
~/.airspray_config.json. The two ports keep their own idiomatic attribute names
-- snake_case here, camelCase there -- but must agree on the JSON keys, and must
leave each other's keys alone. See
tests/test_swift_protocol_parity.py::test_config_keys_match_python.

Rules both ports follow:

* Writes are atomic (temp file in the same directory, then rename).
* A missing file is a fresh config. A file that exists but cannot be parsed is
  retried, then moved aside to ``<name>.corrupt``. Defaults are never silently
  saved over it.
* Every save reads the file fresh, applies only the keys the caller changed,
  and writes. Nothing writes back a whole config it loaded earlier.
* A cleared optional is written by removing its key. An absent key and an
  explicit null mean the same thing.
* Write errors propagate to the caller.
"""

import copy
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union, cast

from airspray.protocol import ROOM_NAMES

logger = logging.getLogger(__name__)

CONFIG_FILE: Path = Path.home() / ".airspray_config.json"

# Python attribute -> JSON key, matching AppConfig.CodingKeys in
# macos/AirSpray/Sources/Config.swift.
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

# A reader can catch the other app between truncate and write if that app is an
# older build without atomic writes. A few short retries ride that out.
READ_RETRIES: int = 4
READ_RETRY_PAUSE_SECONDS: float = 0.05


def default_config_path() -> Path:
    """The shared config path, resolved at call time so tests can redirect it."""
    return CONFIG_FILE


def _parse_file(path: Path) -> Optional[Dict[str, Any]]:
    """The file's JSON object, or None when it is not (yet) a parseable object."""
    # Declared as the union json can actually produce, so narrowing to a dict
    # yields a fully known type under both pyright --strict and mypy --strict.
    loaded: Union[Dict[str, Any], List[Any], str, float, bool, None]
    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except ValueError, UnicodeDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _read_raw(path: Path) -> Dict[str, Any]:
    """Read the shared file. Missing means fresh, and unreadable is never 'empty'."""
    for attempt in range(READ_RETRIES):
        try:
            parsed = _parse_file(path)
        except FileNotFoundError:
            return {}
        if parsed is not None:
            return parsed
        if attempt < READ_RETRIES - 1:
            time.sleep(READ_RETRY_PAUSE_SECONDS)

    corrupt = path.with_name(path.name + ".corrupt")
    os.replace(path, corrupt)
    logger.warning(
        f"Config file {path} could not be parsed. Moved it to {corrupt} "
        "and starting from defaults."
    )
    return {}


def _write_raw(path: Path, raw: Dict[str, Any]) -> None:
    """Atomically replace the shared file. Errors propagate to the caller."""
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f_out:
            json.dump(raw, f_out, indent=2, sort_keys=True)
            f_out.flush()
            os.fsync(f_out.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _coerce_bool(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def _coerce_day(value: object) -> int:
    if isinstance(value, (int, float, str)):
        return int(value)
    raise TypeError("activeDays entries must be numbers")


def _coerce_days(value: object) -> List[int]:
    if not isinstance(value, list):
        raise TypeError("activeDays must be a list")
    # isinstance leaves the element type unknown to pyright. Object is the
    # honest type, and unlike Any it is not a cast mypy calls redundant.
    return [_coerce_day(v) for v in cast(List[object], value)]


def _copy(value: Any) -> Any:
    # A shallow copy, so a caller mutating a list field (activeDays) cannot
    # change the snapshot it was loaded from.
    return copy.copy(value)


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

    def __post_init__(self) -> None:
        # What save() compares against. A constructed config is measured
        # against the defaults. A loaded one against what was on disk.
        self._set_baseline(_default_values())

    def _set_baseline(self, values: Dict[str, Any]) -> None:
        # Kept out of the dataclass fields: the field list is the JSON contract.
        self.__dict__["_baseline"] = {k: _copy(v) for k, v in values.items()}

    def _baseline_values(self) -> Dict[str, Any]:
        return cast(Dict[str, Any], self.__dict__["_baseline"])

    def _values(self) -> Dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @property
    def location_label(self) -> str:
        """The user's own name for the spot, else the room preset's name."""
        if self.location_name and self.location_name.strip():
            return self.location_name
        return ROOM_NAMES.get(self.room_id, "Other")

    @classmethod
    def _from_raw(cls, raw: Dict[str, Any]) -> "AppConfig":
        defaults = _default_values()
        kwargs: Dict[str, Any] = {}
        for f in fields(cls):
            value = raw.get(JSON_KEYS[f.name])
            if value is None:
                continue  # absent and null both mean "the default"
            fallback = defaults[f.name]
            try:
                if isinstance(fallback, bool):
                    kwargs[f.name] = _coerce_bool(value)
                elif isinstance(fallback, list):
                    kwargs[f.name] = _coerce_days(value)
                elif f.name in ("refill_percentage", "last_active_timestamp"):
                    kwargs[f.name] = float(value)
                elif isinstance(fallback, int):
                    kwargs[f.name] = int(value)
                else:
                    kwargs[f.name] = str(value)
            except TypeError, ValueError:
                continue  # keep the default rather than discarding the whole file
        config = cls(**kwargs)
        config._set_baseline(config._values())
        return config

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "AppConfig":
        return cls._from_raw(_read_raw(path or default_config_path()))

    def save(self, path: Optional[Path] = None) -> None:
        """Write only the fields changed since this object was loaded/created.

        The file is re-read first, so keys the Swift app owns -- and fields
        another process changed in the meantime -- are left exactly as they are.
        """
        baseline = self._baseline_values()
        current = self._values()
        changes = {k: v for k, v in current.items() if v != baseline[k]}
        update_config(path, **changes)
        self._set_baseline(current)


def _default_values() -> Dict[str, Any]:
    defaults: Dict[str, Any] = {}
    for f in fields(AppConfig):
        factory: Any = f.default_factory
        defaults[f.name] = factory() if callable(factory) else f.default
    return defaults


def modify_config(
    mutate: Callable[[AppConfig], Dict[str, Any]], path: Optional[Path] = None
) -> AppConfig:
    """Read-fresh / change / write in one step.

    `mutate` receives the config as it is on disk right now and returns the
    attributes to change (None clears an optional by removing its key). This is
    how deltas such as refill depletion are applied to the current value rather
    than to one remembered from earlier.
    """
    target = path or default_config_path()
    raw = _read_raw(target)
    changes = mutate(AppConfig._from_raw(raw))  # pyright: ignore[reportPrivateUsage]
    for name, value in changes.items():
        key = JSON_KEYS[name]
        if value is None:
            raw.pop(key, None)
        else:
            raw[key] = _copy(value)
    # An explicit null left by an older build is the same as an absent key.
    for key in JSON_KEYS.values():
        if key in raw and raw[key] is None:
            del raw[key]
    _write_raw(target, raw)
    return AppConfig._from_raw(raw)  # pyright: ignore[reportPrivateUsage]


def update_config(path: Optional[Path] = None, **changes: Any) -> AppConfig:
    """Set the given attributes on the shared file, leaving every other key."""
    unknown = sorted(set(changes) - set(JSON_KEYS))
    if unknown:
        raise KeyError(f"Unknown config attribute(s): {unknown}")
    return modify_config(lambda _current: dict(changes), path)
