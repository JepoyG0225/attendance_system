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
    full_name:    Optional[str] = None
    section_id:   Optional[int] = None
    parent_name:  Optional[str] = None
    parent_phone: Optional[str] = None
    is_active:    Optional[bool] = None


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
    student:    Optional[StudentOut] = None
    attendance: Optional[AttendanceOut] = None
    sms_sent:   bool = False
    action:     Optional[str] = None   # "am_in"|"am_out"|"pm_in"|"pm_out"|"complete"|"error"
    session:    Optional[str] = None   # "morning" | "afternoon"
