"""
SMS Sender — Dual USB GSM Modem with automatic fallback
=========================================================
Flow:
  1. Try PRIMARY modem (e.g. Globe SIM on COM3)
  2. If it fails for any reason → try SECONDARY modem (e.g. Smart SIM on COM4)
  3. If both fail → log error, return failure

In SIMULATION_MODE the message is only printed to console.

AT command flow per modem:
  AT+CMGF=1          → Switch to text mode
  AT+CMGS="+63..."   → Specify recipient
  <message>\x1a      → Send body (Ctrl+Z = 0x1a)
"""

import serial
from serial.tools import list_ports
import time
import logging
import threading
import os
from dataclasses import dataclass
from typing import Optional, Any
from config import (
    GSM_PRIMARY,
    GSM_SECONDARY,
    GSM_FALLBACK_ENABLED,
    GSM_AUTO_DETECT_PORTS,
    SIMULATION_MODE,
)
try:
    from config import GSM_HOTPLUG_POLL_SECONDS
except ImportError:
    GSM_HOTPLUG_POLL_SECONDS = 5
try:
    from config import (
        SEMAPHORE_ENABLED, SEMAPHORE_API_KEY, SEMAPHORE_SENDER, SEMAPHORE_API_URL,
    )
except ImportError:
    SEMAPHORE_ENABLED = False
    SEMAPHORE_API_KEY = ""
    SEMAPHORE_SENDER = ""
    SEMAPHORE_API_URL = "https://api.semaphore.co/api/v4/messages"
try:
    from config import (
        ONEWAYSMS_ENABLED, ONEWAYSMS_USER, ONEWAYSMS_PASS,
        ONEWAYSMS_SENDER, ONEWAYSMS_API_URL,
    )
except ImportError:
    ONEWAYSMS_ENABLED = False
    ONEWAYSMS_USER = ""
    ONEWAYSMS_PASS = ""
    ONEWAYSMS_SENDER = "SJAC"
    ONEWAYSMS_API_URL = "https://sgateway.onewaysms.com/apis10.aspx"

logger = logging.getLogger(__name__)
_MODEM_IO_LOCK = threading.Lock()

# Hot-plug watcher state
_WATCHER_THREAD: Optional[threading.Thread] = None
_WATCHER_STOP   = threading.Event()
_LAST_PORTS:    set[str] = set()
_WATCHER_LOCK   = threading.Lock()


@dataclass
class SMSResult:
    success:    bool
    modem_used: Optional[str]   # e.g. "Globe (Primary)" or "Smart (Fallback)"
    error:      str          # empty string on success


def _port_text(p: Any) -> str:
    return " ".join([
        p.device or "",
        p.description or "",
        p.manufacturer or "",
        p.product or "",
        p.hwid or "",
    ]).lower()


# Keywords that strongly suggest a GSM modem (vendor names + chipsets).
_GSM_KEYWORDS = (
    "modem", "gsm", "lte", "wwan",
    "simcom", "huawei", "zte", "quectel", "wavecom", "sierra", "telit",
    "fibocom", "neoway", "skyland",
    # Common Philippine carriers' rebadged sticks
    "smart bro", "globe tattoo", "tnt",
    # Popular Huawei / ZTE model numbers
    "e303", "e353", "e1550", "e1750", "e3131", "e3372", "e3531",
    "mf190", "mf180", "mf60", "mf710", "mf823",
    # Common USB-to-serial chips often used in modems
    "usb serial", "cp210", "ch340", "ch9102", "ftdi", "pl2303",
)

# Sub-interface labels that mean "not the AT port" — penalize these so the
# real modem interface wins within the same physical dongle.
_NON_AT_HINTS = (
    "pcui", "pc ui", "diagnostic", "diagnostics", "application",
    "nmea", "gps", "rmnet", "ecm", "ncm", "obex",
)


def _looks_like_gsm_port(p: Any) -> bool:
    return any(k in _port_text(p) for k in _GSM_KEYWORDS)


def _port_exists(device: str) -> bool:
    if not device:
        return False
    # On Windows COM ports aren't files, so os.path.exists is unreliable.
    # Fall back to "is it currently enumerated by pyserial?"
    if device.upper().startswith("COM"):
        try:
            return any((p.device or "").upper() == device.upper() for p in list_ports.comports())
        except Exception:
            return False
    return os.path.exists(device)


