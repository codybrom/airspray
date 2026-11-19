"""
Cross-Platform Command-line interface for Air Wick Bluetooth Diffuser controller & presence daemon.
"""

import argparse
import asyncio
import json
import logging
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Coroutine, Dict, FrozenSet, List, Optional, Tuple, cast
from xml.sax.saxutils import escape as xml_escape

from airspray.client import AirWickDiffuser
from airspray.config import AppConfig, update_config
from airspray.presence import PresenceConfig, PresenceDaemon, parse_clock
from airspray.protocol import (
    FRAGRANCE_NAMES,
    MAX_FRAGRANCE_ID,
    ROOM_NAMES,
    Schedule,
    fragrances_sorted_by_name,
    intensity_label,
    is_room_preset,
    resolve_room_id,
)
from airspray.refill import RefillTracker, clamp_percent
from airspray.schema import schedule_name_from_id

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("airspray")

LAUNCHD_PLIST_LABEL: str = "com.codybrom.airspray"
LAUNCHD_PLIST_PATH: Path = (
    Path.home() / "Library/LaunchAgents" / f"{LAUNCHD_PLIST_LABEL}.plist"
)
SYSTEMD_SERVICE_PATH: Path = Path.home() / ".config/systemd/user/airspray.service"
SERVICE_LOG_DIR: Path = Path.home() / ".airspray"

# Long enough for the daemon to reconnect and switch the diffuser off on SIGTERM.
SERVICE_STOP_TIMEOUT_SECONDS: int = 30

DAY_KEYS: Tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


# ---------------------------------------------------------------------------
# Argument validation (runs at parse time, before any Bluetooth connection)
# ---------------------------------------------------------------------------


def parse_days(value: str) -> FrozenSet[str]:
    """Validate --days: comma-separated mon..sun, or all."""
    tokens = [t.strip().lower() for t in str(value).split(",") if t.strip()]
    if not tokens:
        raise argparse.ArgumentTypeError(
            "No days given. Use a comma-separated list of "
            f"{','.join(DAY_KEYS)} or 'all'."
        )
    unknown = [t for t in tokens if t not in DAY_KEYS and t != "all"]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"Unknown day(s): {', '.join(unknown)}. "
            f"Use {','.join(DAY_KEYS)} or 'all'."
        )
    if "all" in tokens:
        return frozenset(DAY_KEYS)
    return frozenset(tokens)


def clock_arg(value: str) -> str:
    """Validate an HH:MM time (00:00-23:59) and normalise it."""
    try:
        minutes = parse_clock(str(value))
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def end_clock_arg(value: str) -> str:
    """Like clock_arg, but also accepts 24:00 meaning end of day."""
    try:
        minutes = parse_clock(str(value), allow_24=True)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def percent_arg(value: str) -> float:
    try:
        return clamp_percent(float(value))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid percentage {value!r}. Expected a number from 0 to 100"
        ) from None


def resolve_fragrance_id(value: object) -> int:
    """Accept a fragrance name or ID, the way the Mac app's picker does.

    The catalogue is the same table the menu bar renders
    (airspray.protocol.FRAGRANCE_NAMES), so the two ports address the same
    bottle by the same word.
    """
    text = str(value).strip()
    if text.isdigit():
        fid = int(text)
        if fid in FRAGRANCE_NAMES:
            return fid
        raise argparse.ArgumentTypeError(
            f"Fragrance ID must be between 1 and {MAX_FRAGRANCE_ID} (got {fid})"
        )

    folded = text.casefold()
    for fid, name in FRAGRANCE_NAMES.items():
        if name.casefold() == folded:
            return fid
    raise argparse.ArgumentTypeError(
        f"Unknown fragrance {text!r}. Run 'airspray fragrances' to list them."
    )


# ---------------------------------------------------------------------------
# Background service definitions
# ---------------------------------------------------------------------------


