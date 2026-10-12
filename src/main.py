"""
EmuHub — Unified Emulator Launcher
Python + pywebview backend.
"""

import sys, os, json, re, threading, subprocess, time, shutil, shlex, webbrowser
import ctypes, ctypes.wintypes, base64, hashlib, http.client
import urllib.request, urllib.parse, urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import webview

try:
    from controller import ControllerService
except Exception:               # controller support is optional — never block startup
    ControllerService = None

try:
    import taskbar
except Exception:               # cosmetic — never block startup
    taskbar = None

# Keep in step with version_info.txt — that file stamps the exe, this one is
# what the update check compares against the latest GitHub release.
APP_VERSION = '1.3.0-beta'
GITHUB_REPO = 'Tw1tchy0/EmuHub'

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

# Box art lives beside settings: a JSON index of title -> resolved URL, plus the
# downloaded cover files themselves so repeat launches never touch the network.
ART_INDEX_FILE = SETTINGS_DIR / 'art_cache.json'
COVERS_DIR     = SETTINGS_DIR / 'covers'

# Per-game data — favourites, play history, renames, launch overrides. Kept
# apart from settings.json because it is written on every launch and exit,
# and a bad write here should never be able to take the configuration with it.
LIBRARY_FILE   = SETTINGS_DIR / 'library.json'

# Seconds between ROM-folder change checks.
WATCH_INTERVAL = 5

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
                data.setdefault('onboarding_complete', True)   # existing file = existing user, skip tutorial
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


def _stable_id(path) -> str:
    """Identify a ROM by its path, consistently across runs.

    Python salts str.__hash__ per process, so the previous hash()-based ids
    changed on every launch — fine while nothing persisted them, but it would
    quietly break anything stored per game (favourites, play counts, overrides).
    """
    return hashlib.sha1(str(path).casefold().encode('utf-8', 'replace')).hexdigest()[:16]


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
                rom_id = _stable_id(path)
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
                rom_id = _stable_id(path)
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
# BOX ART CACHE
# ══════════════════════════════════════════════════════
# Two layers, both on disk under %APPDATA%\EmuHub:
#   art_cache.json — title -> {url, file, ts}. Stores misses (url=None) too, so
#                    titles SteamGridDB doesn't recognise stop being re-queried
#                    on every launch. Misses expire so art added later is found.
#   covers\        — the actual image files, so a warm library paints without
#                    touching the network at all.
SGDB_HOST     = 'www.steamgriddb.com'
ART_MISS_TTL  = 30 * 24 * 3600     # re-check "no art" titles after 30 days
ART_MAX_BYTES = 8 * 1024 * 1024    # refuse absurdly large downloads
ART_WORKERS   = 6                  # parallel lookups per batch
ART_EXTS      = ('.png', '.jpg', '.jpeg', '.webp')

_art_lock  = threading.Lock()
_art_index = None                  # lazy-loaded dict
_art_dirty = False
_conn_tls  = threading.local()     # one kept-alive HTTPS connection per worker


def _art_key(title: str) -> str:
    return hashlib.sha1(title.encode('utf-8', 'replace')).hexdigest()[:20]


def _load_art_index() -> dict:
    global _art_index
    if _art_index is None:
        try:
            with open(ART_INDEX_FILE, encoding='utf-8') as f:
                data = json.load(f)
            _art_index = data if isinstance(data, dict) else {}
        except Exception:
            _art_index = {}
    return _art_index


def _save_art_index():
    """Write the index out. Callers hold _art_lock."""
    global _art_dirty
    if not _art_dirty or _art_index is None:
        return
    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = ART_INDEX_FILE.with_suffix('.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(_art_index, f)
        os.replace(tmp, ART_INDEX_FILE)
        _art_dirty = False
    except Exception:
        pass


def _sgdb_get(path: str, hdrs: dict):
    """GET from SteamGridDB over a connection this thread keeps alive.

    urllib opens — and throws away — a fresh TCP+TLS connection per call, which
    is most of the latency when resolving art one title at a time. Reusing the
    connection removes a full handshake from every request after the first.
    Returns (status, body_bytes).
    """
    for attempt in (0, 1):
        conn = getattr(_conn_tls, 'sgdb', None)
        if conn is None:
            conn = http.client.HTTPSConnection(SGDB_HOST, timeout=8)
            _conn_tls.sgdb = conn
        try:
            conn.request('GET', path, headers=hdrs)
            resp = conn.getresponse()
            body = resp.read()      # must drain for the socket to be reusable
            return resp.status, body
        except Exception:
            # Stale keep-alive socket — drop it and retry once on a fresh one.
            try:
                conn.close()
            except Exception:
                pass
            _conn_tls.sgdb = None
            if attempt:
                raise
    raise RuntimeError('unreachable')


def _download_cover(url: str, title: str) -> str | None:
    """Fetch the image bytes into covers\\ and return the local path."""
    try:
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        if ext not in ART_EXTS:
            ext = '.png'
        dest = COVERS_DIR / (_art_key(title) + ext)
        if dest.exists() and dest.stat().st_size > 0:
            return str(dest)

        req = urllib.request.Request(url, headers={'User-Agent': 'EmuHub/1.0'})
        with urllib.request.urlopen(req, timeout=15) as r:
            if not r.headers.get('Content-Type', '').startswith('image/'):
                return None
            data = r.read(ART_MAX_BYTES + 1)
        if not data or len(data) > ART_MAX_BYTES:
            return None

        COVERS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + '.tmp')
        with open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, dest)
        return str(dest)
    except Exception:
        return None


