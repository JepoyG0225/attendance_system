"""
Attendance business logic — AM/PM dual-session
================================================
Scan sequence per student per day:
  1st scan → AM time-in   (morning arrival)
  2nd scan → AM time-out  (lunch / morning dismissal)
  3rd scan → PM time-in   (back from lunch)
  4th scan → PM time-out  (end of day, going home)
"""

from datetime import date, datetime, time as dtime
from typing import Optional
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
import logging

from database import Student, Attendance, SMSLog, AttendanceStatus, SMSStatus
from sms import send_sms
from config import (
    SCHOOL_NAME, SCHOOL_TIMEZONE,
    LATE_HOUR, LATE_MINUTE,
    AFTERNOON_HOUR, AFTERNOON_MINUTE,
    SMS_AM_IN_TEMPLATE, SMS_AM_OUT_TEMPLATE,
    SMS_PM_IN_TEMPLATE, SMS_PM_OUT_TEMPLATE,
    SMS_ABSENT_TEMPLATE, SMS_PM_ABSENT_TEMPLATE,
)

logger = logging.getLogger(__name__)
TZ = ZoneInfo(SCHOOL_TIMEZONE)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(TZ)

def _today() -> date:
    return _now().date()

def _is_late(t: dtime) -> bool:
    return t >= dtime(LATE_HOUR, LATE_MINUTE)

def _is_afternoon(t: dtime) -> bool:
    return t >= dtime(AFTERNOON_HOUR, AFTERNOON_MINUTE)

def _fmt_time(t: dtime) -> str:
    return t.strftime("%I:%M %p")

def _fmt_date(d: date) -> str:
    return d.strftime("%B %d, %Y")

def _sms(template: str, student: Student, t: dtime, d: date) -> str:
    return template.format(
        school=SCHOOL_NAME,
        student_name=student.full_name,
        grade=student.grade_name,
        section=student.section_name,
        time=_fmt_time(t),
        date=_fmt_date(d),
    )

def _send_and_log(student: Student, sms_text: str, sms_type: str, db: Session) -> bool:
    result = send_sms(student.parent_phone, sms_text)
    log = SMSLog(
        student_id=student.id,
        phone=student.parent_phone,
        message=sms_text,
        sms_type=sms_type,
        status=SMSStatus.sent if result.success else SMSStatus.failed,
        modem_used=result.modem_used,
        error_msg=result.error if not result.success else None,
    )
    db.add(log)
    logger.info(f"SMS [{sms_type}] {student.full_name} → {'OK' if result.success else 'FAILED'}")
    return result.success


# ── Core scan handler ─────────────────────────────────────────────────────────

def process_scan(rfid_uid: str, db: Session) -> dict:
    """
    Handles RFID scan. Returns a result dict with keys:
      success, message, student, attendance, sms_sent, action, session
    """
    rfid_uid = rfid_uid.strip().upper()
    now = _now()
    today = now.date()
    current_time = now.time().replace(microsecond=0)

    # 1. Look up student
    student: Optional[Student] = (
        db.query(Student)
        .filter(Student.rfid_uid == rfid_uid, Student.is_active == True)
        .first()
    )
    if not student:
        return {
            "success":    False,
            "message":    f"Unknown card: {rfid_uid}. Register this student first.",
            "student":    None,
            "attendance": None,
            "sms_sent":   False,
            "action":     "error",
            "session":    None,
        }

    # 2. Get or create today's attendance record
    record: Optional[Attendance] = (
        db.query(Attendance)
        .filter(Attendance.student_id == student.id, Attendance.date == today)
        .first()
    )

    sms_sent   = False
    is_pm      = _is_afternoon(current_time)

    # ── Route by current time and existing record state ───────────────────────

    if record is None:
        if not is_pm:
            # ── MORNING FIRST SCAN — AM time-in ──────────────────────────────
            status = AttendanceStatus.late if _is_late(current_time) else AttendanceStatus.present
            record = Attendance(
                student_id=student.id,
                date=today,
                am_time_in=current_time,
                status=status,
            )
            db.add(record)
            db.flush()
            action  = "am_in"
            session = "morning"
            label   = "LATE" if status == AttendanceStatus.late else "PRESENT"
            message = (
                f"✓ MORNING IN — {student.full_name} | "
                f"{student.grade_name}-{student.section_name} | "
                f"{_fmt_time(current_time)} | {label}"
            )
            sms_sent = _send_and_log(
                student, _sms(SMS_AM_IN_TEMPLATE, student, current_time, today),
                "am_in", db
            )
        else:
            # ── AFTERNOON FIRST SCAN — morning was missed, log PM time-in ────
            record = Attendance(
                student_id=student.id,
                date=today,
                pm_time_in=current_time,
                status=AttendanceStatus.present,
                notes="Morning session not recorded",
            )
            db.add(record)
            db.flush()
            action  = "pm_in"
            session = "afternoon"
            message = (
                f"✓ AFTERNOON IN — {student.full_name} | "
                f"{student.grade_name}-{student.section_name} | "
                f"{_fmt_time(current_time)} | Morning absent"
            )
            sms_sent = _send_and_log(
                student, _sms(SMS_PM_IN_TEMPLATE, student, current_time, today),
                "pm_in", db
            )

    elif not is_pm:
        # ── MORNING SUBSEQUENT SCANS ──────────────────────────────────────────
        if record.am_time_out is None and record.am_time_in is not None:
            # AM time-out
            record.am_time_out = current_time
            action  = "am_out"
            session = "morning"
            message = (
                f"✓ MORNING OUT — {student.full_name} | "
                f"{student.grade_name}-{student.section_name} | "
                f"{_fmt_time(current_time)}"
            )
            sms_sent = _send_and_log(
                student, _sms(SMS_AM_OUT_TEMPLATE, student, current_time, today),
                "am_out", db
            )
        else:
            db.commit()
            return {
                "success":    True,
                "message":    f"{student.full_name} has completed the morning session.",
                "student":    student,
                "attendance": record,
                "sms_sent":   False,
                "action":     "complete",
                "session":    "morning",
            }

    else:
        # ── AFTERNOON SUBSEQUENT SCANS ────────────────────────────────────────
        if record.pm_time_in is None:
            record.pm_time_in = current_time
            action  = "pm_in"
            session = "afternoon"
            message = (
                f"✓ AFTERNOON IN — {student.full_name} | "
                f"{student.grade_name}-{student.section_name} | "
                f"{_fmt_time(current_time)}"
            )
            sms_sent = _send_and_log(
                student, _sms(SMS_PM_IN_TEMPLATE, student, current_time, today),
                "pm_in", db
            )
        elif record.pm_time_out is None:
            record.pm_time_out = current_time
            action  = "pm_out"
            session = "afternoon"
            message = (
                f"✓ AFTERNOON OUT — {student.full_name} | "
                f"{student.grade_name}-{student.section_name} | "
                f"{_fmt_time(current_time)}"
            )
            sms_sent = _send_and_log(
                student, _sms(SMS_PM_OUT_TEMPLATE, student, current_time, today),
                "pm_out", db
            )
        else:
            db.commit()
            return {
                "success":    True,
                "message":    f"{student.full_name} has completed all sessions for today.",
                "student":    student,
                "attendance": record,
                "sms_sent":   False,
                "action":     "complete",
                "session":    None,
            }

    db.commit()
    db.refresh(record)

    return {
        "success":    True,
        "message":    message,
        "student":    student,
        "attendance": record,
        "sms_sent":   sms_sent,
        "action":     action,
        "session":    session,
    }


