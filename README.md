# AirSpray 💨

**Cross-platform Bluetooth Low Energy (BLE) controller and menubar/tray app for AirWick Essential Mist Bluetooth diffusers.**

> *Disclaimer: AirSpray is an independent, open-source project and is not affiliated with, endorsed by, or associated with Air Wick or Reckitt Benckiser.*

Works on **macOS**, **Linux**, and **Windows**.

---

## 🛠 Features

- **Menu Bar / System Tray Controller**: Native menu bar app on macOS (NSStatusItem), Windows (System Tray), and Linux (AppIndicator) for one-click power toggling, intensity adjustments, live refill status, and burst spraying.
- **Standalone Binary Distribution**: Can be compiled into a single self-contained executable with PyInstaller (`dist/airspray`) requiring zero Python runtime setup.
- **Direct BLE Control**: Turn on/off, adjust intensity (0–4, where 0 pauses spraying), trigger instant spray bursts, query battery & firmware versions without any cloud dependence.
- **On-Device Hardware Schedules**: Program up to 10 on-device EEPROM timers with custom start/end times, active days of the week, and intensity levels that run automatically even when disconnected.
- **Cross-Platform Presence Automation**: Automatically powers on the diffuser when you are at your desk (unlocked screen / active work session) and turns it off when you lock your screen or leave, saving fragrance and battery.
  - **macOS**: Native CoreGraphics C ABI lock detection via standard library `ctypes`.
  - **Windows**: Native `user32` desktop lock detection via `ctypes`.
  - **Linux**: Session & work schedule presence tracking.
- **Refill Tracking**: Algorithmically tracks remaining liquid capacity percentage based on active runtime, intensity setting, and burst sprays. The level is shared with the menu bar app, so both report the same bottle.
- **Custom Location & Clock**: Set custom location labels (e.g., `"Corner Studio"`, `"Executive Desk"`) or presets, choose a fragrance by name or ID, synchronize the RTC clock, and perform factory resets.
- **Background Service**: Integrates with macOS `launchd` and Linux `systemd --user` for automatic startup on login.

---

## 📡 Protocol Summary (Reverse Engineered)

| Parameter | Value |
| --- | --- |
| **Service UUID** | `6e400001-b5a3-f393-e0a9-e50e24dcca9e` |
| **Write Characteristic (TX)** | `6e400007-b5a3-f393-e0a9-e50e24dcca9e` |
| **Notify Characteristic (RX)** | `6e400008-b5a3-f393-e0a9-e50e24dcca9e` |
| **Framing** | `[0xFF, 0x55, CMD, LEN, ...PAYLOAD, CHECKSUM]` |
| **Checksum** | `CMD ^ 0xAA ^ LEN ^ (XOR of all PAYLOAD bytes)` |

### Command Codes

- `0x00`: Query MCU Firmware Version
- `0x01`: Query Status (Power, Spraying, Intensity, Battery)
- `0x02` / `0x03`: Query / Set Schedules (Slots 1–10)
- `0x04`: Clear All Schedules
- `0x05`: Sync RTC Time
- `0x06`: Set Power (`0x01` = ON, `0x00` = OFF)
- `0x07`: Set Intensity (`0x00`=Paused, `0x01`=Low, `0x02`=Medium, `0x03`=High, `0x04`=Max)
- `0x08`: Set Room (1-8) & Fragrance (1-33) Presets — the vendor decoder only echoes 1-31
  - Rooms: `1` Bathroom, `2` Bedroom, `3` Dining Room, `4` Foyer, `5` Kitchen, `6` Living Room, `7` Other, `8` Office
- `0x09`: Trigger Fragrance Boost Spray
- `0x0B`: Factory Reset Device
- `0x11`: Spray Report (unsolicited notification while misting)
- `0x12`: Battery Report (unsolicited, 0–4 bars)
- `0x22`: Query BLE Firmware Version

Intensity level `0` **pauses** spraying without powering the diffuser down; scheduled
windows still run. Battery is reported as 0–4 bars, not a percentage.

---

## 🚀 Quick Start

### 1. Activate Environment & Install

```bash
source .venv/bin/activate
pip install -e .
```

### 2. Menu Bar / System Tray Controller

Launch the cross-platform menu bar icon:

```bash
airspray tray
```

Features:

- Live power indicator & one-click toggle
- Real-time refill capacity level bar
- Quick intensity level selector (Low, Medium, High, Max)
- Instant fragrance boost button

