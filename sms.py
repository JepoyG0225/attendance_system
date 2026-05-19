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

logger = logging.getLogger(__name__)
_PORTS_RESOLVED = False
_MODEM_IO_LOCK = threading.Lock()


@dataclass
class SMSResult:
    success:    bool
    modem_used: Optional[str]   # e.g. "Globe (Primary)" or "Smart (Fallback)"
    error:      str          # empty string on success


def _looks_like_gsm_port(p: Any) -> bool:
    text = " ".join([
        p.device or "",
        p.description or "",
        p.manufacturer or "",
        p.product or "",
        p.hwid or "",
    ]).lower()
    keywords = (
        "modem",
        "gsm",
        "simcom",
        "huawei",
        "zte",
        "quectel",
        "usb serial",
        "cp210",
        "ch340",
        "ftdi",
    )
    return any(k in text for k in keywords)


def _port_exists(device: str) -> bool:
    return bool(device) and os.path.exists(device)


def _port_score(p: Any, preferred_ports: set[str]) -> int:
    device = (p.device or "").lower()
    text = " ".join([
        p.device or "",
        p.description or "",
        p.manufacturer or "",
        p.product or "",
        p.hwid or "",
    ]).lower()

    score = 0
    is_gsm_like = _looks_like_gsm_port(p)
    is_serial_path = any(token in device for token in ("usbserial", "usbmodem", "ttyusb", "ttyacm"))
    is_windows_com = device.startswith("com")
    if not (is_gsm_like or is_serial_path or is_windows_com):
        return -999

    if p.device in preferred_ports:
        score += 50
    if is_gsm_like:
        score += 35

    positive_patterns = (
        "usbserial", "usb modem", "modem", "simcom", "quectel",
        "huawei", "zte", "cp210", "ch340", "ftdi", "ttyusb", "ttyacm",
    )
    if any(k in text for k in positive_patterns):
        score += 20

    if device.startswith("/dev/cu."):
        score += 8
    if device.startswith("/dev/tty."):
        score += 4
    if device.startswith("com"):
        score += 6

    negative_patterns = ("bluetooth", "irda", "debug", "wireless")
    if any(k in text for k in negative_patterns):
        score -= 60

    return score


def _resolve_modem_ports() -> None:
    global _PORTS_RESOLVED
    if not GSM_AUTO_DETECT_PORTS or SIMULATION_MODE:
        return

    ports = list(list_ports.comports())
    if not ports:
        logger.warning("No serial ports detected for GSM auto-detection.")
        return

    configured_ports = {
        GSM_PRIMARY.get("port", ""),
        GSM_SECONDARY.get("port", ""),
    }
    preferred_existing = {port for port in configured_ports if _port_exists(port)}

    scored = []
    for port_obj in ports:
        score = _port_score(port_obj, preferred_existing)
        if score > 0:
            scored.append((score, port_obj.device))

    if not scored:
        logger.warning(
            "GSM auto-detection found no suitable serial device. "
            "Keeping configured GSM ports unchanged."
        )
        return

    scored.sort(key=lambda item: item[0], reverse=True)
    candidates: list[str] = []
    for _, device in scored:
        if device and device not in candidates:
            candidates.append(device)

    if not candidates:
        return

    GSM_PRIMARY["port"] = candidates[0]
    logger.info(f"Auto-detected GSM primary port: {GSM_PRIMARY['port']}")
    if len(candidates) > 1:
        GSM_SECONDARY["port"] = candidates[1]
        logger.info(f"Auto-detected GSM secondary port: {GSM_SECONDARY['port']}")
    else:
        GSM_SECONDARY["port"] = ""
        logger.info("Only one GSM modem detected; fallback unavailable.")

    _PORTS_RESOLVED = True


# ── Low-level AT helpers ──────────────────────────────────────────────────────

def _send_at(ser: serial.Serial, command: str, wait: float = 1.0) -> str:
    ser.write((command + "\r\n").encode())
    time.sleep(wait)
    response = ser.read(ser.in_waiting or 1).decode(errors="ignore")
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
    
                # 3. Recipient
                _send_at(ser, f'AT+CMGS="{phone}"', wait=0.5)
    
                # 4. Message body + Ctrl+Z
                ser.write((message + "\x1a").encode())
                time.sleep(timeout)
    
                resp = ser.read(ser.in_waiting or 1).decode(errors="ignore")
                if "+CMGS:" in resp or "OK" in resp:
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
    if not GSM_FALLBACK_ENABLED:
        return SMSResult(success=False, modem_used=None, error=primary_err)

    ok, err = _try_send(GSM_SECONDARY, phone, message)
    if ok:
        logger.info(f"Fallback modem succeeded.")
        return SMSResult(success=True, modem_used=GSM_SECONDARY["label"], error="")

    # ── Both failed ───────────────────────────────────────────────────────────
    combined = f"Primary: {primary_err} | Fallback: {err}"
    logger.error(f"Both modems failed for {phone}: {combined}")
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
    except (serial.SerialException, OSError) as e:
        info["message"] = str(e)
        return info
    except Exception as e:
        logger.warning(f"[{label}] Unexpected error in test_modem: {e}")
        info["message"] = f"Unexpected: {e}"
        return info


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
