; ============================================
;  EmuHub - Inno Setup Installer Script
; ============================================
;
;  Build with:
;    ISCC.exe installer\EmuHub.iss
;  Or pass version from BUILD.bat:
;    ISCC.exe /DAppVersion=1.3.0 installer\EmuHub.iss
;

#ifndef AppVersion
  #define AppVersion "1.3.0"
#endif

[Setup]
AppId={{8E2F4A1B-3C5D-4E6F-A7B8-9D0E1F2A3B4C}
AppName=EmuHub
AppVersion={#AppVersion}
AppVerName=EmuHub {#AppVersion}
AppPublisher=Tw1tchy0
AppPublisherURL=https://github.com/Tw1tchy0/EmuHub
AppSupportURL=https://github.com/Tw1tchy0/EmuHub/issues
DefaultDirName={autopf}\EmuHub
DefaultGroupName=EmuHub
UninstallDisplayIcon={app}\EmuHub.exe
OutputDir=..\dist
OutputBaseFilename=EmuHub_Setup
SetupIconFile=..\assets\icon.ico
LicenseFile=..\LICENSE
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
PrivilegesRequired=admin
DisableProgramGroupPage=yes
; Uncomment the line below when you have a code signing certificate:
; SignTool=signtool sign /f "$qCERT_FILE$q" /p "$qCERT_PASS$q" /tr http://timestamp.digicert.com /td sha256 /fd sha256 $f

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "..\dist\EmuHub.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\EmuHub"; Filename: "{app}\EmuHub.exe"
Name: "{group}\Uninstall EmuHub"; Filename: "{uninstallexe}"
Name: "{commondesktop}\EmuHub"; Filename: "{app}\EmuHub.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\EmuHub.exe"; Description: "Launch EmuHub"; Flags: nowait postinstall skipifsilent

[Code]
// ── Prerequisite checks ──────────────────────────────────────

function IsVCRedistInstalled: Boolean;
var
  Version: String;
begin
  Result :=
    RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Version', Version) or
    RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Version', Version);
end;

function IsWebView2Installed: Boolean;
var
  Version: String;
begin
  Result :=
    RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) or
    RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) or
    RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version);
end;

function DownloadAndInstall(const URL, FileName, Args, DisplayName: String): Boolean;
var
  TempPath: String;
  PsCmd: String;
  ResultCode: Integer;
begin
  Result := False;
  TempPath := ExpandConstant('{tmp}\') + FileName;

  WizardForm.StatusLabel.Caption := 'Downloading ' + DisplayName + '...';
  Log('Downloading ' + DisplayName + ' from: ' + URL);

  // Use PowerShell to download (no extra plugins needed)
  PsCmd := '-ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri ''' + URL + ''' -OutFile ''' + TempPath + '''"';
  if Exec('powershell.exe', PsCmd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0) then
  begin
    WizardForm.StatusLabel.Caption := 'Installing ' + DisplayName + '...';
    Log('Installing ' + DisplayName + '...');
    if Exec(TempPath, Args, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    begin
      Log(DisplayName + ' installer finished with code: ' + IntToStr(ResultCode));
      Result := True;
    end else
      Log('Failed to run ' + DisplayName + ' installer');
    DeleteFile(TempPath);
  end else
    Log('Failed to download ' + DisplayName);
end;

procedure InstallPrerequisites;
begin
  if not IsVCRedistInstalled then
  begin
    Log('VC++ Redistributable not found, installing...');
    if not DownloadAndInstall(
      'https://aka.ms/vs/17/release/vc_redist.x64.exe',
      'vc_redist.x64.exe',
      '/install /quiet /norestart',
      'Visual C++ Redistributable') then
    begin
      MsgBox('Could not install the Visual C++ Redistributable automatically.' + #13#10 +
             'EmuHub may not work correctly. Please install it manually from:' + #13#10 +
             'https://aka.ms/vs/17/release/vc_redist.x64.exe', mbError, MB_OK);
    end;
  end;

  if not IsWebView2Installed then
  begin
    Log('WebView2 Runtime not found, installing...');
    if not DownloadAndInstall(
      'https://go.microsoft.com/fwlink/p/?LinkId=2124703',
      'MicrosoftEdgeWebview2Setup.exe',
      '/silent /install',
      'Edge WebView2 Runtime') then
    begin
      MsgBox('Could not install the Edge WebView2 Runtime automatically.' + #13#10 +
             'EmuHub requires WebView2 to run. Please install it manually from:' + #13#10 +
             'https://developer.microsoft.com/en-us/microsoft-edge/webview2/', mbError, MB_OK);
    end;
  end;
end;

// ── Install steps ────────────────────────────────────────────

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  NeedsRestart := False;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    InstallPrerequisites;
end;

// ── Uninstall: ask about user data ───────────────────────────

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\EmuHub');
    if DirExists(DataDir) then
    begin
      if MsgBox('Do you want to remove your EmuHub settings and data?' + #13#10 +
                '(Keeping them allows a future reinstall to pick up where you left off.)',
                mbConfirmation, MB_YESNO) = IDYES then
      begin
        DelTree(DataDir, True, True, True);
        Log('User data removed: ' + DataDir);
      end else
        Log('User data preserved: ' + DataDir);
    end;
  end;
end;
