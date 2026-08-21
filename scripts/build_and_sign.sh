#!/usr/bin/env bash
set -e

# Apple Distribution Signing Identity (or pass via SIGN_IDENTITY="...")
SIGN_IDENTITY="${SIGN_IDENTITY:-Apple Distribution: Cody Bromley (YOUR_TEAM_ID)}"

echo "==> 1. Building Airspray.app (macOS 26+ Bundle)..."
.venv/bin/pyinstaller --name Airspray \
  --windowed \
  --noconfirm \
  --icon airspray/assets/AppIcon.icns \
  --add-data "airspray/assets:airspray/assets" \
  --osx-bundle-identifier com.codybrom.airspray \
  airspray/cli.py

echo "==> 2. Compiling Native macOS Asset Catalog (Assets.car)..."
xcrun actool airspray/assets/Assets.xcassets \
  --compile dist/Airspray.app/Contents/Resources \
  --platform macosx \
  --minimum-deployment-target 26.0 \
  --app-icon AppIcon \
  --output-partial-info-plist /tmp/assetcatalog_generated_info.plist

cp -f airspray/assets/AppIcon.icns dist/Airspray.app/Contents/Resources/AppIcon.icns
cp -f airspray/assets/Airspray.icns dist/Airspray.app/Contents/Resources/Airspray.icns

echo "==> 3. Setting Info.plist Keys..."
/usr/libexec/PlistBuddy -c "Add :CFBundleIconName string AppIcon" dist/Airspray.app/Contents/Info.plist 2>/dev/null || \
/usr/libexec/PlistBuddy -c "Set :CFBundleIconName AppIcon" dist/Airspray.app/Contents/Info.plist

/usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string AppIcon" dist/Airspray.app/Contents/Info.plist 2>/dev/null || \
/usr/libexec/PlistBuddy -c "Set :CFBundleIconFile AppIcon" dist/Airspray.app/Contents/Info.plist

/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" dist/Airspray.app/Contents/Info.plist 2>/dev/null || \
/usr/libexec/PlistBuddy -c "Set :LSUIElement true" dist/Airspray.app/Contents/Info.plist

/usr/libexec/PlistBuddy -c 'Add :LSMinimumSystemVersion string "26.0"' dist/Airspray.app/Contents/Info.plist 2>/dev/null || \
/usr/libexec/PlistBuddy -c 'Set :LSMinimumSystemVersion "26.0"' dist/Airspray.app/Contents/Info.plist

/usr/libexec/PlistBuddy -c 'Add :NSBluetoothAlwaysUsageDescription string "Airspray requires Bluetooth to discover and control your Air Wick diffuser."' dist/Airspray.app/Contents/Info.plist 2>/dev/null || \
/usr/libexec/PlistBuddy -c 'Set :NSBluetoothAlwaysUsageDescription "Airspray requires Bluetooth to discover and control your Air Wick diffuser."' dist/Airspray.app/Contents/Info.plist

/usr/libexec/PlistBuddy -c 'Add :NSBluetoothPeripheralUsageDescription string "Airspray requires Bluetooth to discover and control your Air Wick diffuser."' dist/Airspray.app/Contents/Info.plist 2>/dev/null || \
/usr/libexec/PlistBuddy -c 'Set :NSBluetoothPeripheralUsageDescription "Airspray requires Bluetooth to discover and control your Air Wick diffuser."' dist/Airspray.app/Contents/Info.plist

echo "==> 4. Building Standalone CLI Binary..."
.venv/bin/pyinstaller --name airspray \
  --onefile \
  --noconfirm \
  --add-data "airspray/assets:airspray/assets" \
  --osx-bundle-identifier com.codybrom.airspray \
  airspray/cli.py

echo "==> 5. Code Signing Airspray.app with '$SIGN_IDENTITY' and Entitlements..."
codesign --force --deep --options runtime --entitlements entitlements.plist --sign "$SIGN_IDENTITY" dist/Airspray.app

echo "==> 6. Code Signing CLI binary with '$SIGN_IDENTITY'..."
codesign --force --options runtime --entitlements entitlements.plist --sign "$SIGN_IDENTITY" dist/airspray

echo "==> 7. Verifying Signatures..."
codesign --verify --deep --strict --verbose=2 dist/Airspray.app
codesign --verify --strict --verbose=2 dist/airspray

echo ""
echo "✅ Build, Asset Catalog Compilation & Signing Complete for macOS 26+!"
echo "   • App: dist/Airspray.app (with compiled Assets.car)"
echo "   • CLI: dist/airspray"
