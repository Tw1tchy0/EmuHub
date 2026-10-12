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

:: ── 7. Optional: Code signing ──────────────────────────────
if defined SIGN_CERT (
    echo.
    echo  [Signing] Signing EmuHub.exe...
    signtool sign /f "%SIGN_CERT%" /p "%SIGN_PASS%" /tr http://timestamp.digicert.com /td sha256 /fd sha256 dist\EmuHub.exe
    if %errorlevel% NEQ 0 (
        echo  [WARNING] Signing failed. Continuing without signature.
    ) else (
        echo  [OK] EmuHub.exe signed
    )
)

echo.
echo  ============================================
echo   EmuHub.exe is ready at:
echo   dist\EmuHub.exe
echo  ============================================
echo.

:: ── 8. Optional: Build installer ──────────────────────────
echo  Build installer too? (requires Inno Setup 6)
choice /C YN /M "  Build installer"
if %errorlevel% NEQ 1 goto :skip_installer

:: Find ISCC.exe
set "ISCC="
where ISCC.exe >nul 2>&1
if %errorlevel% EQU 0 (
    set "ISCC=ISCC.exe"
) else if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (
    set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
) else if exist "C:\Program Files\Inno Setup 6\ISCC.exe" (
    set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"
)

if not defined ISCC (
    echo.
    echo  [ERROR] Inno Setup 6 not found.
    echo  Install it from: https://jrsoftware.org/isdl.php
    echo  Then re-run BUILD.bat.
    goto :skip_installer
)

:: Parse version from version_info.txt
set "APP_VER="
for /f "tokens=2 delims='" %%V in ('findstr /C:"ProductVersion" version_info.txt') do set "APP_VER=%%V"
if not defined APP_VER set "APP_VER=1.0.0"

echo.
echo  [4/4] Building installer (v%APP_VER%)...
"%ISCC%" /DAppVersion=%APP_VER% installer\EmuHub.iss
if %errorlevel% NEQ 0 (
    echo  [ERROR] Installer build failed.
    goto :skip_installer
)

:: Sign the installer too if certificate is available
if defined SIGN_CERT (
    echo  [Signing] Signing EmuHub_Setup.exe...
    signtool sign /f "%SIGN_CERT%" /p "%SIGN_PASS%" /tr http://timestamp.digicert.com /td sha256 /fd sha256 dist\EmuHub_Setup.exe
    if %errorlevel% NEQ 0 (
        echo  [WARNING] Installer signing failed.
    ) else (
        echo  [OK] EmuHub_Setup.exe signed
    )
)

echo.
echo  ============================================
echo   Installer ready at:
echo   dist\EmuHub_Setup.exe
echo  ============================================

:skip_installer
echo.
echo  Press any key to open the output folder...
pause >nul
explorer dist
