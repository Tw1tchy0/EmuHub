"""
EmuHub — Unified Emulator Launcher
Python + pywebview backend.
"""

import sys, os, json, re, threading, subprocess, time, shutil
import ctypes, ctypes.wintypes, base64
import urllib.request, urllib.parse, urllib.error
from pathlib import Path
import webview

# ══════════════════════════════════════════════════════
# WINDOWS DPAPI — encrypt/decrypt tied to current user account
# ══════════════════════════════════════════════════════
class _BLOB(ctypes.Structure):
    _fields_ = [('cbData', ctypes.wintypes.DWORD),
                ('pbData', ctypes.POINTER(ctypes.c_ubyte))]

def _dpapi_encrypt(plaintext: str) -> str | None:
    data = plaintext.encode('utf-8')
    n    = len(data)
    arr  = (ctypes.c_ubyte * n)(*data)
    b_in = _BLOB(n, arr); b_out = _BLOB()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(b_in), None, None, None, None, 0, ctypes.byref(b_out))
    if not ok:
        return None
    result = bytes(b_out.pbData[:b_out.cbData])
    ctypes.windll.kernel32.LocalFree(b_out.pbData)
    return base64.b64encode(result).decode('ascii')

def _dpapi_decrypt(enc_b64: str) -> str | None:
    try:
        data = base64.b64decode(enc_b64)
        n    = len(data)
        arr  = (ctypes.c_ubyte * n)(*data)
        b_in = _BLOB(n, arr); b_out = _BLOB()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(b_in), None, None, None, None, 0, ctypes.byref(b_out))
        if not ok:
            return None
        result = bytes(b_out.pbData[:b_out.cbData]).decode('utf-8')
        ctypes.windll.kernel32.LocalFree(b_out.pbData)
        return result
    except Exception:
        return None

# ══════════════════════════════════════════════════════
# SETTINGS — persisted to %APPDATA%\EmuHub\settings.json
# ══════════════════════════════════════════════════════
SETTINGS_DIR  = Path(os.environ.get('APPDATA', str(Path.home()))) / 'EmuHub'
SETTINGS_FILE = SETTINGS_DIR / 'settings.json'
SETTINGS_TMP  = SETTINGS_DIR / 'settings.json.tmp'
SETTINGS_BAK  = SETTINGS_DIR / 'settings.json.bak'

DEFAULT_ROM_PATHS = {
    'switch':  '', '3ds': '', 'ds': '', 'gba': '', 'gb': '',
    'n64': '', 'gc': '', 'wii': '', 'nes': '', 'snes': '',
    'genesis': '', 'ps1': '', 'ps2': '', 'ps3': '', 'psp': '',
    'xbox360': '',
}

DEFAULT_EMULATORS = {
    'switch':  {'name': 'Eden',       'args_flag': '-p', 'exe': ''},
    '3ds':     {'name': 'Citra',      'args_flag': '',   'exe': ''},
    'ds':      {'name': 'DeSmuME',    'args_flag': '',   'exe': ''},
    'gba':     {'name': 'mGBA',       'args_flag': '',   'exe': ''},
    'gb':      {'name': 'mGBA',       'args_flag': '',   'exe': ''},
    'n64':     {'name': 'Project64',  'args_flag': '',   'exe': ''},
    'gc':      {'name': 'Dolphin',    'args_flag': '-e', 'exe': ''},
    'wii':     {'name': 'Dolphin',    'args_flag': '-e', 'exe': ''},
    'nes':     {'name': 'FCEUX',      'args_flag': '',   'exe': ''},
    'snes':    {'name': 'Snes9x',     'args_flag': '',   'exe': ''},
    'genesis': {'name': 'BlastEm',    'args_flag': '',   'exe': ''},
    'ps1':     {'name': 'DuckStation','args_flag': '--', 'exe': ''},
    'ps2':     {'name': 'PCSX2',      'args_flag': '--', 'exe': ''},
    'ps3':     {'name': 'RPCS3',      'args_flag': '',   'exe': ''},
    'psp':     {'name': 'PPSSPP',     'args_flag': '',   'exe': ''},
    'xbox360': {'name': 'Xenia',      'args_flag': '',   'exe': ''},
}

