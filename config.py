"""
Attendance System Configuration
================================
SIMULATION_MODE = True  → Test on any PC without RFID reader or GSM modem
SIMULATION_MODE = False → Production mode on the school PC with real hardware
"""

# ── Simulation / Hardware toggle ─────────────────────────────────────────────
SIMULATION_MODE = False      # Set False when real hardware is connected

# ── School info ───────────────────────────────────────────────────────────────
SCHOOL_NAME      = "Saint Joseph Academy Cuyo"
SCHOOL_TIMEZONE  = "Asia/Manila"   # Used for time display

# ── USB GSM Modems — Dual SIM fallback ───────────────────────────────────────
# Plug TWO USB GSM modems (e.g. one Globe SIM, one Smart SIM).
# On Windows: Device Manager → Ports (COM & LPT) to find COM port numbers.
#
# The system always tries PRIMARY first. If it fails (no signal, modem error,
# send timeout), it automatically falls back to SECONDARY.

GSM_PRIMARY = {
    # Windows: "COM3"  |  macOS/Linux: "/dev/tty.usbmodem1401" or "/dev/ttyUSB0"
    # Check macOS: ls /dev/tty.* in Terminal to find your modem port
    "port":     "/dev/cu.usbserial-10",
    "baudrate": 115200,
    "timeout":  10,
    "label":    "Smart (Primary)",
}

GSM_SECONDARY = {
    "port":     "/dev/cu.usbserial-10",
    "baudrate": 115200,
    "timeout":  10,
    "label":    "Globe (Fallback)",
}

# Set to False to disable the fallback (only use primary)
GSM_FALLBACK_ENABLED = True

# Auto-detect available GSM serial ports at runtime.
# When enabled, the system will replace modem "port" values with detected ports.
GSM_AUTO_DETECT_PORTS = True

# ── SMS message templates ─────────────────────────────────────────────────────
# Placeholders: {student_name} {grade} {section} {time} {date} {school}

# 1st scan — morning arrival
SMS_AM_IN_TEMPLATE = (
    "[{school}] Magandang umaga! Si {student_name} ({grade}-{section}) "
    "ay DUMATING sa paaralan ngayong {date} ng {time}. "
    "- Automated Attendance System"
)
# 2nd scan — lunch / morning dismissal
SMS_AM_OUT_TEMPLATE = (
    "[{school}] Si {student_name} ({grade}-{section}) "
    "ay UMALIS para sa tanghalian ngayong {date} ng {time}. "
    "- Automated Attendance System"
)
# 3rd scan — afternoon arrival (back from lunch)
SMS_PM_IN_TEMPLATE = (
    "[{school}] Si {student_name} ({grade}-{section}) "
    "ay BUMALIK mula sa tanghalian ngayong {date} ng {time}. "
    "- Automated Attendance System"
)
# 4th scan — end of day dismissal
SMS_PM_OUT_TEMPLATE = (
    "[{school}] Si {student_name} ({grade}-{section}) "
    "ay UMUWI na mula sa paaralan ngayong {date} ng {time}. "
    "- Automated Attendance System"
)
# Morning absent — sent at 8:30 AM if student never scanned in
SMS_ABSENT_TEMPLATE = (
    "[{school}] Ito ay upang ipaalam na si {student_name} ({grade}-{section}) "
    "ay ABSENT ngayong umaga, {date}. Makipag-ugnayan sa paaralan kung kinakailangan. "
    "- Automated Attendance System"
)
# Afternoon absent — sent at 1:30 PM if student was present in AM but didn't return
SMS_PM_ABSENT_TEMPLATE = (
    "[{school}] Si {student_name} ({grade}-{section}) ay hindi bumalik "
    "para sa hapon ngayong {date}. Makipag-ugnayan sa paaralan kung kinakailangan. "
    "- Automated Attendance System"
)

# ── Attendance time rules ─────────────────────────────────────────────────────
LATE_HOUR   = 8     # Students arriving at or after 8:00 AM are marked LATE
LATE_MINUTE = 0

AFTERNOON_HOUR   = 12   # At or after 12:00 PM → route scans to afternoon session
AFTERNOON_MINUTE = 0

# Absent auto-check times
# At these times the system finds students who never scanned in and sends SMS
ABSENT_AM_HOUR   = 8    # 8:30 AM — check for morning no-shows
ABSENT_AM_MINUTE = 30
ABSENT_PM_HOUR   = 13   # 1:30 PM — check for afternoon no-shows
ABSENT_PM_MINUTE = 30

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_PATH = "attendance.db"

# ── Web server ────────────────────────────────────────────────────────────────
API_HOST = "0.0.0.0"    # 0.0.0.0 = accessible from other devices on same network
API_PORT = 8000