### 3. Basic CLI Commands

```bash
# Scan for nearby diffusers
airspray scan

# Check status (Location, Power, Intensity, Battery, Refill %, Firmware)
airspray status

# Turn ON with Medium intensity
airspray on --intensity 2

# Trigger an immediate fragrance burst
airspray boost

# Adjust intensity (0 to 4; 0 pauses spraying)
airspray intensity 3

# Turn OFF
airspray off
```

### 4. On-Device Hardware Schedules (Standalone EEPROM)

```bash
# List all active hardware schedules stored on the diffuser
airspray schedule list

# Program a schedule in slot #1: 9am to 5:30pm Mon-Fri with intensity 2
airspray schedule set 1 --start 09:00 --end 17:30 --days mon,tue,wed,thu,fri --intensity 2

# Delete a schedule slot
airspray schedule delete 1

# Clear all hardware schedules
airspray schedule clear
```

### 5. Device Management (Location, Clock, Reset)

```bash
# Set custom location name (or preset like Office, Bedroom, Living Room) & fragrance
airspray config-device --location "Corner Studio" --fragrance "Wild Mint"

# List the fragrance names the menu bar app and the CLI both accept
airspray fragrances

# Synchronize internal RTC clock with local system time
airspray sync-time

# Factory reset diffuser settings and memory
airspray reset
```

### 6. Refill Management

```bash
# Check estimated liquid level & stats
airspray refill status

# Reset to 100% when inserting a new refill
airspray refill reset

# Adjust percentage manually if needed (e.g. 75%)
airspray refill adjust 75
```

### 7. Office Presence Automation

```bash
# Run in foreground
airspray daemon --start 09:00 --end 17:30 --workdays-only --intensity 2

# Or install as a background user service (macOS LaunchAgent or Linux systemd user service)
airspray service install --start 09:00 --end 17:30 --workdays-only --intensity 2

# Check service status
airspray service status

# Uninstall service
airspray service uninstall
```

### 8. Build

### 🚀 Running the App

### 1. macOS Menu Bar Application (`Airspray.app`)

You can launch the native macOS application directly from Finder or Terminal:

```bash
open dist/Airspray.app
```

Or copy `dist/Airspray.app` into `/Applications` for easy access.

**Menu Bar Layout:**

- **Header**: connection state, battery gauge (0–4 bars), refill percentage with a
  depletion forecast, and a countdown to the next spray.
- **Pause / Resume Diffuser**: intensity `0` pauses without powering down.
- **Spray Fragrance Boost**: one immediate burst.
- **Intensity**: Low, Medium, High, Max (pausing lives on its own item, not in here).
- **Schedule...**: read, add, edit and delete all ten on-device slots.
- **Refill Bottle...**: set the level from a quick amount or a slider, and pick a
  fragrance by name.
- **Settings ›**: Auto-Pause on Screen Lock, Launch at Login, Room Profile,
  Scan for Diffusers, Switch Diffuser, Copy Diagnostics & Schema.

---

### 2. Standalone CLI Binary

Both the app and CLI are code-signed with your Apple Developer certificate (`com.codybrom.airspray`):

```bash
# Power controls
./dist/airspray on
./dist/airspray off
./dist/airspray boost
./dist/airspray intensity 2

# Status & discovery
./dist/airspray status
./dist/airspray scan
./dist/airspray refill status
```

---

## 🗂 Shared Configuration

The CLI and the macOS menu bar app read and write the same file:

```txt
~/.airspray_config.json
```

Both use the same JSON keys (`lastAddress`, `refillPercentage`, `lastRoomId`, ...)
and both **merge** on save rather than overwriting, so keys owned by one side
survive a write by the other. Refill state lives here too; the older
`~/.airspray/state.json` is migrated once and then ignored.

---

## 🧪 Running Tests

```bash
pytest -v
```

The suite includes a parity layer that pins the Swift menu bar app to the Python
port: `tests/test_swift_protocol_parity.py` compares command codes, UUIDs, room
and fragrance tables, intensity labels, the refill curve and the shared config
key contract by parsing `AirsprayMac/Sources/*.swift`, and
`tests/test_swift_protocol_behavior.py` compiles that Swift and exercises it.
The compiled tests skip automatically when `swiftc` is unavailable.

Both packages are type-checked strictly:

```bash
pyright --pythonpath .venv/bin/python
mypy airspray tests
```