def _port_score(p: Any, preferred_ports: set[str]) -> int:
    device = (p.device or "").lower()
    text   = _port_text(p)

    is_gsm_like    = _looks_like_gsm_port(p)
    is_serial_path = any(token in device for token in ("usbserial", "usbmodem", "ttyusb", "ttyacm"))
    is_windows_com = device.startswith("com")
    if not (is_gsm_like or is_serial_path or is_windows_com):
        return -999

    score = 0
    if p.device in preferred_ports:
        score += 50
    if is_gsm_like:
        score += 35

    # Bonus for the interface that's almost certainly the AT command port.
    if "modem" in text:
        score += 25
    if " at " in f" {text} " or text.endswith(" at"):
        score += 10

    # Generic USB-serial chip names (FTDI etc.) — weaker signal than vendor name.
    if any(k in text for k in ("usbserial", "usb modem", "cp210", "ch340", "ftdi", "pl2303",
                                "ttyusb", "ttyacm")):
        score += 12

    # Path/OS preferences
    if device.startswith("/dev/cu."):
        score += 8
    if device.startswith("/dev/tty."):
        score += 4
    if device.startswith("com"):
        score += 6

    # Penalize sub-interfaces that aren't the AT command port.
    if any(k in text for k in _NON_AT_HINTS):
        score -= 40

    # Hard-blocked categories
    if any(k in text for k in ("bluetooth", "irda", "debug", "wireless lan")):
        score -= 80

    return score


def _usb_group_key(p: Any) -> tuple:
    """
    Identify which physical USB device a port belongs to. A single GSM dongle
    typically exposes 2-3 COM ports (modem / PCUI / diag) — all share the same
    VID, PID, and serial number, but live on different USB interfaces.

    Grouping by this key lets us pick ONE port per dongle so a single stick
    can't get assigned as both primary and fallback (same SIM, no real fallback).
    """
    vid    = getattr(p, "vid", None)
    pid    = getattr(p, "pid", None)
    serial = getattr(p, "serial_number", None) or ""
    # Strip the interface index from `location` so different interfaces on the
    # same physical USB device still share a key.
    location = getattr(p, "location", "") or ""
    location_root = location.split(".")[0] if location else ""
    return (vid, pid, serial.lower(), location_root)


def _probe_at(device: str, baudrate: int = 115200, timeout: float = 2.0) -> bool:
    """
    Open a serial port briefly and send `AT`. Returns True if the device
    replies with `OK` within `timeout` seconds.

    Used by _probe_and_assign() to confirm a candidate port is actually an
    AT-speaking modem (not a debug interface, not a different USB-serial
    device that just happened to be named "USB Serial").
    """
    if not _port_exists(device):
        return False
    try:
        with _MODEM_IO_LOCK:
            with serial.Serial(device, baudrate, timeout=timeout) as ser:
                time.sleep(0.2)
                ser.flushInput()
                ser.write(b"AT\r\n")
                deadline = time.time() + timeout
                buf = ""
                while time.time() < deadline:
                    chunk = ser.read(ser.in_waiting or 1).decode(errors="ignore")
                    if chunk:
                        buf += chunk
                        if "OK" in buf:
                            return True
                        if "ERROR" in buf:
                            return False
                    else:
                        time.sleep(0.05)
                return "OK" in buf
    except (serial.SerialException, OSError, PermissionError) as e:
        logger.debug(f"AT probe failed on {device}: {type(e).__name__}: {e}")
        return False
    except Exception as e:
        logger.debug(f"AT probe unexpected error on {device}: {e}")
        return False


def _assign_ports(devices: list[str]) -> None:
    """Update GSM_PRIMARY/GSM_SECONDARY in place, logging only on change.

    Pass an empty list to clear both — used when the modem is unplugged or
    every candidate failed the AT probe, so we don't keep advertising a
    known-broken port that just returns 'Access denied' on every /status.
    """
    new_primary   = devices[0] if devices else ""
    new_secondary = devices[1] if len(devices) > 1 else ""

    if GSM_PRIMARY.get("port") != new_primary:
        if new_primary:
            logger.info(f"Auto-detected GSM primary port: {new_primary}")
        else:
            logger.info("GSM primary port cleared — no responsive modem detected.")
    GSM_PRIMARY["port"] = new_primary

    if GSM_SECONDARY.get("port") != new_secondary:
        if new_secondary:
            logger.info(f"Auto-detected GSM secondary port: {new_secondary}")
        elif GSM_SECONDARY.get("port"):
            logger.info("GSM fallback port cleared — only one (or zero) modem detected.")
    GSM_SECONDARY["port"] = new_secondary


