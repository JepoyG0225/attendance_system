"""
Database setup and models (SQLite via SQLAlchemy)
"""

from sqlalchemy import (
    create_engine, Column, Integer, String, Date, Time,
    DateTime, Boolean, ForeignKey, Text, Enum, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.sql import func
import enum
from config import DATABASE_PATH

DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ── Enums ─────────────────────────────────────────────────────────────────────

class AttendanceStatus(str, enum.Enum):
    present = "present"
    late    = "late"
    absent  = "absent"
    excused = "excused"


class SMSStatus(str, enum.Enum):
    pending = "pending"
    sent    = "sent"
    failed  = "failed"


# ── Grade ──────────────────────────────────────────────────────────────────────

class Grade(Base):
    __tablename__ = "grades"

    id        = Column(Integer, primary_key=True, index=True)
    name      = Column(String(30), unique=True, nullable=False)   # e.g. "Grade 4"
    order     = Column(Integer, default=0)                         # for sorting
    is_active = Column(Boolean, default=True)

    sections  = relationship("Section", back_populates="grade", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Grade {self.name}>"


# ── Section ────────────────────────────────────────────────────────────────────

class Section(Base):
    __tablename__ = "sections"
    __table_args__ = (
        UniqueConstraint("grade_id", "name", name="uq_grade_section"),
    )

    id        = Column(Integer, primary_key=True, index=True)
    grade_id  = Column(Integer, ForeignKey("grades.id"), nullable=False)
    name      = Column(String(50), nullable=False)   # e.g. "Sampaguita"
    is_active = Column(Boolean, default=True)

    grade    = relationship("Grade", back_populates="sections")
    students = relationship("Student", back_populates="section")

    def __repr__(self):
        return f"<Section {self.name} ({self.grade.name if self.grade else '?'})>"


# ── Student ────────────────────────────────────────────────────────────────────

class Student(Base):
    __tablename__ = "students"

    id           = Column(Integer, primary_key=True, index=True)
    rfid_uid     = Column(String(50), unique=True, index=True, nullable=False)
    full_name    = Column(String(100), nullable=False)
    section_id   = Column(Integer, ForeignKey("sections.id"), nullable=True)
    parent_name  = Column(String(100), nullable=False)
    parent_phone = Column(String(20), nullable=False)
    is_active    = Column(Boolean, default=True)
    photo_path   = Column(String(255), nullable=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())

    section     = relationship("Section", back_populates="students")
    attendances = relationship("Attendance", back_populates="student")
    sms_logs    = relationship("SMSLog", back_populates="student")

    # Convenience properties
    @property
    def grade_name(self) -> str:
        return self.section.grade.name if self.section else ""

    @property
    def section_name(self) -> str:
        return self.section.name if self.section else ""

    def __repr__(self):
        return f"<Student {self.full_name} | RFID: {self.rfid_uid}>"


# ── Attendance ─────────────────────────────────────────────────────────────────

class Attendance(Base):
    __tablename__ = "attendance"

    id          = Column(Integer, primary_key=True, index=True)
    student_id  = Column(Integer, ForeignKey("students.id"), nullable=False)
    date        = Column(Date, nullable=False, index=True)
    # Morning session
    am_time_in  = Column(Time, nullable=True)   # 1st scan — arrive
    am_time_out = Column(Time, nullable=True)   # 2nd scan — lunch out
    # Afternoon session
    pm_time_in  = Column(Time, nullable=True)   # 3rd scan — back from lunch
    pm_time_out = Column(Time, nullable=True)   # 4th scan — go home
    status      = Column(Enum(AttendanceStatus), default=AttendanceStatus.present)
    notes       = Column(Text, nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    student     = relationship("Student", back_populates="attendances")


# ── Holiday ────────────────────────────────────────────────────────────────────

class Holiday(Base):
    __tablename__ = "holidays"

    id           = Column(Integer, primary_key=True, index=True)
    date         = Column(Date, nullable=False, index=True, unique=True)
    name         = Column(String(100), nullable=False)
    is_recurring = Column(Boolean, default=False)   # repeats every year on same month/day
    created_at   = Column(DateTime(timezone=True), server_default=func.now())


# ── Scanner Heartbeat ──────────────────────────────────────────────────────────

class ScannerHeartbeat(Base):
    __tablename__ = "scanner_heartbeats"

    scanner_id   = Column(String(50), primary_key=True)
    label        = Column(String(100), nullable=True)
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    ip_address   = Column(String(45), nullable=True)
    user_agent   = Column(String(255), nullable=True)


# ── SMS Log ────────────────────────────────────────────────────────────────────

class SMSLog(Base):
    __tablename__ = "sms_logs"

    id         = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    phone      = Column(String(20), nullable=False)
    message    = Column(Text, nullable=False)
    sms_type   = Column(String(20), nullable=False)
    status     = Column(Enum(SMSStatus), default=SMSStatus.pending)
    modem_used = Column(String(50), nullable=True)
    error_msg  = Column(Text, nullable=True)
    sent_at    = Column(DateTime(timezone=True), server_default=func.now())

    student    = relationship("Student", back_populates="sms_logs")


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from sqlalchemy import text, inspect
    Base.metadata.create_all(bind=engine)
    # Migrate existing attendance table: add AM/PM columns if missing
    insp = inspect(engine)
    existing = {c["name"] for c in insp.get_columns("attendance")}
    new_cols = {
        "am_time_in":  "TIME",
        "am_time_out": "TIME",
        "pm_time_in":  "TIME",
        "pm_time_out": "TIME",
    }
    with engine.begin() as conn:
        for col, typ in new_cols.items():
            if col not in existing:
                conn.execute(text(f"ALTER TABLE attendance ADD COLUMN {col} {typ}"))
                print(f"[DB] Migrated: added column attendance.{col}")
        # Copy old time_in/time_out into am_time_in/am_time_out if they existed
        if "time_in" in existing and "am_time_in" in existing:
            conn.execute(text(
                "UPDATE attendance SET am_time_in=time_in, am_time_out=time_out "
                "WHERE am_time_in IS NULL AND time_in IS NOT NULL"
            ))
    print("[DB] Tables created / verified.")
