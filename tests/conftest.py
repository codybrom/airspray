"""Shared test isolation.

No test may touch the user's real ~/.airspray_config.json, ~/.airspray/, their
LaunchAgents, or the real macOS login-item service. Every test gets scratch
paths, and SMAppService is unavailable unless a test installs its own fake.
"""

from pathlib import Path
from typing import Iterator

import pytest

from airspray import autostart, cli, config, refill


@pytest.fixture(autouse=True)
def isolated_user_state(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setattr(config, "CONFIG_FILE", home / ".airspray_config.json")
    monkeypatch.setattr(config, "READ_RETRY_PAUSE_SECONDS", 0.0)
    monkeypatch.setattr(refill, "LEGACY_STATE_FILE", home / ".airspray" / "state.json")
    monkeypatch.setattr(cli, "SERVICE_LOG_DIR", home / ".airspray")
    monkeypatch.setattr(
        cli, "LAUNCHD_PLIST_PATH", home / "LaunchAgents" / "airspray.plist"
    )
    monkeypatch.setattr(
        cli, "SYSTEMD_SERVICE_PATH", home / "systemd" / "airspray.service"
    )
    monkeypatch.setattr(autostart, "_get_sm_app_service", lambda: None)
    yield home
