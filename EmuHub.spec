# EmuHub.spec
# PyInstaller build spec — produces a single portable EmuHub.exe
# Run: pyinstaller EmuHub.spec

import os, sdl2dll

a = Analysis(
    ['src/main.py'],
    pathex=['.'],
    binaries=[
        # Controller support. Only SDL2.dll itself — pysdl2-dll also ships the
        # image/audio/font libraries, which EmuHub doesn't use. controller.py
        # points PySDL2 at this folder when running frozen.
        (os.path.join(sdl2dll.get_dllpath(), 'SDL2.dll'), 'sdl2'),
    ],
    datas=[
        # Bundle the entire web folder into the exe
        ('src/web', 'web'),
        # Community controller mappings (SDL_GameControllerDB, zlib licence)
        ('src/data', 'data'),
    ],
    hiddenimports=[
        'controller',
        'sdl2',
        'webview',
        'webview.platforms.winforms',
        'clr',
        'System',
        'System.Windows.Forms',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The DLL above is bundled directly; leave out the package that carries
    # the rest of the SDL libraries.
    excludes=['sdl2dll'],
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
