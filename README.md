# AirSpray 💨

**Cross-platform Bluetooth Low Energy (BLE) controller and menubar/tray app for AirWick Essential Mist Bluetooth diffusers.**

> *Disclaimer: AirSpray is an independent, open-source project and is not affiliated with, endorsed by, or associated with Air Wick or Reckitt Benckiser.*

Developed and tested on **macOS**. The Python tools are written to also run on
**Linux** and **Windows**, but those platforms are untested.

---

## 🛠 Features

- **Menu Bar / System Tray Controller**: Native menu bar app on macOS (NSStatusItem), Windows (System Tray), and Linux (AppIndicator) for one-click power toggling, intensity adjustments, live refill status, and burst spraying.
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

## 🔗 Pairing the Diffuser

The diffuser refuses commands from a computer it is not paired with, and it only
accepts a pairing request while it is in pairing mode. Pair once per computer:

1. Turn Bluetooth off on any phone that has the Air Wick app, so the phone does
   not claim the diffuser first.
2. Switch the diffuser off, wait about 10 seconds, and switch it back on. A
   **blinking blue light** means it is in pairing mode.
3. Launch AirSpray (or run any `airspray` command) while the light is blinking.
4. On macOS, a "Connection Request from: Air Wick Mist" dialog appears. Click
   **Connect**. The diffuser has no screen, so there is no number to compare.

After that the connection is encrypted with the stored key and macOS does not
ask again.

**If the pairing dialog keeps coming back:** the diffuser is not in pairing mode.
It rejects the request by itself about two seconds after the dialog appears, so
clicking Connect has no effect, and every reconnect raises the dialog again.
Quit AirSpray, repeat the steps above, and relaunch.

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

# Turn ON, keeping the current intensity
airspray on

# Turn ON and set Medium intensity
airspray on --intensity 2

# Trigger an immediate fragrance burst
airspray boost

# Adjust intensity (0 to 4, where 0 pauses spraying)
airspray intensity 3

# Turn OFF
airspray off
```

When a command fails, it prints a one-line `❌` message saying why and exits
with an error status, so scripts can tell. It never prints default values as if
they came from the diffuser. That covers a diffuser that doesn't answer, a
refused connection (the message explains pairing), and a settings file that
can't be saved. Mistyped arguments are rejected before connecting.

### 4. On-Device Hardware Schedules (Standalone EEPROM)

```bash
# List every schedule slot stored on the diffuser (disabled ones are marked)
airspray schedule list

# Program a schedule in slot #1: 9am to 5:30pm Mon-Fri with intensity 2
airspray schedule set 1 --start 09:00 --end 17:30 --days mon,tue,wed,thu,fri --intensity 2

# Delete a schedule slot
airspray schedule delete 1

# Clear all hardware schedules
airspray schedule clear
```

`--days` accepts `mon` through `sun`. An unknown day is an error, not silently dropped.

### 5. Device Management (Location, Clock, Reset)

```bash
# Set custom location name (or preset like Office, Bedroom, Living Room) & fragrance
airspray config-device --location "Corner Studio" --fragrance "Wild Mint"

# Change only one of them. The other keeps its current value
airspray config-device --fragrance "Wild Mint"

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

# Insert a partly used bottle: sets the level and restarts the install date
airspray refill reset --percent 88

# Correct the estimate for the bottle already in the diffuser (e.g. 75%)
airspray refill adjust 75
```

Values outside 0–100 are brought back into that range.

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

Overnight windows work (`--start 22:00 --end 06:00`), and `--end 24:00` means
end of day. If the diffuser cannot be reached, the daemon and the tray retry
with a growing delay (10 seconds up to 5 minutes) instead of every few seconds,
so an unpaired diffuser cannot flood you with pairing dialogs. Stopping the
service turns the diffuser off. A manual on/off from the tray is kept until the
next time you arrive or leave.

## 🖥 macOS Menu Bar App

The native menu bar app is built with Xcode from `macos/AirSpray.xcodeproj`.
See [Development](docs/development.md) for building, installing and signing.

**Menu Bar Layout:**

- **Header**: connection state, battery gauge (0–4 bars), refill percentage with a
  depletion forecast, and a countdown to the next spray.
- **Pause / Resume Diffuser**: intensity `0` pauses without powering down.
- **Spray Fragrance Boost**: one immediate burst.
- **Intensity**: Low, Medium, High, Max (pausing lives on its own item, not in here).
- **Schedule...**: read, add, edit and delete all ten on-device slots.
- **Refill Bottle...**: set the level from a quick amount or a slider, and pick a
  fragrance by name. Tick "This is a different bottle" when you swap in a
  partly used bottle, so the install date restarts. Setting 100% always does.
- **Settings ›**: Auto-Pause on Screen Lock, Launch at Login, Room Profile,
  Scan for Diffusers, Switch Diffuser, Copy Diagnostics & Schema, and About
  AirSpray (app version and build, plus the diffuser's firmware versions).
- **Version line**: the running version and build, greyed out above Quit.

Things the menu tells you:

- **Reading State...** right after connecting, until the diffuser reports its
  real power, intensity and battery. Schedules are read at the same time.
- **Needs Pairing** when the diffuser refuses the connection. See
  [Pairing the Diffuser](#-pairing-the-diffuser).
- **Pause/Resume pending** when you toggle power while disconnected. It is sent
  as soon as the diffuser connects. Boost, Intensity, Schedule and Room Profile
  are disabled until then, so nothing appears to succeed when it was not sent.
- **Launch at Login** shows when macOS still needs your approval under System
  Settings › General › Login Items.

Auto-Pause on Screen Lock only resumes what it paused: if you pause manually and
then lock the screen, unlocking leaves the diffuser paused. Scan for Diffusers
lists other diffusers without disconnecting the current one.

---

## 📚 More Documentation

- [Development](docs/development.md): repository layout, building and installing
  the macOS app with Xcode, signing setup, and tests.
- [Shared configuration](docs/shared-config.md): the `~/.airspray_config.json`
  file both apps use, and the rules that keep them from overwriting each other.
- [Protocol](docs/protocol.md): the complete reverse-engineered Bluetooth LE
  protocol (GATT, framing, every command and report, status and schedule
  layouts, the room, fragrance and schedule-name tables), how the original app
  connects and handles refills and batteries, and where this project differs.

---

## 📄 License

[MIT](LICENSE). AirSpray is an independent project and is not affiliated with
Air Wick or Reckitt Benckiser.
