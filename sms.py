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


def _resolve_modem_ports() -> None:
    global _PORTS_RESOLVED
    if _PORTS_RESOLVED or not GSM_AUTO_DETECT_PORTS or SIMULATION_MODE:
        return

    ports = list(list_ports.comports())
    if not ports:
        logger.warning("No serial ports detected for GSM auto-detection.")
        _PORTS_RESOLVED = True
        return

    candidates = [p.device for p in ports if _looks_like_gsm_port(p)]

    if not candidates:
        logger.warning(
            "GSM auto-detection found no modem-like serial device. "
            "Keeping configured GSM ports unchanged."
        )
        _PORTS_RESOLVED = True
        return

    GSM_PRIMARY["port"] = candidates[0]
    logger.info(f"Auto-detected GSM primary port: {GSM_PRIMARY['port']}")
    if len(candidates) > 1:
        GSM_SECONDARY["port"] = candidates[1]
        logger.info(f"Auto-detected GSM secondary port: {GSM_SECONDARY['port']}")
    else:
        GSM_SECONDARY["port"] = candidates[0]

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


def test_modem(modem_cfg: dict) -> tuple[bool, str]:
    """Check one modem's status and signal strength."""
    if SIMULATION_MODE:
        return True, f"Simulation — {modem_cfg['label']} check skipped"
    _resolve_modem_ports()
    try:
        with _MODEM_IO_LOCK:
            with serial.Serial(modem_cfg["port"], modem_cfg["baudrate"], timeout=modem_cfg["timeout"]) as ser:
                time.sleep(0.5)
                ser.flushInput()
                resp = _send_at(ser, "AT")
                if "OK" not in resp:
                    return False, f"No response on {modem_cfg['port']}"
                sig = _send_at(ser, "AT+CSQ").strip()
                # AT+CSQ returns +CSQ: <rssi>,<ber>
                # rssi 0–9 = very weak, 10–14 = OK, 15–19 = good, 20–31 = excellent, 99 = unknown
                return True, f"{modem_cfg['label']} OK | Signal: {sig}"
    except serial.SerialException as e:
        return False, f"{modem_cfg['label']} — {e}"


def test_all_modems() -> dict:
    """Return status of both modems. Used by the /status endpoint."""
    _resolve_modem_ports()
    p_ok, p_msg = test_modem(GSM_PRIMARY)
    if GSM_FALLBACK_ENABLED:
        s_ok, s_msg = test_modem(GSM_SECONDARY)
    else:
        s_ok, s_msg = True, "Fallback disabled"
    return {
        "primary":  {"ok": p_ok, "label": GSM_PRIMARY["label"],   "message": p_msg},
        "secondary": {"ok": s_ok, "label": GSM_SECONDARY["label"], "message": s_msg},
        "fallback_enabled": GSM_FALLBACK_ENABLED,
    }
