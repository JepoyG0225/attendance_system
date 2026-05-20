# Attendance System — Windows Installer

A polished `Setup.exe` wizard built with **Inno Setup 6**. End users get a real Windows installation experience (Welcome → Install Location → Components → Progress → Finish) instead of a console window.

## What the installer does

When a user runs `AttendanceSystem_Setup_<version>.exe`:

1. **Checks for Python 3.11+** before anything else — opens python.org if missing.
2. Shows a **Welcome wizard** with publisher info.
3. Lets the user choose the **install directory** (default: `C:\Program Files\AttendanceSystem`).
4. Shows a **Components/Tasks page** where they can toggle:
   - Desktop shortcut for the server
   - Desktop shortcut to the dashboard
   - Auto-start on boot (Task Scheduler)
   - Firewall rule for port 8000
5. **Copies app files** to the chosen folder.
6. **Creates a Python venv** and installs `requirements.txt` (hidden, with progress).
7. **Initializes the SQLite database** (safe to re-run — preserves existing data).
8. **Adds the firewall rule** for inbound TCP 8000 (if selected).
9. **Registers a Task Scheduler entry** that runs `run_server.bat` at boot (if selected).
10. **Creates shortcuts** in Start Menu (always) and Desktop (if selected).
11. **Finish page** offers to start the server and open the dashboard immediately.

The uninstaller reverses steps 8, 9, 10 — but **preserves**:
- `attendance.db` (your student/attendance data)
- `config.py` (school name, modem ports, SMS templates)
- `static/photos/` (student photos)

This way a reinstall keeps all the school's data.

## Building the installer

### Prerequisites

1. **Inno Setup 6** (free) — download from <https://jrsoftware.org/isdl.php>
2. The project source code (this whole repo)

### Steps

1. Open the `installer` folder.
2. Double-click **`build_installer.bat`**.
3. Wait ~30 seconds for compilation.
4. Output appears in `installer\Output\AttendanceSystem_Setup_2.0.0.exe`.

Build script auto-detects the Inno Setup compiler in the default install locations. If yours is elsewhere, edit `build_installer.bat`.

### Manual compilation
If you prefer the Inno Setup IDE:
1. Launch **Inno Setup Compiler**.
2. **File → Open** → select `installer\AttendanceSystem.iss`.
3. **Build → Compile** (or press F9).
4. Output goes to `installer\Output\`.

## Distribution

Send a single file: `AttendanceSystem_Setup_<version>.exe` (~5–10 MB).

The end user:
1. Double-clicks the Setup.exe.
2. Approves the UAC prompt (admin privileges needed).
3. Walks through the wizard.
4. Done — server is installed, auto-starts at boot, dashboard is at `http://localhost:8000`.

## Customizing per school

Several values in `AttendanceSystem.iss` you may want to change per deployment:

| Define | Where | What it controls |
|---|---|---|
| `MyAppName` | top of `.iss` | Display name in Add/Remove Programs |
| `MyAppVersion` | top of `.iss` | Shown in wizard + Add/Remove |
| `MyAppPublisher` | top of `.iss` | Publisher field |
| `MyAppPort` | top of `.iss` | Server port (default 8000) |
| `AppId` GUID | `[Setup]` | Keep stable across versions — Inno uses it to detect upgrades |

Bump `MyAppVersion` whenever you release. Inno Setup will offer an in-place upgrade when an older version is already installed (it skips files marked `onlyifdoesntexist` such as `config.py` and the database).

## What's installed where

| Item | Location |
|---|---|
| App files | `<InstallDir>` (e.g. `C:\Program Files\AttendanceSystem`) |
| Python venv | `<InstallDir>\.venv` |
| SQLite database | `<InstallDir>\attendance.db` |
| Student photos | `<InstallDir>\static\photos\` |
| Server log | `%LOCALAPPDATA%\AttendanceSystem\server.log` |
| Install log | `%LOCALAPPDATA%\AttendanceSystem\install.log` |
| Start Menu group | "Attendance System" |
| Scheduled Task | `Attendance System` (in Task Scheduler) |
| Firewall rule | `Attendance System` (inbound TCP 8000) |

## Troubleshooting build

| Error | Fix |
|---|---|
| `ISCC.exe not found` | Install Inno Setup 6 from the link above |
| `File "..\main.py" not found` | Run `build_installer.bat` from inside the `installer\` folder, not from the project root |
| `Pascal script compilation failed` | You're probably on Inno Setup 5; upgrade to 6 (free) |

## Troubleshooting end-user install

The installer writes a detailed log to:
```
%LOCALAPPDATA%\AttendanceSystem\install.log
```

Common end-user errors:

| Symptom | Cause | Fix |
|---|---|---|
| "Python 3.11+ not found" — installer refuses to run | No Python on the PC | Install Python from python.org with "Add to PATH" |
| Hangs at "Setting up Python environment" | Slow internet / pip downloading | Check `install.log` — wait for downloads |
| Auto-start doesn't run at boot | Task Scheduler entry missing | Open Task Scheduler → look for "Attendance System" → enable it |
| Dashboard says "could not reach server" from another PC | Firewall blocking | Re-run installer or manually: `netsh advfirewall firewall add rule name="Attendance System" dir=in action=allow protocol=TCP localport=8000` |

## Difference vs the old `windows\install.bat`

| | Old `install.bat` | New `Setup.exe` |
|---|---|---|
| UI | Console window | Wizard with progress bars |
| Looks professional | ❌ Plain text | ✅ Windows-native |
| Asks for install location | ❌ Fixed at script location | ✅ User-chooses |
| Asks which components to install | ❌ All-or-nothing | ✅ Checkboxes |
| Add/Remove Programs entry | ❌ No | ✅ Yes |
| Detects existing install for upgrade | ❌ No | ✅ Yes |
| Distribution | Send whole project folder | Send single Setup.exe |
| Build step | None — just ship | Compile once with Inno Setup |
| User experience | Technical | Friendly |

The old `install.bat` still works for advanced users / developer testing. It's not removed.

## Files in this folder

```
installer/
├── AttendanceSystem.iss      Inno Setup script (the installer recipe)
├── build_installer.bat       Compiles the .iss → Setup.exe
├── post_install.cmd          Runs hidden during install (venv + DB init)
├── README.md                 This file
└── Output/                   Compiled Setup.exe lands here (gitignored)
    └── AttendanceSystem_Setup_2.0.0.exe
```
