# Development

## Repository layout

```txt
airspray/        Python package: CLI, tray app, presence daemon, BLE client
macos/           Native Swift menu bar app
  AirSpray.xcodeproj
  AirSpray/      Swift sources and asset catalog
  Config/        Signing.xcconfig (tracked) and Local.xcconfig (gitignored)
tests/
  python/        Tests for the Python package
  swift/         Swift test harness and the test that compiles and runs it
  parity/        Checks that the Swift app and Python package agree
docs/            Protocol, shared config and development notes
```

## Building the macOS menu bar app

The native app is built with Xcode from `macos/AirSpray.xcodeproj`. Everything the
app needs lives in the `macos/AirSpray` folder (Swift sources and the asset
catalog), and the project includes that whole folder, so new files are picked up
without editing the project. Xcode signs the app automatically (see Signing below), and the Info.plist is generated from the target's build settings.

- **Run while developing:** open `macos/AirSpray.xcodeproj` and press Run.
- **Install to `/Applications`:** choose Product › Archive, then Distribute App ›
  Custom › Copy App, and move the exported `AirSpray.app` into `/Applications`.

The project uses the JSON `project.xcproj` format, which needs Xcode 27 or
later. There is no `project.pbxproj` for older versions.

The same install from Terminal (needs full Xcode selected:
`sudo xcode-select -s /Applications/Xcode.app`). The steps are chained so a
failed build never quits or replaces the installed app, and the old bundle is
removed first so no stale files survive inside the new one:

```bash
xcodebuild -project macos/AirSpray.xcodeproj -scheme AirSpray -configuration Release \
  -archivePath build_xcode/AirSpray.xcarchive archive \
  && { osascript -e 'tell application id "com.codybrom.airspray" to quit'; true; } \
  && rm -rf /Applications/AirSpray.app \
  && ditto build_xcode/AirSpray.xcarchive/Products/Applications/AirSpray.app /Applications/AirSpray.app \
  && open /Applications/AirSpray.app
```

**Signing:** the team ID is deliberately kept out of the repository. Create
`macos/Config/Local.xcconfig` (gitignored) containing one line,
`DEVELOPMENT_TEAM = <your team id>`, and Xcode signs automatically with that
team. Don't choose a team under Signing & Capabilities in Xcode, because that
writes the ID into the committed project file. For a local build with no
developer account, skip the file and add `CODE_SIGN_IDENTITY=-` to the
`xcodebuild` command to sign ad hoc.

## Running tests

```bash
pytest -v
```

The suite includes a parity layer that pins the Swift menu bar app to the Python
port: `tests/parity/test_swift_protocol_parity.py` compares command codes, UUIDs, room
and fragrance tables, intensity labels, the refill curve and the shared config
key contract by parsing `macos/AirSpray/Sources/*.swift`, and
`tests/swift/test_swift_protocol_behavior.py` compiles that Swift and exercises it.
The compiled tests skip automatically when `swiftc` is unavailable.

Both packages are type-checked strictly:

```bash
pyright --pythonpath .venv/bin/python
mypy airspray tests
```
