"""
Native macOS (26+) Launch at Login via Apple ServiceManagement (SMAppService).
Officially registers Airspray directly with the modern macOS subsystem.
"""

import importlib
import sys
from pathlib import Path
from typing import Any


def get_app_bundle_path() -> Path:
    """Get the running Airspray.app bundle or executable path."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        for parent in exe.parents:
            if parent.suffix == ".app":
                return parent
        return exe
    repo_app = Path("dist/AirSpray.app").resolve()
    if repo_app.exists():
        return repo_app
    apps_app = Path("/Applications/AirSpray.app")
    if apps_app.exists():
        return apps_app
    return Path(sys.executable).resolve()


def _get_sm_app_service() -> Any:
    """Load and return the native SMAppService instance."""
    try:
        foundation: Any = importlib.import_module("Foundation")
        objc_mod: Any = importlib.import_module("objc")
        foundation.NSBundle.bundleWithPath_(
            "/System/Library/Frameworks/ServiceManagement.framework"
        ).load()
        sm_class: Any = objc_mod.lookUpClass("SMAppService")
        if sm_class is not None:
            return sm_class.mainAppService()
    except Exception:
        pass
    return None


def set_launch_at_login(enabled: bool) -> bool:
    """Enable or disable autostart at login using modern macOS SMAppService."""
    service = _get_sm_app_service()
    if service is None:
        return False
    try:
        if enabled:
            reg_res: Any = service.registerAndReturnError_(None)
            return bool(reg_res)
        else:
            unreg_res: Any = service.unregisterAndReturnError_(None)
            return bool(unreg_res)
    except Exception:
        return False


def is_launch_at_login_enabled() -> bool:
    """Check if Airspray is registered to launch at login."""
    service = _get_sm_app_service()
    if service is None:
        return False
    try:
        # SMAppServiceStatus: 1 = SMAppServiceStatusEnabled
        return int(service.status()) == 1
    except Exception:
        return False
