"""
Refill capacity tracking for Air Wick Bluetooth Diffuser.
Since the hardware has no internal liquid sensor, refill levels are tracked
algorithmically based on misting duration, intensity, and runtime.

The level lives in the shared ~/.airspray_config.json alongside everything else,
because the Swift menu bar app reads and writes it there. The tracker keeps no
copy of its own: every operation reads the file, applies a delta to whatever is
there now, and writes. A long-running daemon therefore cannot put back a level
that the menu bar app or `airspray refill reset` has since changed.

The level used to live in ~/.airspray/state.json. That file is migrated at most
once -- only while the shared config has no refill level at all -- and is then
renamed out of the way.
"""

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, cast

from airspray import config as config_module
from airspray.config import JSON_KEYS, AppConfig, modify_config, update_config

logger = logging.getLogger(__name__)

LEGACY_STATE_FILE = Path.home() / ".airspray" / "state.json"

INSTALLED_DATE_FORMAT = "%Y-%m-%d"

CONSUMPTION_RATE_PER_SEC: Dict[int, float] = {
    0: 0.0,
    1: 100.0 / (45 * 8 * 3600),
    2: 100.0 / (30 * 8 * 3600),
    3: 100.0 / (20 * 8 * 3600),
    4: 100.0 / (15 * 8 * 3600),
}
BOOST_CONSUMPTION_PERCENT: float = 0.08  # ~0.08% per boost spray

# The pre-shared-config store wrote this whether or not the user chose it.
_LEGACY_DEFAULT_LOCATION = "Office"


def _parse_float(val: object, default: float) -> float:
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val)
        except ValueError:
            pass
    return default


def _parse_int(val: object, default: int) -> int:
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, str):
        try:
            return int(val)
        except ValueError:
            pass
    return default


def _parse_bool(val: object, default: bool) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        return val.lower() in ("true", "1")
    return default


def _parse_str(val: object, default: str) -> str:
    if isinstance(val, str):
        return val
    return default


def clamp_percent(percent: float) -> float:
    value = float(percent)
    if value != value:  # NaN
        raise ValueError("Refill percentage must be a number")
    return max(0.0, min(100.0, value))


@dataclass
class RefillState:
    """A read-only snapshot of the refill keys in the shared config."""

    refill_percent: float = 100.0
    last_updated: float = 0.0
    last_power: bool = False
    last_intensity: int = 2
    installed_date: str = ""
    total_boosts: int = 0
    # None unless the user named the spot. Callers fall back to the room name.
    location_name: Optional[str] = None
    location_label: str = "Other"