# Supported extensions per known console.
# Each emulator ONLY accepts files matching its extension set —
# any other extension is rejected before launch.
CONSOLE_EXTS = {
    'switch':  {'.nsp', '.xci'},
    '3ds':     {'.3ds', '.cia', '.zip'},
    'ds':      {'.nds', '.zip'},
    'gba':     {'.gba', '.zip'},
    'gb':      {'.gb', '.gbc', '.zip'},
    'n64':     {'.z64', '.n64', '.v64', '.zip'},
    'gc':      {'.iso', '.gcm', '.gcz', '.rvz', '.wbfs'},
    'wii':     {'.iso', '.wbfs', '.gcz', '.rvz', '.wad'},
    'nes':     {'.nes', '.unf', '.unif', '.zip'},
    'snes':    {'.sfc', '.smc', '.zip'},
    'genesis': {'.md', '.bin', '.gen', '.smd', '.zip'},
    'ps1':     {'.bin', '.cue', '.iso', '.img', '.mdf', '.pbp', '.chd'},
    'ps2':     {'.iso', '.bin', '.img', '.mdf', '.chd'},
    'ps3':     {'.pkg', '.iso'},
    'psp':     {'.iso', '.cso', '.pbp'},
    'xbox360': {'.iso', '.xex', '.xbla'},
    # 'other' consoles use user-defined extensions stored in settings
}

# Keys that are computed at runtime and must never be persisted to disk
_EPHEMERAL_KEYS = {'exists'}

_settings_lock = threading.Lock()


def _load_settings() -> dict:
    """
    Load settings.json.  On corrupt JSON, backs up the bad file and
    returns defaults so the app can still run.
    """
    with _settings_lock:
        if SETTINGS_FILE.exists():
            try:
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                data.setdefault('rom_paths', {})
                data.setdefault('emulators', {})
                data.setdefault('other_emulators', [])   # custom "Other" entries
                for k, v in DEFAULT_ROM_PATHS.items():
                    data['rom_paths'].setdefault(k, v)
                for k, v in DEFAULT_EMULATORS.items():
                    data['emulators'].setdefault(k, dict(v))
                return data
            except json.JSONDecodeError:
                try:
                    shutil.copy2(SETTINGS_FILE, SETTINGS_BAK)
                except Exception:
                    pass
            except Exception:
                pass

    return {
        'version':        1,
        'rom_paths':      DEFAULT_ROM_PATHS.copy(),
        'emulators':      {k: dict(v) for k, v in DEFAULT_EMULATORS.items()},
        'other_emulators': [],
    }


def _save_settings(data: dict) -> bool:
    """Atomic write via temp-file + os.replace. Strips ephemeral keys."""
    clean = json.loads(json.dumps(data))
    for _emu in clean.get('emulators', {}).values():
        for key in _EPHEMERAL_KEYS:
            _emu.pop(key, None)
    for _emu in clean.get('other_emulators', []):
        for key in _EPHEMERAL_KEYS:
            _emu.pop(key, None)

    with _settings_lock:
        try:
            SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
            with open(SETTINGS_TMP, 'w', encoding='utf-8') as f:
                json.dump(clean, f, indent=2)
            os.replace(SETTINGS_TMP, SETTINGS_FILE)
            return True
        except Exception:
            try:
                SETTINGS_TMP.unlink(missing_ok=True)
            except Exception:
                pass
            return False


# ══════════════════════════════════════════════════════
# FILE TYPE VALIDATION
# ══════════════════════════════════════════════════════
def _get_supported_exts(console: str, settings: dict) -> set:
    """
    Return the set of extensions a console's emulator accepts.
    For known consoles uses CONSOLE_EXTS.
    For 'other' entries uses the user-defined extension list.
    """
    if console in CONSOLE_EXTS:
        return CONSOLE_EXTS[console]
    # Look up in other_emulators by console key
    for entry in settings.get('other_emulators', []):
        if entry.get('console_key') == console:
            raw = entry.get('extensions', '')
            return {e.strip().lower() for e in raw.split(',') if e.strip()}
    return set()


def _validate_rom_extension(console: str, rom_path: str, settings: dict) -> tuple[bool, str]:
    """
    Validates that rom_path has an extension accepted by the emulator
    configured for console.  Returns (ok, error_message).
    """
    ext = Path(rom_path).suffix.lower()
    supported = _get_supported_exts(console, settings)
    if not supported:
        # No extension list defined — allow anything (unknown console)
        return True, ''
    if ext not in supported:
        friendly = ', '.join(sorted(supported))
        return False, (
            f'File type "{ext}" is not supported by the '
            f'{console.upper()} emulator.\n\n'
            f'Accepted formats: {friendly}'
        )
    return True, ''


