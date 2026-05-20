; ============================================================================
;  Attendance System - Windows Installer (Inno Setup script)
;  ----------------------------------------------------------------------------
;  Builds a polished AttendanceSystem_Setup.exe with a wizard UI that:
;    - Checks for Python 3.11+ before starting
;    - Copies app files to Program Files (or user-chosen location)
;    - Creates a Python virtual environment and installs requirements.txt
;    - Initializes the SQLite database (preserves existing one on upgrade)
;    - Adds a Windows Firewall rule for inbound TCP 8000
;    - Registers a Task Scheduler entry to auto-start the server at boot
;    - Creates Desktop and Start Menu shortcuts
;    - Provides a full uninstaller that reverses the above (keeps data)
;
;  Build:
;    1. Install Inno Setup 6 from https://jrsoftware.org/isdl.php
;    2. Run installer\build_installer.bat
;    3. Output: installer\Output\AttendanceSystem_Setup.exe
; ============================================================================

#define MyAppName        "Attendance System"
#define MyAppShortName   "AttendanceSystem"
#define MyAppVersion     "2.0.0"
#define MyAppPublisher   "Saint Joseph Academy Cuyo"
#define MyAppExeName     "run_server.bat"
#define MyAppDashboard   "http://localhost:8000"
#define MyAppPort        "8000"
#define MinPythonMajor   3
#define MinPythonMinor   11

