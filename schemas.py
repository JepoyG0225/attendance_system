"""
Pydantic schemas — request/response shapes for the FastAPI endpoints
"""

from pydantic import BaseModel, field_validator
from typing import Optional
from datetime import date, time, datetime
from database import AttendanceStatus, SMSStatus
import re


# ── Grade ─────────────────────────────────────────────────────────────────────

class GradeCreate(BaseModel):
    name:  str
    order: int = 0


class GradeOut(BaseModel):
    id:        int
    name:      str
    order:     int
    is_active: bool
    model_config = {"from_attributes": True}


# ── Section ───────────────────────────────────────────────────────────────────

class SectionCreate(BaseModel):
    grade_id: int
    name:     str


class SectionOut(BaseModel):
    id:         int
    grade_id:   int
    grade_name: Optional[str] = None
    name:       str
    is_active:  bool
    model_config = {"from_attributes": True}


# ── Student ───────────────────────────────────────────────────────────────────

class StudentCreate(BaseModel):
    rfid_uid:     str
    full_name:    str
    section_id:   int
    parent_name:  str
    parent_phone: str

    @field_validator("parent_phone")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        v = v.strip().replace(" ", "").replace("-", "")
        if v.startswith("09") and len(v) == 11:
            v = "+63" + v[1:]
        if not re.match(r"^\+639\d{9}$", v):
            raise ValueError("Invalid PH mobile. Use 09XXXXXXXXX or +639XXXXXXXXX")
        return v


class StudentOut(BaseModel):
    id:           int
    rfid_uid:     str
    full_name:    str
    section_id:   Optional[int]
    grade:        Optional[str] = None     # populated from section.grade.name
    section:      Optional[str] = None     # populated from section.name
    parent_name:  str
    parent_phone: str
    is_active:    bool
    photo_path:   Optional[str] = None
    created_at:   datetime
    model_config = {"from_attributes": True}


