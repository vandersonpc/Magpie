# -*- mode: python ; coding: utf-8 -*-
import sys
import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs

block_cipher = None

# ------------------------------------------------------------
# Basic App Info
# ------------------------------------------------------------
app_name = "Magpie"
entry_script = "src/magpie/main.py"

# ------------------------------------------------------------
# Platform Detection
# ------------------------------------------------------------
is_windows = sys.platform.startswith("win")
is_linux   = sys.platform.startswith("linux")
is_macos   = sys.platform == "darwin"

# ------------------------------------------------------------
# Icons per Platform
# ------------------------------------------------------------
if is_windows:
    icon_file = "resources/magpie.ico"
elif is_macos:
    icon_file = "resources/magpie.icns"
else:
    icon_file = None   # linuxdeploy will handle the .png icon separately

# ------------------------------------------------------------
# PySide6 / Qt6 bundling improvements
# ------------------------------------------------------------
# 1. Collect submodules (still useful, but PyInstaller hooks improved a lot since ~2023–2024)
hiddenimports = collect_submodules("PySide6")

# 2. Explicitly collect Qt plugins, translations, etc. (helps especially on Linux/AppImage)
#    PyInstaller 6.x+ usually does this automatically, but being explicit avoids surprises
datas = collect_data_files("PySide6", include_py_files=False)

# 3. Also collect dynamic libraries (Qt libs, OpenSSL if used, etc.)
binaries = collect_dynamic_libs("PySide6")

# 4. Add your custom resources (icons, images, qss styles, etc.)
datas += [("resources", "resources")]

# Optional: exclude heavy/unused parts to reduce size
# (adjust based on what your app actually uses)
excludes = [
    "PySide6.examples",           # examples & tests
    "PySide6.Qt3D*",              # if you don't use 3D
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtLocation",
    "PySide6.QtMultimedia",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtPrintSupport",     # if no printing
    "PySide6.QtQuick3D",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngine*",
    "PySide6.QtWebSockets",
    "PySide6.QtXml",
]

# ------------------------------------------------------------
# Analysis
# ------------------------------------------------------------
a = Analysis(
    [entry_script],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
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
# EXE / executable
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
    upx=True,                # usually safe; disable if you see corruption
    console=False,           # GUI app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
)

# ------------------------------------------------------------
# COLLECT → --onedir bundle (best for linuxdeploy / cross-platform)
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
# macOS .app bundle
# ------------------------------------------------------------
if is_macos:
    app = BUNDLE(
        coll,
        name=f"{app_name}.app",
        icon=icon_file,
        bundle_identifier="com.yourcompany.magpie",  # ← customize!
    )