def _resolve_modem_ports(active_probe: bool = False) -> dict:
    """
    Scan available serial ports, identify the GSM-likely ones, and assign
    GSM_PRIMARY / GSM_SECONDARY ports.

    Steps:
      1. Score every enumerated COM/tty by name, description, hwid.
      2. Drop ports with negative scores.
      3. Sort by score, then dedupe by physical USB device (so a single
         dongle exposing 3 COM ports doesn't get assigned as both primary
         and fallback for itself).
      4. If `active_probe=True`, open each top candidate and send AT.
         Keep only ports that respond OK.
      5. Assign the top one as PRIMARY, the next one (from a *different*
         USB device) as SECONDARY.

    Returns a small debug dict so callers (e.g. /modems/rescan) can show
    what happened.
    """
    info = {"considered": [], "rejected": [], "probed": [], "assigned": {"primary": "", "secondary": ""}}

    if not GSM_AUTO_DETECT_PORTS or SIMULATION_MODE:
        return info

    ports = list(list_ports.comports())
    if not ports:
        logger.warning("No serial ports detected for GSM auto-detection.")
        return info

    configured_ports = {GSM_PRIMARY.get("port", ""), GSM_SECONDARY.get("port", "")}
    preferred        = {p for p in configured_ports if _port_exists(p)}

    # ── 1. Score everything ──────────────────────────────────────────────────
    scored: list[tuple[int, Any]] = []
    for p in ports:
        sc = _port_score(p, preferred)
        info["considered"].append({"device": p.device, "score": sc, "desc": p.description or ""})
        if sc > 0:
            scored.append((sc, p))
        else:
            info["rejected"].append({"device": p.device, "reason": p.description or "low score"})

    if not scored:
        logger.warning("GSM auto-detection found no candidate serial device.")
        return info

    scored.sort(key=lambda item: item[0], reverse=True)

    # ── 2. Dedupe by physical USB device (keep the highest-scored interface) ─
    seen_groups: set = set()
    deduped: list[Any] = []
    for sc, p in scored:
        key = _usb_group_key(p)
        if key in seen_groups:
            continue
        seen_groups.add(key)
        deduped.append(p)

    if not deduped:
        return info

    # ── 3. Optional active AT probe ──────────────────────────────────────────
    # Ports that fail with PermissionError are almost certainly:
    #   - Windows phantoms (the device used to be here, isn't now), or
    #   - Held by another process (Mobile Partner / Smart Bro Connect / etc.).
    # In either case, blindly assigning them as "primary" guarantees that
    # every /status poll surfaces an "Access denied" error in the UI — so we
    # demote them and prefer ANY responsive port, even with a lower score.
    final: list[str] = []
    if active_probe:
        denied: list[str] = []
        for p in deduped:
            ok = _probe_at(p.device, baudrate=GSM_PRIMARY.get("baudrate", 115200), timeout=1.5)
            info["probed"].append({"device": p.device, "responded": ok})
            if ok:
                final.append(p.device)
                if len(final) >= 2:
                    break
            else:
                # We can't tell PermissionError from "no AT response" here,
                # but both indicate "don't auto-assign this port" — caller
                # can always pick it manually via config.py.
                denied.append(p.device)
        if not final:
            # Nothing responsive at all. Pick name-scored ports that we did
            # NOT just fail to probe — avoids re-assigning a known-broken port.
            safe_fallback = [p.device for p in deduped if p.device and p.device not in denied]
            if safe_fallback:
                logger.warning(
                    "Active AT probe found no responsive modem. Falling back to "
                    "name-scored ports that weren't proven broken: %s",
                    safe_fallback[:2],
                )
                final = safe_fallback[:2]
            else:
                logger.warning(
                    "All candidates failed AT probe (%s). Leaving modem ports "
                    "UNASSIGNED — fix the hardware or close any program "
                    "holding the COM port, then click Rescan.",
                    denied,
                )
                final = []
    else:
        final = [p.device for p in deduped[:2] if p.device]

    info["assigned"]["primary"]   = final[0] if final else ""
    info["assigned"]["secondary"] = final[1] if len(final) > 1 else ""

    _assign_ports(final)
    return info


