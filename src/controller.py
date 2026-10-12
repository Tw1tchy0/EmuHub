"""
Controller input through SDL2.

The browser's Gamepad API only gives a standard button layout for controllers
Chromium already knows, and it can't see a controller until a button is
pressed. SDL — the input library most emulators use — recognises far more
pads, and with the community GameControllerDB bundled in data/ it presents
over two thousand models in one layout (A/B/X/Y, D-pad, shoulders, sticks).

Everything SDL is done on one thread, as SDL requires. The page only hears
about navigation-relevant changes (a direction or button going down or up);
the controller test screen polls a full snapshot instead.

Controllers are opened only while EmuHub has focus. When it loses focus —
usually because an emulator just launched — every device is closed and the
subsystem shut down, so EmuHub never holds a controller the game is using.
"""

import os, sys, json, time, queue, threading, warnings, ctypes

DB_FILE = 'gamecontrollerdb.txt'

STICK_PRESS   = 0.55        # stick deflection that counts as a direction…
STICK_RELEASE = 0.35        # …and how far it has to come back to let go
POLL_INTERVAL = 0.008       # ~125 Hz while active
IDLE_INTERVAL = 0.05
# After focus comes back, how long a released controller stays listed while
# SDL finds it again — so it doesn't blink out of the test screen.
REACQUIRE_GRACE = 3.0

# Directions and buttons the page acts on. Everything else is test-screen only.
NAV_BUTTONS = {'a', 'b', 'x', 'y', 'lb', 'rb', 'back', 'start'}


def _data_path(name: str) -> str:
    base = sys._MEIPASS if getattr(sys, 'frozen', False) \
           else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, 'data', name)


def _load_sdl():
    """Import PySDL2, pointing it at the SDL2.dll bundled into the exe."""
    if getattr(sys, 'frozen', False):
        os.environ.setdefault('PYSDL2_DLL_PATH', os.path.join(sys._MEIPASS, 'sdl2'))
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')     # "Using SDL2 binaries from pysdl2-dll"
        import sdl2
    return sdl2


