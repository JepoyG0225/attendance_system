"""
FastAPI Application — Student Attendance System
================================================
Run: uvicorn main:app --host 0.0.0.0 --port 8000 --reload
Dashboard:  http://localhost:8000
Scanner 1:  http://localhost:8000/scanner?id=1&label=Entrance
Scanner 2:  http://localhost:8000/scanner?id=2&label=Exit
"""

import os
import re
import csv
import io
import shutil
import logging
from datetime import date as date_type
from typing import Optional

import asyncio
import json

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Query, Form, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from events import bus as event_bus

from database import (
    init_db, get_db,
    Grade, Section, Student, Attendance, SMSLog,
    Holiday, ScannerHeartbeat,
    AttendanceStatus,
)
from schemas import (
    GradeCreate, GradeOut,
    SectionCreate, SectionOut,
    StudentCreate, StudentOut, StudentUpdate,
    AttendanceOut, RFIDScan, ScanResponse,
    BulkPromote, CSVImportRow, CSVImportPreview, CSVImportResult,
    DailySummary, SMSLogOut,
    HolidayCreate, HolidayOut,
    HeartbeatIn, HeartbeatOut,
)
from attendance import process_scan, get_daily_summary, check_absences
from sms import (
    test_all_modems,
    _resolve_modem_ports,
    start_modem_watcher,
    stop_modem_watcher,
    rescan_modems,
)
from config import (
    SCHOOL_NAME, SIMULATION_MODE,
    ABSENT_AM_HOUR, ABSENT_AM_MINUTE,
    ABSENT_PM_HOUR, ABSENT_PM_MINUTE,
)
from apscheduler.schedulers.background import BackgroundScheduler
from database import SessionLocal

# ── App setup ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title=f"{SCHOOL_NAME} — Attendance System", version="2.0.0")

# ── Absent auto-check scheduler ───────────────────────────────────────────────
scheduler = BackgroundScheduler(timezone="Asia/Manila")

def _run_absent_check(session: str):
    db = SessionLocal()
    try:
        result = check_absences(session, db)
        logger.info(f"[Absent Check {session.upper()}] Notified {result['notified']} student(s): {result['names']}")
    except Exception as e:
        logger.error(f"[Absent Check {session.upper()}] Error: {e}")
    finally:
        db.close()

scheduler.add_job(_run_absent_check, "cron",
    hour=ABSENT_AM_HOUR, minute=ABSENT_AM_MINUTE,
    args=["am"], id="absent_am", replace_existing=True)
scheduler.add_job(_run_absent_check, "cron",
    hour=ABSENT_PM_HOUR, minute=ABSENT_PM_MINUTE,
    args=["pm"], id="absent_pm", replace_existing=True)

@app.on_event("shutdown")
def shutdown_event():
    scheduler.shutdown(wait=False)
    stop_modem_watcher()