def _set_art_entry(title: str, entry: dict | None):
    """Replace a title's art index entry, deleting the cover file it drops.

    Callers hold _art_lock.
    """
    global _art_dirty
    idx = _load_art_index()
    old = idx.get(title) or {}
    old_file = old.get('file')
    if entry is None:
        idx.pop(title, None)
    else:
        idx[title] = entry
    _art_dirty = True
    _save_art_index()
    new_file = (entry or {}).get('file')
    if old_file and old_file != new_file:
        try:
            os.remove(old_file)
        except OSError:
            pass


# ══════════════════════════════════════════════════════
# GAME LIBRARY DATA
# ══════════════════════════════════════════════════════
# library.json — {"version": 1, "games": {rom_id: {...}}}, keyed by the stable
# ROM id. An entry holds only what has been set for that game:
#   favorite, hidden   bool
#   title              display name replacing the one cleaned from the filename
#   launch             {"exe": str, "args": str} overriding the console default
#   plays, last_played, seconds   play history
GAME_META_KEYS = ('favorite', 'hidden', 'title', 'launch')
TITLE_MAX      = 200
# A run shorter than this is treated as a launcher handing off to another
# process rather than play. Its launch still counts; its time can't be followed.
MIN_SESSION    = 10
SESSION_TICK   = 60                # credit time while running, not just on exit

_lib_lock = threading.Lock()
_lib_data = None


def _load_library() -> dict:
    """Callers hold _lib_lock."""
    global _lib_data
    if _lib_data is None:
        games = {}
        try:
            with open(LIBRARY_FILE, encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get('games'), dict):
                games = data['games']
        except FileNotFoundError:
            pass
        except Exception:
            # Unreadable — keep a copy rather than silently overwriting a
            # player's history the next time anything is saved.
            try:
                shutil.copy2(LIBRARY_FILE, LIBRARY_FILE.with_suffix('.json.bak'))
            except Exception:
                pass
        _lib_data = {'version': 1, 'games': games}
    return _lib_data


