"""
Native desktop launcher for the Attendance System.
====================================================
Wraps the FastAPI server in a pywebview window so the admin runs as a
real Windows app (taskbar icon, no browser, single window) instead of
"open Chrome, type localhost:8000".

Flow:
  1. Open the window immediately with a splash page (file://).
  2. Start the uvicorn server on a background thread.
  3. Set the window's icon to the app-icon.ico (Win32).
  4. Poll http://localhost:8000 until it's reachable.
  5. Navigate the window to the dashboard.

The HTTP server still listens on 0.0.0.0:8000 — Pi scanners on the LAN
keep working exactly as before.

If the server is already running (e.g. Task Scheduler started it at boot),
this script skips starting another one and just opens the webview window.

Run:  python app.py
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time

import uvicorn
import webview

HOST = "0.0.0.0"
PORT = 8000
URL  = f"http://localhost:{PORT}"

WINDOW_TITLE  = "Attendance System"
WINDOW_WIDTH  = 1400
WINDOW_HEIGHT = 900
MIN_WIDTH     = 1024
MIN_HEIGHT    = 700

MIN_SPLASH_MS = 800

HERE     = os.path.dirname(os.path.abspath(__file__))
ICON_ICO = os.path.join(HERE, "static", "app-icon.ico")

logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        try:
            return s.connect_ex((host, port)) == 0
        except OSError:
            return False


def run_server() -> None:
    uvicorn.run("main:app", host=HOST, port=PORT, log_level="info", access_log=False)


def wait_for_server(timeout_s: float = 15.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if port_in_use("127.0.0.1", PORT):
            return True
        time.sleep(0.1)
    return False


def splash_url() -> str:
    return "file:///" + os.path.join(HERE, "static", "splash.html").replace("\\", "/")


def set_window_icon(title: str, ico_path: str) -> None:
    """Replace the pythonw.exe default icon with the app icon on the taskbar."""
    if not sys.platform.startswith("win") or not os.path.exists(ico_path):
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        IMAGE_ICON   = 1
        LR_LOADFROMFILE = 0x0010
        LR_DEFAULTSIZE  = 0x0040
        WM_SETICON   = 0x0080
        ICON_SMALL   = 0
        ICON_BIG     = 1

        # Set per-process AppUserModelID so Windows groups taskbar buttons
        # under our app instead of "Python" — gives the icon a stable identity.
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "NexDev.AttendanceSystem.2"
            )
        except Exception:
            pass

        # Wait briefly for the window to actually exist
        hwnd = 0
        for _ in range(40):
            hwnd = user32.FindWindowW(None, title)
            if hwnd:
                break
            time.sleep(0.05)
        if not hwnd:
            return

        # Load icon at small (16/32) and large (32/256) sizes
        small_icon = user32.LoadImageW(
            None, ico_path, IMAGE_ICON, 16, 16,
            LR_LOADFROMFILE | LR_DEFAULTSIZE,
        )
        big_icon = user32.LoadImageW(
            None, ico_path, IMAGE_ICON, 256, 256,
            LR_LOADFROMFILE | LR_DEFAULTSIZE,
        )

        if small_icon:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small_icon)
        if big_icon:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG,   big_icon)
    except Exception:
        pass


def main() -> int:
    server_already_running = port_in_use("127.0.0.1", PORT)
    if not server_already_running:
        threading.Thread(target=run_server, daemon=True).start()

    window = webview.create_window(
        WINDOW_TITLE,
        splash_url(),
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        min_size=(MIN_WIDTH, MIN_HEIGHT),
        confirm_close=False,
    )

    def boot_then_load():
        # Apply the taskbar icon as soon as the window exists
        threading.Thread(
            target=set_window_icon,
            args=(WINDOW_TITLE, ICON_ICO),
            daemon=True,
        ).start()

        start = time.time()
        ready = server_already_running or wait_for_server()
        elapsed_ms = (time.time() - start) * 1000
        if elapsed_ms < MIN_SPLASH_MS:
            time.sleep((MIN_SPLASH_MS - elapsed_ms) / 1000)
        if ready:
            window.load_url(URL)
        else:
            window.load_html(
                "<body style='font-family:sans-serif;padding:40px;color:#dc2626'>"
                f"<h2>Could not start the server on port {PORT}.</h2>"
                "<p>Check the install log at "
                "<code>%LOCALAPPDATA%\\AttendanceSystem\\install.log</code>"
                " and try restarting the app.</p></body>"
            )

    webview.start(boot_then_load)
    return 0


if __name__ == "__main__":
    sys.exit(main())
