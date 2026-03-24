@echo off
title EmuHub - Build
echo.
echo  ============================================
echo   EmuHub Builder
echo  ============================================
echo.

:: ── 1. Python check ────────────────────────────────────────
python --version >nul 2>&1
if %errorlevel% NEQ 0 (
    echo  [ERROR] Python is not installed or not on your PATH.
    echo.
    echo  HOW TO FIX:
    echo    1. Go to: https://www.python.org/downloads/
    echo    2. Download the latest Python 3 installer
    echo    3. Run it and CHECK the box that says:
    echo       "Add Python to PATH"  ^<-- THIS IS IMPORTANT
    echo    4. Finish the install, then run BUILD.bat again
    echo.
    pause
    exit /b 1
)
echo  [OK] Python found
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo       %%v

:: ── 2. Visual C++ Redistributable check ────────────────────
echo.
echo  [Checking] Microsoft Visual C++ Redistributable...
reg query "HKLM\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" /v Version >nul 2>&1
if %errorlevel% NEQ 0 (
    reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" /v Version >nul 2>&1
)
if %errorlevel% NEQ 0 (
    echo  [MISSING] Visual C++ Redistributable not found.
    echo  Downloading and installing it now...
    echo  (This is required by pywebview and only needs to happen once^)
    echo.
    :: Download the official Microsoft MSVC redistributable silently
    powershell -ExecutionPolicy Bypass -Command ^
      "Invoke-WebRequest -Uri 'https://aka.ms/vs/17/release/vc_redist.x64.exe' -OutFile '%TEMP%\vc_redist.x64.exe'"
    if %errorlevel% NEQ 0 (
        echo  [ERROR] Could not download Visual C++ Redistributable.
        echo  Please install it manually from:
        echo  https://aka.ms/vs/17/release/vc_redist.x64.exe
        pause & exit /b 1
    )
    "%TEMP%\vc_redist.x64.exe" /install /quiet /norestart
    del /q "%TEMP%\vc_redist.x64.exe" 2>nul
    echo  [OK] Visual C++ Redistributable installed
) else (
    echo  [OK] Visual C++ Redistributable found
)

:: ── 3. WebView2 Runtime check ───────────────────────────────
echo.
echo  [Checking] Microsoft Edge WebView2 Runtime...
reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" >nul 2>&1
if %errorlevel% NEQ 0 (
    reg query "HKCU\Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" >nul 2>&1
)
if %errorlevel% NEQ 0 (
    echo  [MISSING] WebView2 Runtime not found.
    echo  Downloading and installing it now...
    echo  (This is required to display the EmuHub window^)
    echo.
    powershell -ExecutionPolicy Bypass -Command ^
      "Invoke-WebRequest -Uri 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' -OutFile '%TEMP%\MicrosoftEdgeWebview2Setup.exe'"
    if %errorlevel% NEQ 0 (
        echo  [ERROR] Could not download WebView2 Runtime.
        echo  Please install it manually from:
        echo  https://developer.microsoft.com/en-us/microsoft-edge/webview2/
        pause & exit /b 1
    )
    "%TEMP%\MicrosoftEdgeWebview2Setup.exe" /silent /install
    del /q "%TEMP%\MicrosoftEdgeWebview2Setup.exe" 2>nul
    echo  [OK] WebView2 Runtime installed
) else (
    echo  [OK] WebView2 Runtime found
)

:: ── 4. pip dependencies ─────────────────────────────────────
echo.
echo  [1/3] Installing Python dependencies...
pip install pywebview pyinstaller --quiet
if %errorlevel% NEQ 0 (
    echo  [ERROR] pip install failed. Check your internet connection.
    pause & exit /b 1
)
echo  [OK] Dependencies installed

:: ── 5. Clean previous artefacts ────────────────────────────
echo.
echo  [2/3] Cleaning previous build artefacts...
if exist build\ ( rmdir /s /q build & echo       Removed build\ )

:: Orphaned PyInstaller temp extractions from crashed EmuHub.exe runs
for /d %%D in ("%TEMP%\_MEI*") do ( rmdir /s /q "%%D" 2>nul )
for /d %%D in ("%TEMP%\EmuHub_runtime*") do ( rmdir /s /q "%%D" 2>nul )
echo       Cleaned orphaned temp folders

:: PyInstaller system-wide hook/bootloader cache
if exist "%LOCALAPPDATA%\pyinstaller\" (
    rmdir /s /q "%LOCALAPPDATA%\pyinstaller"
    echo       Cleared PyInstaller system cache
)

:: ── 6. Build ────────────────────────────────────────────────
echo.
echo  [3/3] Building EmuHub.exe...
pyinstaller EmuHub.spec --noconfirm --clean
if %errorlevel% NEQ 0 (
    echo.
    echo  [ERROR] PyInstaller build failed.
    echo  Common causes:
    echo    - Missing Visual C++ Redistributable  (handled above, try re-running)
    echo    - Antivirus blocking the build        (temporarily disable AV and retry)
    echo    - Corrupted Python install            (reinstall Python and retry)
    pause & exit /b 1
)

:: Remove build folder — only needed during compilation
if exist build\ rmdir /s /q build

echo.
echo  ============================================
echo   EmuHub.exe is ready at:
echo   dist\EmuHub.exe
echo.
echo   Copy it anywhere — no install needed.
echo   Double-click to launch.
echo  ============================================
echo.
echo  Press any key to open the output folder...
pause >nul
explorer dist
