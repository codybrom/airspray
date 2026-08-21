"""
Cross-Platform Command-line interface for Air Wick Bluetooth Diffuser controller & presence daemon.
"""

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Coroutine, Dict, Optional

from airspray.client import AirWickDiffuser
from airspray.presence import PresenceConfig, PresenceDaemon
from airspray.protocol import (
    FRAGRANCE_NAMES,
    MAX_FRAGRANCE_ID,
    ROOM_NAMES,
    Schedule,
    fragrances_sorted_by_name,
    intensity_label,
    resolve_room_id,
)
from airspray.refill import RefillTracker

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


def get_plist_content(
    python_path: str,
    repo_path: str,
    intensity: int,
    start_time: str = "00:00",
    end_time: str = "24:00",
    workdays_only: bool = False,
    no_boost: bool = False,
    address: str = "",
) -> str:
    addr_arg = (
        f"<string>--address</string><string>{address}</string>" if address else ""
    )
    workdays_arg = "<string>--workdays-only</string>" if workdays_only else ""
    no_boost_arg = "<string>--no-boost</string>" if no_boost else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>-m</string>
        <string>airspray.cli</string>
        <string>daemon</string>
        <string>--intensity</string>
        <string>{intensity}</string>
        <string>--start</string>
        <string>{start_time}</string>
        <string>--end</string>
        <string>{end_time}</string>
        {workdays_arg}
        {no_boost_arg}
        {addr_arg}
    </array>
    <key>WorkingDirectory</key>
    <string>{repo_path}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{os.path.expanduser('~')}/.airspray/airspray.log</string>
    <key>StandardErrorPath</key>
    <string>{os.path.expanduser('~')}/.airspray/airspray.err</string>
</dict>
</plist>
"""


def get_systemd_service_content(
    python_path: str,
    repo_path: str,
    intensity: int,
    start_time: str = "00:00",
    end_time: str = "24:00",
    workdays_only: bool = False,
    no_boost: bool = False,
    address: str = "",
) -> str:
    addr_arg = f"--address {address}" if address else ""
    workdays_arg = "--workdays-only" if workdays_only else ""
    no_boost_arg = "--no-boost" if no_boost else ""
    return f"""[Unit]
Description=Air Wick Presence Daemon
After=network.target bluetooth.target

[Service]
Type=simple
WorkingDirectory={repo_path}
ExecStart={python_path} -m airspray.cli daemon --intensity {intensity} --start {start_time} --end {end_time} {workdays_arg} {no_boost_arg} {addr_arg}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""


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
    intensity_arg: Optional[int] = getattr(args, "intensity", None)
    intensity: int = int(intensity_arg) if intensity_arg is not None else 2

    diffuser = AirWickDiffuser(address)
    print("🔌 Connecting to diffuser...")
    await diffuser.connect()
    print("⚡ Turning ON...")
    await diffuser.turn_on()
    if intensity_arg is not None:
        print(f"🌡 Setting intensity to {intensity}...")
        await diffuser.set_intensity(intensity)
    tracker.update_usage(True, intensity)
    await diffuser.disconnect()
    print("✅ Done.")
    return 0


async def cmd_off(args: argparse.Namespace) -> int:
    tracker = RefillTracker()
    address: Optional[str] = getattr(args, "address", None)
    diffuser = AirWickDiffuser(address)
    print("🔌 Connecting to diffuser...")
    await diffuser.connect()
    print("💤 Turning OFF...")
    await diffuser.turn_off()
    tracker.update_usage(False, 0)
    await diffuser.disconnect()
    print("✅ Done.")
    return 0


async def cmd_boost(args: argparse.Namespace) -> int:
    tracker = RefillTracker()
    address: Optional[str] = getattr(args, "address", None)
    diffuser = AirWickDiffuser(address)
    print("🔌 Connecting to diffuser...")
    await diffuser.connect()
    print("💨 Triggering instant fragrance burst...")
    await diffuser.boost()
    tracker.record_boost()
    await diffuser.disconnect()
    print("✅ Done.")
    return 0