# ── Low-level AT helpers ──────────────────────────────────────────────────────

def _read_until(ser: serial.Serial, terminators: tuple[str, ...], max_wait: float) -> str:
    """Accumulate serial output until one of `terminators` appears (e.g. OK,
    ERROR, '>') or max_wait elapses. Replaces the old fixed-sleep + single-read
    approach, which missed the reply whenever the modem answered a beat slower
    than the sleep — the cause of spurious 'Failed to set text mode' errors."""
    deadline = time.time() + max_wait
    response = ""
    while time.time() < deadline:
        n = ser.in_waiting
        if n:
            response += ser.read(n).decode(errors="ignore")
            if any(t in response for t in terminators):
                break
        else:
            time.sleep(0.05)
    return response


def _send_at(ser: serial.Serial, command: str, wait: float = 1.0,
             terminators: tuple[str, ...] = ("OK", "ERROR"), max_wait: float = 8.0) -> str:
    ser.write((command + "\r\n").encode())
    response = _read_until(ser, terminators, max_wait)
    logger.debug(f"AT>> {command!r}  <<  {response!r}")
    return response


def _try_send(modem_cfg: dict, phone: str, message: str) -> tuple[bool, str]:
    """
    Attempt to send SMS using one modem config.
    Returns (success, error_message).
    """
    port    = modem_cfg["port"]
    label   = modem_cfg["label"]
    baud    = modem_cfg["baudrate"]
    timeout = modem_cfg["timeout"]

    try:
        with _MODEM_IO_LOCK:
            with serial.Serial(port=port, baudrate=baud, timeout=timeout) as ser:
                time.sleep(0.5)
                ser.flushInput()
    
                # 1. Alive check
                resp = _send_at(ser, "AT")
                if "OK" not in resp:
                    return False, f"[{label}] Modem not responding on {port}"
    
                # 2. Text mode
                resp = _send_at(ser, "AT+CMGF=1")
                if "OK" not in resp:
                    return False, f"[{label}] Failed to set text mode"
    
                # 3. Recipient — wait for the '>' prompt before sending the body
                resp = _send_at(ser, f'AT+CMGS="{phone}"', terminators=(">", "ERROR"), max_wait=8.0)
                if ">" not in resp:
                    return False, f"[{label}] No '>' prompt for recipient ({resp.strip() or 'no reply'})"

                # 4. Message body + Ctrl+Z — submission can take several seconds
                ser.write((message + "\x1a").encode())
                resp = _read_until(ser, ("+CMGS", "ERROR"), max_wait=max(timeout, 20.0))
                if "+CMGS" in resp or "OK" in resp:
                    logger.info(f"SMS sent via {label} to {phone}")
                    return True, ""
                else:
                    err = resp.strip() or "No +CMGS confirmation"
                    logger.warning(f"[{label}] SMS unconfirmed: {err}")
                    return False, f"[{label}] {err}"

    except serial.SerialException as e:
        logger.warning(f"[{label}] Serial error: {e}")
        return False, f"[{label}] Serial error: {e}"
    except Exception as e:
        logger.warning(f"[{label}] Unexpected error: {e}")
        return False, f"[{label}] {e}"


# ── Semaphore HTTP SMS fallback ─────────────────────────────────────────────────

def _ph_local(phone: str) -> str:
    """Normalise a PH number to 11-digit local form (09xxxxxxxxx) for Semaphore."""
    p = (phone or "").strip().replace(" ", "").replace("-", "")
    if p.startswith("+63"):
        p = "0" + p[3:]
    elif p.startswith("63") and len(p) == 12:
        p = "0" + p[2:]
    return p


def _ph_intl(phone: str) -> str:
    """Normalise a PH number to international form without '+' (639xxxxxxxxx)."""
    p = (phone or "").strip().replace(" ", "").replace("-", "").replace("+", "")
    if p.startswith("0"):
        p = "63" + p[1:]
    elif not p.startswith("63"):
        p = "63" + p
    return p