class StudentUpdate(BaseModel):
    rfid_uid:     Optional[str] = None
    full_name:    Optional[str] = None
    section_id:   Optional[int] = None
    parent_name:  Optional[str] = None
    parent_phone: Optional[str] = None
    is_active:    Optional[bool] = None

    @field_validator("parent_phone")
    @classmethod
    def normalize_phone_update(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().replace(" ", "").replace("-", "")
        if v.startswith("09") and len(v) == 11:
            v = "+63" + v[1:]
        if not re.match(r"^\+639\d{9}$", v):
            raise ValueError("Invalid PH mobile. Use 09XXXXXXXXX or +639XXXXXXXXX")
        return v


# ── Teacher (Faculty & Staff) ─────────────────────────────────────────────────
# We keep the API name "Teacher" so existing endpoints don't break, but the
# UI labels these as "Faculty & Staff". Role identifies which of the two.

ALLOWED_TEACHER_ROLES = ("Faculty", "Staff")


class TeacherCreate(BaseModel):
    rfid_uid:   str
    full_name:  str
    role:       str = "Faculty"

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        v = (v or "Faculty").strip().title()
        if v not in ALLOWED_TEACHER_ROLES:
            raise ValueError(f"role must be one of {ALLOWED_TEACHER_ROLES}")
        return v


class TeacherOut(BaseModel):
    id:         int
    rfid_uid:   str
    full_name:  str
    role:       str
    is_active:  bool
    photo_path: Optional[str] = None
    created_at: datetime
    model_config = {"from_attributes": True}


class TeacherUpdate(BaseModel):
    rfid_uid:   Optional[str]  = None
    full_name:  Optional[str]  = None
    role:       Optional[str]  = None
    is_active:  Optional[bool] = None

    @field_validator("role")
    @classmethod
    def validate_role_update(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return v
        v = v.strip().title()
        if v not in ALLOWED_TEACHER_ROLES:
            raise ValueError(f"role must be one of {ALLOWED_TEACHER_ROLES}")
        return v


class TeacherAttendanceOut(BaseModel):
    id:           int
    teacher_id:   int
    date:         date
    am_time_in:   Optional[time]
    am_time_out:  Optional[time]
    pm_time_in:   Optional[time]
    pm_time_out:  Optional[time]
    status:       AttendanceStatus
    notes:        Optional[str]
    teacher_name: Optional[str] = None
    role:         Optional[str] = None
    model_config = {"from_attributes": True}


# ── Bulk operations ───────────────────────────────────────────────────────────

class BulkPromote(BaseModel):
    student_ids:    list[int]
    to_section_id:  int


class CSVImportRow(BaseModel):
    row:           int                       # 1-based, including header offset
    rfid_uid:      str = ""
    full_name:     str = ""
    grade:         str = ""
    section:       str = ""
    parent_name:   str = ""
    parent_phone:  str = ""
    valid:         bool = False
    error:         Optional[str] = None
    section_id:    Optional[int] = None      # resolved when valid


class CSVImportPreview(BaseModel):
    total_rows:    int
    valid_count:   int
    error_count:   int
    rows:          list[CSVImportRow]


class CSVImportResult(BaseModel):
    created:       int
    skipped:       int
    errors:        list[str]


# ── Attendance ─────────────────────────────────────────────────────────────────

class RFIDScan(BaseModel):
    rfid_uid:   str
    scanner_id: Optional[str] = "1"   # which physical scanner sent this


class AttendanceOut(BaseModel):
    id:           int
    student_id:   int
    date:         date
    # Morning session
    am_time_in:   Optional[time]
    am_time_out:  Optional[time]
    # Afternoon session
    pm_time_in:   Optional[time]
    pm_time_out:  Optional[time]
    status:       AttendanceStatus
    notes:        Optional[str]
    student_name: Optional[str] = None
    grade:        Optional[str] = None
    section:      Optional[str] = None
    model_config = {"from_attributes": True}


class DailySummary(BaseModel):
    date:    date
    total:   int
    present: int
    late:    int
    absent:  int
    excused: int


# ── Holiday ───────────────────────────────────────────────────────────────────

class HolidayCreate(BaseModel):
    date:         date
    name:         str
    is_recurring: bool = False


class HolidayOut(BaseModel):
    id:           int
    date:         date
    name:         str
    is_recurring: bool
    created_at:   datetime
    model_config = {"from_attributes": True}


# ── Scanner Heartbeat ─────────────────────────────────────────────────────────

class HeartbeatIn(BaseModel):
    scanner_id: str
    label:      Optional[str] = None


class HeartbeatOut(BaseModel):
    scanner_id:   str
    label:        Optional[str]
    last_seen_at: datetime
    seconds_ago:  int
    online:       bool       # True if last_seen_at within ~90 sec
    ip_address:   Optional[str]
    model_config = {"from_attributes": True}


# ── SMS Log ───────────────────────────────────────────────────────────────────

class SMSLogOut(BaseModel):
    id:         int
    student_id: int
    phone:      str
    message:    str
    sms_type:   str
    status:     SMSStatus
    modem_used: Optional[str]
    error_msg:  Optional[str]
    sent_at:    datetime
    model_config = {"from_attributes": True}


# ── Generic responses ─────────────────────────────────────────────────────────

class ScanResponse(BaseModel):
    success:    bool
    message:    str
    # One of `student` or `teacher` is populated depending on whose card was
    # scanned. `kind` discriminates so the scanner UI knows which to render.
    kind:       Optional[str] = None   # "student" | "teacher"
    student:    Optional[StudentOut] = None
    teacher:    Optional[TeacherOut] = None
    # Same — exactly one of these will be set if `kind` is set.
    attendance:         Optional[AttendanceOut]        = None
    teacher_attendance: Optional[TeacherAttendanceOut] = None
    sms_sent:   bool = False
    action:     Optional[str] = None   # "am_in"|"am_out"|"pm_in"|"pm_out"|"complete"|"error"
    session:    Optional[str] = None   # "morning" | "afternoon"