# ══════════════════════════════════════════════════════
# EMULATOR AUTO-DETECTION
# ══════════════════════════════════════════════════════
EMU_SIGNATURES = {
    # ── Nintendo 3DS
    'citra-qt.exe':           {'consoles': ['3ds'],             'name': 'Citra',           'args_flag': ''},
    'citra.exe':              {'consoles': ['3ds'],             'name': 'Citra',           'args_flag': ''},
    'lime3ds.exe':            {'consoles': ['3ds'],             'name': 'Lime3DS',         'args_flag': ''},
    'azahar.exe':             {'consoles': ['3ds'],             'name': 'Azahar',          'args_flag': ''},
    # ── Nintendo DS
    'desmume_0.9.13_x64.exe': {'consoles': ['ds'],             'name': 'DeSmuME',         'args_flag': ''},
    'desmume.exe':            {'consoles': ['ds'],             'name': 'DeSmuME',         'args_flag': ''},
    'melonds.exe':            {'consoles': ['ds'],             'name': 'melonDS',         'args_flag': ''},
    # ── Game Boy / GBA
    'mgba.exe':               {'consoles': ['gba', 'gb'],      'name': 'mGBA',            'args_flag': ''},
    'visualboyadvance-m.exe': {'consoles': ['gba', 'gb'],      'name': 'VBA-M',           'args_flag': ''},
    'vba-m.exe':              {'consoles': ['gba', 'gb'],      'name': 'VBA-M',           'args_flag': ''},
    'retroarch.exe':          {'consoles': ['gba','gb','nes','snes','n64','genesis','ps1','psp'],
                                                                'name': 'RetroArch',       'args_flag': '-L'},
    # ── Nintendo Switch
    'eden.exe':               {'consoles': ['switch'],          'name': 'Eden',            'args_flag': '-p'},
    'yuzu.exe':               {'consoles': ['switch'],          'name': 'Yuzu',            'args_flag': ''},
    'ryujinx.exe':            {'consoles': ['switch'],          'name': 'Ryujinx',         'args_flag': ''},
    'suyu.exe':               {'consoles': ['switch'],          'name': 'Suyu',            'args_flag': '-p'},
    'citron.exe':             {'consoles': ['switch'],          'name': 'Citron',          'args_flag': ''},
    # ── Nintendo 64
    'project64.exe':          {'consoles': ['n64'],             'name': 'Project64',       'args_flag': ''},
    'mupen64plus.exe':        {'consoles': ['n64'],             'name': 'Mupen64Plus',     'args_flag': ''},
    'mupen64plus-ui-console.exe': {'consoles': ['n64'],         'name': 'Mupen64Plus',     'args_flag': ''},
    'simple64.exe':           {'consoles': ['n64'],             'name': 'simple64',        'args_flag': ''},
    'ares.exe':               {'consoles': ['n64','snes','nes','genesis'],
                                                                 'name': 'ares',            'args_flag': ''},
    # ── GameCube / Wii
    'dolphin.exe':            {'consoles': ['gc', 'wii'],       'name': 'Dolphin',         'args_flag': '-e'},
    'dolphin-emu.exe':        {'consoles': ['gc', 'wii'],       'name': 'Dolphin',         'args_flag': '-e'},
    'dolphin-emu-nogui.exe':  {'consoles': ['gc', 'wii'],       'name': 'Dolphin (NoGUI)', 'args_flag': '-e'},
    # ── NES
    'fceux.exe':              {'consoles': ['nes'],             'name': 'FCEUX',           'args_flag': ''},
    'nestopia.exe':           {'consoles': ['nes'],             'name': 'Nestopia',        'args_flag': ''},
    'punesexe.exe':           {'consoles': ['nes'],             'name': 'puNES',           'args_flag': ''},
    'mesen.exe':              {'consoles': ['nes', 'snes'],     'name': 'Mesen',           'args_flag': ''},
    # ── SNES
    'bsnes.exe':              {'consoles': ['snes'],            'name': 'bsnes',           'args_flag': ''},
    'snes9x.exe':             {'consoles': ['snes'],            'name': 'Snes9x',          'args_flag': ''},
    'snes9x-x64.exe':         {'consoles': ['snes'],            'name': 'Snes9x',          'args_flag': ''},
    # ── Sega Genesis
    'gens.exe':               {'consoles': ['genesis'],         'name': 'Gens',            'args_flag': ''},
    'fusion.exe':             {'consoles': ['genesis'],         'name': 'Gens/GS (Fusion)','args_flag': ''},
    'blastem.exe':            {'consoles': ['genesis'],         'name': 'BlastEm',         'args_flag': ''},
    # ── PS1
    'epsxe.exe':              {'consoles': ['ps1'],             'name': 'ePSXe',           'args_flag': '-nogui -loadbin'},
    'pcsx-r.exe':             {'consoles': ['ps1'],             'name': 'PCSX-R',          'args_flag': ''},
    'duckstation.exe':        {'consoles': ['ps1'],             'name': 'DuckStation',     'args_flag': '-fullscreen --'},
    'duckstation-qt.exe':     {'consoles': ['ps1'],             'name': 'DuckStation',     'args_flag': '--'},
    'xebra.exe':              {'consoles': ['ps1'],             'name': 'Xebra',           'args_flag': ''},
    # ── PSP
    'ppssppwindows.exe':      {'consoles': ['psp'],             'name': 'PPSSPP',          'args_flag': ''},
    'ppssppwindows64.exe':    {'consoles': ['psp'],             'name': 'PPSSPP',          'args_flag': ''},
    'ppsspp.exe':             {'consoles': ['psp'],             'name': 'PPSSPP',          'args_flag': ''},
    # ── PS2
    'pcsx2.exe':              {'consoles': ['ps2'],             'name': 'PCSX2',           'args_flag': '--'},
    'pcsx2-qt.exe':           {'consoles': ['ps2'],             'name': 'PCSX2',           'args_flag': '--'},
    'pcsx2-avx2.exe':         {'consoles': ['ps2'],             'name': 'PCSX2 (AVX2)',    'args_flag': '--'},
    # ── PS3
    'rpcs3.exe':              {'consoles': ['ps3'],             'name': 'RPCS3',           'args_flag': ''},
    # ── Xbox 360
    'xenia.exe':              {'consoles': ['xbox360'],         'name': 'Xenia',           'args_flag': ''},
    'xenia_canary.exe':       {'consoles': ['xbox360'],         'name': 'Xenia Canary',    'args_flag': ''},
}


