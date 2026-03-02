# -*- mode: python ; coding: utf-8 -*-

import sys
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# ------------------------------------------------------------
# Basic App Info
# ------------------------------------------------------------

app_name = "Magpie"
entry_script = "src/myapp/main.py"  # keep src/myapp even if folder not renamed

# ------------------------------------------------------------
# Platform Detection
# ------------------------------------------------------------

is_windows = sys.platform.startswith("win")
is_linux = sys.platform.startswith("linux")
is_macos = sys.platform == "darwin"

# ------------------------------------------------------------
# Icons per Platform
# ------------------------------------------------------------

if is_windows:
    icon_file = "resources/magpie.ico"
elif is_macos:
    icon_file = "resources/magpie.icns"
else:
    icon_file = "resources/magpie-256.png"

# ------------------------------------------------------------
# Collect All PySide6 Submodules
# (Ensures Qt plugins are bundled correctly)
# ------------------------------------------------------------

hiddenimports = collect_submodules("PySide6")

# ------------------------------------------------------------
# Data Files (icons, images, etc.)
# ------------------------------------------------------------

datas = [
    ("resources", "resources"),
]

# ------------------------------------------------------------
# Analysis
# ------------------------------------------------------------

a = Analysis(
    [entry_script],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# ------------------------------------------------------------
# PYZ
# ------------------------------------------------------------

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ------------------------------------------------------------
# EXE
# ------------------------------------------------------------

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=app_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # GUI app
    icon=icon_file,
)

# ------------------------------------------------------------
# COLLECT
# ------------------------------------------------------------

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=app_name,
)

# ------------------------------------------------------------
# macOS Bundle
# ------------------------------------------------------------

if is_macos:
    app = BUNDLE(
        coll,
        name=f"{app_name}.app",
        icon=icon_file,
        bundle_identifier="com.mycompany.magpie",
    )