class ControllerService:
    def __init__(self, emit):
        """emit(event: dict) is called off the SDL thread for each page event."""
        self._emit_cb   = emit
        self._cmds      = queue.Queue()
        self._out       = queue.Queue()
        self._lock      = threading.Lock()
        self._devices   = {}      # instance id -> device record
        self._active    = True
        self._open      = False   # subsystem initialised
        self._seen      = set()   # GUIDs already announced this session
        self._parked    = []      # devices released on blur, shown until re-found
        self._parked_until = None
        self.backend    = None
        self.version    = ''
        self.mappings   = 0
        self.error      = None

    # ── public, any thread ───────────────────────────
    def start(self):
        threading.Thread(target=self._run, daemon=True, name='emuhub-sdl').start()
        threading.Thread(target=self._dispatch, daemon=True, name='emuhub-pad').start()

    def set_active(self, active: bool):
        self._cmds.put(('active', bool(active)))

    def rumble(self, instance_id: int):
        self._cmds.put(('rumble', int(instance_id)))

    def status(self) -> dict:
        return {'backend': self.backend, 'version': self.version,
                'mappings': self.mappings, 'error': self.error, 'active': self._active}

    def snapshot(self) -> list:
        with self._lock:
            live = [{k: v for k, v in d.items() if not k.startswith('_')}
                    for d in self._devices.values()]
            # Shown while EmuHub is in the background (no deadline yet) and
            # for a grace period after it comes back.
            until = self._parked_until
            parked = self._parked if (until is None or until > time.monotonic()) else []
            return json.loads(json.dumps(live + parked))

    # ── SDL thread ───────────────────────────────────
    def _run(self):
        try:
            self.sdl = sdl = _load_sdl()
            v = sdl.SDL_version(); sdl.SDL_GetVersion(v)
            self.version = f'{v.major}.{v.minor}.{v.patch}'
            # No SDL window exists, so without this SDL would treat every
            # event as "in the background" and drop it.
            sdl.SDL_SetHint(sdl.SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, b'1')
            sdl.SDL_SetHint(sdl.SDL_HINT_NO_SIGNAL_HANDLERS, b'1')
            # RawInput and Windows.Gaming.Input only deliver input to a window
            # SDL owns while it's in the foreground. EmuHub's window belongs to
            # the web view, so through those drivers an Xbox pad showed its
            # triggers (read via XInput) but no buttons or sticks. XInput, and
            # SDL's HIDAPI/DirectInput paths for other pads, have no such limit.
            sdl.SDL_SetHint(b'SDL_JOYSTICK_RAWINPUT', b'0')
            sdl.SDL_SetHint(b'SDL_JOYSTICK_WGI', b'0')
            # Leave controller lights alone — they're the emulator's to set.
            sdl.SDL_SetHint(b'SDL_JOYSTICK_HIDAPI_PS5_PLAYER_LED', b'0')
            sdl.SDL_SetHint(b'SDL_JOYSTICK_HIDAPI_SWITCH_PLAYER_LED', b'0')
            self.backend = 'sdl'
        except Exception as ex:
            self.error = f'SDL2 could not be loaded: {ex}'
            self._emit({'type': 'backend', 'backend': None, 'error': self.error})
            return

        if self._active:
            self._open_subsystem()
        self._emit({'type': 'backend', 'backend': 'sdl', 'error': self.error})

        ev = sdl.SDL_Event()
        while True:
            try:
                while True:
                    self._command(*self._cmds.get_nowait())
            except queue.Empty:
                pass
            if not self._open:
                time.sleep(IDLE_INTERVAL)
                continue
            try:
                while sdl.SDL_PollEvent(ctypes.byref(ev)):
                    self._event(ev)
            except Exception as ex:
                self.error = f'Controller error: {ex}'
            time.sleep(POLL_INTERVAL)

    def _command(self, name, arg):
        if name == 'active':
            if arg == self._active:
                return
            self._active = arg
            if arg:
                # Start the grace window *before* SDL starts up — startup itself
                # takes a moment, and that was the gap the controller blinked out in.
                with self._lock:
                    self._parked_until = time.monotonic() + REACQUIRE_GRACE
                self._open_subsystem()
            else:
                self._close_subsystem()
        elif name == 'rumble':
            d = self._devices.get(arg)
            if d and d.get('_gc') and d.get('rumble'):
                self.sdl.SDL_GameControllerRumble(d['_gc'], 0x9000, 0xC000, 450)

    def _open_subsystem(self):
        sdl = self.sdl
        if self._open:
            return
        if sdl.SDL_InitSubSystem(sdl.SDL_INIT_GAMECONTROLLER) != 0:
            self.error = 'SDL could not start: ' + sdl.SDL_GetError().decode(errors='replace')
            return
        self._open = True
        db = _data_path(DB_FILE)
        n = sdl.SDL_GameControllerAddMappingsFromFile(db.encode('utf-8')) if os.path.isfile(db) else -1
        self.mappings = max(n, 0)
        if n < 0:
            self.error = 'Controller database missing — only controllers SDL knows natively will be recognised.'
        # Already-connected devices arrive as JOYDEVICEADDED events on the next poll.

    def _close_subsystem(self):
        if not self._open:
            return
        sdl = self.sdl
        with self._lock:
            devices = list(self._devices.values())
            self._devices.clear()
            # Remember them, with nothing pressed, so the test screen keeps
            # showing them while EmuHub is in the background and while SDL
            # finds them again on return.
            self._parked = [self._at_rest(d) for d in devices]
            self._parked_until = None
        for d in devices:
            self._set_nav(d, None)      # release any held direction first
            self._close_device(d)
        sdl.SDL_QuitSubSystem(sdl.SDL_INIT_GAMECONTROLLER)
        self._open = False

    @staticmethod
    def _at_rest(d):
        r = {k: v for k, v in d.items() if not k.startswith('_')}
        r['paused'] = True
        if 'buttons' in r:
            r['buttons'] = {}
            r['axes'] = {k: 0 for k in r['axes']}
        if 'raw_buttons' in r:
            r['raw_buttons'] = [False] * len(r['raw_buttons'])
            r['raw_axes'] = [0.0] * len(r['raw_axes'])
            r['raw_hats'] = [0] * len(r['raw_hats'])
        return r

    def _close_device(self, d):
        try:
            if d.get('_gc'):
                self.sdl.SDL_GameControllerClose(d['_gc'])
            elif d.get('_js'):
                self.sdl.SDL_JoystickClose(d['_js'])
        except Exception:
            pass

    # ── devices ──────────────────────────────────────
    def _add_device(self, index):
        sdl = self.sdl
        if sdl.SDL_IsGameController(index):
            gc = sdl.SDL_GameControllerOpen(index)
            if not gc:
                return
            js = sdl.SDL_GameControllerGetJoystick(gc)
            ctype = self._type_label(sdl.SDL_GameControllerGetType(gc))
            name = (sdl.SDL_GameControllerName(gc) or b'Controller').decode(errors='replace')
            # The XInput driver only knows "XInput Controller"; the type is a
            # better name ("Xbox One / Series Controller").
            if name.startswith('XInput Controller') and ctype not in ('Generic', 'Virtual'):
                name = f'{ctype} Controller'
            d = {'_gc': gc, 'mapped': True,
                 'name': name,
                 'type': ctype,
                 'rumble': bool(sdl.SDL_GameControllerHasRumble(gc)),
                 'buttons': {}, 'axes': {'lx': 0, 'ly': 0, 'rx': 0, 'ry': 0, 'lt': 0, 'rt': 0}}
        else:
            # Not in SDL's list or the database: still usable with a basic
            # layout — hat as D-pad, first two buttons as A/B, first stick.
            js = sdl.SDL_JoystickOpen(index)
            if not js:
                return
            d = {'_js': js, 'mapped': False,
                 'name': (sdl.SDL_JoystickName(js) or b'Joystick').decode(errors='replace'),
                 'type': 'Unrecognized', 'rumble': False,
                 'raw_buttons': [False] * sdl.SDL_JoystickNumButtons(js),
                 'raw_axes':    [0.0]   * sdl.SDL_JoystickNumAxes(js),
                 'raw_hats':    [0]     * sdl.SDL_JoystickNumHats(js)}
        iid = sdl.SDL_JoystickInstanceID(js)
        buf = ctypes.create_string_buffer(33)
        sdl.SDL_JoystickGetGUIDString(sdl.SDL_JoystickGetGUID(js), buf, 33)
        d.update({'id': iid, 'guid': buf.value.decode(),
                  'vid': f'{sdl.SDL_JoystickGetVendor(js):04x}',
                  'pid': f'{sdl.SDL_JoystickGetProduct(js):04x}',
                  'battery': self._power_label(sdl.SDL_JoystickCurrentPowerLevel(js)),
                  '_dpad': None, '_stick': None, '_nav': None})
        with self._lock:
            self._devices[iid] = d
            # Found again: drop its parked stand-in.
            for i, pd in enumerate(self._parked):
                if pd['guid'] == d['guid']:
                    del self._parked[i]
                    break
        # Regaining focus re-opens every controller; only a new one is news.
        first = d['guid'] not in self._seen
        self._seen.add(d['guid'])
        self._emit({'type': 'connected', 'id': iid, 'name': d['name'],
                    'mapped': d['mapped'], 'first': first})

    def _remove_device(self, iid):
        with self._lock:
            d = self._devices.pop(iid, None)
        if d:
            self._close_device(d)
            self._set_nav(d, None)
            self._seen.discard(d['guid'])     # unplugged: announce it again next time
            self._emit({'type': 'disconnected', 'id': iid, 'name': d['name']})

    # ── events ───────────────────────────────────────
    def _event(self, ev):
        sdl, t = self.sdl, ev.type
        if t == sdl.SDL_JOYDEVICEADDED:
            self._add_device(ev.jdevice.which)          # device index
        elif t == sdl.SDL_JOYDEVICEREMOVED:
            self._remove_device(ev.jdevice.which)       # instance id
        elif t in (sdl.SDL_CONTROLLERBUTTONDOWN, sdl.SDL_CONTROLLERBUTTONUP):
            d = self._devices.get(ev.cbutton.which)
            name = self._button_name(ev.cbutton.button)
            if d and name:
                down = t == sdl.SDL_CONTROLLERBUTTONDOWN
                with self._lock:
                    d['buttons'][name] = down
                if name in ('up', 'down', 'left', 'right'):
                    self._dpad(d)
                elif name in NAV_BUTTONS:
                    self._emit({'type': 'button', 'id': d['id'], 'button': name, 'down': down})
        elif t == sdl.SDL_CONTROLLERAXISMOTION:
            d = self._devices.get(ev.caxis.which)
            name = self._axis_name(ev.caxis.axis)
            if d and name:
                v = ev.caxis.value
                # Sticks are -32768..32767; triggers 0..32767.
                val = round(max(-1.0, v / 32767), 3) if name not in ('lt', 'rt') else round(max(0, v) / 32767, 3)
                with self._lock:
                    d['axes'][name] = val
                if name in ('lx', 'ly'):
                    self._stick(d, d['axes']['lx'], d['axes']['ly'])
        # Unmapped joysticks — raw events, basic layout
        elif t in (sdl.SDL_JOYBUTTONDOWN, sdl.SDL_JOYBUTTONUP):
            d = self._devices.get(ev.jbutton.which)
            if d and not d['mapped']:
                down = t == sdl.SDL_JOYBUTTONDOWN
                with self._lock:
                    if ev.jbutton.button < len(d['raw_buttons']):
                        d['raw_buttons'][ev.jbutton.button] = down
                btn = {0: 'a', 1: 'b', 2: 'x', 3: 'y', 4: 'lb', 5: 'rb'}.get(ev.jbutton.button)
                if btn:
                    self._emit({'type': 'button', 'id': d['id'], 'button': btn, 'down': down})
        elif t == sdl.SDL_JOYHATMOTION:
            d = self._devices.get(ev.jhat.which)
            if d and not d['mapped']:
                with self._lock:
                    if ev.jhat.hat < len(d['raw_hats']):
                        d['raw_hats'][ev.jhat.hat] = ev.jhat.value
                if ev.jhat.hat == 0:
                    v = ev.jhat.value
                    d['_dpad'] = ('up' if v & 1 else 'down' if v & 4 else
                                  'left' if v & 8 else 'right' if v & 2 else None)
                    self._set_nav(d, d['_dpad'] or d['_stick'])
        elif t == sdl.SDL_JOYAXISMOTION:
            d = self._devices.get(ev.jaxis.which)
            if d and not d['mapped']:
                val = round(max(-1.0, ev.jaxis.value / 32767), 3)
                with self._lock:
                    if ev.jaxis.axis < len(d['raw_axes']):
                        d['raw_axes'][ev.jaxis.axis] = val
                if ev.jaxis.axis in (0, 1) and len(d['raw_axes']) >= 2:
                    self._stick(d, d['raw_axes'][0], d['raw_axes'][1])
        elif t == sdl.SDL_JOYBATTERYUPDATED:
            d = self._devices.get(ev.jbattery.which)
            if d:
                with self._lock:
                    d['battery'] = self._power_label(ev.jbattery.level)

    # ── navigation direction = D-pad if held, else left stick ─
    def _dpad(self, d):
        b = d['buttons']
        d['_dpad'] = next((k for k in ('up', 'down', 'left', 'right') if b.get(k)), None)
        self._set_nav(d, d['_dpad'] or d['_stick'])

    def _stick(self, d, x, y):
        cur = d['_stick']
        # Hysteresis: a direction engages at STICK_PRESS and holds until the
        # stick comes back under STICK_RELEASE, so it can't flicker at the edge.
        if cur and {'up': -y, 'down': y, 'left': -x, 'right': x}[cur] >= STICK_RELEASE:
            return
        new = None
        if max(abs(x), abs(y)) >= STICK_PRESS:
            new = ('right' if x > 0 else 'left') if abs(x) > abs(y) else ('down' if y > 0 else 'up')
        d['_stick'] = new
        self._set_nav(d, d['_dpad'] or new)

    def _set_nav(self, d, direction):
        if direction == d['_nav']:
            return
        if d['_nav']:
            self._emit({'type': 'dir', 'id': d['id'], 'dir': d['_nav'], 'down': False})
        d['_nav'] = direction
        if direction:
            self._emit({'type': 'dir', 'id': d['id'], 'dir': direction, 'down': True})

    # ── labels ───────────────────────────────────────
    def _button_name(self, b):
        sdl = self.sdl
        names = {
            sdl.SDL_CONTROLLER_BUTTON_A: 'a', sdl.SDL_CONTROLLER_BUTTON_B: 'b',
            sdl.SDL_CONTROLLER_BUTTON_X: 'x', sdl.SDL_CONTROLLER_BUTTON_Y: 'y',
            sdl.SDL_CONTROLLER_BUTTON_BACK: 'back', sdl.SDL_CONTROLLER_BUTTON_GUIDE: 'guide',
            sdl.SDL_CONTROLLER_BUTTON_START: 'start',
            sdl.SDL_CONTROLLER_BUTTON_LEFTSTICK: 'ls', sdl.SDL_CONTROLLER_BUTTON_RIGHTSTICK: 'rs',
            sdl.SDL_CONTROLLER_BUTTON_LEFTSHOULDER: 'lb', sdl.SDL_CONTROLLER_BUTTON_RIGHTSHOULDER: 'rb',
            sdl.SDL_CONTROLLER_BUTTON_DPAD_UP: 'up', sdl.SDL_CONTROLLER_BUTTON_DPAD_DOWN: 'down',
            sdl.SDL_CONTROLLER_BUTTON_DPAD_LEFT: 'left', sdl.SDL_CONTROLLER_BUTTON_DPAD_RIGHT: 'right',
            sdl.SDL_CONTROLLER_BUTTON_MISC1: 'misc', sdl.SDL_CONTROLLER_BUTTON_TOUCHPAD: 'touchpad',
            sdl.SDL_CONTROLLER_BUTTON_PADDLE1: 'p1', sdl.SDL_CONTROLLER_BUTTON_PADDLE2: 'p2',
            sdl.SDL_CONTROLLER_BUTTON_PADDLE3: 'p3', sdl.SDL_CONTROLLER_BUTTON_PADDLE4: 'p4',
        }
        return names.get(b)

    def _axis_name(self, a):
        sdl = self.sdl
        return {sdl.SDL_CONTROLLER_AXIS_LEFTX: 'lx', sdl.SDL_CONTROLLER_AXIS_LEFTY: 'ly',
                sdl.SDL_CONTROLLER_AXIS_RIGHTX: 'rx', sdl.SDL_CONTROLLER_AXIS_RIGHTY: 'ry',
                sdl.SDL_CONTROLLER_AXIS_TRIGGERLEFT: 'lt',
                sdl.SDL_CONTROLLER_AXIS_TRIGGERRIGHT: 'rt'}.get(a)

    def _type_label(self, t):
        sdl = self.sdl
        labels = {
            'SDL_CONTROLLER_TYPE_XBOX360': 'Xbox 360',
            'SDL_CONTROLLER_TYPE_XBOXONE': 'Xbox One / Series',
            'SDL_CONTROLLER_TYPE_PS3': 'PlayStation 3',
            'SDL_CONTROLLER_TYPE_PS4': 'PlayStation 4',
            'SDL_CONTROLLER_TYPE_PS5': 'PlayStation 5',
            'SDL_CONTROLLER_TYPE_NINTENDO_SWITCH_PRO': 'Switch Pro',
            'SDL_CONTROLLER_TYPE_NINTENDO_SWITCH_JOYCON_LEFT': 'Joy-Con (L)',
            'SDL_CONTROLLER_TYPE_NINTENDO_SWITCH_JOYCON_RIGHT': 'Joy-Con (R)',
            'SDL_CONTROLLER_TYPE_NINTENDO_SWITCH_JOYCON_PAIR': 'Joy-Con pair',
            'SDL_CONTROLLER_TYPE_GOOGLE_STADIA': 'Stadia',
            'SDL_CONTROLLER_TYPE_AMAZON_LUNA': 'Luna',
            'SDL_CONTROLLER_TYPE_NVIDIA_SHIELD': 'NVIDIA Shield',
            'SDL_CONTROLLER_TYPE_VIRTUAL': 'Virtual',
        }
        for const, label in labels.items():
            if getattr(sdl, const, None) == t:
                return label
        return 'Generic'

    def _power_label(self, p):
        sdl = self.sdl
        return {sdl.SDL_JOYSTICK_POWER_EMPTY: 'Empty', sdl.SDL_JOYSTICK_POWER_LOW: 'Low',
                sdl.SDL_JOYSTICK_POWER_MEDIUM: 'Medium', sdl.SDL_JOYSTICK_POWER_FULL: 'Full',
                sdl.SDL_JOYSTICK_POWER_WIRED: 'Wired'}.get(p, '')

    # ── to the page ──────────────────────────────────
    def _emit(self, evt):
        self._out.put(evt)

    def _dispatch(self):
        """Hand events to the page on a separate thread, so a busy UI thread
        (evaluate_js blocks until the page runs it) never stalls SDL polling."""
        while True:
            evt = self._out.get()
            try:
                self._emit_cb(evt)
            except Exception:
                pass