def _send_via_onewaysms(phone: str, message: str) -> tuple[bool, str]:
    """Send one SMS via the OneWaySMS HTTP API. Returns (success, error).
    OneWaySMS returns a positive MTID on success, or a negative error code."""
    import urllib.request, urllib.parse, urllib.error
    if not (ONEWAYSMS_ENABLED and ONEWAYSMS_USER):
        return False, "OneWaySMS not configured"
    params = urllib.parse.urlencode({
        "apiusername":  ONEWAYSMS_USER,
        "apipassword":  ONEWAYSMS_PASS,
        "mobileno":     _ph_intl(phone),
        "senderid":     ONEWAYSMS_SENDER,
        "languagetype": "1",
        "message":      message,
    })
    url = f"{ONEWAYSMS_API_URL}?{params}"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            resp = r.read().decode(errors="ignore").strip()
        try:
            mtid = int(resp.split()[0]) if resp else -999
        except ValueError:
            mtid = -999
        if mtid > 0:
            logger.info(f"SMS sent via OneWaySMS to {phone} (MTID={mtid})")
            return True, ""
        return False, f"OneWaySMS error code: {resp or 'empty'}"
    except Exception as e:
        return False, f"OneWaySMS error: {e}"


def _send_via_semaphore(phone: str, message: str) -> tuple[bool, str]:
    """Send one SMS via the Semaphore HTTP API. Returns (success, error)."""
    import json
    import urllib.request, urllib.parse, urllib.error
    if not (SEMAPHORE_ENABLED and SEMAPHORE_API_KEY):
        return False, "Semaphore not configured"
    fields = {
        "apikey":  SEMAPHORE_API_KEY,
        "number":  _ph_local(phone),
        "message": message,
    }
    if SEMAPHORE_SENDER:
        fields["sendername"] = SEMAPHORE_SENDER
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(SEMAPHORE_API_URL, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode(errors="ignore")
        arr = json.loads(body)
        # Success: a JSON array of message objects each carrying a message_id.
        if isinstance(arr, list) and arr and arr[0].get("message_id"):
            status = arr[0].get("status", "")
            logger.info(f"SMS sent via Semaphore to {phone} (status={status})")
            return True, ""
        return False, f"Semaphore: unexpected response {body[:160]}"
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="ignore")[:160] if hasattr(e, "read") else ""
        return False, f"Semaphore HTTP {e.code}: {detail}"
    except Exception as e:
        return False, f"Semaphore error: {e}"


# ── Public API ─────────────────────────────────────────────────────────────────

def send_sms(phone: str, message: str) -> SMSResult:
    """
    Send SMS with automatic fallback to secondary modem.

    Returns an SMSResult with .success, .modem_used, and .error.
    """
    if SIMULATION_MODE:
        print(f"\n[SMS SIMULATION]")
        print(f"  To      : {phone}")
        print(f"  Message : {message}\n")
        logger.info(f"[SIM] SMS to {phone}")
        return SMSResult(success=True, modem_used="Simulation", error="")

    _resolve_modem_ports()

    # ── Try primary ───────────────────────────────────────────────────────────
    ok, err = _try_send(GSM_PRIMARY, phone, message)
    if ok:
        return SMSResult(success=True, modem_used=GSM_PRIMARY["label"], error="")

    primary_err = err
    logger.warning(f"Primary modem failed: {primary_err}. Trying fallback...")

    # ── Try secondary (fallback) ──────────────────────────────────────────────
    gsm_err = primary_err
    if GSM_FALLBACK_ENABLED:
        ok, err = _try_send(GSM_SECONDARY, phone, message)
        if ok:
            logger.info(f"Fallback modem succeeded.")
            return SMSResult(success=True, modem_used=GSM_SECONDARY["label"], error="")
        gsm_err = f"Primary: {primary_err} | Fallback: {err}"

    # ── Online fallbacks (GSM modem is unreliable: flaky SIM holder) ──────────
    # Try OneWaySMS first (works now), then Semaphore as a last resort.
    errors = [f"GSM[{gsm_err}]"]

    if ONEWAYSMS_ENABLED and ONEWAYSMS_USER:
        logger.warning(f"GSM failed ({gsm_err}). Trying OneWaySMS...")
        ok, ow_err = _send_via_onewaysms(phone, message)
        if ok:
            return SMSResult(success=True, modem_used="OneWaySMS", error="")
        errors.append(f"OneWaySMS[{ow_err}]")

    if SEMAPHORE_ENABLED and SEMAPHORE_API_KEY:
        logger.warning("Trying Semaphore HTTP API...")
        ok, sem_err = _send_via_semaphore(phone, message)
        if ok:
            return SMSResult(success=True, modem_used="Semaphore", error="")
        errors.append(f"Semaphore[{sem_err}]")

    combined = " | ".join(errors)
    logger.error(f"All SMS channels failed for {phone}: {combined}")
    return SMSResult(success=False, modem_used=None, error=combined)