async def cmd_intensity(args: argparse.Namespace) -> int:
    tracker = RefillTracker()
    address: Optional[str] = getattr(args, "address", None)
    level: int = int(getattr(args, "level", 2))

    diffuser = AirWickDiffuser(address)
    print("🔌 Connecting to diffuser...")
    await diffuser.connect()
    print(f"🌡 Setting intensity to {level} ({intensity_label(level)})...")
    await diffuser.set_intensity(level)
    tracker.update_usage(diffuser.status.power, level)
    await diffuser.disconnect()
    print("✅ Done.")
    return 0


async def cmd_status(args: argparse.Namespace) -> int:
    tracker = RefillTracker()
    address: Optional[str] = getattr(args, "address", None)
    diffuser = AirWickDiffuser(address)
    print("🔌 Connecting to diffuser...")
    await diffuser.connect()
    status = await diffuser.get_status()
    mcu_fw, ble_fw = await diffuser.get_firmware_versions()
    tracker.update_usage(status.power, status.intensity)
    await diffuser.disconnect()

    refill_pct = tracker.state.refill_percent
    filled = int(refill_pct / 10)
    bar = "█" * filled + "░" * (10 - filled)

    print("\n📊 Air Wick Diffuser Status:")
    print(f"  • Location:       {tracker.state.location_name}")
    print(f"  • Power:          {'🟢 ON' if status.power else '🔴 OFF'}")
    print(
        f"  • Intensity:      {status.intensity} / 4 "
        f"({intensity_label(status.intensity)})"
    )
    print(f"  • Spraying Now:   {'Yes 💨' if status.spraying else 'No'}")
    print(f"  • Battery Level:  {status.battery_level} / 4")
    print(f"  • Refill Level:   [{bar}] {refill_pct:.1f}%")
    if mcu_fw:
        print(f"  • MCU Firmware:   {mcu_fw}")
    if ble_fw:
        print(f"  • BLE Firmware:   {ble_fw}")
    print()
    return 0


async def cmd_sync_time(args: argparse.Namespace) -> int:
    address: Optional[str] = getattr(args, "address", None)
    diffuser = AirWickDiffuser(address)
    print("🔌 Connecting to diffuser...")
    await diffuser.connect()
    now = datetime.now()
    print(
        f"⏰ Syncing diffuser internal RTC clock to {now.strftime('%Y-%m-%d %H:%M:%S')}..."
    )
    await diffuser.sync_time(now)
    await diffuser.disconnect()
    print("✅ Time synchronized successfully.")
    return 0


async def cmd_reset(args: argparse.Namespace) -> int:
    address: Optional[str] = getattr(args, "address", None)
    diffuser = AirWickDiffuser(address)
    print("🔌 Connecting to diffuser...")
    await diffuser.connect()
    print("⚠️ Resetting diffuser to factory settings...")
    await diffuser.reset()
    await diffuser.disconnect()
    print("✅ Diffuser reset.")
    return 0


async def cmd_fragrances(_args: argparse.Namespace) -> int:
    """Print the catalogue in the same order the Mac app's picker shows it."""
    print("🌸 Fragrances (name or ID accepted by --fragrance):\n")
    for fid, name in fragrances_sorted_by_name():
        note = "  (not echoed by the device)" if fid > 31 else ""
        print(f"  {fid:>2}  {name}{note}")
    return 0


def resolve_fragrance_id(value: str) -> int:
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


async def cmd_config_device(args: argparse.Namespace) -> int:
    tracker = RefillTracker()
    address: Optional[str] = getattr(args, "address", None)
    location_input: str = str(getattr(args, "location", "Office") or "Office")
    fragrance_id: int = resolve_fragrance_id(getattr(args, "fragrance", "1"))
    fragrance_name = FRAGRANCE_NAMES[fragrance_id]

    room_id = resolve_room_id(location_input)
    room_preset_name = ROOM_NAMES.get(room_id, "Other")

    diffuser = AirWickDiffuser(address)
    print("🔌 Connecting to diffuser...")
    await diffuser.connect()
    print(
        f"🏷 Setting Location: '{location_input}' (Hardware Preset: {room_preset_name} "
        f"[ID {room_id}]), Fragrance: {fragrance_name} [ID {fragrance_id}]..."
    )
    await diffuser.set_room_and_fragrance(room_id, fragrance_id)
    tracker.set_location_name(location_input)
    await diffuser.disconnect()
    print("✅ Location and fragrance saved.")
    return 0


