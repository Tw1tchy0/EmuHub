# EmuHub — Unified Emulator Launcher

A **single portable .exe** — no browser, no Node.js, no installation.
Built with Python + pywebview.

---

## Quick Start

### Option A — Run from source (development)
1. Install Python 3.10+ from https://www.python.org  
   ✓ Check "Add Python to PATH"
2. Double-click **RUN_DEV.bat**  
   (installs deps + opens EmuHub on first run)

### Option B — Build a standalone .exe (recommended)
1. Install Python 3.10+ from https://www.python.org  
   ✓ Check "Add Python to PATH"
2. Double-click **BUILD.bat**  
   Wait ~90 seconds while PyInstaller bundles everything.
3. Your finished `EmuHub.exe` appears in `dist\`  
   Copy it anywhere — USB drive, Desktop, D:\EMULATION — no dependencies.
4. BUILD.bat will offer to create a Desktop shortcut automatically.

---

## Box Art — SteamGridDB Setup (free, takes 2 minutes)

1. Sign up free at https://www.steamgriddb.com
2. Click your avatar → **Preferences** → **API** → **Generate API Key**
3. Open EmuHub → click **⚙ SGDB KEY** in the top-right
4. Paste your key → click **SAVE & REFRESH ART**

Box art loads automatically for every game, and is cached locally so it
only needs to download once. New ROMs you add are scraped automatically.

---

## Adding New ROMs

Drop files into the correct subfolder under `D:\EMULATION\ROMS\`:

```
D:\EMULATION\ROMS\
  3DS\     →  .3ds  .cia
  DS\      →  .nds  .zip
  GBA\     →  .gba  .zip
  GB\      →  .gb   .gbc
  SWITCH\  →  .nsp  .xci  (subfolders are fine)
```

EmuHub watches the folder and detects new files automatically —
the game appears in the library within a few seconds.

---

## How Games Launch

| Console      | Emulator  | Launch method                     |
|--------------|-----------|-----------------------------------|
| Switch       | Eden      | `eden.exe -p "game.nsp"`          |
| 3DS          | Citra     | `citra-qt.exe "game.3ds"`         |
| DS           | DeSmuME   | `DeSmuME.exe "game.nds"`          |
| GBA / GB     | mGBA      | `mGBA.exe "game.gba"`             |

Clicking **▶ LAUNCH GAME** calls the emulator directly via Python's
`subprocess.Popen` — the emulator opens in its own window instantly,
EmuHub stays open in the background.

---

## Files in this folder

| File               | Purpose                                          |
|--------------------|--------------------------------------------------|
| `BUILD.bat`        | Compiles a standalone `EmuHub.exe`               |
| `RUN_DEV.bat`      | Run from source without building                |
| `src/main.py`      | Python backend (ROM scan, launch, file watch)   |
| `src/web/index.html` | The GUI                                        |
| `EmuHub.spec`      | PyInstaller build config                        |
| `version_info.txt` | Windows exe version metadata                    |

---

## Troubleshooting

**"Python not found"** — Re-run the Python installer and check "Add to PATH".

**Blank window / white screen** — pywebview needs .NET / WebView2 on Windows.
Install [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/).

**Game doesn't launch** — Check that the emulator `.exe` paths in `src/main.py`
match your actual install locations (the EMULATORS dict at the top of the file).

**Box art not loading** — Confirm your SteamGridDB API key is entered correctly
in EmuHub (⚙ SGDB KEY). The key must have API access enabled in your profile.