[Setup]
AppId={{6E2A2A7B-3D38-4B53-A5B4-7B0C6A3F1D11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://github.com/JepoyG0225/attendance_system
AppSupportURL=https://github.com/JepoyG0225/attendance_system/issues
AppUpdatesURL=https://github.com/JepoyG0225/attendance_system
DefaultDirName={autopf}\{#MyAppShortName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=AttendanceSystem_Setup_{#MyAppVersion}
SetupIconFile=..\static\app-icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#MyAppName} {#MyAppVersion}
UninstallDisplayIcon={app}\static\app-icon.ico
DisableWelcomePage=no
DisableDirPage=no
DisableReadyPage=no
DisableFinishedPage=no
CloseApplications=force
RestartApplications=no
WizardSmallImageFile=
WizardImageFile=

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";       Description: "Create a Desktop shortcut for the Attendance Server";  GroupDescription: "Shortcuts"; Flags: checkedonce
Name: "dashboardicon";     Description: "Create a Desktop shortcut to the Dashboard (http://localhost:{#MyAppPort})"; GroupDescription: "Shortcuts"; Flags: checkedonce
Name: "autostart";         Description: "Start the server automatically when Windows boots"; GroupDescription: "System integration"; Flags: checkedonce
Name: "firewall";          Description: "Open Windows Firewall for port {#MyAppPort} (LAN access for Pi scanners)"; GroupDescription: "System integration"; Flags: checkedonce

[Files]
; Application source files (copied from project root, which is one level up)
Source: "..\main.py";            DestDir: "{app}"; Flags: ignoreversion
Source: "..\database.py";        DestDir: "{app}"; Flags: ignoreversion
Source: "..\schemas.py";         DestDir: "{app}"; Flags: ignoreversion
Source: "..\attendance.py";      DestDir: "{app}"; Flags: ignoreversion
Source: "..\sms.py";             DestDir: "{app}"; Flags: ignoreversion
Source: "..\events.py";          DestDir: "{app}"; Flags: ignoreversion
Source: "..\reports.py";         DestDir: "{app}"; Flags: ignoreversion
Source: "..\app.py";             DestDir: "{app}"; Flags: ignoreversion
Source: "..\config.py";          DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist
Source: "..\requirements.txt";   DestDir: "{app}"; Flags: ignoreversion

; Static UI files (recursive)
Source: "..\static\*";           DestDir: "{app}\static"; Flags: ignoreversion recursesubdirs createallsubdirs

; Helper launcher / uninstall scripts
Source: "..\windows\run_app.bat";    DestDir: "{app}\windows"; Flags: ignoreversion
Source: "..\windows\run_server.bat"; DestDir: "{app}\windows"; Flags: ignoreversion
Source: "..\windows\uninstall.bat";  DestDir: "{app}\windows"; Flags: ignoreversion
Source: "..\windows\README_WINDOWS.md"; DestDir: "{app}\windows"; Flags: ignoreversion

; Post-install setup helper script (lives only in installer; used during Run section)
Source: "post_install.cmd";      DestDir: "{app}\windows"; Flags: ignoreversion

; CH340 USB-serial driver (for SIM800L modems and other CH340-based GSM sticks).
; Download from https://www.wch-ic.com/downloads/CH341SER_EXE.html and place
; at installer/drivers/CH341SER.EXE before building this installer.
; Bundled into {app}\drivers so the user can re-run it later if needed.
Source: "drivers\CH341SER.EXE";  DestDir: "{app}\drivers"; Flags: ignoreversion

[Icons]
; Start Menu (always)
Name: "{group}\Attendance System";        Filename: "{app}\windows\run_app.bat";    WorkingDir: "{app}"; IconFilename: "{app}\static\app-icon.ico"; Comment: "Open the Attendance System (native app)"
Name: "{group}\Attendance Server (headless)"; Filename: "{app}\windows\run_server.bat"; WorkingDir: "{app}"; IconFilename: "shell32.dll"; IconIndex: 21; Comment: "Run the server with no window (for unattended servers)"
Name: "{group}\Attendance Dashboard (browser)"; Filename: "{#MyAppDashboard}"; Comment: "Open the dashboard in your browser"
Name: "{group}\App Log";              Filename: "{localappdata}\AttendanceSystem\app.log"; Comment: "View the app log file"
Name: "{group}\Open Install Folder";  Filename: "{app}"; Comment: "Open the installation directory"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

; Desktop (optional, controlled by [Tasks])
Name: "{commondesktop}\Attendance System";    Filename: "{app}\windows\run_app.bat"; WorkingDir: "{app}"; IconFilename: "{app}\static\app-icon.ico"; Tasks: desktopicon
Name: "{commondesktop}\Attendance Dashboard"; Filename: "{#MyAppDashboard}"; Tasks: dashboardicon

[Dirs]
Name: "{app}\static\photos"; Permissions: users-modify
Name: "{localappdata}\AttendanceSystem"

[Run]
; ── 0. CH340 USB-serial driver (required for SIM800L GSM modems) ─────────────
;     Runs WCH's official installer in silent mode (/S). It's idempotent —
;     if the driver is already installed, the installer just exits cleanly.
;     We don't abort the install on driver-install failure (the rest of the
;     app still works, you just can't talk to a CH340-based modem until you
;     install the driver manually).
Filename: "{app}\drivers\CH341SER.EXE"; Parameters: "/S"; \
    StatusMsg: "Installing CH340 USB-serial driver (for GSM modems)..."; \
    Flags: runhidden waituntilterminated skipifdoesntexist

; ── 1. Create venv and install dependencies ──────────────────────────────────
Filename: "{app}\windows\post_install.cmd"; Parameters: "venv ""{app}"""; \
    StatusMsg: "Setting up Python environment (this may take a few minutes)..."; \
    Flags: runhidden waituntilterminated

; ── 2. Initialize the database ───────────────────────────────────────────────
Filename: "{app}\windows\post_install.cmd"; Parameters: "initdb ""{app}"""; \
    StatusMsg: "Initializing database..."; \
    Flags: runhidden waituntilterminated

; ── 3. Firewall rule (optional task) ─────────────────────────────────────────
Filename: "{cmd}"; Parameters: "/c netsh advfirewall firewall delete rule name=""Attendance System"" >nul 2>&1 & netsh advfirewall firewall add rule name=""Attendance System"" dir=in action=allow protocol=TCP localport={#MyAppPort} profile=any"; \
    StatusMsg: "Configuring Windows Firewall (port {#MyAppPort})..."; \
    Flags: runhidden waituntilterminated; Tasks: firewall

; ── 4. Task Scheduler entry (optional task) ──────────────────────────────────
;     Auto-start runs the HEADLESS server at boot (run_server.bat) so it's
;     available to Pi scanners even when no one is logged in. The native app
;     window is launched separately by the user from the Desktop shortcut.
Filename: "{cmd}"; Parameters: "/c schtasks /delete /tn ""Attendance System"" /f >nul 2>&1 & schtasks /create /tn ""Attendance System"" /tr ""\""{app}\windows\run_server.bat\"""" /sc onstart /rl highest /ru SYSTEM /f"; \
    StatusMsg: "Registering auto-start at boot..."; \
    Flags: runhidden waituntilterminated; Tasks: autostart

; ── 5. Launch options on Finish page ─────────────────────────────────────────
Filename: "{app}\windows\run_app.bat"; Description: "Launch {#MyAppName} now (native window)"; \
    Flags: postinstall nowait skipifsilent
Filename: "{#MyAppDashboard}"; Description: "Open the dashboard in browser instead"; \
    Flags: postinstall shellexec nowait skipifsilent unchecked

[UninstallRun]
; Stop any running task before removing the registration
Filename: "{cmd}"; Parameters: "/c schtasks /end /tn ""Attendance System"" >nul 2>&1 & schtasks /delete /tn ""Attendance System"" /f"; \
    Flags: runhidden waituntilterminated; RunOnceId: "RemoveTask"

; Remove firewall rule
Filename: "{cmd}"; Parameters: "/c netsh advfirewall firewall delete rule name=""Attendance System"""; \
    Flags: runhidden waituntilterminated; RunOnceId: "RemoveFirewall"

[UninstallDelete]
; Remove the venv on uninstall (it's ~80MB and easy to recreate)
Type: filesandordirs; Name: "{app}\.venv"
; Remove cached __pycache__ folders
Type: filesandordirs; Name: "{app}\__pycache__"
; Note: attendance.db, config.py, and static/photos are deliberately NOT removed.
; They contain user data and should be preserved across uninstall/reinstall.

; ============================================================================
;  Pascal Script: Python detection + custom pages
; ============================================================================
[Code]
const
  MIN_PY_MAJOR = 3;
  MIN_PY_MINOR = 11;

var
  PythonOk: Boolean;
  PythonVersion: String;

{ Run "py -3 --version" or "python --version" and parse out the version. }
function DetectPython(): Boolean;
var
  TmpFile: String;
  Output: AnsiString;
  ResultCode: Integer;
  P, MajorVal, MinorVal: Integer;
  VerStr, MajorStr, MinorStr: String;
begin
  Result := False;
  TmpFile := ExpandConstant('{tmp}\pyver.txt');

  { Try "py -3 --version" first (Python Launcher for Windows) }
  if not Exec(ExpandConstant('{cmd}'),
              '/c py -3 --version > "' + TmpFile + '" 2>&1',
              '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    ResultCode := 1;

  if ResultCode <> 0 then
  begin
    { Fall back to plain "python --version" }
    if not Exec(ExpandConstant('{cmd}'),
                '/c python --version > "' + TmpFile + '" 2>&1',
                '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
      ResultCode := 1;
  end;

  if (ResultCode = 0) and LoadStringFromFile(TmpFile, Output) then
  begin
    VerStr := Trim(String(Output));
    { Expect "Python 3.11.5" -> strip "Python " prefix }
    P := Pos(' ', VerStr);
    if P > 0 then VerStr := Copy(VerStr, P + 1, Length(VerStr));
    PythonVersion := VerStr;

    { Split major.minor }
    P := Pos('.', VerStr);
    if P > 0 then
    begin
      MajorStr := Copy(VerStr, 1, P - 1);
      VerStr   := Copy(VerStr, P + 1, Length(VerStr));
      P := Pos('.', VerStr);
      if P > 0 then
        MinorStr := Copy(VerStr, 1, P - 1)
      else
        MinorStr := VerStr;

      MajorVal := StrToIntDef(MajorStr, 0);
      MinorVal := StrToIntDef(MinorStr, 0);

      if (MajorVal > MIN_PY_MAJOR) or
         ((MajorVal = MIN_PY_MAJOR) and (MinorVal >= MIN_PY_MINOR)) then
        Result := True;
    end;
  end;

  DeleteFile(TmpFile);
end;

{ Called by Inno Setup before the wizard appears. }
function InitializeSetup(): Boolean;
var
  ShellResult: Integer;
begin
  PythonOk := DetectPython();
  if not PythonOk then
  begin
    if MsgBox(
      'Python ' + IntToStr(MIN_PY_MAJOR) + '.' + IntToStr(MIN_PY_MINOR) + ' or newer was not found on this PC.' + #13#10 + #13#10 +
      'The Attendance System needs Python to run. You can:' + #13#10 +
      '  - Click YES to open the Python download page in your browser, then re-run this installer.' + #13#10 +
      '  - Click NO to cancel.',
      mbError, MB_YESNO or MB_DEFBUTTON1) = IDYES then
    begin
      ShellExec('open', 'https://www.python.org/downloads/windows/', '', '', SW_SHOW, ewNoWait, ShellResult);
    end;
    Result := False;
    Exit;
  end;
  Result := True;
end;

{ Show detected Python version on the Ready page. }
function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo,
                         MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo,
                         MemoTasksInfo: String): String;
var
  S: String;
begin
  S := '';
  if PythonVersion <> '' then
    S := S + 'Detected Python:' + NewLine + Space + PythonVersion + NewLine + NewLine;
  S := S + MemoDirInfo + NewLine + NewLine;
  if MemoTasksInfo <> '' then
    S := S + 'Options:' + NewLine + MemoTasksInfo + NewLine;
  Result := S;
end;
