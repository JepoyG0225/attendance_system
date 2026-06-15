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
import threading
from queue import Queue

from database import (
    Student, Attendance, SMSLog, Holiday, AttendanceStatus, SMSStatus, SessionLocal,
    Teacher, TeacherAttendance,
)
from sms import send_sms
from config import (
    SCHOOL_NAME, SCHOOL_TIMEZONE,
    LATE_HOUR, LATE_MINUTE,
    AFTERNOON_HOUR, AFTERNOON_MINUTE,
)
# SMS templates + teacher recipients are read at send time from the
# settings table so the dashboard can edit them without a restart.
from settings import get_setting, get_teacher_recipients
# Optional newer config values; provide sensible defaults if the user's
# config.py was written before these settings existed.
try:
    from config import SKIP_WEEKENDS
except ImportError:
    SKIP_WEEKENDS = True
try:
    from config import WEEKEND_DAYS
except ImportError:
    WEEKEND_DAYS = (5, 6)   # Saturday, Sunday

logger = logging.getLogger(__name__)
TZ = ZoneInfo(SCHOOL_TIMEZONE)
_SMS_QUEUE: "Queue[dict]" = Queue()


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


def is_school_day(d: date, db: Session) -> tuple[bool, Optional[str]]:
    """Return (is_school_day, reason_if_not).
    Skips configured weekend days and any date in the holidays table
    (recurring holidays match by month+day regardless of year).
    """
    if SKIP_WEEKENDS and d.weekday() in WEEKEND_DAYS:
        return False, "Weekend"
    fixed = db.query(Holiday).filter(Holiday.date == d, Holiday.is_recurring == False).first()
    if fixed:
        return False, fixed.name
    # Recurring: same month + day, any year
    recurring = (
        db.query(Holiday)
        .filter(Holiday.is_recurring == True)
        .all()
    )
    for h in recurring:
        if h.date.month == d.month and h.date.day == d.day:
            return False, h.name
    return True, None

def _sms(template_key: str, student: Student, t: dtime, d: date) -> str:
    """Render a STUDENT SMS template stored in settings.
    Supports both {name} (new) and {student_name} (legacy) placeholders."""
    template = get_setting(template_key)
    return template.format(
        school=SCHOOL_NAME,
        name=student.full_name,
        student_name=student.full_name,        # legacy alias
        grade=student.grade_name,
        section=student.section_name,
        time=_fmt_time(t),
        date=_fmt_date(d),
    )