def _walk_limited(base: Path, max_depth: int):
    try:
        for item in base.iterdir():
            if item.is_file():
                yield item
            elif item.is_dir() and max_depth > 0:
                try:
                    yield from _walk_limited(item, max_depth - 1)
                except (PermissionError, OSError):
                    pass
    except (PermissionError, OSError):
        pass


def _scan_for_emulators() -> list:
    found      = []
    seen_paths = set()
    scan_dirs  = []

    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).parent
    else:
        exe_dir = Path(os.path.abspath(__file__)).parent
    scan_dirs.append((exe_dir, 6))
    if exe_dir.parent != exe_dir:
        scan_dirs.append((exe_dir.parent, 4))

    for env_var in ('ProgramFiles', 'ProgramFiles(x86)', 'ProgramW6432'):
        pf = os.environ.get(env_var, '')
        if pf:
            p = Path(pf)
            if p.exists() and (p, 3) not in scan_dirs:
                scan_dirs.append((p, 3))

    for env_var in ('LOCALAPPDATA', 'APPDATA'):
        ad = os.environ.get(env_var, '')
        if ad:
            p = Path(ad)
            if p.exists():
                scan_dirs.append((p, 3))

    for base_dir, depth in scan_dirs:
        for filepath in _walk_limited(base_dir, depth):
            sig = EMU_SIGNATURES.get(filepath.name.lower())
            if sig and str(filepath) not in seen_paths:
                seen_paths.add(str(filepath))
                found.append({
                    'exe':       str(filepath),
                    'name':      sig['name'],
                    'consoles':  sig['consoles'],
                    'args_flag': sig['args_flag'],
                })
    return found


# ══════════════════════════════════════════════════════
# ROM SCANNING
# ══════════════════════════════════════════════════════
def _clean_title(filename: str) -> str:
    name = Path(filename).stem
    name = re.sub(r'\[.*?\]', '', name)
    name = re.sub(r'\(.*?\)', '', name)
    name = re.sub(r'-decrypted', '', name, flags=re.IGNORECASE)
    name = re.sub(r'[-_]', ' ', name)
    return re.sub(r'\s+', ' ', name).strip()