# ── Absent auto-check ─────────────────────────────────────────────────────────

def check_absences(session: str, db: Session) -> dict:
    """
    session="am"  → run at 8:30 AM: find students with no morning scan → mark absent + SMS
    session="pm"  → run at 1:30 PM: find students who came in AM but never returned → SMS
    Returns {"notified": int, "session": str, "names": list}
    """
    today = _today()
    now_time = _now().time().replace(microsecond=0)
    students = db.query(Student).filter(Student.is_active == True).all()
    notified = []

    for student in students:
        if not student.parent_phone:
            continue

        record: Optional[Attendance] = (
            db.query(Attendance)
            .filter(Attendance.student_id == student.id, Attendance.date == today)
            .first()
        )

        if session == "am":
            # No scan at all this morning → absent
            if record is None or record.am_time_in is None:
                # Check if we already sent an absent SMS today for this student
                already_sent = (
                    db.query(SMSLog)
                    .filter(
                        SMSLog.student_id == student.id,
                        SMSLog.sms_type == "absent_am",
                    )
                    .filter(SMSLog.sent_at >= datetime.combine(today, dtime.min))
                    .first()
                )
                if already_sent:
                    continue

                if record is None:
                    record = Attendance(
                        student_id=student.id,
                        date=today,
                        status=AttendanceStatus.absent,
                        notes="Auto-marked absent (no morning scan)",
                    )
                    db.add(record)
                    db.flush()
                else:
                    record.status = AttendanceStatus.absent
                    record.notes = "Auto-marked absent (no morning scan)"

                msg = _sms(SMS_ABSENT_TEMPLATE, student, now_time, today)
                _send_and_log(student, msg, "absent_am", db)
                notified.append(student.full_name)
                logger.info(f"[ABSENT AM] {student.full_name}")

        elif session == "pm":
            # Present in AM but no PM time-in → didn't return after lunch
            if record is not None and record.am_time_in is not None and record.pm_time_in is None:
                already_sent = (
                    db.query(SMSLog)
                    .filter(
                        SMSLog.student_id == student.id,
                        SMSLog.sms_type == "absent_pm",
                    )
                    .filter(SMSLog.sent_at >= datetime.combine(today, dtime.min))
                    .first()
                )
                if already_sent:
                    continue

                msg = _sms(SMS_PM_ABSENT_TEMPLATE, student, now_time, today)
                _send_and_log(student, msg, "absent_pm", db)
                notified.append(student.full_name)
                logger.info(f"[ABSENT PM] {student.full_name}")

    db.commit()
    return {"notified": len(notified), "session": session, "names": notified}


# ── Daily summary ──────────────────────────────────────────────────────────────

def get_daily_summary(target_date: date, db: Session) -> dict:
    records = db.query(Attendance).filter(Attendance.date == target_date).all()
    total   = db.query(Student).filter(Student.is_active == True).count()
    counts  = {s.value: 0 for s in AttendanceStatus}
    for r in records:
        counts[r.status.value] += 1
    return {
        "date":    target_date,
        "total":   total,
        "present": counts["present"],
        "late":    counts["late"],
        "absent":  total - len(records),
        "excused": counts["excused"],
    }
