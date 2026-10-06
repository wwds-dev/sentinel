#!/usr/bin/env bash
# Install "Sentinel.app" into /Applications — a thin launcher that runs the
# project's own main.py through the project's .venv.
#
#   ./scripts/install_app.sh
#
# RUN THIS ONCE. The bundle contains no application code, only a launcher, so
# edits to main.py (or anything else in the project) are live on the next launch
# — no rebuild step. Re-run this only if the icon, the bundle identity, or the
# launcher itself changes, or if the project moves to a different path.
#
# Trade-off vs. the old PyInstaller build: the app now depends on this project
# folder and its .venv staying where they are. Moving or deleting either breaks
# the launcher (it reports the missing path instead of failing silently).
#
# Data lives in the project (data/, config/, .env) exactly as it does when you
# run `python main.py` by hand, so the app and the terminal share one state.
#
# Built as a small native launcher rather than a shell-script bundle or a
# blocking AppleScript applet. The installed bundle is ad-hoc signed locally.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="Sentinel"
INSTALLED="/Applications/${APP_NAME}.app"
PY="${PROJECT_ROOT}/.venv/bin/python"
APP_VERSION="$(tr -d '[:space:]' < "${PROJECT_ROOT}/VERSION")"

if [[ ! "$APP_VERSION" =~ ^[1-9][0-9]*\.[0-9]{3}$ ]]; then
    echo "Error: VERSION must use MAJOR.SEQUENCE format, for example 2.001" >&2
    exit 2
fi

if [ ! -x "$PY" ]; then
    echo "Error: no interpreter at ${PY}" >&2
    echo "Create it first:  uv venv && uv pip install -r requirements.txt" >&2
    exit 1
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
APP_DIR="$STAGE/${APP_NAME}.app"

mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"
xcrun clang -std=c11 -Wall -Wextra -Werror \
    "$PROJECT_ROOT/scripts/thin_launcher.c" \
    -o "$APP_DIR/Contents/MacOS/SentinelLauncher"
cp "$PROJECT_ROOT/assets/icon.icns" "$APP_DIR/Contents/Resources/icon.icns"
printf '%s\n' "$PROJECT_ROOT" > "$APP_DIR/Contents/Resources/project_root.txt"

defaults write "$APP_DIR/Contents/Info" CFBundleName -string "${APP_NAME}"
defaults write "$APP_DIR/Contents/Info" CFBundleDisplayName -string "${APP_NAME}"
defaults write "$APP_DIR/Contents/Info" CFBundleIdentifier -string "com.netrunner3000.sentinel"
defaults write "$APP_DIR/Contents/Info" CFBundleExecutable -string "SentinelLauncher"
defaults write "$APP_DIR/Contents/Info" CFBundleIconFile -string "icon.icns"
defaults write "$APP_DIR/Contents/Info" CFBundlePackageType -string "APPL"
defaults write "$APP_DIR/Contents/Info" CFBundleGetInfoString -string "${APP_NAME} ${APP_VERSION}"
defaults write "$APP_DIR/Contents/Info" CFBundleShortVersionString -string "$APP_VERSION"
defaults write "$APP_DIR/Contents/Info" CFBundleVersion -string "$APP_VERSION"
defaults write "$APP_DIR/Contents/Info" NSHighResolutionCapable -bool true
defaults write "$APP_DIR/Contents/Info" LSUIElement -bool false
plutil -convert xml1 "$APP_DIR/Contents/Info.plist"
printf 'APPL????' > "$APP_DIR/Contents/PkgInfo"

# Stop a running copy so Launch Services picks up the new bundle.
pkill -f "${PROJECT_ROOT}/main.py" 2>/dev/null || true
pkill -f "${INSTALLED}/Contents/MacOS/applet" 2>/dev/null || true

# Clean up jobs created by the faulty launchctl-based installer. The prefix is
# unique to Sentinel and no new installation creates such a job.
while IFS= read -r legacy_job; do
    [ -n "$legacy_job" ] && launchctl remove "$legacy_job" 2>/dev/null || true
done < <(launchctl list | awk '$3 ~ /^com\.netrunner3000\.sentinel\.launch\./ {print $3}')
sleep 1

rm -rf "$INSTALLED"
LSREG="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
# "Sentinel Fork" is the name the app shipped under before 2026-09-12, not a
# spelling of the product. It appears here only as something to migrate away
# from, so it stays literal.
LEGACY_INSTALLED="/Applications/Sentinel Fork.app"
if [ -d "$LEGACY_INSTALLED" ]; then
    "$LSREG" -u "$LEGACY_INSTALLED" 2>/dev/null || true
    rm -rf "$LEGACY_INSTALLED"
fi

# Preserve the predecessor's packaged-app state under the final product name.
# Sentinel AI is intentionally not a migration source: it is a separate,
# archived application and must remain untouched.
LEGACY_SUPPORT="${HOME}/Library/Application Support/Sentinel Fork"
CURRENT_SUPPORT="${HOME}/Library/Application Support/Sentinel"
if [ -d "$LEGACY_SUPPORT" ] && [ ! -e "$CURRENT_SUPPORT" ]; then
    mv "$LEGACY_SUPPORT" "$CURRENT_SUPPORT"
    echo "  Migrated app data: ${LEGACY_SUPPORT} → ${CURRENT_SUPPORT}"
elif [ -d "$LEGACY_SUPPORT" ] && [ -e "$CURRENT_SUPPORT" ]; then
    echo "  Kept both app-data folders because Sentinel already exists; no automatic merge was attempted." >&2
fi
cp -R "$APP_DIR" "$INSTALLED"
xattr -cr "$INSTALLED" 2>/dev/null || true
codesign --force --deep --sign - "$INSTALLED"

"$LSREG" -f "$INSTALLED"

echo ""
echo "✓ Installed: ${INSTALLED}"
echo "  Version: v${APP_VERSION}"
echo "  Runs live from: ${PROJECT_ROOT}"
echo "  Edit the code, relaunch the app — no rebuild."
echo "  API keys: ${PROJECT_ROOT}/.env"