def _parse_signal(raw: str) -> tuple[Optional[int], str]:
    """Parse AT+CSQ response into (rssi, human label). 99 = unknown."""
    import re
    m = re.search(r"\+CSQ:\s*(\d+)", raw)
    if not m:
        return None, "Unknown"
    rssi = int(m.group(1))
    if rssi == 99:
        return None, "Unknown"
    if rssi <= 9:
        return rssi, "Very weak"
    if rssi <= 14:
        return rssi, "OK"
    if rssi <= 19:
        return rssi, "Good"
    return rssi, "Excellent"


def test_modem(modem_cfg: dict) -> dict:
    """Check one modem's status and signal strength. Returns full info dict."""
    label = modem_cfg.get("label", "Modem")
    port  = modem_cfg.get("port", "")
    info  = {"ok": False, "label": label, "port": port, "signal_rssi": None,
             "signal_label": "", "message": ""}

    if SIMULATION_MODE:
        info.update(ok=True, message="Simulation — check skipped",
                    signal_label="Simulation")
        return info

    _resolve_modem_ports()
    info["port"] = modem_cfg.get("port", "")

    if not info["port"] or not _port_exists(info["port"]):
        info["message"] = "Not detected"
        return info

    try:
        with _MODEM_IO_LOCK:
            with serial.Serial(info["port"], modem_cfg["baudrate"], timeout=modem_cfg["timeout"]) as ser:
                time.sleep(0.5)
                ser.flushInput()
                resp = _send_at(ser, "AT")
                if "OK" not in resp:
                    info["message"] = f"No response on {info['port']}"
                    return info
                sig_raw = _send_at(ser, "AT+CSQ").strip()
                rssi, sig_label = _parse_signal(sig_raw)
                info.update(ok=True, signal_rssi=rssi, signal_label=sig_label,
                            message=f"OK | Signal: {sig_label}"
                                    + (f" ({rssi}/31)" if rssi is not None else ""))
                return info
    except (serial.SerialException, OSError, PermissionError) as e:
        info["message"] = _humanize_serial_error(info["port"], e)
        return info
    except Exception as e:
        logger.warning(f"[{label}] Unexpected error in test_modem: {e}")
        info["message"] = f"Unexpected: {e}"
        return info


def _humanize_serial_error(port: str, err: Exception) -> str:
    """
    Translate raw pyserial / Windows errors into short, actionable messages
    for the dashboard. Falls back to str(err) for unknown cases so we don't
    lose information.
    """
    msg = str(err).lower()
    # Windows error 13 / ERROR_ACCESS_DENIED — port is held by another process
    # or the COM number is a phantom left behind in the registry.
    if "access is denied" in msg or "permissionerror" in msg or "errno 13" in msg:
        return (f"{port} is locked or unplugged. Close any modem-manager app "
                f"(Mobile Partner / Smart Bro / Globe Tattoo) or unplug + replug "
                f"the modem, then click Rescan.")
    # Windows error 2 / ERROR_FILE_NOT_FOUND — the COM port doesn't exist.
    if "system cannot find" in msg or "no such file" in msg or "errno 2" in msg:
        return f"{port} no longer exists. Plug the modem back in and click Rescan."
    # Generic "could not open" — usually a driver problem.
    if "could not open port" in msg:
        return f"{port} could not be opened. Check Device Manager and reinstall the modem driver if needed."
    return str(err)


