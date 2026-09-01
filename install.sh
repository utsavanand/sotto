#!/bin/zsh
set -euo pipefail

[[ "$(uname -m)" == "arm64" ]] || { echo "Sotto requires Apple Silicon (transcription runs on MLX)"; exit 1; }
command -v python3 >/dev/null || { echo "python3 not found — install with: brew install python"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || { echo "python3 >= 3.10 required — install with: brew install python"; exit 1; }

SRC="$(cd "$(dirname "$0")" && pwd)"
SUPPORT="$HOME/Library/Application Support/Sotto"
APP="/Applications/Sotto.app"

echo "installing python environment into $SUPPORT ..."
mkdir -p "$SUPPORT"
[[ -x "$SUPPORT/venv/bin/python" ]] || python3 -m venv "$SUPPORT/venv"
"$SUPPORT/venv/bin/pip" install --quiet --upgrade --timeout 60 --retries 10 -r "$SRC/requirements.txt"

echo "building $APP ..."
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$SRC/sotto.py" "$APP/Contents/Resources/"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Sotto</string>
  <key>CFBundleDisplayName</key><string>Sotto</string>
  <key>CFBundleIdentifier</key><string>com.utsavanand.sotto</string>
  <key>CFBundleExecutable</key><string>sotto</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0.0</string>
  <key>LSUIElement</key><true/>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSMicrophoneUsageDescription</key><string>Sotto records while you hold the hotkey and transcribes on-device.</string>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/sotto" <<LAUNCH
#!/bin/zsh
exec "$SUPPORT/venv/bin/python" "\$(cd "\$(dirname "\$0")/../Resources" && pwd)/sotto.py"
LAUNCH
chmod +x "$APP/Contents/MacOS/sotto"

# Ad-hoc signature: local install needs no notarization, and a signature gives
# the bundle a stabler TCC identity than none at all
codesign --force -s - "$APP"

echo ""
echo "done. launch with:  open /Applications/Sotto.app"
echo "then grant Sotto in System Settings > Privacy & Security:"
echo "  Microphone, Accessibility, and Input Monitoring — and relaunch."
echo "log file: ~/Library/Logs/Sotto.log (also in the menu bar: 🎙 > Open Log)"