class RefillTracker:
    def __init__(
        self,
        config_path: Optional[Path] = None,
        legacy_path: Optional[Path] = None,
    ) -> None:
        self.config_path: Path = config_path or config_module.default_config_path()
        self.legacy_path: Path = legacy_path or LEGACY_STATE_FILE
        self._migrate_legacy_once()

    # -- reading ---------------------------------------------------------

    @property
    def state(self) -> RefillState:
        """The refill state as it is on disk right now."""
        return self._from_config(AppConfig.load(self.config_path))

    def load_state(self) -> RefillState:
        return self.state

    @staticmethod
    def _from_config(config: AppConfig) -> RefillState:
        return RefillState(
            refill_percent=config.refill_percentage,
            last_updated=config.last_active_timestamp or 0.0,
            last_power=config.last_power_on,
            last_intensity=config.last_intensity,
            installed_date=config.refill_installed_date or "",
            total_boosts=config.total_puffs,
            location_name=config.location_name,
            location_label=config.location_label,
        )

    # -- legacy migration --------------------------------------------------

    def _migrate_legacy_once(self) -> None:
        """Import ~/.airspray/state.json into the shared config, once.

        Decided by whether the shared file has a refill level *key*, never by
        its value: a bottle that reads exactly 100% is a real level, not a sign
        that nothing was migrated yet.
        """
        if not self.legacy_path.exists():
            return
        try:
            loaded: Any = json.loads(self.legacy_path.read_text())
        except Exception as e:
            logger.warning(f"Could not load legacy refill state: {e}")
            return
        if not isinstance(loaded, dict):
            return
        # isinstance leaves the key and value types unknown to pyright. Object
        # is the honest type, and unlike Any it is not a cast mypy calls redundant.
        raw = cast(Dict[str, object], loaded)

        on_disk = config_module._read_raw(  # pyright: ignore[reportPrivateUsage]
            self.config_path
        )
        if JSON_KEYS["refill_percentage"] not in on_disk:
            changes: Dict[str, Any] = {
                "refill_percentage": clamp_percent(
                    _parse_float(raw.get("refill_percent"), 100.0)
                ),
                "last_active_timestamp": _parse_float(raw.get("last_updated"), 0.0)
                or None,
                "last_power_on": _parse_bool(raw.get("last_power"), False),
                "last_intensity": _parse_int(raw.get("last_intensity"), 2),
                "refill_installed_date": _parse_str(raw.get("installed_date"), "")
                or None,
                "total_puffs": _parse_int(raw.get("total_boosts"), 0),
            }
            name = _parse_str(raw.get("location_name"), "").strip()
            if name and name != _LEGACY_DEFAULT_LOCATION:
                changes["location_name"] = name
            update_config(self.config_path, **changes)
            logger.info("Migrated legacy refill state into the shared config.")

        # Either way the legacy file has served its purpose. Retire it so it can
        # never be read again, even if the shared config is later recreated.
        try:
            self.legacy_path.rename(
                self.legacy_path.with_name(self.legacy_path.name + ".migrated")
            )
        except OSError as e:
            logger.warning(f"Could not retire legacy refill state: {e}")

    # -- writing -----------------------------------------------------------

    @staticmethod
    def _accrue(current: AppConfig, now: float) -> Dict[str, Any]:
        """Depletion since the last update, as a change to the on-disk level."""
        last = current.last_active_timestamp or 0.0
        elapsed = now - last if last > 0 else 0.0
        changes: Dict[str, Any] = {"last_active_timestamp": now}
        if current.last_power_on and elapsed > 0:
            rate = CONSUMPTION_RATE_PER_SEC.get(
                current.last_intensity, CONSUMPTION_RATE_PER_SEC[2]
            )
            changes["refill_percentage"] = max(
                0.0, current.refill_percentage - rate * elapsed
            )
        return changes

    def set_location_name(self, name: Optional[str]) -> None:
        """Set the user's own location name. Blank or None clears it."""
        cleaned = name.strip() if name else ""
        update_config(self.config_path, location_name=cleaned or None)

    def update_usage(
        self, power: Optional[bool] = None, intensity: Optional[int] = None
    ) -> None:
        """Account for runtime since the last update, then record new state.

        Pass None for whichever of power / intensity this caller did not just
        learn or change. The stored value is kept.
        """
        now = time.time()

        def mutate(current: AppConfig) -> Dict[str, Any]:
            changes = self._accrue(current, now)
            if power is not None:
                changes["last_power_on"] = power
            if intensity is not None:
                changes["last_intensity"] = intensity
            return changes

        modify_config(mutate, self.config_path)

    def record_boost(self) -> None:
        """Record a manual boost burst spray."""

        def mutate(current: AppConfig) -> Dict[str, Any]:
            return {
                "refill_percentage": max(
                    0.0, current.refill_percentage - BOOST_CONSUMPTION_PERCENT
                ),
                "total_puffs": current.total_puffs + 1,
            }

        modify_config(mutate, self.config_path)

    def reset_refill(self, percent: float = 100.0) -> float:
        """Reset when inserting a new bottle. Returns the (clamped) level set."""
        level = clamp_percent(percent)
        update_config(
            self.config_path,
            refill_percentage=level,
            refill_installed_date=datetime.now().strftime(INSTALLED_DATE_FORMAT),
            total_puffs=0,
            last_active_timestamp=time.time(),
        )
        return level

    def adjust_level(self, percent: float) -> float:
        """Manually set refill percentage. Returns the (clamped) level set."""
        level = clamp_percent(percent)
        update_config(
            self.config_path,
            refill_percentage=level,
            last_active_timestamp=time.time(),
        )
        return level