async def cmd_schedule(args: argparse.Namespace) -> int:
    address: Optional[str] = getattr(args, "address", None)
    action: str = str(getattr(args, "action", ""))
    diffuser = AirWickDiffuser(address)

    print("🔌 Connecting to diffuser...")
    await diffuser.connect()

    try:
        if action == "list":
            print("📅 Fetching on-device hardware schedules (slots 1..10)...")
            schedules = await diffuser.get_all_schedules()
            if not schedules:
                print("ℹ️ No active hardware schedules programmed on the device.")
            else:
                print(f"\n📋 Active Hardware Schedules ({len(schedules)}):")
                for s in schedules:
                    start_str = f"{s.start_hour:02d}:{s.start_min:02d}"
                    end_str = f"{s.end_hour:02d}:{s.end_min:02d}"
                    print(
                        f"  • Slot {s.id}: {start_str} - {end_str} | Days: {s.days_str} | Intensity: {s.intensity}"
                    )
                print()

        elif action == "set":
            sched_id: int = int(getattr(args, "id", 1))
            start_val: str = str(getattr(args, "start", "08:00"))
            end_val: str = str(getattr(args, "end", "17:00"))
            intensity: int = int(getattr(args, "intensity", 2))
            days_str: str = str(getattr(args, "days", "mon,tue,wed,thu,fri")).lower()
            disabled: bool = bool(getattr(args, "disable", False))

            start_parts = [int(p) for p in start_val.split(":")]
            end_parts = [int(p) for p in end_val.split(":")]

            days_list = [d.strip() for d in days_str.split(",")]
            sched = Schedule(
                id=sched_id,
                enabled=not disabled,
                start_hour=start_parts[0],
                start_min=start_parts[1],
                end_hour=end_parts[0],
                end_min=end_parts[1],
                intensity=intensity,
                monday="mon" in days_list or "all" in days_list,
                tuesday="tue" in days_list or "all" in days_list,
                wednesday="wed" in days_list or "all" in days_list,
                thursday="thu" in days_list or "all" in days_list,
                friday="fri" in days_list or "all" in days_list,
                saturday="sat" in days_list or "all" in days_list,
                sunday="sun" in days_list or "all" in days_list,
            )

            print(
                f"📅 Programming schedule slot #{sched_id} ({start_val} - {end_val}, Days: {sched.days_str}, Intensity: {intensity})..."
            )
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

    finally:
        await diffuser.disconnect()

    return 0