def _teacher_sms(template_key: str, teacher: "Teacher", t: dtime, d: date) -> str:
    """Render a TEACHER SMS template stored in settings."""
    template = get_setting(template_key)
    return template.format(
        school=SCHOOL_NAME,
        name=teacher.full_name,
        department=teacher.department or "—",
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


def _enqueue_sms_and_log(student: Student, sms_text: str, sms_type: str, db: Session) -> bool:
    log = SMSLog(
        student_id=student.id,
        phone=student.parent_phone,
        message=sms_text,
        sms_type=sms_type,
        status=SMSStatus.pending,
        modem_used=None,
        error_msg=None,
    )
    db.add(log)
    db.flush()
    _SMS_QUEUE.put({
        "log_id": log.id,
        "student_name": student.full_name,
        "phone": student.parent_phone,
        "message": sms_text,
        "sms_type": sms_type,
    })
    return True


def _enqueue_teacher_sms(teacher: Teacher, sms_text: str, sms_type: str, db: Session) -> bool:
    """
    Fan out a teacher SMS to every configured recipient (typically 2 admin
    numbers like principal + HR). Each recipient gets its own SMSLog row +
    queue job so we can track delivery per-number.
    Returns True if at least one SMS was queued.
    """
    recipients = get_teacher_recipients(db=db)
    if not recipients:
        return False
    sent_count = 0
    for phone in recipients:
        log = SMSLog(
            teacher_id=teacher.id,
            phone=phone,
            message=sms_text,
            sms_type=f"teacher_{sms_type}",
            status=SMSStatus.pending,
        )
        db.add(log)
        db.flush()
        _SMS_QUEUE.put({
            "log_id":       log.id,
            "student_name": f"Teacher: {teacher.full_name}",
            "phone":        phone,
            "message":      sms_text,
            "sms_type":     f"teacher_{sms_type}",
        })
        sent_count += 1
    return sent_count > 0


def _sms_worker() -> None:
    while True:
        job = _SMS_QUEUE.get()
        worker_db = SessionLocal()
        try:
            result = send_sms(job["phone"], job["message"])
            log = worker_db.query(SMSLog).filter(SMSLog.id == job["log_id"]).first()
            if log:
                log.status = SMSStatus.sent if result.success else SMSStatus.failed
                log.modem_used = result.modem_used
                log.error_msg = result.error if not result.success else None
                worker_db.commit()
            logger.info(f"SMS [{job['sms_type']}] {job['student_name']} processed")
        except Exception as e:
            log = worker_db.query(SMSLog).filter(SMSLog.id == job["log_id"]).first()
            if log:
                log.status = SMSStatus.failed
                log.error_msg = str(e)
                worker_db.commit()
            logger.error(f"SMS worker failed for log_id={job['log_id']}: {e}")
        finally:
            worker_db.close()
            _SMS_QUEUE.task_done()


threading.Thread(target=_sms_worker, daemon=True).start()


# ── Core scan handler ─────────────────────────────────────────────────────────

def process_scan(rfid_uid: str, db: Session) -> dict:
    """
    Top-level RFID dispatch. Tries the students table first, then teachers,
    so cards are routed to the right attendance ledger. Returns a result dict
    with the unified keys:
      success, message, kind ("student"|"teacher"|None),
      student, teacher, attendance, teacher_attendance,
      sms_sent, action, session
    """
    rfid_uid_norm = rfid_uid.strip().upper()

    student = (
        db.query(Student)
        .filter(Student.rfid_uid == rfid_uid_norm, Student.is_active == True)
        .first()
    )
    if student:
        return _process_student_scan(student, db)

    teacher = (
        db.query(Teacher)
        .filter(Teacher.rfid_uid == rfid_uid_norm, Teacher.is_active == True)
        .first()
    )
    if teacher:
        return _process_teacher_scan(teacher, db)

    # No match in either table
    return {
        "success":            False,
        "message":            f"Unknown card: {rfid_uid_norm}. Register this person first.",
        "kind":               None,
        "student":            None,
        "teacher":            None,
        "attendance":         None,
        "teacher_attendance": None,
        "sms_sent":           False,
        "action":             "error",
        "session":            None,
    }


def _process_student_scan(student: Student, db: Session) -> dict:
    """
    Per-student scan handler. Returns the same result dict shape as
    process_scan, with kind="student".
    """
    now = _now()
    today = now.date()
    current_time = now.time().replace(microsecond=0)

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
            sms_sent = _enqueue_sms_and_log(
                student, _sms("sms.student.am_in_template", student, current_time, today),
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
            sms_sent = _enqueue_sms_and_log(
                student, _sms("sms.student.pm_in_template", student, current_time, today),
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
            sms_sent = _enqueue_sms_and_log(
                student, _sms("sms.student.am_out_template", student, current_time, today),
                "am_out", db
            )
        else:
            db.commit()
            return {
                "success":            True,
                "message":            f"{student.full_name} has completed the morning session.",
                "kind":               "student",
                "student":            student,
                "teacher":            None,
                "attendance":         record,
                "teacher_attendance": None,
                "sms_sent":           False,
                "action":             "complete",
                "session":            "morning",
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
            sms_sent = _enqueue_sms_and_log(
                student, _sms("sms.student.pm_in_template", student, current_time, today),
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
            sms_sent = _enqueue_sms_and_log(
                student, _sms("sms.student.pm_out_template", student, current_time, today),
                "pm_out", db
            )
        else:
            db.commit()
            return {
                "success":            True,
                "message":            f"{student.full_name} has completed all sessions for today.",
                "kind":               "student",
                "student":            student,
                "teacher":            None,
                "attendance":         record,
                "teacher_attendance": None,
                "sms_sent":           False,
                "action":             "complete",
                "session":            None,
            }

    db.commit()
    db.refresh(record)

    return {
        "success":            True,
        "message":            message,
        "kind":               "student",
        "student":            student,
        "teacher":            None,
        "attendance":         record,
        "teacher_attendance": None,
        "sms_sent":           sms_sent,
        "action":             action,
        "session":            session,
    }


# ── Teacher scan handler ──────────────────────────────────────────────────────

def _process_teacher_scan(teacher: Teacher, db: Session) -> dict:
    """
    Per-teacher scan handler. Same AM/PM 4-scan flow as students, but:
      - writes to teacher_attendance table
      - never sends SMS (teachers don't have parent contacts)
      - status doesn't track late/absent for now (could add later if needed)
    Returns the same dict shape as _process_student_scan, with kind="teacher".
    """
    now = _now()
    today = now.date()
    current_time = now.time().replace(microsecond=0)
    dept = teacher.department or "—"

    record: Optional[TeacherAttendance] = (
        db.query(TeacherAttendance)
        .filter(TeacherAttendance.teacher_id == teacher.id,
                TeacherAttendance.date == today)
        .first()
    )

    is_pm = _is_afternoon(current_time)

    if record is None:
        if not is_pm:
            status = AttendanceStatus.late if _is_late(current_time) else AttendanceStatus.present
            record = TeacherAttendance(
                teacher_id=teacher.id,
                date=today,
                am_time_in=current_time,
                status=status,
            )
            db.add(record); db.flush()
            action  = "am_in"
            session = "morning"
            label   = "LATE" if status == AttendanceStatus.late else "PRESENT"
            message = f"✓ TEACHER IN — {teacher.full_name} | {dept} | {_fmt_time(current_time)} | {label}"
        else:
            record = TeacherAttendance(
                teacher_id=teacher.id,
                date=today,
                pm_time_in=current_time,
                status=AttendanceStatus.present,
                notes="Morning session not recorded",
            )
            db.add(record); db.flush()
            action  = "pm_in"
            session = "afternoon"
            message = f"✓ TEACHER PM IN — {teacher.full_name} | {dept} | {_fmt_time(current_time)} | Morning absent"

    elif not is_pm:
        if record.am_time_out is None and record.am_time_in is not None:
            record.am_time_out = current_time
            action  = "am_out"
            session = "morning"
            message = f"✓ TEACHER OUT — {teacher.full_name} | {dept} | {_fmt_time(current_time)}"
        else:
            db.commit()
            return {
                "success":            True,
                "message":            f"{teacher.full_name} has completed the morning session.",
                "kind":               "teacher",
                "student":            None,
                "teacher":            teacher,
                "attendance":         None,
                "teacher_attendance": record,
                "sms_sent":           False,
                "action":             "complete",
                "session":            "morning",
            }

    else:
        if record.pm_time_in is None:
            record.pm_time_in = current_time
            action  = "pm_in"
            session = "afternoon"
            message = f"✓ TEACHER PM IN — {teacher.full_name} | {dept} | {_fmt_time(current_time)}"
        elif record.pm_time_out is None:
            record.pm_time_out = current_time
            action  = "pm_out"
            session = "afternoon"
            message = f"✓ TEACHER PM OUT — {teacher.full_name} | {dept} | {_fmt_time(current_time)}"
        else:
            db.commit()
            return {
                "success":            True,
                "message":            f"{teacher.full_name} has completed all sessions for today.",
                "kind":               "teacher",
                "student":            None,
                "teacher":            teacher,
                "attendance":         None,
                "teacher_attendance": record,
                "sms_sent":           False,
                "action":             "complete",
                "session":            None,
            }

    # Queue teacher SMS to each configured admin recipient (principal, HR,
    # etc.) — never goes to the teacher themselves.
    sms_sent = False
    template_key = {
        "am_in":  "sms.teacher.am_in_template",
        "am_out": "sms.teacher.am_out_template",
        "pm_in":  "sms.teacher.pm_in_template",
        "pm_out": "sms.teacher.pm_out_template",
    }.get(action)
    if template_key:
        try:
            body = _teacher_sms(template_key, teacher, current_time, today)
            sms_sent = _enqueue_teacher_sms(teacher, body, action, db)
        except Exception as e:
            logger.warning(f"[Teacher SMS] enqueue failed for {teacher.full_name}: {e}")

    db.commit()
    db.refresh(record)
    return {
        "success":            True,
        "message":            message,
        "kind":               "teacher",
        "student":            None,
        "teacher":            teacher,
        "attendance":         None,
        "teacher_attendance": record,
        "sms_sent":           sms_sent,
        "action":             action,
        "session":            session,
    }


# ── Absent auto-check ─────────────────────────────────────────────────────────

def check_absences(session: str, db: Session) -> dict:
    """
    session="am"  → run at 8:30 AM: find students with no morning scan → mark absent + SMS
    session="pm"  → run at 1:30 PM: find students who came in AM but never returned → SMS
    Returns {"notified": int, "session": str, "names": list}
    Skipped automatically on weekends and configured holidays.
    """
    today = _today()
    school_day, reason = is_school_day(today, db)
    if not school_day:
        logger.info(f"[Absent Check {session.upper()}] Skipped — non-school day ({reason}).")
        return {"notified": 0, "session": session, "names": [], "skipped": True, "reason": reason}

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

                msg = _sms("sms.student.absent_template", student, now_time, today)
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

                msg = _sms("sms.student.pm_absent_template", student, now_time, today)
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