def _save_library() -> bool:
    """Atomic write. Callers hold _lib_lock."""
    if _lib_data is None:
        return True
    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = LIBRARY_FILE.with_suffix('.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(_lib_data, f, indent=1)
        os.replace(tmp, LIBRARY_FILE)
        return True
    except Exception:
        return False


def _public_meta(g: dict | None) -> dict:
    """The per-game fields the frontend sees, with defaults filled in."""
    g = g or {}
    launch = g.get('launch') or {}
    return {
        'favorite':   bool(g.get('favorite')),
        'hidden':     bool(g.get('hidden')),
        'customTitle': g.get('title') or None,
        'plays':      int(g.get('plays') or 0),
        'lastPlayed': g.get('last_played'),
        'seconds':    int(g.get('seconds') or 0),
        'launch':     {'exe': launch.get('exe', ''), 'args': launch.get('args', '')},
    }


def _clean_meta_patch(patch: dict) -> dict:
    """Validate a frontend edit. Unknown keys are dropped; '' or None clears."""
    out = {}
    for k in GAME_META_KEYS:
        if k not in (patch or {}):
            continue
        v = patch[k]
        if k in ('favorite', 'hidden'):
            out[k] = bool(v)
        elif k == 'title':
            out[k] = str(v or '').strip()[:TITLE_MAX] or None
        elif k == 'launch':
            v = v if isinstance(v, dict) else {}
            exe  = str(v.get('exe')  or '').strip()
            args = str(v.get('args') or '').strip()
            out[k] = {'exe': exe, 'args': args} if (exe or args) else None
    return out


def _apply_meta_patch(rom_id: str, patch: dict) -> dict:
    """Merge a cleaned patch into a game's entry. Callers hold _lib_lock."""
    games = _load_library()['games']
    g = dict(games.get(rom_id) or {})
    for k, v in patch.items():
        if v in (None, False):
            g.pop(k, None)          # keep the file to what's actually been set
        else:
            g[k] = v
    if g:
        games[rom_id] = g
    else:
        games.pop(rom_id, None)
    return g


def _split_args(s: str) -> list:
    """Split a user-typed argument string the way a Windows shell would.

    shlex in POSIX mode treats backslashes as escapes, which mangles Windows
    paths; non-POSIX mode keeps them but leaves the quotes on, so strip those.
    """
    out = []
    for tok in shlex.split(s or '', posix=False):
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in '"\'':
            tok = tok[1:-1]
        if tok:
            out.append(tok)
    return out


def _build_argv(exe: str, flag: str, custom_args: str, rom_path: str) -> list:
    """Command line for a launch.

    Custom arguments may place the ROM with {rom}; without it the ROM goes last.
    The default flag is split too — detected flags like DuckStation's
    '-fullscreen --' used to reach the emulator as one quoted argument.
    """
    if custom_args:
        toks = _split_args(custom_args)
        if any('{rom}' in t for t in toks):
            return [exe] + [t.replace('{rom}', rom_path) for t in toks]
        return [exe] + toks + [rom_path]
    return [exe] + _split_args(flag) + [rom_path]


def _version_tuple(v: str):
    """'v1.2.0-beta' -> (1, 2, 0, 0); a final release outranks its pre-release."""
    m = re.match(r'\s*v?(\d+)\.(\d+)(?:\.(\d+))?(?:[-+.]?(.+))?', v or '')
    if not m:
        return None
    major, minor, patch, pre = m.groups()
    return (int(major), int(minor), int(patch or 0), 0 if pre else 1)


# ══════════════════════════════════════════════════════
# WINDOW GEOMETRY
# ══════════════════════════════════════════════════════
WIN_DEFAULT = {'width': 1280, 'height': 820}
WIN_MIN     = (900, 600)


def _rect_on_screen(x: int, y: int, w: int, h: int) -> bool:
    """True if the rectangle overlaps a connected monitor. A window saved on a
    display that's since been unplugged would otherwise open out of reach."""
    try:
        r = ctypes.wintypes.RECT(x, y, x + w, y + h)
        MONITOR_DEFAULTTONULL = 0
        return bool(ctypes.windll.user32.MonitorFromRect(ctypes.byref(r),
                                                          MONITOR_DEFAULTTONULL))
    except Exception:
        return False


def _saved_geometry(settings: dict) -> dict | None:
    """The stored window geometry, or None if there's nothing usable."""
    g = settings.get('window')
    if not isinstance(g, dict):
        return None
    try:
        w = max(int(g['width']),  WIN_MIN[0])
        h = max(int(g['height']), WIN_MIN[1])
    except (KeyError, TypeError, ValueError):
        return None
    out = {'width': w, 'height': h, 'maximized': bool(g.get('maximized'))}
    try:
        x, y = int(g['x']), int(g['y'])
        if _rect_on_screen(x, y, w, h):
            out['x'], out['y'] = x, y
    except (KeyError, TypeError, ValueError):
        pass
    return out


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
        self._art_pool           = None
        self._normal_geo         = None   # size/position to restore to
        self._geo_saved          = False
        self._pads               = None   # ControllerService, once the window exists
        self._load_key_internal()

    def set_window(self, win):
        self._window = win
        if ControllerService and self._pads is None:
            self._pads = ControllerService(self._pad_emit)
            self._pads.start()

    # ── Controllers ──────────────────────────────────
    def _pad_emit(self, evt: dict):
        if self._window:
            self._window.evaluate_js(
                f'window.__padEvent && window.__padEvent({json.dumps(evt)})')

    def controller_status(self) -> dict:
        if not self._pads:
            return {'backend': None, 'error': 'Controller support is unavailable in this build.'}
        return self._pads.status()

    def controller_state(self) -> list:
        return self._pads.snapshot() if self._pads else []

    def controller_active(self, active: bool):
        """The page reports focus: controllers are only held while it has it."""
        if self._pads:
            self._pads.set_active(bool(active))

    def controller_rumble(self, device_id: int):
        if self._pads:
            self._pads.rumble(device_id)

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
            'rom_paths':            rom_paths,
            'emulators':            emulators,
            'other_emulators':      other,
            'has_api_key':          bool(self._api_key),
            'key_decrypt_failed':   self._key_decrypt_failed,
            'onboarding_complete':  s.get('onboarding_complete', False),
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

    def complete_onboarding(self) -> dict:
        try:
            s = _load_settings()
            s['onboarding_complete'] = True
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
                webview.FileDialog.FOLDER, directory=init, allow_multiple=False)
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
                webview.FileDialog.OPEN, directory=init, allow_multiple=False,
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
    def _cached_art(self, title: str) -> dict | None:
        """Return a usable cache entry for this title, or None to go online."""
        with _art_lock:
            entry = _load_art_index().get(title)
        if not entry:
            return None
        url   = entry.get('url')
        local = entry.get('file')
        # Checked before the miss logic: a cover picked from an image file has
        # a file but no URL, and must not be mistaken for a remembered miss.
        if local and os.path.isfile(local):
            return {'url': url, 'local': Path(local).as_uri(),
                    'custom': bool(entry.get('pinned')),
                    'error': None, 'bad_key': False}
        if not url:
            if entry.get('pinned'):
                return None         # the chosen file is gone — resolve as normal
            # Remembered miss — honour it until it goes stale.
            if time.time() - entry.get('ts', 0) < ART_MISS_TTL:
                return {'url': None, 'local': None, 'error': None, 'bad_key': False}
            return None
        # Known URL but the file is gone (or predates local caching) — re-pull it.
        local = _download_cover(url, title)
        if local:
            with _art_lock:
                global _art_dirty
                _load_art_index()[title] = {'url': url, 'file': local, 'ts': time.time()}
                _art_dirty = True
        return {'url': url, 'local': Path(local).as_uri() if local else None,
                'error': None, 'bad_key': False}

    def _resolve_art(self, title: str) -> dict:
        """Cache lookup, then SteamGridDB. Safe to run on a worker thread."""
        hit = self._cached_art(title)
        if hit is not None:
            return hit

        hdrs = {'Authorization': f'Bearer {self._api_key}', 'User-Agent': 'EmuHub/1.0'}
        try:
            status, body = _sgdb_get(
                '/api/v2/search/autocomplete/' + urllib.parse.quote(title), hdrs)
            if status == 401:
                return {'url': None, 'local': None,
                        'error': 'Invalid or expired API key', 'bad_key': True}
            if status == 429:
                return {'url': None, 'local': None,
                        'error': 'Rate limited — try again shortly', 'bad_key': False}
            if status != 200:
                return {'url': None, 'local': None,
                        'error': f'HTTP {status}', 'bad_key': False}

            sd  = json.loads(body)
            url = None
            if sd.get('data'):
                gid = sd['data'][0]['id']
                status, body = _sgdb_get(
                    f'/api/v2/grids/game/{gid}?dimensions=600x900&limit=5', hdrs)
                if status == 401:
                    return {'url': None, 'local': None,
                            'error': 'Invalid or expired API key', 'bad_key': True}
                if status == 200:
                    grids = (json.loads(body).get('data') or [])
                    still = [g for g in grids if not g.get('is_animated', False)]
                    pick  = (still or grids or [None])[0]
                    url   = pick.get('url') if pick else None

            local = _download_cover(url, title) if url else None
            # Record misses as well as hits — that is what stops unrecognised
            # titles from being looked up again on every single launch.
            with _art_lock:
                global _art_dirty
                _load_art_index()[title] = {'url': url, 'file': local, 'ts': time.time()}
                _art_dirty = True
            return {'url': url, 'local': Path(local).as_uri() if local else None,
                    'error': None, 'bad_key': False}
        except urllib.error.URLError as e:
            return {'url': None, 'local': None,
                    'error': f'Network error: {e.reason}', 'bad_key': False}
        except Exception as ex:
            return {'url': None, 'local': None, 'error': str(ex), 'bad_key': False}

    def fetch_art(self, title: str) -> dict:
        if not self._api_key:
            return {'url': None, 'local': None, 'error': None, 'bad_key': False}
        res = self._resolve_art(title)
        with _art_lock:
            _save_art_index()
        return res

    def fetch_art_batch(self, titles: list) -> dict:
        """Resolve several titles at once.

        Lookups run in parallel and share this process, so a batch costs about
        as much as the slowest single title rather than the sum of all of them.
        Returns {'results': {title: {url, local}}, 'error': str|None, 'bad_key': bool}
        """
        titles = [t for t in dict.fromkeys(titles or []) if t]
        if not titles:
            return {'results': {}, 'error': None, 'bad_key': False}
        if not self._api_key:
            # No key means no new lookups — but covers already on disk,
            # including ones the user picked by hand, should still show.
            results = {}
            for t in titles:
                hit = self._cached_art(t) or {}
                results[t] = {'url': hit.get('url'), 'local': hit.get('local'),
                              'custom': bool(hit.get('custom'))}
            with _art_lock:
                _save_art_index()
            return {'results': results, 'error': None, 'bad_key': False}

        # One long-lived pool: its worker threads persist between batches, and
        # so do the kept-alive connections they hold. A per-batch pool would
        # hand every batch a fresh set of threads and re-handshake each time.
        if self._art_pool is None:
            self._art_pool = ThreadPoolExecutor(
                max_workers=ART_WORKERS, thread_name_prefix='emuhub-art')

        results, error, bad_key = {}, None, False
        for title, res in zip(titles, self._art_pool.map(self._resolve_art, titles)):
            results[title] = {'url': res.get('url'), 'local': res.get('local'),
                              'custom': bool(res.get('custom'))}
            if res.get('bad_key'):
                bad_key = True
            if res.get('error') and not error:
                error = res['error']
        with _art_lock:
            _save_art_index()
        return {'results': results, 'error': error, 'bad_key': bad_key}

    def clear_art_cache(self) -> dict:
        """Forget every automatically resolved title. Used when the API key
        changes, since misses recorded under a rejected key would otherwise
        stick around. Covers the user chose by hand are kept."""
        global _art_index, _art_dirty
        try:
            with _art_lock:
                keep = {t: e for t, e in _load_art_index().items() if e.get('pinned')}
                _art_index = keep
                _art_dirty = True
                _save_art_index()
            keep_files = {os.path.normcase(os.path.abspath(e['file']))
                          for e in keep.values() if e.get('file')}
            if COVERS_DIR.is_dir():
                for f in COVERS_DIR.iterdir():
                    if os.path.normcase(os.path.abspath(f)) not in keep_files:
                        try:
                            f.unlink()
                        except OSError:
                            pass
            return {'ok': True}
        except Exception as ex:
            return {'ok': False, 'error': str(ex)}

    # ── Using your own cover image ───────────────────
    def browse_image(self):
        """Pick an image file. Returns {path, preview} without copying it yet."""
        if not self._window:
            return None
        try:
            res = self._window.create_file_dialog(
                webview.FileDialog.OPEN, allow_multiple=False,
                file_types=('Images (*.png;*.jpg;*.jpeg;*.webp)',))
            if not res:
                return None
            p = Path(res[0])
            return {'path': str(p), 'preview': p.as_uri()}
        except Exception:
            return None

    def set_custom_art_file(self, title: str, path: str) -> dict:
        """Pin an image from disk to a title. The file is copied into covers\\
        so moving or deleting the original doesn't take the cover with it."""
        try:
            src = Path(path or '')
            ext = src.suffix.lower()
            if not title or ext not in ART_EXTS or not src.is_file():
                return {'ok': False, 'error': 'Choose a PNG, JPG or WebP image.'}
            data = src.read_bytes()
            if not data or len(data) > ART_MAX_BYTES:
                return {'ok': False, 'error': 'That image is empty or larger than 8 MB.'}
            COVERS_DIR.mkdir(parents=True, exist_ok=True)
            dest = COVERS_DIR / f'{_art_key(title)}-file-{hashlib.sha1(data).hexdigest()[:8]}{ext}'
            tmp  = dest.with_suffix(ext + '.tmp')
            tmp.write_bytes(data)
            os.replace(tmp, dest)
            with _art_lock:
                _set_art_entry(title, {'url': None, 'file': str(dest),
                                       'ts': time.time(), 'pinned': True})
            return {'ok': True, 'url': None, 'local': dest.as_uri(), 'custom': True}
        except Exception as ex:
            return {'ok': False, 'error': str(ex)}

    def rename_art(self, old: str, new: str) -> dict:
        """Carry a user-supplied cover over when its game is renamed. Art is
        keyed by title, so without this a rename would drop the chosen image."""
        try:
            with _art_lock:
                global _art_dirty
                idx = _load_art_index()
                entry = idx.get(old)
                if not (entry and entry.get('pinned')) or (idx.get(new) or {}).get('pinned'):
                    return {'ok': True, 'moved': False}
                idx[new] = entry
                idx.pop(old, None)
                _art_dirty = True
                _save_art_index()
            return {'ok': True, 'moved': True}
        except Exception as ex:
            return {'ok': False, 'error': str(ex)}

    def reset_art(self, title: str) -> dict:
        """Drop a title's cover so the next lookup picks one automatically."""
        try:
            with _art_lock:
                _set_art_entry(title, None)
            return {'ok': True}
        except Exception as ex:
            return {'ok': False, 'error': str(ex)}

    def seed_art_cache(self, entries: dict) -> dict:
        """One-time import of the old localStorage cache (title -> url)."""
        try:
            added = 0
            with _art_lock:
                global _art_dirty
                idx = _load_art_index()
                for title, url in (entries or {}).items():
                    if title and url and title not in idx:
                        idx[title] = {'url': url, 'file': None, 'ts': time.time()}
                        added += 1
                if added:
                    _art_dirty = True
                    _save_art_index()
            return {'ok': True, 'added': added}
        except Exception as ex:
            return {'ok': False, 'error': str(ex)}

    # ── ROM scanning ─────────────────────────────────
    def scan_roms(self) -> list:
        roms = _scan_roms(_load_settings())
        with _lib_lock:
            games = _load_library()['games']
            for r in roms:
                meta = _public_meta(games.get(r['id']))
                r['fileTitle'] = r['title']
                if meta['customTitle']:
                    r['title'] = meta['customTitle']
                r.update(meta)
        roms.sort(key=lambda r: r['title'].lower())    # renames change the order
        return roms

    # ── Per-game data ────────────────────────────────
    def set_game_meta(self, rom_id: str, patch: dict) -> dict:
        """Update favourite / hidden / title / launch override for one game."""
        if not rom_id or not isinstance(patch, dict):
            return {'ok': False, 'error': 'Invalid request'}
        clean = _clean_meta_patch(patch)
        launch = clean.get('launch')
        if launch and launch['exe'] and not Path(launch['exe']).is_file():
            return {'ok': False, 'error': f'Emulator not found:\n{launch["exe"]}'}
        with _lib_lock:
            g = _apply_meta_patch(rom_id, clean)
            if not _save_library():
                return {'ok': False, 'error': 'Could not write library.json'}
        return {'ok': True, 'meta': _public_meta(g)}

    def _credit_session(self, rom_id: str, seconds: int) -> dict:
        with _lib_lock:
            g = _load_library()['games'].setdefault(rom_id, {})
            g['seconds'] = int(g.get('seconds') or 0) + int(seconds)
            _save_library()
            return _public_meta(g)

    def _track_session(self, rom_id: str, proc):
        """Follow a launched emulator and add its running time to the game.

        Time is credited every SESSION_TICK seconds as well as at exit, so
        closing EmuHub mid-session loses at most one tick instead of all of it.
        """
        started  = time.monotonic()
        credited = 0
        while True:
            try:
                proc.wait(timeout=SESSION_TICK)
                exited = True
            except subprocess.TimeoutExpired:
                exited = False
            except Exception:
                return
            elapsed = int(time.monotonic() - started)
            if exited and credited == 0 and elapsed < MIN_SESSION:
                return              # launcher stub — the real process isn't ours to follow
            if elapsed > credited:
                meta = self._credit_session(rom_id, elapsed - credited)
                credited = elapsed
            if exited:
                break
        if self._window:
            try:
                self._window.evaluate_js(
                    'window.__onGameExited && window.__onGameExited('
                    f'{json.dumps(rom_id)}, {json.dumps(meta)})')
            except Exception:
                pass

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

        rom_id = _stable_id(Path(rom_path))
        with _lib_lock:
            override = (_load_library()['games'].get(rom_id) or {}).get('launch') or {}

        if not emu and not override.get('exe'):
            return {'ok': False, 'error': (
                f'No emulator configured for "{console}".\n'
                f'Open ⚙ Settings to add one.')}
        emu = emu or {}

        exe = override.get('exe') or emu.get('exe', '')
        if not exe or not Path(exe).exists():
            where = ('this game\'s launch options' if override.get('exe')
                     else '⚙ Settings → Emulators')
            return {'ok': False, 'error': (
                f'Emulator executable not found:\n{exe}\n\n'
                f'Update the path in {where}.')}

        if not Path(rom_path).exists():
            return {'ok': False, 'error': f'ROM file not found:\n{rom_path}'}

        # ── File type validation ──────────────────────
        # Reject the launch if the file extension is not in the
        # emulator's supported extension list.
        ok, err = _validate_rom_extension(console, rom_path, settings)
        if not ok:
            return {'ok': False, 'error': err}

        try:
            argv = _build_argv(exe, emu.get('args_flag', ''),
                               override.get('args', ''), rom_path)
            proc = subprocess.Popen(
                argv,
                cwd=str(Path(exe).parent),
                creationflags=0x00000008,
                close_fds=True,
            )
        except PermissionError:
            return {'ok': False, 'error': (
                f'Permission denied launching:\n{exe}\n\n'
                f'Try running EmuHub as Administrator.')}
        except Exception as ex:
            return {'ok': False, 'error': str(ex)}

        # Hand the controllers to the emulator straight away rather than
        # waiting for the focus change to reach the page.
        if self._pads:
            self._pads.set_active(False)

        # The launch counts whether or not its running time can be followed.
        with _lib_lock:
            g = _load_library()['games'].setdefault(rom_id, {})
            g['plays'] = int(g.get('plays') or 0) + 1
            g['last_played'] = int(time.time())
            _save_library()
            meta = _public_meta(g)
        threading.Thread(target=self._track_session, args=(rom_id, proc),
                         daemon=True, name='emuhub-session').start()
        return {'ok': True, 'meta': meta}

    # ── Updates ──────────────────────────────────────
    def check_for_update(self) -> dict:
        """Compare this build with the latest GitHub release.

        Uses the public releases API (60 unauthenticated requests per hour per
        IP — one per launch is nowhere near that). Fails quietly when offline.
        """
        try:
            req = urllib.request.Request(
                f'https://api.github.com/repos/{GITHUB_REPO}/releases/latest',
                headers={'User-Agent': f'EmuHub/{APP_VERSION}',
                         'Accept': 'application/vnd.github+json'})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.load(r)
            tag    = str(data.get('tag_name') or '')
            latest = _version_tuple(tag)
            mine   = _version_tuple(APP_VERSION)
            newer  = bool(latest and mine and latest > mine)
            dismissed = _load_settings().get('update_dismissed') == tag
            url = str(data.get('html_url') or '')
            if not url.startswith(f'https://github.com/{GITHUB_REPO}/'):
                url = f'https://github.com/{GITHUB_REPO}/releases/latest'
            return {'ok': True, 'available': newer and not dismissed,
                    'version': tag, 'current': APP_VERSION, 'url': url}
        except Exception as ex:
            return {'ok': False, 'error': str(ex), 'current': APP_VERSION}

    def dismiss_update(self, tag: str) -> dict:
        s = _load_settings()
        s['update_dismissed'] = str(tag or '')
        return {'ok': _save_settings(s)}

    def get_version(self) -> str:
        return APP_VERSION

    def open_url(self, url: str) -> bool:
        """Open a web page in the default browser. https only — this is reachable
        from page script, and shouldn't be a way to launch arbitrary handlers."""
        if not isinstance(url, str) or not url.startswith('https://'):
            return False
        try:
            return bool(webbrowser.open(url))
        except Exception:
            return False

    # ── File watching ────────────────────────────────
    def start_watching(self):
        if self._watching:
            return
        self._watching = True
        self._watch_thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._watch_thread.start()

    def _watch_loop(self):
        def snapshot():
            """Cheap signature of the ROM folders.

            Only directories are stat'd, not every file inside them: a folder's
            mtime already changes when a ROM is added, removed or renamed, and
            the file count catches two changes landing in the same second. The
            old version stat'd every file in the library every couple of
            seconds, which pinned the disk on large or networked collections.
            """
            try:
                sig = {}
                s = _load_settings()
                all_paths = list(s.get('rom_paths', {}).values())
                for entry in s.get('other_emulators', []):
                    rp = entry.get('rom_path', '')
                    if rp:
                        all_paths.append(rp)
                for p in all_paths:
                    if isinstance(p, dict):
                        p = p.get('path', '')
                    if not p or not Path(p).exists():
                        continue
                    try:
                        for dirpath, _dirnames, filenames in os.walk(p):
                            try:
                                sig[dirpath] = (os.stat(dirpath).st_mtime, len(filenames))
                            except (OSError, PermissionError):
                                pass
                    except (OSError, PermissionError):
                        pass
                return sig
            except Exception:
                return {}

        last = snapshot()
        while self._watching:
            time.sleep(WATCH_INTERVAL)
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
            # Snapshot first: pywebview reports the maximized position before
            # it reports the maximize itself, so the normal size can't be
            # recovered from events afterwards.
            self._normal_geo = self._current_geometry()
            self._window.maximize()
            self._is_maximized = True

    def is_maximized(self) -> bool:
        return self._is_maximized

    def _current_geometry(self) -> dict | None:
        try:
            w = self._window
            return {'width': int(w.width), 'height': int(w.height),
                    'x': int(w.x), 'y': int(w.y)}
        except Exception:
            return None

    def save_window_state(self):
        """Remember size, position and maximized state for the next launch."""
        if not self._window or self._geo_saved:
            return
        geo = self._normal_geo if self._is_maximized else self._current_geometry()
        if not geo:
            return
        geo = dict(geo)
        # Minimized windows report a position near -32000; keep the size only.
        x, y = geo.get('x'), geo.get('y')
        if x is None or y is None or not _rect_on_screen(x, y, geo['width'], geo['height']):
            geo.pop('x', None); geo.pop('y', None)
        geo['maximized'] = self._is_maximized
        s = _load_settings()
        s['window'] = geo
        _save_settings(s)
        self._geo_saved = True

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
        if not self._window: return
        self.save_window_state()
        self._window.destroy()


