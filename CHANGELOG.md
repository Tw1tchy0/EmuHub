# Changelog

All notable changes to EmuHub are documented here.

---

## [1.0.1-beta] — 2026-03-24

### Fixed
- **Scrollbar** — Custom scrollbar is now visible, grabbable, and draggable with smooth thumb feedback (hover + active states, minimum thumb height so it's always easy to grab).
- **Auto-detect — duplicate filtering** — Emulators that are already configured in the Emulators tab no longer appear in the Auto-Detect results. If every detected emulator is already set up, a clear "All detected emulators are already configured" message is shown instead of an empty list.
- **Auto-detect — APPLY button** — Fixed a bug where clicking APPLY on a detected emulator did nothing. The root cause was unescaped double-quote characters from `JSON.stringify` breaking the inline `onclick` HTML attribute.
- **File dialog deprecation warnings** — Replaced deprecated `webview.OPEN_DIALOG` / `webview.FOLDER_DIALOG` constants with `webview.FileDialog.OPEN` / `webview.FileDialog.FOLDER` to suppress pywebview deprecation warnings.

### Added
- **Clear (✕) buttons on path inputs** — Each emulator executable path and ROM folder path input in the Settings modal now has a one-click ✕ button to instantly clear the field, matching the existing clear-button pattern on ROM path rows. Appears only when the field has a value.

---

## [1.0.0-beta] — initial release

- Initial public release of EmuHub.
- Unified launcher for Switch (Eden), 3DS (Citra), DS (DeSmuME), GBA/GB (mGBA).
- SteamGridDB box art integration with local caching.
- Frameless PyWebView window with custom title bar drag.
- ROM folder watcher — new files appear in the library automatically.
- Settings modal with per-console emulator paths, ROM folders, and custom emulators.
- Auto-detect system scan to find installed emulators automatically.
- Encrypted SteamGridDB API key storage.
