#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# NOT the everyday script. Use scripts/install_app.sh instead — that installs a
# launcher that runs this project's code live, so edits apply on next launch
# with no rebuild.
#
# This one FREEZES the code into the bundle: running it replaces the live app in
# /Applications with a snapshot, and you are back to re-running a build after
# every edit. Keep it only for producing a self-contained bundle to hand to
# another machine (no project folder or .venv required there).
# ─────────────────────────────────────────────────────────────────────────────
#
# Build & install "Sentinel.app" — a self-contained macOS application bundle
# produced by PyInstaller (bundles its own Python + every dependency). Unlike a
# dev checkout, the app does NOT need the project's .venv or even the project
# folder once installed.
#
#   ./scripts/build_app.sh            # build only (dist.noindex/Sentinel.app)
#   ./scripts/build_app.sh --install  # explicitly replace /Applications copy
#   ./scripts/build_app.sh --skip-tests # for a caller that already ran its release checks
#
# IMPORTANT: the bundle freezes the CODE at build time. After editing main.py (or
# anything else), re-run this script to refresh the installed app.
#
# Writable state (SQLite DB, saved chats, logs, editable config, API keys) lives
# in  ~/Library/Application Support/Sentinel/  — seeded on first launch and
# preserved across rebuilds. Put API keys in that folder's .env file.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="Sentinel"
APP_BUNDLE="${APP_NAME}.app"
# macOS Tahoe's Apps launcher indexes .app bundles anywhere under Documents,
# and .metadata_never_index is not reliable for PyInstaller output.  A folder
# whose name ends in .noindex is excluded by Spotlight/Core Spotlight.
DIST_DIR="${PROJECT_ROOT}/dist.noindex"
DIST_APP="${DIST_DIR}/${APP_BUNDLE}"
LEGACY_DIST="${PROJECT_ROOT}/dist"
INSTALLED="/Applications/${APP_BUNDLE}"
PY="${PROJECT_ROOT}/.venv/bin/python"

cd "$PROJECT_ROOT"

INSTALL=false
SKIP_TESTS=false
for arg in "$@"; do
    case "$arg" in
        --install) INSTALL=true ;;
        --skip-tests) SKIP_TESTS=true ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

echo "▸ Building ${APP_BUNDLE} with PyInstaller (this takes ~1 min)…"
if [ "$SKIP_TESTS" = false ]; then
    echo "▸ Running release tests…"
    "$PY" -m pytest -q
fi
mkdir -p "$DIST_DIR"
touch "${DIST_DIR}/.metadata_never_index"

# Remove old generated bundles from the historical dist/ location.  Keeping
# source history is sufficient; these bundles are reproducible build artifacts
# and otherwise reappear as duplicate apps in Tahoe's Spotlight Apps launcher.
LSREG="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
# "Sentinel AI" and "Sentinel Fork" are names this app shipped under before,
# listed so their stale bundles get cleaned up — not spellings of the product.
for stale_name in "Sentinel.app" "Sentinel AI.app" "Sentinel Fork.app"; do
    stale_app="${LEGACY_DIST}/${stale_name}"
    if [ -d "$stale_app" ]; then
        "$LSREG" -u "$stale_app" 2>/dev/null || true
        rm -rf "$stale_app"
    fi
done

"$PY" -m PyInstaller --noconfirm --clean --distpath "$DIST_DIR" Sentinel.spec

if [ "$INSTALL" = false ]; then
    echo "✓ Built: ${DIST_APP}"
    echo "  Not installed. Use --install only when you intend to replace the thin Lab launcher."
    exit 0
fi

echo "▸ Installing to ${INSTALLED}…"
# Stop any running instance so Launch Services picks up the new binary.
pkill -f "${APP_BUNDLE}/Contents/MacOS" 2>/dev/null || true
pkill -f "Sentinel Fork.app/Contents/MacOS" 2>/dev/null || true
sleep 1
rm -rf "$INSTALLED"
LEGACY_INSTALLED="/Applications/Sentinel Fork.app"
if [ -d "$LEGACY_INSTALLED" ]; then
    "$LSREG" -u "$LEGACY_INSTALLED" 2>/dev/null || true
    rm -rf "$LEGACY_INSTALLED"
fi

# Migrate only this product's predecessor. Sentinel AI is a separate archived
# app and is never read, moved, merged or removed by this installer.
LEGACY_SUPPORT="${HOME}/Library/Application Support/Sentinel Fork"
CURRENT_SUPPORT="${HOME}/Library/Application Support/Sentinel"
if [ -d "$LEGACY_SUPPORT" ] && [ ! -e "$CURRENT_SUPPORT" ]; then
    mv "$LEGACY_SUPPORT" "$CURRENT_SUPPORT"
    echo "  Migrated app data: ${LEGACY_SUPPORT} → ${CURRENT_SUPPORT}"
elif [ -d "$LEGACY_SUPPORT" ] && [ -e "$CURRENT_SUPPORT" ]; then
    echo "  Kept both app-data folders because Sentinel already exists; no automatic merge was attempted." >&2
fi
cp -R "$DIST_APP" "$INSTALLED"

# Clear any extended attributes and ad-hoc sign so Gatekeeper allows the local app.
xattr -cr "$INSTALLED" 2>/dev/null || true
codesign --force --deep --sign - "$INSTALLED"

# Register with Launch Services so it shows up in Spotlight / Launchpad.
"$LSREG" -f "$INSTALLED"
# ...and drop the build copy from Launch Services, so Spotlight/Launchpad only
# ever offer the installed /Applications one.
"$LSREG" -u "$DIST_APP" 2>/dev/null || true

# Deregistering alone does not stick once Spotlight has indexed the bundle (it
# keeps returning for "Sentinel", and launching THAT copy silently runs a
# build that drifts out of date). The build output is byte-identical to what we
# just installed and is regenerated by the next build, so remove it outright —
# that guarantees exactly one "Sentinel" app exists on disk.
rm -rf "$DIST_APP"

echo ""
echo "✓ Installed: ${INSTALLED}"
echo "  Launch it from Spotlight, Launchpad, or /Applications."
echo "  API keys: ~/Library/Application Support/${APP_NAME}/.env"
