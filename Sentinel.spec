# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Sentinel — self-contained macOS .app bundle.

Build:   .venv/bin/pyinstaller --noconfirm Sentinel.spec
Output:  dist.noindex/Sentinel.app (when built through scripts/build_app.sh)
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules


APP_VERSION = (Path(SPECPATH) / "VERSION").read_text(encoding="utf-8").strip()

datas = []
binaries = []
hiddenimports = [
    "tiktoken_ext",
    "tiktoken_ext.openai_public",
    "whois",                         # lazy import in providers/domain_lookup
    "dns", "dns.resolver",           # lazy import in providers/domain_lookup
    "segno",                         # QR export in the Tunnel Servers tab
    "cryptography.hazmat.primitives.ciphers.aead",   # site backups
    "cryptography.hazmat.primitives.kdf.scrypt",
]

# SDKs / libs with data files or plugin discovery that static analysis can miss.
for pkg in ("google.genai", "tiktoken", "anthropic", "openai", "certifi"):
    d, b, h = collect_all(pkg)
    if pkg == "google.genai":
        # The SDK wheel ships its own large pytest suite. It is neither runtime
        # data nor an application test and must not be bundled or collected by
        # Sentinel's pytest runs.
        d = [item for item in d if "google/genai/tests" not in item[0].replace("\\", "/")]
        h = [name for name in h if ".tests" not in name]
    datas += d
    binaries += b
    hiddenimports += h

# Read-only resources seeded into the writable user-data dir on first launch.
datas += [
    ("VERSION", "."),
    # Menu bar glyph and app icon, read at runtime from RESOURCE_DIR by
    # ui/tray.py and ui/app_identity.py. BUNDLE's icon= below only stamps the
    # icns into the bundle at build time; it does not put the file where the
    # running app can open it.
    ("assets/tray.png", "assets"),
    ("assets/tray@2x.png", "assets"),
    ("assets/icon.icns", "assets"),
    ("config", "config"),
    ("agents/vpn_agent/config/vpn_profiles.json", "agents/vpn_agent/config"),
    ("README.md", "."),
    ("docs/versioning.md", "docs"),
    (".env.example", "."),
    ("docs/agents", "docs/agents"),   # per-agent capability sheets (Docs button)
    ("docs/training", "docs/training"),  # in-app Learning Centre lessons
    ("docs/testing_roadmap.md", "docs"),  # complete in-app testing roadmap
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Dev-only weight.
        "pytest", "pip", "setuptools",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Sentinel",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,             # windowed GUI app — no terminal
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,          # native arch (arm64 on this Mac)
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Sentinel",
)

app = BUNDLE(
    coll,
    name="Sentinel.app",
    icon="assets/icon.icns",
    bundle_identifier="com.netrunner3000.sentinel",
    info_plist={
        "CFBundleName": "Sentinel",
        "CFBundleDisplayName": "Sentinel",
        "CFBundleGetInfoString": f"Sentinel {APP_VERSION}",
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleVersion": APP_VERSION,
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,   # allow dark mode
        "LSMinimumSystemVersion": "12.0",
        "LSApplicationCategoryType": "public.app-category.developer-tools",
        # File dialogs can read selected evidence and save reports.
        "NSDesktopFolderUsageDescription": "Sentinel reads files you select and saves reports you choose.",
        "NSDocumentsFolderUsageDescription": "Sentinel reads files you select and saves reports you choose.",
    },
)
