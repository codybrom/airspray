"""
Refill capacity tracking for Air Wick Bluetooth Diffuser.
Since the hardware has no internal liquid sensor, refill levels are tracked
algorithmically based on misting duration, intensity, and runtime.

The level lives in the shared ~/.airspray_config.json alongside everything else,
because the Swift menu bar app reads and writes it there. It used to live in its
own ~/.airspray/state.json, which meant the CLI and the menu bar quoted two
different bottle levels for the same bottle; LEGACY_STATE_FILE is migrated once
and then left alone.
"""

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from airspray.config import CONFIG_FILE, AppConfig

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


@dataclass
class RefillState:
    refill_percent: float = 100.0
    last_updated: float = 0.0
    last_power: bool = False
    last_intensity: int = 2
    installed_date: str = ""
    total_boosts: int = 0
    location_name: str = "Office"


class RefillTracker:
    def __init__(
        self,
        config_path: Path = CONFIG_FILE,
        legacy_path: Path = LEGACY_STATE_FILE,
    ) -> None:
        self.config_path: Path = config_path
        self.legacy_path: Path = legacy_path
        self.state: RefillState = self.load_state()

    def _from_config(self, config: AppConfig) -> RefillState:
        return RefillState(
            refill_percent=config.refill_percentage,
            last_updated=config.last_active_timestamp or 0.0,
            last_power=config.last_power_on,
            last_intensity=config.last_intensity,
            installed_date=config.refill_installed_date or "",
            total_boosts=config.total_puffs,
            location_name=config.location_name or "Office",
        )

    def _legacy_state(self) -> Optional[RefillState]:
        """Read the pre-shared-config store, if this machine still has one."""
        if not self.legacy_path.exists():
            return None
        try:
            raw: Dict[str, object] = json.loads(self.legacy_path.read_text())
        except Exception as e:
            logger.warning(f"Could not load legacy refill state: {e}")
            return None
        return RefillState(
            refill_percent=_parse_float(raw.get("refill_percent"), 100.0),
            last_updated=_parse_float(raw.get("last_updated"), 0.0),
            last_power=_parse_bool(raw.get("last_power"), False),
            last_intensity=_parse_int(raw.get("last_intensity"), 2),
            installed_date=_parse_str(raw.get("installed_date"), ""),
            total_boosts=_parse_int(raw.get("total_boosts"), 0),
            location_name=_parse_str(raw.get("location_name"), "Office"),
        )

    def load_state(self) -> RefillState:
        config = AppConfig.load(self.config_path)
        if self.config_path.exists():
            return self._from_config(config)

        migrated = self._legacy_state()
        if migrated is not None:
            self.state = migrated
            self.save_state()
            return migrated

        fresh = RefillState(
            last_updated=time.time(),
            installed_date=datetime.now().strftime(INSTALLED_DATE_FORMAT),
        )
        self.state = fresh
        self.save_state()
        return fresh

    def save_state(self) -> None:
        try:
            config = AppConfig.load(self.config_path)
            config.refill_percentage = self.state.refill_percent
            config.last_active_timestamp = self.state.last_updated
            config.last_power_on = self.state.last_power
            config.last_intensity = self.state.last_intensity
            config.refill_installed_date = self.state.installed_date or None
            config.total_puffs = self.state.total_boosts
            config.location_name = self.state.location_name
            config.save(self.config_path)
        except Exception as e:
            logger.error(f"Failed to save refill state: {e}")

    def set_location_name(self, name: str) -> None:
        """Update custom location name."""
        self.state.location_name = name.strip() if name.strip() else "Office"
        self.save_state()

    def update_usage(self, power: bool, intensity: int) -> None:
        """Update refill percentage based on elapsed active time."""
        now = time.time()
        elapsed = now - self.state.last_updated if self.state.last_updated > 0 else 0.0

        # Only consume if power was ON
        if self.state.last_power and elapsed > 0:
            rate = CONSUMPTION_RATE_PER_SEC.get(
                self.state.last_intensity, CONSUMPTION_RATE_PER_SEC[2]
            )
            consumed = rate * elapsed
            self.state.refill_percent = max(0.0, self.state.refill_percent - consumed)

        self.state.last_power = power
        self.state.last_intensity = intensity
        self.state.last_updated = now
        self.save_state()

    def record_boost(self) -> None:
        """Record a manual boost burst spray."""
        self.state.refill_percent = max(
            0.0, self.state.refill_percent - BOOST_CONSUMPTION_PERCENT
        )
        self.state.total_boosts += 1
        self.save_state()

    def reset_refill(self, percent: float = 100.0) -> None:
        """Reset when inserting a new bottle."""
        self.state.refill_percent = float(percent)
        self.state.installed_date = datetime.now().strftime(INSTALLED_DATE_FORMAT)
        self.state.total_boosts = 0
        self.state.last_updated = time.time()
        self.save_state()

    def adjust_level(self, percent: float) -> None:
        """Manually set refill percentage."""
        self.state.refill_percent = max(0.0, min(100.0, float(percent)))
        self.state.last_updated = time.time()
        self.save_state()
