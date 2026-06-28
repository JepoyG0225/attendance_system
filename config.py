"""
Attendance System Configuration
================================
SIMULATION_MODE = True  → Test on any PC without RFID reader or GSM modem
SIMULATION_MODE = False → Production mode on the school PC with real hardware
"""
import os

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

# Hot-plug watcher: re-scan serial ports every N seconds so newly inserted
# USB GSM modems are picked up automatically while the server is running.
# Set to 0 to disable (detection still happens on send_sms / test_all_modems).
GSM_HOTPLUG_POLL_SECONDS = 5

# ── Semaphore HTTP SMS API (online fallback) ──────────────────────────────────
# Used when the GSM modem fails (or as the only channel if no modem is working).
# Sends over the internet via https://semaphore.co — no SIM/modem required.
SEMAPHORE_ENABLED  = True
SEMAPHORE_API_KEY  = "aef206109fe75b5278c86eb2ad586ede"
# Must be a sender name APPROVED on the Semaphore dashboard. "SJAC" was applied
# for on 2026-06-25 and is currently Pending — sends will start succeeding
# automatically once Semaphore approves it (status -> Active). This account has
# no other active sender, and Semaphore's generic "SEMAPHORE" sender is rejected
# for it, so a working approved name is required before any SMS can be sent.
SEMAPHORE_SENDER   = "SJAC"
SEMAPHORE_API_URL  = "https://api.semaphore.co/api/v4/messages"

# ── OneWaySMS HTTP API (online fallback — primary online channel) ─────────────
# Works now (sender "SJAC" accepted, no approval needed). Uses the HTTPS/port-443
# endpoint so it works even if the data center blocks port 10001.
ONEWAYSMS_ENABLED = True
ONEWAYSMS_USER    = "APIUZHF1GEDWV"
ONEWAYSMS_PASS    = "APIUZHF1GEDWVUZHF1"
ONEWAYSMS_SENDER  = "SJAC"
ONEWAYSMS_API_URL = "https://sgateway.onewaysms.com/apis10.aspx"

# ── smsapiph (FREE SMS API) — primary channel ─────────────────────────────────
# Free SMS API for PH numbers (https://smsapiph.netlify.app). Tried first since
# it's free; GSM/OneWaySMS/Semaphore act as fallbacks if it's ever unavailable.
SMSAPIPH_ENABLED = True
SMSAPIPH_API_KEY = "sk-2b10k8zihqxqa9z0jzh5ixsmxe99ofp5"
SMSAPIPH_API_URL = "https://smsapiph.onrender.com/api/v1/send/sms"

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

# ── School calendar ───────────────────────────────────────────────────────────
# When True, weekends (Sat/Sun) are treated as non-school days — absent SMS
# checks are skipped, and they're excluded from monthly reports.
SKIP_WEEKENDS = True
# Days of the week treated as weekend (0=Mon, 6=Sun). Default = Sat & Sun.
WEEKEND_DAYS  = (5, 6)

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_PATH = os.getenv("ATTENDANCE_DB_PATH", "attendance.db")

# ── Web server ────────────────────────────────────────────────────────────────
API_HOST = "0.0.0.0"    # 0.0.0.0 = accessible from other devices on same network
API_PORT = 8000