# ══════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════
def _html_path() -> str:
    base = sys._MEIPASS if getattr(sys, 'frozen', False) \
           else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, 'web', 'index.html')


def _html_url() -> str:
    """A file:// URI, deliberately — not the bare path.

    pywebview treats a bare path as a "local url" (webview.util.is_local_url)
    and boots a Bottle server for it, so the page ends up on
    http://127.0.0.1:<random port>. Chromium then refuses to load the
    file:// covers under %APPDATA% into that page, and the random port
    changes the origin on every launch, throwing away WebView2's image cache.
    A file:// URI skips the server: covers load, the origin is stable, and
    the js_api bridge is unaffected.
    """
    return Path(_html_path()).as_uri()


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

    # Only the packaged exe has something meaningful to pin; in dev mode the
    # "app" is python.exe running a script.
    pinnable = bool(taskbar and getattr(sys, 'frozen', False))
    if pinnable:
        taskbar.set_process_app_id()      # before the window exists

    api   = EmuHubAPI()
    saved = _saved_geometry(_load_settings())
    geo   = saved or WIN_DEFAULT
    win = webview.create_window(
        title='EmuHub', url=_html_url(), js_api=api,
        width=geo['width'], height=geo['height'],
        x=geo.get('x'), y=geo.get('y'), min_size=WIN_MIN,
        frameless=True, background_color='#0a0a0f', easy_drag=True)
    api.set_window(win)
    # Alt+F4 and closing from the taskbar don't go through close_window.
    win.events.closing += api.save_window_state
    webview.start(_on_start, (api, saved, pinnable), debug=False)