def test_all_modems() -> dict:
    """Return status of both modems. Used by the /status endpoint."""
    _resolve_modem_ports()
    primary = test_modem(GSM_PRIMARY)
    if GSM_FALLBACK_ENABLED:
        secondary = test_modem(GSM_SECONDARY)
    else:
        secondary = {"ok": False, "label": GSM_SECONDARY.get("label", "Secondary"),
                     "port": "", "signal_rssi": None, "signal_label": "",
                     "message": "Fallback disabled"}
    return {
        "primary":          primary,
        "secondary":        secondary,
        "fallback_enabled": GSM_FALLBACK_ENABLED,
    }


# ── Hot-plug watcher ──────────────────────────────────────────────────────────

def _current_port_set() -> set[str]:
    """Snapshot of devices currently exposed by pyserial."""
    try:
        return {p.device for p in list_ports.comports() if p.device}
    except Exception as e:
        logger.debug(f"comports() failed: {e}")
        return set()


def rescan_modems() -> dict:
    """
    Force a re-detection of GSM modems right now and return their status.

    Performs an active AT probe so we accept only ports that actually respond
    as a GSM modem, then dedupes by USB device so a single dongle (which
    typically exposes 2–3 COM ports) can't get assigned as both primary and
    fallback. Call this from POST /modems/rescan after plugging in a stick.
    """
    global _LAST_PORTS
    with _WATCHER_LOCK:
        _LAST_PORTS = _current_port_set()
    _resolve_modem_ports(active_probe=True)
    return test_all_modems()


def _modem_watcher_loop(poll_seconds: int) -> None:
    """
    Background thread: poll serial ports and re-resolve modem mapping when a
    new device appears or one disappears.

    The thread is idempotent — if nothing changes, it does almost nothing
    (just a list_ports.comports() call every `poll_seconds`).
    """
    global _LAST_PORTS
    logger.info(f"GSM hot-plug watcher started (poll every {poll_seconds}s)")

    # Prime the snapshot without logging churn on first tick.
    with _WATCHER_LOCK:
        _LAST_PORTS = _current_port_set()

    while not _WATCHER_STOP.is_set():
        if _WATCHER_STOP.wait(timeout=poll_seconds):
            break

        try:
            current = _current_port_set()
            with _WATCHER_LOCK:
                previous = _LAST_PORTS
                added    = current - previous
                removed  = previous - current
                _LAST_PORTS = current

            if not added and not removed:
                continue

            if added:
                logger.info(f"[Hot-plug] New serial port(s) detected: {sorted(added)}")
            if removed:
                logger.info(f"[Hot-plug] Serial port(s) removed: {sorted(removed)}")

            # Re-resolve the GSM port mapping. We active-probe on hot-plug so
            # newly-inserted dongles are verified (and PCUI/diag interfaces
            # from the same stick get filtered out). _assign_ports() only
            # logs when primary/secondary actually changes.
            _resolve_modem_ports(active_probe=True)

        except Exception as e:
            logger.exception(f"GSM hot-plug watcher tick failed: {e}")

    logger.info("GSM hot-plug watcher stopped")


def start_modem_watcher() -> None:
    """
    Start the background hot-plug watcher. Safe to call multiple times — only
    one thread will ever be running. No-op in simulation mode or if disabled.
    """
    global _WATCHER_THREAD

    if SIMULATION_MODE:
        logger.info("Modem watcher disabled (SIMULATION_MODE)")
        return
    if not GSM_AUTO_DETECT_PORTS:
        logger.info("Modem watcher disabled (GSM_AUTO_DETECT_PORTS = False)")
        return
    if GSM_HOTPLUG_POLL_SECONDS <= 0:
        logger.info("Modem hot-plug watcher disabled (GSM_HOTPLUG_POLL_SECONDS = 0)")
        return
    if _WATCHER_THREAD is not None and _WATCHER_THREAD.is_alive():
        return

    _WATCHER_STOP.clear()
    _WATCHER_THREAD = threading.Thread(
        target=_modem_watcher_loop,
        args=(GSM_HOTPLUG_POLL_SECONDS,),
        name="gsm-hotplug-watcher",
        daemon=True,
    )
    _WATCHER_THREAD.start()


def stop_modem_watcher() -> None:
    """Stop the watcher thread (called on shutdown)."""
    _WATCHER_STOP.set()
    if _WATCHER_THREAD is not None:
        _WATCHER_THREAD.join(timeout=2)
