"""
Native macOS (26+) Launch at Login via Apple ServiceManagement (SMAppService).
Officially registers Airspray directly with the modern macOS subsystem.

SMAppService.mainAppService() registers *the running app bundle*. When Airspray
is run as a plain Python process there is no bundle to register, so these
functions report failure rather than registering the interpreter.
"""

import importlib
import logging
from typing import Any, List, Tuple, cast

logger = logging.getLogger(__name__)


def _get_sm_app_service() -> Any:
    """Load and return the native SMAppService instance, or None if unavailable."""
    try:
        foundation: Any = importlib.import_module("Foundation")
        objc_mod: Any = importlib.import_module("objc")
        if foundation.NSBundle.mainBundle().bundleIdentifier() is None:
            return None  # not running from an app bundle. Nothing to register
        foundation.NSBundle.bundleWithPath_(
            "/System/Library/Frameworks/ServiceManagement.framework"
        ).load()
        sm_class: Any = objc_mod.lookUpClass("SMAppService")
        if sm_class is not None:
            return sm_class.mainAppService()
    except Exception:
        pass
    return None


def _succeeded(result: object) -> bool:
    """Interpret the return of a PyObjC ``...AndReturnError_(None)`` call.

    PyObjC returns ``(ok, error)`` for methods with an NSError** out-parameter.
    A non-empty tuple is always truthy, so the first element has to be checked.
    """
    if isinstance(result, tuple):
        # isinstance leaves the element type unknown to pyright. Object is the
        # honest type, and unlike Any it is not a cast mypy calls redundant.
        parts: List[object] = list(cast(Tuple[object, ...], result))
        if not parts:
            return False
        if not bool(parts[0]) and len(parts) > 1 and parts[1] is not None:
            logger.warning(f"Launch-at-login change failed: {parts[1]}")
        return bool(parts[0])
    return bool(result)


def set_launch_at_login(enabled: bool) -> bool:
    """Enable or disable autostart at login. Returns True only on success."""
    service = _get_sm_app_service()
    if service is None:
        return False
    try:
        if enabled:
            return _succeeded(service.registerAndReturnError_(None))
        return _succeeded(service.unregisterAndReturnError_(None))
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
