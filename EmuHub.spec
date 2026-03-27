# EmuHub.spec
# PyInstaller build spec — produces a single portable EmuHub.exe
# Run: pyinstaller EmuHub.spec

a = Analysis(
    ['src/main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        # Bundle the entire web folder into the exe
        ('src/web', 'web'),
    ],
    hiddenimports=[
        'webview',
        'webview.platforms.winforms',
        'clr',
        'System',
        'System.Windows.Forms',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='EmuHub',
    debug=False,
    bootloader_ignore_signals=False,
    upx=True,
    upx_exclude=[],
    # Fixed extraction folder in %TEMP% — re-runs overwrite the same location
    # instead of spawning new orphaned _MEI* folders each time the exe crashes.
    runtime_tmpdir='EmuHub_runtime',
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
    version='version_info.txt',
)