def _scan_roms(settings: dict) -> list:
    results  = []
    seen_ids = set()

    # ── Known consoles ────────────────────────────────
    rom_paths = settings.get('rom_paths', DEFAULT_ROM_PATHS)
    for console, base_str in rom_paths.items():
        if isinstance(base_str, dict):
            base_str = base_str.get('path', '')
        if not base_str:
            continue
        base = Path(base_str)
        if not base.exists():
            continue
        valid_exts = CONSOLE_EXTS.get(console, set())

        try:
            all_files = list(base.rglob('*'))
        except (OSError, PermissionError):
            continue

        for path in all_files:
            try:
                if not path.is_file():
                    continue
                rel_parts = path.relative_to(base).parts
                if any(p.lower() == 'updates' for p in rel_parts):
                    continue
                if path.suffix.lower() not in valid_exts:
                    continue
                rom_id = str(abs(hash(str(path))))
                if rom_id in seen_ids:
                    continue
                seen_ids.add(rom_id)
                stat = path.stat()
                results.append({
                    'id':       rom_id,
                    'title':    _clean_title(path.name),
                    'console':  console,
                    'filename': path.name,
                    'romPath':  str(path),
                    'added':    time.strftime('%Y-%m-%d', time.localtime(stat.st_mtime)),
                })
            except (OSError, PermissionError):
                continue

    # ── "Other" / custom emulators ────────────────────
    for entry in settings.get('other_emulators', []):
        console_key = entry.get('console_key', '')
        base_str    = entry.get('rom_path', '')
        raw_exts    = entry.get('extensions', '')
        if not console_key or not base_str:
            continue
        base = Path(base_str)
        if not base.exists():
            continue
        valid_exts = {e.strip().lower() for e in raw_exts.split(',') if e.strip()}

        try:
            all_files = list(base.rglob('*'))
        except (OSError, PermissionError):
            continue

        for path in all_files:
            try:
                if not path.is_file():
                    continue
                rel_parts = path.relative_to(base).parts
                if any(p.lower() == 'updates' for p in rel_parts):
                    continue
                if valid_exts and path.suffix.lower() not in valid_exts:
                    continue
                rom_id = str(abs(hash(str(path))))
                if rom_id in seen_ids:
                    continue
                seen_ids.add(rom_id)
                stat = path.stat()
                results.append({
                    'id':       rom_id,
                    'title':    _clean_title(path.name),
                    'console':  console_key,
                    'filename': path.name,
                    'romPath':  str(path),
                    'added':    time.strftime('%Y-%m-%d', time.localtime(stat.st_mtime)),
                    'isOther':  True,
                })
            except (OSError, PermissionError):
                continue

    results.sort(key=lambda r: r['title'].lower())
    return results