def cmd_refill(args: argparse.Namespace) -> int:
    tracker = RefillTracker()
    action: str = str(getattr(args, "action", ""))
    if action == "status":
        pct = tracker.state.refill_percent
        filled = int(pct / 10)
        bar = "█" * filled + "░" * (10 - filled)
        print("\n🧪 Refill Bottle Status:")
        print(f"  • Location:        {tracker.state.location_name}")
        print(f"  • Liquid Level:    [{bar}] {pct:.1f}%")
        print(f"  • Installed Date:  {tracker.state.installed_date}")
        print(f"  • Total Boosts:    {tracker.state.total_boosts}")
        print()
    elif action == "reset":
        percent: float = float(getattr(args, "percent", 100.0))
        tracker.reset_refill(percent=percent)
        print(f"✅ Refill reset to {percent:.1f}%.")
    elif action == "adjust":
        percent = float(getattr(args, "percent", 100.0))
        tracker.adjust_level(percent)
        print(f"✅ Refill adjusted to {percent:.1f}%.")
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
    try:
        await daemon.run()
    except KeyboardInterrupt:
        daemon.stop()
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

    python_bin = sys.executable
    repo_dir = str(Path(__file__).resolve().parent.parent)

    if sys.platform == "darwin":
        if action == "install":
            LAUNCHD_PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
            content = get_plist_content(
                python_bin,
                repo_dir,
                intensity,
                start_time,
                end_time,
                workdays_only,
                no_boost,
                address,
            )
            LAUNCHD_PLIST_PATH.write_text(content)
            subprocess.run(
                ["launchctl", "unload", str(LAUNCHD_PLIST_PATH)],
                capture_output=True,
                check=False,
            )
            proc = subprocess.run(
                ["launchctl", "load", str(LAUNCHD_PLIST_PATH)], check=False
            )
            if proc.returncode == 0:
                print(f"✅ Service installed and started: {LAUNCHD_PLIST_LABEL}")
                print(
                    f"⏰ Schedule: {start_time} - {end_time} ({'Mon-Fri' if workdays_only else 'Everyday'})"
                )
                print(f"🌡 Intensity: {intensity} / 4")
                print(f"📄 Plist: {LAUNCHD_PLIST_PATH}")
                print(f"📝 Logs: ~/.airspray/airspray.log")
            else:
                print("❌ Failed to load launchd service.")
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
            subprocess.run(["launchctl", "list", LAUNCHD_PLIST_LABEL], check=False)

    elif sys.platform.startswith("linux"):
        if action == "install":
            SYSTEMD_SERVICE_PATH.parent.mkdir(parents=True, exist_ok=True)
            SYSTEMD_SERVICE_PATH.write_text(
                get_systemd_service_content(
                    python_bin,
                    repo_dir,
                    intensity,
                    start_time,
                    end_time,
                    workdays_only,
                    no_boost,
                    address,
                )
            )
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
            subprocess.run(
                ["systemctl", "--user", "enable", "--now", "airspray"], check=False
            )
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
            subprocess.run(["systemctl", "--user", "status", "airspray"], check=False)

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
        default=2,
        help="Intensity level (0-4; 0 pauses spraying)",
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
        "level", type=int, choices=[0, 1, 2, 3, 4], help="Intensity level (0-4; 0 pauses spraying)"
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
        "config-device", help="Configure custom Location name and Fragrance ID"
    )
    p_cfg.add_argument("--address", "-a", help="BLE address / UUID")
    p_cfg.add_argument(
        "--location",
        "-l",
        default="Office",
        help="Custom location name (e.g. 'Corner Desk', 'Studio', 'Master Bedroom') or preset (Living Room, Bedroom, Office, etc.)",
    )
    p_cfg.add_argument(
        "--fragrance",
        "-f",
        default="1",
        help=(
            "Fragrance name (e.g. 'Wild Mint') or ID 1-33. "
            "Run 'airspray fragrances' to list them."
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
        "--start", default="08:00", help="Start time in HH:MM (e.g. 09:00)"
    )
    p_sched_set.add_argument(
        "--end", default="17:00", help="End time in HH:MM (e.g. 17:00)"
    )
    p_sched_set.add_argument(
        "--days",
        default="mon,tue,wed,thu,fri",
        help="Comma-separated days: mon,tue,wed,thu,fri,sat,sun,all",
    )
    p_sched_set.add_argument(
        "--intensity",
        "-i",
        type=int,
        choices=[0, 1, 2, 3, 4],
        default=2,
        help="Intensity (0-4; 0 pauses spraying)",
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
        type=float,
        default=100.0,
        help="Initial refill percent (default 100)",
    )

    p_ref_adj = sub_refill.add_parser("adjust", help="Manually set refill percentage")
    p_ref_adj.add_argument("percent", type=float, help="Refill percentage (0-100)")

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
        help="Intensity (0-4; 0 pauses spraying)",
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
        "--start", default="00:00", help="Start time in HH:MM (e.g. 09:00)"
    )
    p_daemon.add_argument(
        "--end", default="24:00", help="End time in HH:MM (e.g. 17:30)"
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
        help="Intensity (0-4; 0 pauses spraying)",
    )
    p_service.add_argument(
        "--start", default="00:00", help="Start time in HH:MM (e.g. 09:00)"
    )
    p_service.add_argument(
        "--end", default="24:00", help="End time in HH:MM (e.g. 17:30)"
    )
    p_service.add_argument(
        "--workdays-only", action="store_true", help="Only run Mon-Fri"
    )
    p_service.add_argument(
        "--no-boost", action="store_true", help="Do not fire a burst spray on arrival"
    )

    return parser


def main() -> None:
    if len(sys.argv) == 1:
        sys.argv.append("tray")

    args = build_parser().parse_args()

    if args.command == "service":
        sys.exit(cmd_service(args))
    elif args.command == "refill":
        sys.exit(cmd_refill(args))
    elif args.command == "tray":
        sys.exit(cmd_tray(args))
    elif args.command == "schema":
        sys.exit(cmd_schema(args))
    elif args.command == "dump-schema":
        sys.exit(cmd_dump_schema(args))

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
    sys.exit(asyncio.run(handler(args)))


if __name__ == "__main__":
    main()