def daemon_command(
    intensity: int,
    start_time: str = "00:00",
    end_time: str = "24:00",
    workdays_only: bool = False,
    no_boost: bool = False,
    address: str = "",
) -> List[str]:
    """The argv a service manager should run for the presence daemon.

    The standalone PyInstaller binary *is* the CLI, so it takes the subcommand
    directly. `-m airspray.cli` only makes sense for a real interpreter.
    """
    if getattr(sys, "frozen", False):
        command = [sys.executable]
    else:
        command = [sys.executable, "-m", "airspray.cli"]
    command += [
        "daemon",
        "--intensity",
        str(intensity),
        "--start",
        start_time,
        "--end",
        end_time,
    ]
    if workdays_only:
        command.append("--workdays-only")
    if no_boost:
        command.append("--no-boost")
    if address:
        command += ["--address", address]
    return command


def service_working_dir() -> str:
    if getattr(sys, "frozen", False):
        return str(Path.home())
    return str(Path(__file__).resolve().parent.parent)


def get_plist_content(command: List[str], working_dir: str, log_dir: str) -> str:
    args_xml = "\n".join(
        f"        <string>{xml_escape(arg)}</string>" for arg in command
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{xml_escape(LAUNCHD_PLIST_LABEL)}</string>
    <key>ProgramArguments</key>
    <array>
{args_xml}
    </array>
    <key>WorkingDirectory</key>
    <string>{xml_escape(working_dir)}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ExitTimeOut</key>
    <integer>{SERVICE_STOP_TIMEOUT_SECONDS}</integer>
    <key>StandardOutPath</key>
    <string>{xml_escape(log_dir)}/airspray.log</string>
    <key>StandardErrorPath</key>
    <string>{xml_escape(log_dir)}/airspray.err</string>
</dict>
</plist>
"""


def systemd_quote(arg: str) -> str:
    """Quote one argument for a systemd unit's ExecStart line."""
    escaped = (
        arg.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("%", "%%")
        .replace("$", "$$")
    )
    return f'"{escaped}"'


def get_systemd_service_content(command: List[str], working_dir: str) -> str:
    exec_start = " ".join(systemd_quote(arg) for arg in command)
    return f"""[Unit]
Description=Air Wick Presence Daemon
After=network.target bluetooth.target

[Service]
Type=simple
WorkingDirectory={working_dir.replace("%", "%%")}
ExecStart={exec_start}
Restart=always
RestartSec=5
TimeoutStopSec={SERVICE_STOP_TIMEOUT_SECONDS}

[Install]
WantedBy=default.target
"""


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _refill_bar(percent: float) -> str:
    filled = int(max(0.0, min(100.0, percent)) / 10)
    return "█" * filled + "░" * (10 - filled)


async def cmd_scan(args: argparse.Namespace) -> int:
    print("🔍 Scanning for nearby Air Wick diffusers...")
    devices = await AirWickDiffuser.discover(timeout=6.0)
    if not devices:
        print(
            "❌ No Air Wick diffuser found. Ensure it is powered on and within Bluetooth range."
        )
        return 1
    print(f"✅ Found {len(devices)} device(s):")
    for d in devices:
        print(f"  • Name: {d.name or 'Unknown'} | Address/UUID: {d.address}")
    return 0


async def cmd_on(args: argparse.Namespace) -> int:
    tracker = RefillTracker()
    address: Optional[str] = getattr(args, "address", None)
    intensity: Optional[int] = getattr(args, "intensity", None)

    print("🔌 Connecting to diffuser...")
    async with AirWickDiffuser(address) as diffuser:
        print("⚡ Turning ON...")
        await diffuser.turn_on()
        if intensity is not None:
            print(f"🌡 Setting intensity to {intensity}...")
            await diffuser.set_intensity(intensity)
    # None keeps the recorded intensity: plain `on` does not change it.
    tracker.update_usage(True, intensity)
    print("✅ Done.")
    return 0


async def cmd_off(args: argparse.Namespace) -> int:
    tracker = RefillTracker()
    address: Optional[str] = getattr(args, "address", None)
    print("🔌 Connecting to diffuser...")
    async with AirWickDiffuser(address) as diffuser:
        print("💤 Turning OFF...")
        await diffuser.turn_off()
    tracker.update_usage(False, None)
    print("✅ Done.")
    return 0


async def cmd_boost(args: argparse.Namespace) -> int:
    tracker = RefillTracker()
    address: Optional[str] = getattr(args, "address", None)
    print("🔌 Connecting to diffuser...")
    async with AirWickDiffuser(address) as diffuser:
        print("💨 Triggering instant fragrance burst...")
        await diffuser.boost()
    tracker.record_boost()
    print("✅ Done.")
    return 0


async def cmd_intensity(args: argparse.Namespace) -> int:
    tracker = RefillTracker()
    address: Optional[str] = getattr(args, "address", None)
    level: int = int(getattr(args, "level", 2))

    print("🔌 Connecting to diffuser...")
    async with AirWickDiffuser(address) as diffuser:
        print(f"🌡 Setting intensity to {level} ({intensity_label(level)})...")
        await diffuser.set_intensity(level)
    # Power was not queried or changed here, so leave the recorded power alone.
    tracker.update_usage(None, level)
    print("✅ Done.")
    return 0


async def cmd_status(args: argparse.Namespace) -> int:
    tracker = RefillTracker()
    address: Optional[str] = getattr(args, "address", None)
    print("🔌 Connecting to diffuser...")
    async with AirWickDiffuser(address) as diffuser:
        status = await diffuser.get_status()  # raises if the diffuser is silent
        mcu_fw, ble_fw = await diffuser.get_firmware_versions()
    tracker.update_usage(status.power, status.intensity)

    state = tracker.state
    refill_pct = state.refill_percent

    print("\n📊 Air Wick Diffuser Status:")
    print(f"  • Location:       {state.location_label}")
    print(f"  • Power:          {'🟢 ON' if status.power else '🔴 OFF'}")
    print(
        f"  • Intensity:      {status.intensity} / 4 "
        f"({intensity_label(status.intensity)})"
    )
    print(f"  • Spraying Now:   {'Yes 💨' if status.spraying else 'No'}")
    print(f"  • Battery Level:  {status.battery_level} / 4")
    print(f"  • Refill Level:   [{_refill_bar(refill_pct)}] {refill_pct:.1f}%")
    if mcu_fw:
        print(f"  • MCU Firmware:   {mcu_fw}")
    if ble_fw:
        print(f"  • BLE Firmware:   {ble_fw}")
    print()
    return 0


async def cmd_sync_time(args: argparse.Namespace) -> int:
    address: Optional[str] = getattr(args, "address", None)
    print("🔌 Connecting to diffuser...")
    async with AirWickDiffuser(address) as diffuser:
        now = datetime.now()
        print(
            f"⏰ Syncing diffuser internal RTC clock to {now.strftime('%Y-%m-%d %H:%M:%S')}..."
        )
        await diffuser.sync_time(now)
    print("✅ Time synchronized successfully.")
    return 0


async def cmd_reset(args: argparse.Namespace) -> int:
    address: Optional[str] = getattr(args, "address", None)
    print("🔌 Connecting to diffuser...")
    async with AirWickDiffuser(address) as diffuser:
        print("⚠️ Resetting diffuser to factory settings...")
        await diffuser.reset()
    print("✅ Diffuser reset.")
    return 0


async def cmd_fragrances(_args: argparse.Namespace) -> int:
    """Print the catalogue in the same order the Mac app's picker shows it."""
    print("🌸 Fragrances (name or ID accepted by --fragrance):\n")
    for fid, name in fragrances_sorted_by_name():
        note = "  (not echoed by the device)" if fid > 31 else ""
        print(f"  {fid:>2}  {name}{note}")
    return 0


def plan_config_device(
    current: AppConfig, location: Optional[str], fragrance: Optional[int]
) -> Tuple[int, int, Dict[str, object]]:
    """Work out what `config-device` should send and save.

    The diffuser takes room and fragrance in one packet, so whichever was not
    given on the command line is carried over from the shared config instead of
    being reset to a default. Returns (room_id, fragrance_id, config changes).
    """
    room_id = current.room_id
    fragrance_id = current.fragrance_id if fragrance is None else fragrance
    changes: Dict[str, object] = {}
    if location is not None:
        room_id = resolve_room_id(location)
        # A preset names itself. Only free text is a custom location name.
        changes["location_name"] = (
            None if is_room_preset(location) else location.strip() or None
        )
    changes["room_id"] = room_id
    changes["fragrance_id"] = fragrance_id
    return room_id, fragrance_id, changes


async def cmd_config_device(args: argparse.Namespace) -> int:
    address: Optional[str] = getattr(args, "address", None)
    location: Optional[str] = getattr(args, "location", None)
    fragrance_arg: Optional[object] = getattr(args, "fragrance", None)
    if location is None and fragrance_arg is None:
        print(
            "❌ Nothing to change: pass --location and/or --fragrance.",
            file=sys.stderr,
        )
        return 2
    fragrance: Optional[int] = (
        None if fragrance_arg is None else resolve_fragrance_id(fragrance_arg)
    )

    room_id, fragrance_id, changes = plan_config_device(
        AppConfig.load(), location, fragrance
    )
    room_preset_name = ROOM_NAMES.get(room_id, "Other")
    fragrance_name = FRAGRANCE_NAMES.get(fragrance_id, f"ID {fragrance_id}")

    print("🔌 Connecting to diffuser...")
    async with AirWickDiffuser(address) as diffuser:
        print(
            f"🏷 Setting Room: {room_preset_name} [ID {room_id}], "
            f"Fragrance: {fragrance_name} [ID {fragrance_id}]..."
        )
        await diffuser.set_room_and_fragrance(room_id, fragrance_id)
    update_config(None, **changes)
    print("✅ Location and fragrance saved.")
    return 0


def describe_schedule(s: Schedule) -> str:
    start_str = f"{s.start_hour:02d}:{s.start_min:02d}"
    end_str = f"{s.end_hour:02d}:{s.end_min:02d}"
    state = "" if s.enabled else " | DISABLED"
    return (
        f"Slot {s.id} ({schedule_name_from_id(s.name_id)}): {start_str} - {end_str} "
        f"| Days: {s.days_str} | Intensity: {s.intensity}{state}"
    )


def build_schedule(
    sched_id: int,
    start: str,
    end: str,
    days: FrozenSet[str],
    intensity: int,
    enabled: bool,
) -> Schedule:
    start_min = parse_clock(start)
    end_min = parse_clock(end)
    return Schedule(
        id=sched_id,
        enabled=enabled,
        start_hour=start_min // 60,
        start_min=start_min % 60,
        end_hour=end_min // 60,
        end_min=end_min % 60,
        intensity=intensity,
        monday="mon" in days,
        tuesday="tue" in days,
        wednesday="wed" in days,
        thursday="thu" in days,
        friday="fri" in days,
        saturday="sat" in days,
        sunday="sun" in days,
    )


async def cmd_schedule(args: argparse.Namespace) -> int:
    address: Optional[str] = getattr(args, "address", None)
    action: str = str(getattr(args, "action", ""))

    # Everything that can be rejected is rejected before connecting.
    sched: Optional[Schedule] = None
    if action == "set":
        days_value: object = getattr(args, "days", "mon,tue,wed,thu,fri")
        days: FrozenSet[str] = (
            parse_days(days_value)
            if isinstance(days_value, str)
            else cast(FrozenSet[str], days_value)
        )
        sched = build_schedule(
            sched_id=int(getattr(args, "id", 1)),
            start=str(getattr(args, "start", "08:00")),
            end=str(getattr(args, "end", "17:00")),
            days=days,
            intensity=int(getattr(args, "intensity", 2)),
            enabled=not bool(getattr(args, "disable", False)),
        )

    print("🔌 Connecting to diffuser...")
    async with AirWickDiffuser(address) as diffuser:
        if action == "list":
            print("📅 Fetching on-device hardware schedules (slots 1..10)...")
            schedules = await diffuser.get_all_schedules()
            if not schedules:
                print("ℹ️ No hardware schedules programmed on the device.")
            else:
                print(f"\n📋 Hardware Schedules ({len(schedules)}):")
                for s in schedules:
                    print(f"  • {describe_schedule(s)}")
                print()

        elif action == "set" and sched is not None:
            print(f"📅 Programming schedule: {describe_schedule(sched)}...")
            await diffuser.set_schedule(sched)
            print("✅ Hardware schedule saved to diffuser EEPROM.")

        elif action == "delete":
            sched_id = int(getattr(args, "id", 1))
            print(f"🗑 Deleting schedule slot #{sched_id}...")
            await diffuser.delete_schedule(sched_id)
            print("✅ Schedule slot cleared.")

        elif action == "clear":
            print("🗑 Clearing all on-device hardware schedules...")
            await diffuser.clear_schedules()
            print("✅ All hardware schedules cleared.")

    return 0


def cmd_refill(args: argparse.Namespace) -> int:
    tracker = RefillTracker()
    action: str = str(getattr(args, "action", ""))
    if action == "status":
        state = tracker.state
        pct = state.refill_percent
        print("\n🧪 Refill Bottle Status:")
        print(f"  • Location:        {state.location_label}")
        print(f"  • Liquid Level:    [{_refill_bar(pct)}] {pct:.1f}%")
        print(f"  • Installed Date:  {state.installed_date or 'not recorded'}")
        print(f"  • Total Boosts:    {state.total_boosts}")
        print()
    elif action == "reset":
        level = tracker.reset_refill(float(getattr(args, "percent", 100.0)))
        print(f"✅ Refill reset to {level:.1f}%.")
    elif action == "adjust":
        level = tracker.adjust_level(float(getattr(args, "percent", 100.0)))
        print(f"✅ Refill adjusted to {level:.1f}%.")
    return 0


async def cmd_daemon(args: argparse.Namespace) -> int:
    address: Optional[str] = getattr(args, "address", None)
    intensity: int = int(getattr(args, "intensity", 2))
    no_boost: bool = bool(getattr(args, "no_boost", False))
    interval: float = float(getattr(args, "interval", 20.0))
    start_time: str = str(getattr(args, "start", "00:00"))
    end_time: str = str(getattr(args, "end", "24:00"))
    workdays_only: bool = bool(getattr(args, "workdays_only", False))

    config = PresenceConfig(
        diffuser_address=address,
        intensity=intensity,
        boost_on_arrival=not no_boost,
        check_interval_seconds=interval,
        start_time=start_time,
        end_time=end_time,
        workdays_only=workdays_only,
    )
    daemon = PresenceDaemon(config)

    # SIGTERM (launchd / systemd stopping the service) and Ctrl-C both cancel
    # the daemon task, which then switches the diffuser off before exiting.
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    if task is not None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, task.cancel)
            except NotImplementedError, RuntimeError, ValueError:
                pass  # e.g. Windows: Ctrl-C still cancels via asyncio.run

    await daemon.run()
    return 0