# Static files (dashboard, scanner, photos)
os.makedirs("static/photos", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Live event bus loop attachment ────────────────────────────────────────────
# Sync route handlers run in a worker thread, so they can't grab the running
# loop themselves. We capture it here on startup so event_bus.publish() can
# hop back to the main loop via call_soon_threadsafe.
@app.on_event("startup")
async def _attach_event_loop():
    event_bus.attach_loop(asyncio.get_running_loop())


@app.on_event("startup")
def on_startup():
    init_db()
    scheduler.start()
    mode = "SIMULATION" if SIMULATION_MODE else "PRODUCTION"
    logger.info(f"Attendance System v2 started | Mode: {mode}")
    logger.info(f"Absent SMS scheduled — AM: {ABSENT_AM_HOUR:02d}:{ABSENT_AM_MINUTE:02d} | PM: {ABSENT_PM_HOUR:02d}:{ABSENT_PM_MINUTE:02d}")
    # Active-probe at startup so the boot-time assignment matches what we'd
    # pick after a hot-plug — verified AT-responsive ports only.
    _resolve_modem_ports(active_probe=True)
    start_modem_watcher()
    _prefill_ph_holidays()


def _prefill_ph_holidays():
    """Pre-fill the current year (and next year if we're in Nov/Dec) with
    Philippine holidays. Idempotent — skips dates already present."""
    from datetime import date as _d
    db = SessionLocal()
    try:
        today = _d.today()
        years = [today.year]
        if today.month >= 11:                # roll next year in early
            years.append(today.year + 1)
        for y in years:
            result = _sync_ph_year(y, db, include_specials=True)
            if result["added"]:
                logger.info(f"[PH Holidays] Pre-filled {result['added']} entries for {y}.")
            else:
                logger.info(f"[PH Holidays] {y} already has {result['skipped']} entries — no changes.")
    except Exception as e:
        logger.warning(f"[PH Holidays] Pre-fill skipped: {type(e).__name__}: {e}")
    finally:
        db.close()


# ── Helper: build StudentOut from ORM object ──────────────────────────────────

def _student_out(s: Student) -> StudentOut:
    return StudentOut(
        id=s.id,
        rfid_uid=s.rfid_uid,
        full_name=s.full_name,
        section_id=s.section_id,
        grade=s.grade_name,
        section=s.section_name,
        parent_name=s.parent_name,
        parent_phone=s.parent_phone,
        is_active=s.is_active,
        photo_path=s.photo_path,
        created_at=s.created_at,
    )


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse("static/index.html")


@app.get("/scanner", include_in_schema=False)
def scanner_page():
    return FileResponse("static/scanner.html")


# ── RFID Scan ─────────────────────────────────────────────────────────────────

@app.post("/scan", response_model=ScanResponse, tags=["Scanning"])
def rfid_scan(payload: RFIDScan, db: Session = Depends(get_db)):
    result = process_scan(payload.rfid_uid, db)
    student_out = _student_out(result["student"]) if result["student"] else None
    response = ScanResponse(
        success=result["success"],
        message=result["message"],
        student=student_out,
        attendance=result["attendance"],
        sms_sent=result["sms_sent"],
        action=result["action"],
        session=result.get("session"),
    )

    # Live broadcast to all connected dashboards (SSE). Only on a successful
    # scan that produced an attendance row — error scans don't update the
    # table, so no need to wake the UI.
    if result["success"] and result.get("action") and result["action"] != "complete":
        try:
            event_bus.publish("scan", response.model_dump(mode="json"))
        except Exception as e:
            logger.warning(f"SSE publish failed: {e}")

    return response


# ── Live events (Server-Sent Events) ──────────────────────────────────────────

@app.get("/events", tags=["System"])
async def live_events(request: Request):
    """
    SSE stream of live system events. The dashboard opens this with
    EventSource('/events') on page load and reacts to messages as they arrive.

    Event types emitted today:
      - scan: a card was tapped and an attendance row was written/updated.

    Each message is plain SSE text — `event: <type>\\ndata: <json>\\n\\n`.
    A keepalive comment is sent every 15 s so proxies/firewalls don't drop
    an otherwise-idle connection.
    """
    async def event_gen():
        q = event_bus.subscribe()
        # Tell the client we're alive immediately.
        yield ": connected\n\n"
        try:
            while True:
                # Bail out if the client disconnected.
                if await request.is_disconnected():
                    break
                try:
                    evt = await asyncio.wait_for(q.get(), timeout=15.0)
                    payload = json.dumps(evt["data"], default=str)
                    yield f"event: {evt['type']}\ndata: {payload}\n\n"
                except asyncio.TimeoutError:
                    # 15-sec keepalive (any line starting with ':' is a comment)
                    yield ": keepalive\n\n"
        finally:
            event_bus.unsubscribe(q)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # tell nginx not to buffer if proxied
        },
    )


# ── Grades ────────────────────────────────────────────────────────────────────

@app.get("/grades", response_model=list[GradeOut], tags=["Grades & Sections"])
def list_grades(db: Session = Depends(get_db)):
    return db.query(Grade).filter(Grade.is_active == True).order_by(Grade.order, Grade.name).all()


