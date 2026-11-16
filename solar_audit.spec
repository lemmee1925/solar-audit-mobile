# PyInstaller spec for Solar Audit Platform (offline site edition).
#
# Build on Windows with:
#     pyinstaller solar_audit.spec
#
# Produces dist/SolarAudit/SolarAudit.exe plus its bundled runtime — this
# whole dist/SolarAudit folder is what the Inno Setup script (installer.iss)
# packages into SolarAuditSetup.exe.
#
# Bundles: templates/, static/ (including vendored fonts + logo), and the
# question_data.py taxonomy module. Does NOT bundle any database, photos, or
# site_config.json — those are written at runtime under %PROGRAMDATA%\
# SolarAudit (see paths.py), never inside the installed application folder.

# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

hidden_imports = (
    collect_submodules('sqlalchemy.dialects.sqlite')
    + collect_submodules('reportlab')
)

a = Analysis(
    ['backend/launcher.py'],
    pathex=['backend'],
    binaries=[],
    datas=[
        ('frontend/templates', 'frontend/templates'),
        ('frontend/static', 'frontend/static'),
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SolarAudit',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # no terminal window; this is a desktop app
    icon=None,              # optional: point at an .ico file if you have one
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='SolarAudit',
)