def cmd_tray(args: argparse.Namespace) -> int:
    address: Optional[str] = getattr(args, "address", None)
    from airspray.tray import AirSprayTrayApp

    app = AirSprayTrayApp(address)
    app.run()
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    from airspray.schema import DiffuserDeviceSchema

    print(json.dumps(DiffuserDeviceSchema.get_json_schema(), indent=2))
    return 0


def cmd_dump_schema(args: argparse.Namespace) -> int:
    from airspray.schema import DiffuserDeviceSchema

    schema = DiffuserDeviceSchema()
    print(schema.to_json(indent=2))
    return 0


def cmd_service(args: argparse.Namespace) -> int:
    action: str = str(getattr(args, "action", ""))
    intensity: int = int(getattr(args, "intensity", 2))
    address: str = str(getattr(args, "address", "") or "")
    start_time: str = str(getattr(args, "start", "00:00"))
    end_time: str = str(getattr(args, "end", "24:00"))
    workdays_only: bool = bool(getattr(args, "workdays_only", False))
    no_boost: bool = bool(getattr(args, "no_boost", False))

    command = daemon_command(
        intensity, start_time, end_time, workdays_only, no_boost, address
    )
    working_dir = service_working_dir()

    if sys.platform == "darwin":
        if action == "install":
            LAUNCHD_PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
            # launchd does not create the log directory, and will not start a
            # job whose stdout path cannot be opened.
            SERVICE_LOG_DIR.mkdir(parents=True, exist_ok=True)
            LAUNCHD_PLIST_PATH.write_text(
                get_plist_content(command, working_dir, str(SERVICE_LOG_DIR))
            )
            subprocess.run(
                ["launchctl", "unload", str(LAUNCHD_PLIST_PATH)],
                capture_output=True,
                check=False,
            )
            proc = subprocess.run(
                ["launchctl", "load", str(LAUNCHD_PLIST_PATH)], check=False
            )
            if proc.returncode != 0:
                print("❌ Failed to load launchd service.", file=sys.stderr)
                return 1
            print(f"✅ Service installed and started: {LAUNCHD_PLIST_LABEL}")
            print(
                f"⏰ Schedule: {start_time} - {end_time} ({'Mon-Fri' if workdays_only else 'Everyday'})"
            )
            print(f"🌡 Intensity: {intensity} / 4")
            print(f"📄 Plist: {LAUNCHD_PLIST_PATH}")
            print(f"📝 Logs: {SERVICE_LOG_DIR / 'airspray.log'}")
        elif action == "uninstall":
            subprocess.run(
                ["launchctl", "unload", str(LAUNCHD_PLIST_PATH)],
                capture_output=True,
                check=False,
            )
            if LAUNCHD_PLIST_PATH.exists():
                LAUNCHD_PLIST_PATH.unlink()
            print(f"✅ Service uninstalled: {LAUNCHD_PLIST_LABEL}")
        elif action == "status":
            return subprocess.run(
                ["launchctl", "list", LAUNCHD_PLIST_LABEL], check=False
            ).returncode

    elif sys.platform.startswith("linux"):
        if action == "install":
            SYSTEMD_SERVICE_PATH.parent.mkdir(parents=True, exist_ok=True)
            SYSTEMD_SERVICE_PATH.write_text(
                get_systemd_service_content(command, working_dir)
            )
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
            proc = subprocess.run(
                ["systemctl", "--user", "enable", "--now", "airspray"], check=False
            )
            if proc.returncode != 0:
                print("❌ Failed to enable systemd user service.", file=sys.stderr)
                return 1
            print("✅ systemd user service enabled and started.")
        elif action == "uninstall":
            subprocess.run(
                ["systemctl", "--user", "disable", "--now", "airspray"], check=False
            )
            if SYSTEMD_SERVICE_PATH.exists():
                SYSTEMD_SERVICE_PATH.unlink()
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
            print("✅ systemd user service uninstalled.")
        elif action == "status":
            return subprocess.run(
                ["systemctl", "--user", "status", "airspray"], check=False
            ).returncode

    else:
        print(
            f"ℹ️ Background service management for {sys.platform} is not natively automated yet."
        )
        print(
            f"You can run 'airspray daemon --intensity {intensity}' in your task scheduler."
        )

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cross-Platform Air Wick Bluetooth Diffuser Controller & Presence"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # scan
    subparsers.add_parser("scan", help="Scan for nearby Air Wick diffusers")

    subparsers.add_parser(
        "fragrances", help="List the fragrance names accepted by --fragrance"
    )

    # on
    p_on = subparsers.add_parser("on", help="Turn diffuser ON")
    p_on.add_argument("--address", "-a", help="BLE address / UUID")
    p_on.add_argument(
        "--intensity",
        "-i",
        type=int,
        choices=[0, 1, 2, 3, 4],
        default=None,
        help="Also set the intensity (0-4, where 0 pauses spraying). "
        "Omit to keep the diffuser's current intensity.",
    )

    # off
    p_off = subparsers.add_parser("off", help="Turn diffuser OFF")
    p_off.add_argument("--address", "-a", help="BLE address / UUID")

    # boost
    p_boost = subparsers.add_parser(
        "boost", help="Trigger an immediate fragrance spray burst"
    )
    p_boost.add_argument("--address", "-a", help="BLE address / UUID")

    # intensity
    p_int = subparsers.add_parser("intensity", help="Set diffuser intensity")
    p_int.add_argument(
        "level",
        type=int,
        choices=[0, 1, 2, 3, 4],
        help="Intensity level (0-4, where 0 pauses spraying)",
    )
    p_int.add_argument("--address", "-a", help="BLE address / UUID")

    # status
    p_status = subparsers.add_parser(
        "status", help="Get current diffuser state, battery, refill level, and firmware"
    )
    p_status.add_argument("--address", "-a", help="BLE address / UUID")

    # schema subparser
    subparsers.add_parser("schema", help="Print the JSON Schema Draft-07 specification")
    subparsers.add_parser("dump-schema", help="Export full device data model schema")

    # sync-time
    p_sync = subparsers.add_parser(
        "sync-time",
        help="Synchronize diffuser internal RTC clock with local system time",
    )
    p_sync.add_argument("--address", "-a", help="BLE address / UUID")

    # reset
    p_reset = subparsers.add_parser(
        "reset", help="Factory reset diffuser memory and settings"
    )
    p_reset.add_argument("--address", "-a", help="BLE address / UUID")

    # config-device
    p_cfg = subparsers.add_parser(
        "config-device",
        help="Set the location and/or fragrance (whichever is omitted is kept)",
    )
    p_cfg.add_argument("--address", "-a", help="BLE address / UUID")
    p_cfg.add_argument(
        "--location",
        "-l",
        default=None,
        help="Custom location name (e.g. 'Corner Desk', 'Studio', 'Master Bedroom') or preset (Living Room, Bedroom, Office, etc.). Omit to keep the current room.",
    )
    p_cfg.add_argument(
        "--fragrance",
        "-f",
        default=None,
        type=resolve_fragrance_id,
        help=(
            "Fragrance name (e.g. 'Wild Mint') or ID 1-33. "
            "Run 'airspray fragrances' to list them. "
            "Omit to keep the current fragrance."
        ),
    )

    # schedule
    p_sched = subparsers.add_parser(
        "schedule", help="Manage on-device hardware schedules"
    )
    p_sched.add_argument("--address", "-a", help="BLE address / UUID")
    sub_sched = p_sched.add_subparsers(dest="action", required=True)

    sub_sched.add_parser("list", help="List active hardware schedules")

    p_sched_set = sub_sched.add_parser(
        "set", help="Program a hardware schedule slot (1-10)"
    )
    p_sched_set.add_argument(
        "id", type=int, choices=list(range(1, 11)), help="Schedule slot ID (1-10)"
    )
    p_sched_set.add_argument(
        "--start",
        default="08:00",
        type=clock_arg,
        help="Start time in HH:MM (e.g. 09:00)",
    )
    p_sched_set.add_argument(
        "--end", default="17:00", type=clock_arg, help="End time in HH:MM (e.g. 17:00)"
    )
    p_sched_set.add_argument(
        "--days",
        default="mon,tue,wed,thu,fri",
        type=parse_days,
        help="Comma-separated days: mon,tue,wed,thu,fri,sat,sun,all",
    )
    p_sched_set.add_argument(
        "--intensity",
        "-i",
        type=int,
        choices=[0, 1, 2, 3, 4],
        default=2,
        help="Intensity (0-4, where 0 pauses spraying)",
    )
    p_sched_set.add_argument(
        "--disable", action="store_true", help="Disable schedule slot"
    )

    p_sched_del = sub_sched.add_parser(
        "delete", help="Delete a hardware schedule slot (1-10)"
    )
    p_sched_del.add_argument(
        "id", type=int, choices=list(range(1, 11)), help="Schedule slot ID (1-10)"
    )

    sub_sched.add_parser("clear", help="Clear all on-device hardware schedules")

    # refill
    p_refill = subparsers.add_parser(
        "refill", help="Manage and track refill bottle liquid level"
    )
    sub_refill = p_refill.add_subparsers(dest="action", required=True)
    sub_refill.add_parser("status", help="Show refill liquid level & stats")

    p_ref_reset = sub_refill.add_parser(
        "reset", help="Reset bottle to 100%% when inserting a new refill"
    )
    p_ref_reset.add_argument(
        "--percent",
        "-p",
        type=percent_arg,
        default=100.0,
        help="Initial refill percent, clamped to 0-100 (default 100)",
    )

    p_ref_adj = sub_refill.add_parser("adjust", help="Manually set refill percentage")
    p_ref_adj.add_argument(
        "percent", type=percent_arg, help="Refill percentage (clamped to 0-100)"
    )

    # daemon
    p_daemon = subparsers.add_parser(
        "daemon", help="Run office presence automation daemon in foreground"
    )
    p_daemon.add_argument("--address", "-a", help="BLE address / UUID")
    p_daemon.add_argument(
        "--intensity",
        "-i",
        type=int,
        choices=[0, 1, 2, 3, 4],
        default=2,
        help="Intensity (0-4, where 0 pauses spraying)",
    )
    p_daemon.add_argument(
        "--interval",
        type=float,
        default=20.0,
        help="Check interval in seconds (default: 20)",
    )
    p_daemon.add_argument(
        "--no-boost", action="store_true", help="Do not fire a burst spray on arrival"
    )
    p_daemon.add_argument(
        "--start",
        default="00:00",
        type=clock_arg,
        help="Start time in HH:MM (e.g. 09:00)",
    )
    p_daemon.add_argument(
        "--end",
        default="24:00",
        type=end_clock_arg,
        help="End time in HH:MM (e.g. 17:30). May be earlier than --start "
        "for an overnight window such as 22:00-06:00.",
    )
    p_daemon.add_argument(
        "--workdays-only", action="store_true", help="Only run Mon-Fri"
    )

    # tray
    p_tray = subparsers.add_parser(
        "tray", help="Launch cross-platform menu bar / system tray controller"
    )
    p_tray.add_argument("--address", "-a", help="BLE address / UUID")

    # service
    p_service = subparsers.add_parser(
        "service", help="Manage background user service (macOS launchd / Linux systemd)"
    )
    p_service.add_argument(
        "action", choices=["install", "uninstall", "status"], help="Service action"
    )
    p_service.add_argument("--address", "-a", default="", help="BLE address / UUID")
    p_service.add_argument(
        "--intensity",
        "-i",
        type=int,
        choices=[0, 1, 2, 3, 4],
        default=2,
        help="Intensity (0-4, where 0 pauses spraying)",
    )
    p_service.add_argument(
        "--start",
        default="00:00",
        type=clock_arg,
        help="Start time in HH:MM (e.g. 09:00)",
    )
    p_service.add_argument(
        "--end",
        default="24:00",
        type=end_clock_arg,
        help="End time in HH:MM (e.g. 17:30). May be earlier than --start "
        "for an overnight window such as 22:00-06:00.",
    )
    p_service.add_argument(
        "--workdays-only", action="store_true", help="Only run Mon-Fri"
    )
    p_service.add_argument(
        "--no-boost", action="store_true", help="Do not fire a burst spray on arrival"
    )

    return parser


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "service":
        return cmd_service(args)
    elif args.command == "refill":
        return cmd_refill(args)
    elif args.command == "tray":
        return cmd_tray(args)
    elif args.command == "schema":
        return cmd_schema(args)
    elif args.command == "dump-schema":
        return cmd_dump_schema(args)

    handlers: Dict[
        str, Callable[[argparse.Namespace], Coroutine[object, object, int]]
    ] = {
        "scan": cmd_scan,
        "on": cmd_on,
        "off": cmd_off,
        "boost": cmd_boost,
        "fragrances": cmd_fragrances,
        "intensity": cmd_intensity,
        "status": cmd_status,
        "sync-time": cmd_sync_time,
        "reset": cmd_reset,
        "config-device": cmd_config_device,
        "schedule": cmd_schedule,
        "daemon": cmd_daemon,
    }

    handler = handlers[str(args.command)]
    return asyncio.run(handler(args))


def run(argv: Optional[List[str]] = None) -> int:
    """Parse and run one command. Every failure becomes a message and exit 1."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        arguments = ["tray"]

    args = build_parser().parse_args(arguments)
    try:
        return _dispatch(args)
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        logger.debug("Command failed", exc_info=True)
        message = str(e).strip() or type(e).__name__
        print(f"❌ {message}", file=sys.stderr)
        return 1


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