def _on_start(api, saved, pinnable):
    _restore_geometry(api, saved)
    if pinnable:
        _set_taskbar_identity()


def _own_window(timeout=5.0) -> int:
    """This process's main window handle, once it exists."""
    user32, me = ctypes.windll.user32, os.getpid()
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = []
        def cb(h, _):
            pid = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value == me and user32.IsWindowVisible(h) and not user32.GetWindow(h, 4):
                found.append(h)                  # visible, unowned: the main window
            return True
        user32.EnumWindows(proc(cb), 0)
        if found:
            return found[0]
        time.sleep(0.1)
    return 0


def _set_taskbar_identity():
    """Give the window an explicit relaunch target, so pinning it to the
    taskbar makes a pin that launches EmuHub.exe with its own icon."""
    try:
        hwnd = _own_window()
        if hwnd:
            taskbar.set_window_relaunch(hwnd, sys.executable)
    except Exception:
        pass


def _restore_geometry(api, saved):
    """Apply the saved geometry exactly, once the window exists.

    create_window treats width/height as including a border allowance that a
    frameless window doesn't have, so a saved 1250x780 would open as 1234x741 —
    and shrink a little more on every launch. resize() and move() use the same
    units the window reports, so the round trip is exact.
    """
    if not saved:
        return
    w = api._window
    try:
        w.resize(saved['width'], saved['height'])
        if 'x' in saved:
            w.move(saved['x'], saved['y'])
        if saved['maximized']:
            api.toggle_maximize()
            # The page may already be up and showing the "maximize" icon.
            w.evaluate_js('window.syncMaximizeBtn && syncMaximizeBtn(true)')
    except Exception:
        pass


if __name__ == '__main__':
    main()