@app.post("/grades", response_model=GradeOut, status_code=201, tags=["Grades & Sections"])
def create_grade(data: GradeCreate, db: Session = Depends(get_db)):
    existing = db.query(Grade).filter(Grade.name == data.name.strip()).first()
    if existing:
        raise HTTPException(400, f"Grade '{data.name}' already exists")
    grade = Grade(name=data.name.strip(), order=data.order)
    db.add(grade)
    db.commit()
    db.refresh(grade)
    return grade


@app.delete("/grades/{grade_id}", tags=["Grades & Sections"])
def delete_grade(grade_id: int, db: Session = Depends(get_db)):
    grade = db.query(Grade).filter(Grade.id == grade_id).first()
    if not grade:
        raise HTTPException(404, "Grade not found")
    if db.query(Section).filter(Section.grade_id == grade_id).count() > 0:
        raise HTTPException(400, "Remove all sections under this grade first")
    db.delete(grade)
    db.commit()
    return {"message": f"{grade.name} deleted"}


# ── Sections ──────────────────────────────────────────────────────────────────

@app.get("/sections", response_model=list[SectionOut], tags=["Grades & Sections"])
def list_sections(grade_id: Optional[int] = Query(None), db: Session = Depends(get_db)):
    q = db.query(Section).filter(Section.is_active == True)
    if grade_id:
        q = q.filter(Section.grade_id == grade_id)
    sections = q.order_by(Section.name).all()
    result = []
    for s in sections:
        out = SectionOut.model_validate(s)
        out.grade_name = s.grade.name if s.grade else None
        result.append(out)
    return result


@app.post("/sections", response_model=SectionOut, status_code=201, tags=["Grades & Sections"])
def create_section(data: SectionCreate, db: Session = Depends(get_db)):
    grade = db.query(Grade).filter(Grade.id == data.grade_id).first()
    if not grade:
        raise HTTPException(404, "Grade not found")
    existing = db.query(Section).filter(
        Section.grade_id == data.grade_id,
        Section.name == data.name.strip()
    ).first()
    if existing:
        raise HTTPException(400, f"Section '{data.name}' already exists in {grade.name}")
    section = Section(grade_id=data.grade_id, name=data.name.strip())
    db.add(section)
    db.commit()
    db.refresh(section)
    out = SectionOut.model_validate(section)
    out.grade_name = grade.name
    return out


@app.delete("/sections/{section_id}", tags=["Grades & Sections"])
def delete_section(section_id: int, db: Session = Depends(get_db)):
    section = db.query(Section).filter(Section.id == section_id).first()
    if not section:
        raise HTTPException(404, "Section not found")
    if db.query(Student).filter(Student.section_id == section_id, Student.is_active == True).count() > 0:
        raise HTTPException(400, "Move or remove students in this section first")
    db.delete(section)
    db.commit()
    return {"message": f"Section '{section.name}' deleted"}


# ── Students ──────────────────────────────────────────────────────────────────

