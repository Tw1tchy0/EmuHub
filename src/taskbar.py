"""
Taskbar identity for the packaged exe.

The one-file build runs as two processes: a launcher that unpacks the app,
and a child that owns the window. Without being told otherwise, Windows has
to guess what a pinned taskbar item should launch and which icon it shows —
and for this launcher/child pair it guessed wrong, leaving a pin with a blank
white page for an icon.

So both are spelled out: an explicit AppUserModelID for the process, and
relaunch properties on the window (the exe to run, its icon, its name). A pin
made from the running window then points straight at EmuHub.exe.

Windows-only, and best-effort: any failure leaves the default behaviour.
"""

import ctypes
import ctypes.wintypes as wt

APP_ID = 'Tw1tchy0.EmuHub'

_ole32 = ctypes.windll.ole32
_shell32 = ctypes.windll.shell32

# ctypes assumes an int return unless told otherwise, which truncates a 64-bit
# pointer — CoTaskMemAlloc's result then crashed the first write to it.
_ole32.CoTaskMemAlloc.restype = ctypes.c_void_p
_ole32.CoTaskMemAlloc.argtypes = [ctypes.c_size_t]
_shell32.SHGetPropertyStoreForWindow.argtypes = [wt.HWND, ctypes.c_void_p, ctypes.c_void_p]

VT_LPWSTR = 31
_AUMID_FMTID = '{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}'
PID_RELAUNCH_COMMAND = 2
PID_RELAUNCH_ICON = 3
PID_RELAUNCH_NAME = 4
PID_APP_ID = 5


class _GUID(ctypes.Structure):
    _fields_ = [('a', ctypes.c_uint32), ('b', ctypes.c_uint16),
                ('c', ctypes.c_uint16), ('d', ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, s):
        g = cls()
        _ole32.CLSIDFromString(ctypes.c_wchar_p(s), ctypes.byref(g))
        return g


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [('fmtid', _GUID), ('pid', ctypes.c_uint32)]


class _PROPVARIANT(ctypes.Structure):
    # vt + 3 reserved WORDs, then a 16-byte union (two pointers on x64).
    _fields_ = [('vt', ctypes.c_ushort), ('r1', ctypes.c_ushort),
                ('r2', ctypes.c_ushort), ('r3', ctypes.c_ushort),
                ('p', ctypes.c_void_p), ('p2', ctypes.c_void_p)]


_IID_IPropertyStore = _GUID.parse('{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}')


def set_process_app_id(app_id: str = APP_ID) -> bool:
    """Must run before the window is created to apply to it."""
    try:
        return _shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(app_id)) == 0
    except Exception:
        return False


def _string_variant(s: str) -> _PROPVARIANT:
    """A VT_LPWSTR PROPVARIANT. The string is CoTaskMem-allocated, as
    PropVariantClear expects; the store copies it, then the caller clears it."""
    buf = ctypes.create_unicode_buffer(s)
    size = ctypes.sizeof(buf)
    mem = _ole32.CoTaskMemAlloc(size)
    if not mem:
        raise MemoryError('CoTaskMemAlloc failed')
    ctypes.memmove(mem, buf, size)
    pv = _PROPVARIANT()
    pv.vt = VT_LPWSTR
    pv.p = mem
    return pv


def set_window_relaunch(hwnd: int, exe_path: str, display_name: str = 'EmuHub',
                        app_id: str = APP_ID) -> bool:
    """Tell the taskbar what a pin of this window should launch and look like."""
    _ole32.CoInitialize(None)          # this runs on a worker thread
    store = ctypes.c_void_p()
    hr = _shell32.SHGetPropertyStoreForWindow(
        wt.HWND(hwnd), ctypes.byref(_IID_IPropertyStore), ctypes.byref(store))
    if hr != 0 or not store:
        return False
    vtbl = ctypes.cast(ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0],
                       ctypes.POINTER(ctypes.c_void_p))
    # IPropertyStore: QueryInterface, AddRef, Release, GetCount, GetAt,
    #                 GetValue, SetValue, Commit
    Release  = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])
    SetValue = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                  ctypes.POINTER(_PROPERTYKEY),
                                  ctypes.POINTER(_PROPVARIANT))(vtbl[6])
    Commit   = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtbl[7])
    ok = True
    try:
        # The relaunch properties only take effect alongside an explicit ID,
        # and Windows reads them as a set — write them all, then commit.
        for pid, value in ((PID_RELAUNCH_COMMAND, f'"{exe_path}"'),
                           (PID_RELAUNCH_ICON, f'{exe_path},0'),
                           (PID_RELAUNCH_NAME, display_name),
                           (PID_APP_ID, app_id)):
            key = _PROPERTYKEY(_GUID.parse(_AUMID_FMTID), pid)
            pv = _string_variant(value)
            ok &= SetValue(store, ctypes.byref(key), ctypes.byref(pv)) == 0
            _ole32.PropVariantClear(ctypes.byref(pv))
        ok &= Commit(store) == 0
    finally:
        Release(store)
    return bool(ok)