# ══════════════════════════════════════════════════════
# PYWEBVIEW JS API
# ══════════════════════════════════════════════════════
class EmuHubAPI:
    def __init__(self):
        self._window             = None
        self._watching           = False
        self._watch_thread       = None
        self._api_key            = None
        self._is_maximized       = False
        self._key_decrypt_failed = False
        self._load_key_internal()

    def set_window(self, win):
        self._window = win

    def _load_key_internal(self):
        try:
            enc = _load_settings().get('encrypted_api_key')
            if enc:
                result = _dpapi_decrypt(enc)
                if result:
                    self._api_key = result
                else:
                    self._key_decrypt_failed = True
        except Exception:
            self._api_key = None

    # ── Settings ─────────────────────────────────────
    def get_settings(self) -> dict:
        s = _load_settings()
        rom_paths = {
            c: {'path': p or '', 'exists': bool(p and Path(p).exists())}
            for c, p in s['rom_paths'].items()
        }
        emulators = {
            c: {**{k: v for k, v in e.items() if k not in _EPHEMERAL_KEYS},
                'exists': bool(e.get('exe') and Path(e['exe']).exists())}
            for c, e in s['emulators'].items()
        }
        # Annotate each other_emulator with live exists flags
        other = []
        for entry in s.get('other_emulators', []):
            e = dict(entry)
            e['exe_exists']      = bool(e.get('exe')      and Path(e['exe']).exists())
            e['rom_path_exists'] = bool(e.get('rom_path') and Path(e['rom_path']).exists())
            other.append(e)

        return {
            'rom_paths':          rom_paths,
            'emulators':          emulators,
            'other_emulators':    other,
            'has_api_key':        bool(self._api_key),
            'key_decrypt_failed': self._key_decrypt_failed,
        }

    def save_settings(self, data: dict) -> dict:
        try:
            s = _load_settings()

            if 'rom_paths' in data:
                for c, p in data['rom_paths'].items():
                    if isinstance(p, dict):
                        p = p.get('path', '')
                    s['rom_paths'][c] = str(p).strip()

            if 'emulators' in data:
                for c, e in data['emulators'].items():
                    if isinstance(e, dict):
                        safe = {k: v for k, v in e.items() if k not in _EPHEMERAL_KEYS}
                        if c in s['emulators']:
                            s['emulators'][c].update(safe)
                        else:
                            s['emulators'][c] = safe

            if 'other_emulators' in data:
                # Replace entire other_emulators list; strip ephemeral keys
                clean_other = []
                for entry in data['other_emulators']:
                    if isinstance(entry, dict):
                        clean_other.append({
                            k: v for k, v in entry.items()
                            if k not in _EPHEMERAL_KEYS
                            and k not in ('exe_exists', 'rom_path_exists')
                        })
                s['other_emulators'] = clean_other

            _save_settings(s)
            return {'ok': True}
        except Exception as ex:
            return {'ok': False, 'error': str(ex)}

    def validate_path(self, path: str) -> bool:
        try:
            return bool(path and Path(path.strip()).exists())
        except (OSError, ValueError):
            return False

    def browse_folder(self, title: str = 'Select Folder', initial: str = ''):
        if not self._window:
            return None
        try:
            init_str = str(initial).strip() if initial else ''
            init = init_str if init_str and Path(init_str).exists() else ''
            res  = self._window.create_file_dialog(
                webview.FOLDER_DIALOG, directory=init, allow_multiple=False)
            return res[0] if res else None
        except Exception:
            return None

    def browse_exe(self, initial: str = ''):
        if not self._window:
            return None
        try:
            p    = Path(initial.strip()) if initial else None
            init = str(p.parent) if p and p.exists() else ''
            res  = self._window.create_file_dialog(
                webview.OPEN_DIALOG, directory=init, allow_multiple=False,
                file_types=('Executables (*.exe)',))
            return res[0] if res else None
        except Exception:
            return None

    # ── Emulator detection ───────────────────────────
    def detect_emulators(self) -> dict:
        found    = _scan_for_emulators()
        settings = _load_settings()
        for emu in found:
            cfg = any(
                settings['emulators'].get(c, {}).get('exe', '').lower() == emu['exe'].lower()
                for c in emu['consoles']
            )
            emu['configured'] = cfg
        return {'found': found}

    def apply_detected_emulator(self, exe: str, consoles: list,
                                 name: str, args_flag: str) -> dict:
        try:
            s = _load_settings()
            for c in consoles:
                entry = {'name': name, 'exe': exe, 'args_flag': args_flag}
                if c in s['emulators']:
                    s['emulators'][c].update(entry)
                else:
                    s['emulators'][c] = entry
            _save_settings(s)
            return {'ok': True}
        except Exception as ex:
            return {'ok': False, 'error': str(ex)}

    # ── Other emulators (custom) ─────────────────────
    def add_other_emulator(self) -> dict:
        """Add a blank Other emulator entry and return its index."""
        try:
            s = _load_settings()
            import uuid
            new_entry = {
                'id':          str(uuid.uuid4())[:8],
                'console_key': '',
                'name':        'Custom Emulator',
                'exe':         '',
                'args_flag':   '',
                'rom_path':    '',
                'extensions':  '',
            }
            s.setdefault('other_emulators', []).append(new_entry)
            _save_settings(s)
            return {'ok': True, 'entry': new_entry}
        except Exception as ex:
            return {'ok': False, 'error': str(ex)}

    def remove_other_emulator(self, entry_id: str) -> dict:
        try:
            s = _load_settings()
            s['other_emulators'] = [
                e for e in s.get('other_emulators', [])
                if e.get('id') != entry_id
            ]
            _save_settings(s)
            return {'ok': True}
        except Exception as ex:
            return {'ok': False, 'error': str(ex)}

    # ── API Key ─────────────────────────────────────
    def save_api_key(self, key: str) -> dict:
        key = (key or '').strip()
        if not key:
            return {'ok': False, 'error': 'Key cannot be empty'}
        try:
            enc = _dpapi_encrypt(key)
            if not enc:
                return {'ok': False, 'error': (
                    'Windows DPAPI encryption failed.\n'
                    'This can happen in restricted/sandboxed environments.\n'
                    'Your key has NOT been saved.')}
            s = _load_settings()
            s['encrypted_api_key'] = enc
            if not _save_settings(s):
                return {'ok': False, 'error': 'Failed to write settings file'}
            self._api_key = key
            self._key_decrypt_failed = False
            return {'ok': True}
        except Exception as ex:
            return {'ok': False, 'error': str(ex)}

    def delete_api_key(self) -> dict:
        try:
            s = _load_settings()
            s.pop('encrypted_api_key', None)
            if not _save_settings(s):
                return {'ok': False, 'error': 'Failed to write settings file'}
            self._api_key = None
            self._key_decrypt_failed = False
            return {'ok': True}
        except Exception as ex:
            return {'ok': False, 'error': str(ex)}

    def has_api_key(self) -> bool:
        return bool(self._api_key)

    # ── Art fetching ─────────────────────────────────
    def fetch_art(self, title: str) -> dict:
        if not self._api_key:
            return {'url': None, 'error': None, 'bad_key': False}
        hdrs = {'Authorization': f'Bearer {self._api_key}', 'User-Agent': 'EmuHub/1.0'}
        def _get(url):
            return urllib.request.urlopen(
                urllib.request.Request(url, headers=hdrs), timeout=8)
        try:
            with _get('https://www.steamgriddb.com/api/v2/search/autocomplete/'
                      + urllib.parse.quote(title)) as r:
                sd = json.loads(r.read())
            if not sd.get('data'):
                return {'url': None, 'error': None, 'bad_key': False}
            gid = sd['data'][0]['id']
            with _get(f'https://www.steamgriddb.com/api/v2/grids/game/{gid}'
                      '?dimensions=600x900&limit=5') as r2:
                gd = json.loads(r2.read())
            grids = gd.get('data') or []
            for g in grids:
                if not g.get('is_animated', False):
                    return {'url': g['url'], 'error': None, 'bad_key': False}
            return {'url': grids[0]['url'] if grids else None, 'error': None, 'bad_key': False}
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return {'url': None, 'error': 'Invalid or expired API key', 'bad_key': True}
            if e.code == 429:
                return {'url': None, 'error': 'Rate limited — try again shortly', 'bad_key': False}
            return {'url': None, 'error': f'HTTP {e.code}', 'bad_key': False}
        except urllib.error.URLError as e:
            return {'url': None, 'error': f'Network error: {e.reason}', 'bad_key': False}
        except Exception as ex:
            return {'url': None, 'error': str(ex), 'bad_key': False}

    # ── ROM scanning ─────────────────────────────────
    def scan_roms(self) -> list:
        return _scan_roms(_load_settings())

    # ── Game launch ──────────────────────────────────
    def launch_game(self, console: str, rom_path: str) -> dict:
        settings = _load_settings()

        # ── Resolve emulator config ───────────────────
        # Check known consoles first, then other_emulators
        emu = settings['emulators'].get(console)
        if not emu:
            for entry in settings.get('other_emulators', []):
                if entry.get('console_key') == console:
                    emu = entry
                    break

        if not emu:
            return {'ok': False, 'error': (
                f'No emulator configured for "{console}".\n'
                f'Open ⚙ Settings to add one.')}

        exe = emu.get('exe', '')
        if not exe or not Path(exe).exists():
            return {'ok': False, 'error': (
                f'Emulator executable not found:\n{exe}\n\n'
                f'Update the path in ⚙ Settings → Emulators.')}

        if not Path(rom_path).exists():
            return {'ok': False, 'error': f'ROM file not found:\n{rom_path}'}

        # ── File type validation ──────────────────────
        # Reject the launch if the file extension is not in the
        # emulator's supported extension list.
        ok, err = _validate_rom_extension(console, rom_path, settings)
        if not ok:
            return {'ok': False, 'error': err}

        try:
            flag = emu.get('args_flag', '')
            args = [flag, rom_path] if flag else [rom_path]
            subprocess.Popen(
                [exe] + args,
                cwd=str(Path(exe).parent),
                creationflags=0x00000008,
                close_fds=True,
            )
            return {'ok': True}
        except PermissionError:
            return {'ok': False, 'error': (
                f'Permission denied launching:\n{exe}\n\n'
                f'Try running EmuHub as Administrator.')}
        except Exception as ex:
            return {'ok': False, 'error': str(ex)}

    # ── File watching ────────────────────────────────
    def start_watching(self):
        if self._watching:
            return
        self._watching = True
        self._watch_thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._watch_thread.start()

    def _watch_loop(self):
        def snapshot():
            try:
                files = {}
                s = _load_settings()
                all_paths = list(s.get('rom_paths', {}).values())
                for entry in s.get('other_emulators', []):
                    rp = entry.get('rom_path', '')
                    if rp:
                        all_paths.append(rp)
                for p in all_paths:
                    if isinstance(p, dict):
                        p = p.get('path', '')
                    if p and Path(p).exists():
                        try:
                            for f in Path(p).rglob('*'):
                                try:
                                    if f.is_file():
                                        files[str(f)] = f.stat().st_mtime
                                except (OSError, PermissionError):
                                    pass
                        except (OSError, PermissionError):
                            pass
                return files
            except Exception:
                return {}

        last = snapshot()
        while self._watching:
            time.sleep(2)
            current = snapshot()
            if current != last:
                last = current
                if self._window:
                    try:
                        self._window.evaluate_js(
                            "window.__onRomsChanged && window.__onRomsChanged()")
                    except Exception:
                        pass

    # ── Window controls ──────────────────────────────
    def minimize(self):
        if self._window: self._window.minimize()

    def toggle_maximize(self):
        if not self._window: return
        if self._is_maximized:
            self._window.restore()
            self._is_maximized = False
        else:
            self._window.maximize()
            self._is_maximized = True

    def resize_grip(self, edge: str):
        SC_SIZE = 0xF000
        SC_SIZE_DIRS = {'n':8,'s':2,'e':6,'w':7,'nw':13,'ne':14,'sw':16,'se':17}
        direction = SC_SIZE_DIRS.get(edge.lower())
        if direction is None:
            return
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            ctypes.windll.user32.ReleaseCapture()
            ctypes.windll.user32.SendMessageW(hwnd, 0x0112, SC_SIZE + direction, 0)
        except Exception:
            pass

    def close_window(self):
        if self._window: self._window.destroy()