@app.get("/students", response_model=list[StudentOut], tags=["Students"])
def list_students(
    active_only: bool = True,
    section_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(Student)
    if active_only:
        q = q.filter(Student.is_active == True)
    if section_id:
        q = q.filter(Student.section_id == section_id)
    return [_student_out(s) for s in q.order_by(Student.full_name).all()]


@app.post("/students", response_model=StudentOut, status_code=201, tags=["Students"])
def register_student(data: StudentCreate, db: Session = Depends(get_db)):
    existing = db.query(Student).filter(Student.rfid_uid == data.rfid_uid.upper()).first()
    if existing:
        raise HTTPException(400, f"RFID {data.rfid_uid} already assigned to {existing.full_name}")
    section = db.query(Section).filter(Section.id == data.section_id).first()
    if not section:
        raise HTTPException(404, "Section not found. Add grades and sections first.")
    student = Student(
        rfid_uid=data.rfid_uid.strip().upper(),
        full_name=data.full_name.strip(),
        section_id=data.section_id,
        parent_name=data.parent_name.strip(),
        parent_phone=data.parent_phone,
    )
    db.add(student)
    db.commit()
    db.refresh(student)
    logger.info(f"Registered: {student.full_name} | RFID: {student.rfid_uid}")
    return _student_out(student)


@app.get("/students/{student_id}", response_model=StudentOut, tags=["Students"])
def get_student(student_id: int, db: Session = Depends(get_db)):
    s = db.query(Student).filter(Student.id == student_id).first()
    if not s:
        raise HTTPException(404, "Student not found")
    return _student_out(s)


@app.patch("/students/{student_id}", response_model=StudentOut, tags=["Students"])
def update_student(student_id: int, data: StudentUpdate, db: Session = Depends(get_db)):
    s = db.query(Student).filter(Student.id == student_id).first()
    if not s:
        raise HTTPException(404, "Student not found")
    payload = data.model_dump(exclude_none=True)
    if "rfid_uid" in payload:
        payload["rfid_uid"] = payload["rfid_uid"].strip().upper()
        existing = db.query(Student).filter(Student.rfid_uid == payload["rfid_uid"], Student.id != student_id).first()
        if existing:
            raise HTTPException(400, f"RFID {payload['rfid_uid']} already assigned to {existing.full_name}")
    for field, value in payload.items():
        setattr(s, field, value)
    db.commit()
    db.refresh(s)
    return _student_out(s)


@app.delete("/students/{student_id}", tags=["Students"])
def deactivate_student(student_id: int, db: Session = Depends(get_db)):
    s = db.query(Student).filter(Student.id == student_id).first()
    if not s:
        raise HTTPException(404, "Student not found")
    s.is_active = False
    db.commit()
    return {"message": f"{s.full_name} deactivated"}


@app.post("/students/{student_id}/photo", response_model=StudentOut, tags=["Students"])
async def upload_photo(
    student_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    s = db.query(Student).filter(Student.id == student_id).first()
    if not s:
        raise HTTPException(404, "Student not found")
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in ("jpg", "jpeg", "png", "webp"):
        raise HTTPException(400, "Only JPG, PNG, or WEBP photos allowed")
    photo_path = f"static/photos/{student_id}.jpg"
    with open(photo_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    s.photo_path = f"/static/photos/{student_id}.jpg"
    db.commit()
    db.refresh(s)
    return _student_out(s)


@app.post("/students/promote", tags=["Students"])
def promote_students(
    from_section_id: int,
    to_section_id: int,
    db: Session = Depends(get_db),
):
    """Move all active students from one section to another (school year promotion)."""
    from_section = db.query(Section).filter(Section.id == from_section_id).first()
    if not from_section:
        raise HTTPException(404, "Source section not found")
    to_section = db.query(Section).filter(Section.id == to_section_id).first()
    if not to_section:
        raise HTTPException(404, "Target section not found")
    if from_section_id == to_section_id:
        raise HTTPException(400, "Source and target sections must be different")

    students = db.query(Student).filter(
        Student.section_id == from_section_id,
        Student.is_active == True,
    ).all()

    for s in students:
        s.section_id = to_section_id
    db.commit()

    return {
        "promoted": len(students),
        "from_section": from_section.name,
        "to_section": to_section.name,
        "names": [s.full_name for s in students],
    }


@app.post("/students/bulk-promote", tags=["Students"])
def bulk_promote_students(data: BulkPromote, db: Session = Depends(get_db)):
    """Move a specific list of students into a target section."""
    if not data.student_ids:
        raise HTTPException(400, "No students selected")
    to_section = db.query(Section).filter(Section.id == data.to_section_id).first()
    if not to_section:
        raise HTTPException(404, "Target section not found")

    students = db.query(Student).filter(Student.id.in_(data.student_ids)).all()
    found_ids = {s.id for s in students}
    missing   = [sid for sid in data.student_ids if sid not in found_ids]

    for s in students:
        s.section_id = data.to_section_id
    db.commit()

    return {
        "promoted":   len(students),
        "missing":    missing,
        "to_section": to_section.name,
        "names":      [s.full_name for s in students],
    }


# ── CSV student import ────────────────────────────────────────────────────────

_CSV_REQUIRED_FIELDS = ("rfid_uid", "full_name", "grade", "section", "parent_name", "parent_phone")


def _normalize_ph_phone(raw: str) -> Optional[str]:
    v = (raw or "").strip().replace(" ", "").replace("-", "")
    if v.startswith("09") and len(v) == 11:
        v = "+63" + v[1:]
    if re.match(r"^\+639\d{9}$", v):
        return v
    return None


def _parse_csv_rows(content: bytes, db: Session) -> tuple[list[CSVImportRow], dict[tuple[str, str], int]]:
    """
    Parse + validate CSV bytes. Returns (rows, section_lookup) where section_lookup
    maps (grade_lower, section_lower) -> section_id. Rows include validation status.
    """
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(400, "CSV is empty or has no header row")
    missing_fields = [f for f in _CSV_REQUIRED_FIELDS if f not in reader.fieldnames]
    if missing_fields:
        raise HTTPException(400, f"CSV missing required column(s): {', '.join(missing_fields)}")

    # Build (grade_lower, section_lower) -> section.id map.
    # Defensive: skip sections whose grade row was deleted (orphaned FK).
    section_lookup: dict[tuple[str, str], int] = {}
    for sec in db.query(Section).join(Grade).all():
        if sec.grade is None or not sec.grade.name or not sec.name:
            continue
        section_lookup[(sec.grade.name.strip().lower(), sec.name.strip().lower())] = sec.id

    # Existing RFIDs (case-insensitive). Skip None values just in case.
    existing_rfids = {
        r[0].upper() for r in db.query(Student.rfid_uid).all() if r[0]
    }
    seen_rfids: set[str] = set()

    rows: list[CSVImportRow] = []
    for i, raw in enumerate(reader, start=2):           # start=2 → row 1 is header
        rfid    = (raw.get("rfid_uid")     or "").strip().upper()
        name    = (raw.get("full_name")    or "").strip()
        grade   = (raw.get("grade")        or "").strip()
        section = (raw.get("section")      or "").strip()
        parent  = (raw.get("parent_name")  or "").strip()
        phone   = (raw.get("parent_phone") or "").strip()

        row = CSVImportRow(
            row=i, rfid_uid=rfid, full_name=name, grade=grade,
            section=section, parent_name=parent, parent_phone=phone,
        )

        if not rfid:
            row.error = "rfid_uid is required"
        elif not name:
            row.error = "full_name is required"
        elif not grade or not section:
            row.error = "grade and section are required"
        elif not parent:
            row.error = "parent_name is required"
        else:
            normalized = _normalize_ph_phone(phone)
            if not normalized:
                row.error = "Invalid PH mobile (use 09XXXXXXXXX)"
            elif rfid in existing_rfids:
                row.error = "RFID already exists in the system"
            elif rfid in seen_rfids:
                row.error = "Duplicate RFID earlier in this file"
            else:
                section_id = section_lookup.get((grade.lower(), section.lower()))
                if section_id is None:
                    row.error = f"Section not found: {grade} / {section}"
                else:
                    row.parent_phone = normalized
                    row.section_id   = section_id
                    row.valid        = True
                    seen_rfids.add(rfid)
        rows.append(row)
    return rows, section_lookup


@app.post("/students/import-csv/preview", response_model=CSVImportPreview, tags=["Students"])
async def preview_csv_import(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Validate a CSV file without writing anything to the DB."""
    try:
        content = await file.read()
        rows, _ = _parse_csv_rows(content, db)
        return CSVImportPreview(
            total_rows  = len(rows),
            valid_count = sum(1 for r in rows if r.valid),
            error_count = sum(1 for r in rows if not r.valid),
            rows        = rows,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("CSV preview failed")
        raise HTTPException(500, f"CSV preview failed: {type(e).__name__}: {e}")


@app.post("/students/import-csv", response_model=CSVImportResult, tags=["Students"])
async def import_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Import valid CSV rows. Rows with errors are skipped."""
    content = await file.read()
    rows, _ = _parse_csv_rows(content, db)
    created  = 0
    errors: list[str] = []
    for r in rows:
        if not r.valid or r.section_id is None:
            if r.error:
                errors.append(f"Row {r.row}: {r.error}")
            continue
        student = Student(
            rfid_uid     = r.rfid_uid,
            full_name    = r.full_name,
            section_id   = r.section_id,
            parent_name  = r.parent_name,
            parent_phone = r.parent_phone,
        )
        db.add(student)
        created += 1
    db.commit()
    logger.info(f"CSV import: created {created}, skipped {len(rows) - created}")
    return CSVImportResult(created=created, skipped=len(rows) - created, errors=errors)


# ── Attendance ────────────────────────────────────────────────────────────────

@app.get("/attendance", response_model=list[AttendanceOut], tags=["Attendance"])
def get_attendance(
    target_date: Optional[date_type] = None,
    student_id:  Optional[int]  = None,
    db: Session = Depends(get_db),
):
    q = db.query(Attendance).join(Student)
    if target_date:
        q = q.filter(Attendance.date == target_date)
    if student_id:
        q = q.filter(Attendance.student_id == student_id)
    records = q.order_by(Attendance.date.desc(), Attendance.am_time_in.desc()).all()
    result = []
    for r in records:
        out = AttendanceOut.model_validate(r)
        out.student_name = r.student.full_name
        out.grade   = r.student.grade_name
        out.section = r.student.section_name
        result.append(out)
    return result


@app.get("/attendance/summary", response_model=DailySummary, tags=["Attendance"])
def daily_summary(target_date: Optional[date_type] = None, db: Session = Depends(get_db)):
    if not target_date:
        from datetime import date
        target_date = date.today()
    return get_daily_summary(target_date, db)


@app.delete("/attendance/{attendance_id}", tags=["Attendance"])
def delete_attendance(attendance_id: int, db: Session = Depends(get_db)):
    r = db.query(Attendance).filter(Attendance.id == attendance_id).first()
    if not r:
        raise HTTPException(404, "Record not found")
    db.delete(r)
    db.commit()
    return {"message": "Attendance record deleted"}


@app.patch("/attendance/{attendance_id}", response_model=AttendanceOut, tags=["Attendance"])
def update_attendance(
    attendance_id: int,
    notes:  Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    r = db.query(Attendance).filter(Attendance.id == attendance_id).first()
    if not r:
        raise HTTPException(404, "Record not found")
    if notes is not None:
        r.notes = notes
    if status is not None:
        try:
            r.status = AttendanceStatus(status)
        except ValueError:
            raise HTTPException(400, f"Invalid status: {status}")
    db.commit()
    db.refresh(r)
    out = AttendanceOut.model_validate(r)
    out.student_name = r.student.full_name
    out.grade   = r.student.grade_name
    out.section = r.student.section_name
    return out


# ── SMS Logs ──────────────────────────────────────────────────────────────────

@app.get("/sms-logs", response_model=list[SMSLogOut], tags=["SMS"])
def get_sms_logs(limit: int = 50, db: Session = Depends(get_db)):
    return db.query(SMSLog).order_by(SMSLog.sent_at.desc()).limit(limit).all()


# ── Holidays ──────────────────────────────────────────────────────────────────

@app.get("/holidays", response_model=list[HolidayOut], tags=["Calendar"])
def list_holidays(db: Session = Depends(get_db)):
    return db.query(Holiday).order_by(Holiday.date).all()


@app.post("/holidays", response_model=HolidayOut, status_code=201, tags=["Calendar"])
def create_holiday(data: HolidayCreate, db: Session = Depends(get_db)):
    existing = db.query(Holiday).filter(Holiday.date == data.date).first()
    if existing:
        raise HTTPException(400, f"A holiday on {data.date} already exists: {existing.name}")
    h = Holiday(date=data.date, name=data.name.strip(), is_recurring=data.is_recurring)
    db.add(h)
    db.commit()
    db.refresh(h)
    return h


@app.delete("/holidays/{holiday_id}", tags=["Calendar"])
def delete_holiday(holiday_id: int, db: Session = Depends(get_db)):
    h = db.query(Holiday).filter(Holiday.id == holiday_id).first()
    if not h:
        raise HTTPException(404, "Holiday not found")
    db.delete(h)
    db.commit()
    return {"message": f"Holiday '{h.name}' deleted"}


def _sync_ph_year(year: int, db: Session, include_specials: bool = True) -> dict:
    """Core sync logic. Returns {added, skipped, items}. Idempotent."""
    try:
        import holidays as _hol
    except ImportError:
        raise HTTPException(500, "The `holidays` library is not installed in this environment.")

    ph_cal = _hol.country_holidays("PH", years=year)
    if not ph_cal:
        return {"year": year, "added": 0, "skipped": 0, "items": []}

    existing_dates = {
        h.date for h in db.query(Holiday).filter(
            Holiday.date >= date_type(year, 1, 1),
            Holiday.date <= date_type(year, 12, 31),
        ).all()
    }

    added: list[dict] = []
    skipped = 0
    for d, name in sorted(ph_cal.items()):
        if not include_specials:
            cat = getattr(ph_cal, "get_categories", lambda *_: set())(d)
            if cat and "special" in {str(c).lower() for c in cat}:
                continue
        if d in existing_dates:
            skipped += 1
            continue
        h = Holiday(date=d, name=str(name).strip(), is_recurring=False)
        db.add(h)
        added.append({"date": d.isoformat(), "name": h.name})

    db.commit()
    return {"year": year, "added": len(added), "skipped": skipped, "items": added}


@app.post("/holidays/sync-ph", tags=["Calendar"])
def sync_ph_holidays(
    year:           int  = Query(..., ge=2000, le=2100),
    include_specials: bool = Query(True),
    db: Session = Depends(get_db),
):
    """Re-sync Philippine holidays for the requested year. Already runs
    automatically on server startup; this endpoint is for manual top-ups."""
    return _sync_ph_year(year, db, include_specials)


# ── Scanner Heartbeats ────────────────────────────────────────────────────────

from datetime import datetime, timedelta
from fastapi import Request

@app.post("/scanner-heartbeat", tags=["Scanning"])
def scanner_heartbeat(data: HeartbeatIn, request: Request, db: Session = Depends(get_db)):
    """Scanner clients (Pi / browser kiosk) ping this every ~30 sec so the
    dashboard can show which scanners are online."""
    hb = db.query(ScannerHeartbeat).filter(ScannerHeartbeat.scanner_id == data.scanner_id).first()
    client_ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent", "")[:255]
    if hb is None:
        hb = ScannerHeartbeat(
            scanner_id=data.scanner_id,
            label=data.label,
            ip_address=client_ip,
            user_agent=ua,
        )
        db.add(hb)
    else:
        hb.label = data.label or hb.label
        hb.ip_address = client_ip
        hb.user_agent = ua
        hb.last_seen_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@app.get("/scanner-heartbeat", response_model=list[HeartbeatOut], tags=["Scanning"])
def list_scanner_heartbeats(db: Session = Depends(get_db)):
    now = datetime.utcnow()
    out = []
    for hb in db.query(ScannerHeartbeat).order_by(ScannerHeartbeat.scanner_id).all():
        last = hb.last_seen_at
        # SQLite returns naive datetimes; compare safely
        if last is not None and last.tzinfo is not None:
            last = last.replace(tzinfo=None)
        seconds_ago = int((now - last).total_seconds()) if last else 999999
        out.append(HeartbeatOut(
            scanner_id   = hb.scanner_id,
            label        = hb.label,
            last_seen_at = hb.last_seen_at,
            seconds_ago  = seconds_ago,
            online       = seconds_ago < 90,
            ip_address   = hb.ip_address,
        ))
    return out


@app.delete("/scanner-heartbeat/{scanner_id}", tags=["Scanning"])
def delete_scanner_heartbeat(scanner_id: str, db: Session = Depends(get_db)):
    hb = db.query(ScannerHeartbeat).filter(ScannerHeartbeat.scanner_id == scanner_id).first()
    if not hb:
        raise HTTPException(404, "Scanner not found")
    db.delete(hb)
    db.commit()
    return {"message": f"Scanner {scanner_id} removed from list"}


# ── Reports ───────────────────────────────────────────────────────────────────

@app.get("/reports/monthly", tags=["Reports"])
def monthly_report(
    year:       int,
    month:      int,
    format:     str = Query("xlsx", pattern="^(xlsx|pdf)$"),
    section_id: Optional[int] = None,
    grade_id:   Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Download a monthly attendance report as Excel (.xlsx) or PDF.
    Filter by section_id or grade_id; omit both for school-wide.
    """
    if month < 1 or month > 12:
        raise HTTPException(400, "month must be between 1 and 12")
    if year < 2000 or year > 2100:
        raise HTTPException(400, "year out of range")

    try:
        from reports import build_xlsx, build_pdf
    except ImportError as e:
        raise HTTPException(500, f"Report dependencies missing: {e}. Install openpyxl + reportlab.")

    label_parts = [f"{year}-{month:02d}"]
    if section_id:
        sec = db.query(Section).filter(Section.id == section_id).first()
        if sec: label_parts.append(f"{(sec.grade.name if sec.grade else '').replace(' ','')}_{sec.name}")
    elif grade_id:
        g = db.query(Grade).filter(Grade.id == grade_id).first()
        if g: label_parts.append(g.name.replace(" ", ""))
    fname_stem = "attendance_" + "_".join(label_parts)

    if format == "xlsx":
        data = build_xlsx(year, month, db, section_id=section_id, grade_id=grade_id)
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{fname_stem}.xlsx"'},
        )
    else:
        data = build_pdf(year, month, db, section_id=section_id, grade_id=grade_id)
        return Response(
            content=data,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{fname_stem}.pdf"'},
        )


# ── System Status ─────────────────────────────────────────────────────────────

@app.get("/ping", tags=["System"])
def ping():
    return {"ok": True}


@app.get("/status", tags=["System"])
def system_status():
    modems = test_all_modems()
    return {
        "school":          SCHOOL_NAME,
        "simulation_mode": SIMULATION_MODE,
        "modems":          modems,
        "absent_schedule": {
            "am": f"{ABSENT_AM_HOUR:02d}:{ABSENT_AM_MINUTE:02d}",
            "pm": f"{ABSENT_PM_HOUR:02d}:{ABSENT_PM_MINUTE:02d}",
        },
    }


@app.get("/modems", tags=["System"])
def list_modems():
    """Current status of both GSM modems (port, signal, ok)."""
    return test_all_modems()


@app.post("/modems/rescan", tags=["System"])
def modem_rescan():
    """
    Force a re-scan of USB serial ports right now. Use this after plugging
    in a new GSM modem if you don't want to wait for the background watcher.
    Returns the freshly-detected modem status.
    """
    return rescan_modems()


@app.get("/modems/ports", tags=["System"])
def list_serial_ports():
    """
    Debug endpoint: list every serial port the OS is reporting, with the
    metadata pyserial exposes. Useful for diagnosing "Windows doesn't see
    my dongle" or "wrong COM port got picked" issues.

    Note: this does NOT open or probe the ports — read-only enumeration.
    """
    from serial.tools import list_ports as _lp
    return [
        {
            "device":        p.device,
            "name":          p.name,
            "description":   p.description,
            "manufacturer":  p.manufacturer,
            "product":       p.product,
            "serial_number": p.serial_number,
            "vid":           p.vid,
            "pid":           p.pid,
            "hwid":          p.hwid,
            "location":      p.location,
            "interface":     p.interface,
        }
        for p in _lp.comports()
    ]


@app.post("/attendance/check-absent", tags=["Attendance"])
def manual_absent_check(session: str = "am", db: Session = Depends(get_db)):
    """Manually trigger the absent check. session = 'am' or 'pm'. Useful for testing."""
    if session not in ("am", "pm"):
        raise HTTPException(400, "session must be 'am' or 'pm'")
    result = check_absences(session, db)
    return result
