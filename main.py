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

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Query, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from database import (
    init_db, get_db,
    Grade, Section, Student, Attendance, SMSLog,
    AttendanceStatus,
)
from schemas import (
    GradeCreate, GradeOut,
    SectionCreate, SectionOut,
    StudentCreate, StudentOut, StudentUpdate,
    AttendanceOut, RFIDScan, ScanResponse,
    BulkPromote, CSVImportRow, CSVImportPreview, CSVImportResult,
    DailySummary, SMSLogOut,
)
from attendance import process_scan, get_daily_summary, check_absences
from sms import test_all_modems, _resolve_modem_ports
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

# Static files (dashboard, scanner, photos)
os.makedirs("static/photos", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
def on_startup():
    init_db()
    scheduler.start()
    mode = "SIMULATION" if SIMULATION_MODE else "PRODUCTION"
    logger.info(f"Attendance System v2 started | Mode: {mode}")
    logger.info(f"Absent SMS scheduled — AM: {ABSENT_AM_HOUR:02d}:{ABSENT_AM_MINUTE:02d} | PM: {ABSENT_PM_HOUR:02d}:{ABSENT_PM_MINUTE:02d}")
    _resolve_modem_ports()


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
    return ScanResponse(
        success=result["success"],
        message=result["message"],
        student=student_out,
        attendance=result["attendance"],
        sms_sent=result["sms_sent"],
        action=result["action"],
        session=result.get("session"),
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

    # Build (grade_lower, section_lower) -> section.id map
    section_lookup: dict[tuple[str, str], int] = {}
    for sec in db.query(Section).join(Grade).all():
        section_lookup[(sec.grade.name.strip().lower(), sec.name.strip().lower())] = sec.id

    # Existing RFIDs (case-insensitive)
    existing_rfids = {r[0].upper() for r in db.query(Student.rfid_uid).all()}
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
    content = await file.read()
    rows, _ = _parse_csv_rows(content, db)
    return CSVImportPreview(
        total_rows  = len(rows),
        valid_count = sum(1 for r in rows if r.valid),
        error_count = sum(1 for r in rows if not r.valid),
        rows        = rows,
    )


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


@app.post("/attendance/check-absent", tags=["Attendance"])
def manual_absent_check(session: str = "am", db: Session = Depends(get_db)):
    """Manually trigger the absent check. session = 'am' or 'pm'. Useful for testing."""
    if session not in ("am", "pm"):
        raise HTTPException(400, "session must be 'am' or 'pm'")
    result = check_absences(session, db)
    return result