# ══════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════
def _html_path() -> str:
    base = sys._MEIPASS if getattr(sys, 'frozen', False) \
           else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, 'web', 'index.html')


def _check_webview2() -> bool:
    import winreg
    keys = [
        (winreg.HKEY_LOCAL_MACHINE,
         r'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients'
         r'\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'),
        (winreg.HKEY_LOCAL_MACHINE,
         r'SOFTWARE\Microsoft\EdgeUpdate\Clients'
         r'\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'),
        (winreg.HKEY_CURRENT_USER,
         r'Software\Microsoft\EdgeUpdate\Clients'
         r'\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'),
    ]
    for hive, path in keys:
        try:
            winreg.OpenKey(hive, path)
            return True
        except OSError:
            pass
    return False


def _show_webview2_error():
    import ctypes
    msg = (
        "EmuHub requires the Microsoft Edge WebView2 Runtime, "
        "which is not installed on this machine.\n\n"
        "To fix this:\n"
        "  1. Visit: https://developer.microsoft.com/en-us/microsoft-edge/webview2/\n"
        "  2. Download and run the 'Evergreen Bootstrapper'\n"
        "  3. Launch EmuHub again\n\n"
        "Alternatively, re-run BUILD.bat — it will install WebView2 automatically."
    )
    ctypes.windll.user32.MessageBoxW(0, msg, "EmuHub — Missing Dependency", 0x10)


def main():
    # Gate on WebView2 before creating any pywebview window.
    if not _check_webview2():
        _show_webview2_error()
        sys.exit(1)

    api = EmuHubAPI()
    win = webview.create_window(
        title='EmuHub', url=_html_path(), js_api=api,
        width=1280, height=820, min_size=(900, 600),
        frameless=True, background_color='#0a0a0f', easy_drag=True)
    api.set_window(win)
    webview.start(debug=False)


if __name__ == '__main__':
    main()
