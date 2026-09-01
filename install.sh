#!/bin/zsh
set -euo pipefail

[[ "$(uname -m)" == "arm64" ]] || { echo "Sotto requires Apple Silicon (transcription runs on MLX)"; exit 1; }
command -v python3 >/dev/null || { echo "python3 not found — install with: brew install python"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 13) else 1)' \
  || { echo "python 3.13 required (the hashed lock file pins 3.13 wheels) — install with: brew install python@3.13"; exit 1; }

SRC="$(cd "$(dirname "$0")" && pwd)"
SUPPORT="$HOME/Library/Application Support/Sotto"
APP="/Applications/Sotto.app"
STAGE="/Applications/.Sotto.app.new"

echo "installing python environment into $SUPPORT ..."
mkdir -p "$SUPPORT"
[[ -x "$SUPPORT/venv/bin/python" ]] || python3 -m venv "$SUPPORT/venv"
# Hash-verified, fully pinned install: a compromised upstream release can't
# slip into an app that holds mic + Accessibility permissions
"$SUPPORT/venv/bin/pip" install --quiet --require-hashes --no-deps --timeout 60 --retries 10 -r "$SRC/requirements.lock"

echo "building $APP ..."
# Stage the new bundle completely before touching the existing app, so a
# failed build never destroys a working installation
rm -rf "$STAGE"
mkdir -p "$STAGE/Contents/MacOS" "$STAGE/Contents/Resources"
cp "$SRC/sotto.py" "$STAGE/Contents/Resources/"
cp "$SRC/assets/Sotto.icns" "$STAGE/Contents/Resources/"

cat > "$STAGE/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Sotto</string>
  <key>CFBundleDisplayName</key><string>Sotto</string>
  <key>CFBundleIdentifier</key><string>com.utsavanand.sotto</string>
  <key>CFBundleExecutable</key><string>sotto</string>
  <key>CFBundleIconFile</key><string>Sotto</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0.0</string>
  <key>LSUIElement</key><true/>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSMicrophoneUsageDescription</key><string>Sotto records while you hold the hotkey and transcribes on-device.</string>
</dict>
</plist>
PLIST

cat > "$STAGE/Contents/MacOS/sotto" <<LAUNCH
#!/bin/zsh
exec "$SUPPORT/venv/bin/python" "\$(cd "\$(dirname "\$0")/../Resources" && pwd)/sotto.py"
LAUNCH
chmod +x "$STAGE/Contents/MacOS/sotto"

# Ad-hoc signature: local install needs no notarization, and a signature gives
# the bundle a stabler TCC identity than none at all
codesign --force -s - "$STAGE"
rm -rf "$APP"
mv "$STAGE" "$APP"

echo ""
echo "done. launch with:  open /Applications/Sotto.app"
echo "then grant Sotto in System Settings > Privacy & Security:"
echo "  Microphone and Accessibility — and relaunch."
echo "log file: ~/Library/Logs/Sotto.log (also in the menu bar: 🎙 > Open Log)"
