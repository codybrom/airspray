#!/usr/bin/env bash
set -e

SIGN_IDENTITY="${SIGN_IDENTITY:-Apple Distribution: Cody Bromley (YOUR_TEAM_ID)}"
APP_BUNDLE="dist/Airspray.app"

echo "==> 1. Compiling Native Swift Binary (macOS 26+)..."
mkdir -p "$APP_BUNDLE/Contents/MacOS"
mkdir -p "$APP_BUNDLE/Contents/Resources"

swiftc -O -target arm64-apple-macosx26.0 \
  AirsprayMac/Sources/*.swift \
  -o "$APP_BUNDLE/Contents/MacOS/Airspray" \
  -framework Cocoa \
  -framework CoreBluetooth \
  -framework ServiceManagement

echo "==> 2. Copying Assets & Compiling Asset Catalog..."
cp -f airspray/assets/Airspray.icns "$APP_BUNDLE/Contents/Resources/icon.icns"
cp -f airspray/assets/Airspray.icns "$APP_BUNDLE/Contents/Resources/AppIcon.icns"
cp -f airspray/assets/Airspray.icns "$APP_BUNDLE/Contents/Resources/Airspray.icns"

xcrun actool airspray/assets/Assets.xcassets \
  --compile "$APP_BUNDLE/Contents/Resources" \
  --platform macosx \
  --minimum-deployment-target 26.0 \
  --app-icon AppIcon \
  --output-partial-info-plist /tmp/assetcatalog_generated_info.plist || true

echo "==> 3. Creating Native Info.plist..."
cat << 'PLIST' > "$APP_BUNDLE/Contents/Info.plist"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>Airspray</string>
    <key>CFBundleDisplayName</key>
    <string>Airspray</string>
    <key>CFBundleIdentifier</key>
    <string>com.codybrom.airspray</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundleExecutable</key>
    <string>Airspray</string>
    <key>CFBundleIconFile</key>
    <string>icon.icns</string>
    <key>CFBundleIconName</key>
    <string>AppIcon</string>
    <key>NSPrincipalClass</key>
    <string>NSApplication</string>
    <key>LSUIElement</key>
    <true/>
    <key>LSMinimumSystemVersion</key>
    <string>26.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSBluetoothAlwaysUsageDescription</key>
    <string>Airspray requires Bluetooth to discover and control your Air Wick diffuser.</string>
    <key>NSBluetoothPeripheralUsageDescription</key>
    <string>Airspray requires Bluetooth to discover and control your Air Wick diffuser.</string>
</dict>
</plist>
PLIST

echo "==> 4. Code Signing Airspray.app with '$SIGN_IDENTITY'..."
codesign --force --deep --options runtime --entitlements entitlements.plist --sign "$SIGN_IDENTITY" "$APP_BUNDLE"

echo "==> 5. Verifying Signature..."
codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"

echo "==> 6. Registering with LaunchServices & Resetting Icon Cache..."
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP_BUNDLE"
qlmanage -r >/dev/null 2>&1 || true
qlmanage -r cache >/dev/null 2>&1 || true
killall Finder QuickLookUIService 2>/dev/null || true

echo ""
echo "✅ Pure Native Swift macOS App Built & Signed Successfully!"
