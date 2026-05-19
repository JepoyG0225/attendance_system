# Attendance System — Windows Deployment Guide

A one-time setup for the school's desktop server. The installer handles dependencies, firewall, auto-start at boot, and shortcuts.

## Prerequisites

- **Windows 10 or 11** (64-bit)
- **Python 3.11 or newer**
  - Download from https://www.python.org/downloads/windows/
  - During install, tick **"Add python.exe to PATH"**
- **Administrator account** on the PC (needed for firewall + auto-start)
- The USB GSM modem driver, if it's not auto-detected:
  - CH340 chip → https://www.wch-ic.com/downloads/CH341SER_EXE.html
  - CP210x chip → https://www.silabs.com/developer-tools/usb-to-uart-bridge-vcp-drivers

## Install

1. Copy the entire project folder to the school PC (e.g. `C:\AttendanceSystem`).
2. Open `windows\` inside the project.
3. **Right-click `install.bat` → "Run as administrator"**.
4. Approve the UAC prompt.
5. Wait while it sets up the venv (~2 minutes on first run).
6. When done, you'll have:
   - A **"Attendance Server"** shortcut on the Desktop and Start Menu
   - A **"Attendance Dashboard"** link on the Desktop pointing to `http://localhost:8000`
   - Auto-start at every Windows boot
   - Inbound TCP 8000 allowed through Windows Firewall

## Start the server

Either:

- Double-click **Attendance Server** on the Desktop, **or**
- Restart Windows (auto-start kicks in), **or**
- Open Task Scheduler → run "Attendance System" task manually

A console window will show the server's startup logs. **Leave it open** while the system is in use — closing it stops the server.

## Verify

Open `http://localhost:8000` on the server itself, or from any other device on the same LAN:
- `http://<server-hostname>.local:8000` (mDNS — works if Bonjour is available)
- `http://<server-ip>:8000` (find IP with `ipconfig`)

## Scanner setup

Each Raspberry Pi scanner needs to point at this server. The Pi's kiosk URL is in `~/.config/labwc/autostart` on the Pi. Change it to:

```
http://<this-pc-hostname>.local:8000/scanner?id=1&label=Entrance
```

Set the Windows hostname under **Settings → System → About → Rename this PC** before deployment.

## Where things live

| Item | Location |
|---|---|
| Project files | `<install dir>` (e.g. `C:\AttendanceSystem`) |
| Python venv | `<install dir>\.venv` |
| Database | `<install dir>\attendance.db` |
| Student photos | `<install dir>\static\photos\` |
| Server log | `%LOCALAPPDATA%\AttendanceSystem\server.log` |
| Config | `<install dir>\config.py` |

## Backups

Copy `attendance.db` and `static/photos/` to an external drive periodically. That's the entire state of the system.

## Stop / restart the server

- **Stop:** close the console window or press `Ctrl+C` inside it
- **Restart:** double-click **Attendance Server** again

## Uninstall

Right-click `uninstall.bat` → **"Run as administrator"**. This removes auto-start, firewall rule, and shortcuts. It leaves the project folder and database alone so you can keep student data — delete the folder manually if you want a full removal.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Console flashes and closes immediately | Open Command Prompt, navigate to `windows\`, run `run_server.bat` and read the error |
| Dashboard shows GSM "Not detected" | Plug in modem; install the chip driver listed above; check Device Manager → Ports |
| Pi scanner shows "Disconnected" | On the Pi: `ping <server-hostname>.local`. If it fails, the school network may be blocking mDNS — fall back to the server's IP address in the Pi's autostart |
| Auto-start doesn't run after reboot | Open Task Scheduler → Task Scheduler Library → check "Attendance System" task. Right-click → Run. If it errors, see "Last Run Result" |
| Firewall still blocks port 8000 | Run as admin: `netsh advfirewall firewall add rule name="Attendance System" dir=in action=allow protocol=TCP localport=8000` |
| Updated requirements.txt and need to refresh | Delete `.venv` folder, re-run `install.bat` |

## Updating the app

To deploy a new version:

1. Stop the running server (close its console window).
2. Replace the project files (keep `.venv`, `attendance.db`, and `static/photos/`).
3. If `requirements.txt` changed, run `install.bat` again — it reuses the existing venv and only installs new deps.
4. Start the server.
