#!/bin/bash
set -euo pipefail

APP_NAME="Attendance System"
BUNDLE_ID="com.sja.attendance-system"
VERSION="1.0.0"
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="$ROOT_DIR/dist"
APP_DIR="$DIST_DIR/$APP_NAME.app"
CONTENTS_DIR="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RES_DIR="$CONTENTS_DIR/Resources"
PAYLOAD_DIR="$RES_DIR/app"
STAGE_DIR="$DIST_DIR/dmg-stage"
DMG_PATH="$DIST_DIR/${APP_NAME// /_}-Installer.dmg"

rm -rf "$APP_DIR" "$STAGE_DIR" "$DMG_PATH"
mkdir -p "$MACOS_DIR" "$PAYLOAD_DIR" "$STAGE_DIR"

cat > "$CONTENTS_DIR/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleExecutable</key>
  <string>attendance-launcher</string>
  <key>CFBundleIdentifier</key>
  <string>$BUNDLE_ID</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>$APP_NAME</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>$VERSION</string>
  <key>CFBundleVersion</key>
  <string>$VERSION</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
</dict>
</plist>
PLIST

cat > "$MACOS_DIR/attendance-launcher" <<'LAUNCHER'
#!/bin/bash
set -euo pipefail

APP_RESOURCES="$(cd "$(dirname "$0")/../Resources" && pwd)"
BUNDLED_APP_DIR="$APP_RESOURCES/app"
STATE_DIR="$HOME/Library/Application Support/AttendanceSystem"
RUNTIME_DIR="$STATE_DIR/runtime"
LOG_DIR="$STATE_DIR/logs"
VENV_DIR="$STATE_DIR/.venv"
RUN_PID_FILE="$STATE_DIR/server.pid"

mkdir -p "$RUNTIME_DIR" "$LOG_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  osascript -e 'display alert "Python 3 is not installed" message "Install Python 3 from python.org, then open Attendance System again." as critical'
  exit 1
fi

rsync -a --delete \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  --exclude 'attendance.db' \
  "$BUNDLED_APP_DIR/" "$RUNTIME_DIR/"

if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install -q --upgrade pip
"$VENV_DIR/bin/pip" install -q -r "$RUNTIME_DIR/requirements.txt"

if [ -f "$RUN_PID_FILE" ]; then
  OLD_PID="$(cat "$RUN_PID_FILE" || true)"
  if [ -n "${OLD_PID:-}" ] && kill -0 "$OLD_PID" >/dev/null 2>&1; then
    open "http://localhost:8000"
    exit 0
  fi
fi

cd "$RUNTIME_DIR"
ATTENDANCE_DB_PATH="$STATE_DIR/attendance.db" \
nohup "$VENV_DIR/bin/python" -m uvicorn main:app --host 0.0.0.0 --port 8000 \
  >> "$LOG_DIR/server.log" 2>&1 &

echo $! > "$RUN_PID_FILE"

for _ in {1..25}; do
  if curl -fsS "http://127.0.0.1:8000/status" >/dev/null 2>&1; then
    open "http://localhost:8000"
    exit 0
  fi
  sleep 1
done

osascript -e 'display alert "Attendance System failed to start" message "Could not start server on port 8000. Check ~/Library/Application Support/AttendanceSystem/logs/server.log" as critical'
exit 1
LAUNCHER

chmod +x "$MACOS_DIR/attendance-launcher"

rsync -a \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude 'dist' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  --exclude 'attendance.db' \
  "$ROOT_DIR/" "$PAYLOAD_DIR/"

cp -R "$APP_DIR" "$STAGE_DIR/"
ln -s /Applications "$STAGE_DIR/Applications"

hdiutil create -volname "$APP_NAME Installer" \
  -srcfolder "$STAGE_DIR" \
  -ov -format UDZO "$DMG_PATH" >/dev/null

echo "Created app: $APP_DIR"
echo "Created dmg: $DMG_PATH"
