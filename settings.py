"""
Runtime-editable settings — SMS templates + teacher SMS recipients.
===================================================================
Stored in the `app_settings` key/value table (see database.AppSetting).

Defaults come from config.py constants the first time the server starts,
so existing installs upgrade seamlessly. After that, the dashboard's "SMS
Templates" page can override any of them without redeploying.

Setting keys (all strings):

  sms.student.am_in_template       (str)
  sms.student.am_out_template      (str)
  sms.student.pm_in_template       (str)
  sms.student.pm_out_template      (str)
  sms.student.absent_template      (str)
  sms.student.pm_absent_template   (str)

  sms.teacher.am_in_template       (str)
  sms.teacher.am_out_template      (str)
  sms.teacher.pm_in_template       (str)
  sms.teacher.pm_out_template      (str)
  sms.teacher.recipients           (JSON list of phone numbers)

Template placeholders:
  {school}, {name}, {time}, {date}
  {grade}, {section}     — student templates only
  {role}                 — teacher templates only (resolves to Faculty/Staff)

For backward compatibility student templates may also use the legacy
{student_name} placeholder — it's aliased to {name} at render time.
"""

import json
import logging
from typing import Optional

from sqlalchemy.orm import Session

from database import AppSetting, SessionLocal
from config import (
    SMS_AM_IN_TEMPLATE, SMS_AM_OUT_TEMPLATE,
    SMS_PM_IN_TEMPLATE, SMS_PM_OUT_TEMPLATE,
    SMS_ABSENT_TEMPLATE, SMS_PM_ABSENT_TEMPLATE,
)

logger = logging.getLogger(__name__)


# ── Default Faculty/Staff templates ───────────────────────────────────────────
# These ship as defaults — users override them via the SMS Templates page.
# `{role}` resolves to "Faculty" or "Staff" depending on the person's role.
DEFAULT_TEACHER_AM_IN_TEMPLATE = (
    "[{school}] {role} {name} clocked IN this morning ({date}) at {time}. "
    "- Automated Attendance System"
)
DEFAULT_TEACHER_AM_OUT_TEMPLATE = (
    "[{school}] {role} {name} clocked OUT for lunch on {date} at {time}. "
    "- Automated Attendance System"
)
DEFAULT_TEACHER_PM_IN_TEMPLATE = (
    "[{school}] {role} {name} returned from lunch on {date} at {time}. "
    "- Automated Attendance System"
)
DEFAULT_TEACHER_PM_OUT_TEMPLATE = (
    "[{school}] {role} {name} clocked out for the day on {date} at {time}. "
    "- Automated Attendance System"
)


# Map of setting key → default value. Used to seed first-run and to back
# get_setting() when the row hasn't been written yet.
DEFAULTS: dict[str, str] = {
    "sms.student.am_in_template":      SMS_AM_IN_TEMPLATE,
    "sms.student.am_out_template":     SMS_AM_OUT_TEMPLATE,
    "sms.student.pm_in_template":      SMS_PM_IN_TEMPLATE,
    "sms.student.pm_out_template":     SMS_PM_OUT_TEMPLATE,
    "sms.student.absent_template":     SMS_ABSENT_TEMPLATE,
    "sms.student.pm_absent_template":  SMS_PM_ABSENT_TEMPLATE,

    "sms.teacher.am_in_template":      DEFAULT_TEACHER_AM_IN_TEMPLATE,
    "sms.teacher.am_out_template":     DEFAULT_TEACHER_AM_OUT_TEMPLATE,
    "sms.teacher.pm_in_template":      DEFAULT_TEACHER_PM_IN_TEMPLATE,
    "sms.teacher.pm_out_template":     DEFAULT_TEACHER_PM_OUT_TEMPLATE,

    # JSON-encoded list of phone numbers. School's default admin contacts
    # (principal + HR) — overridable via the SMS Templates dashboard page.
    "sms.teacher.recipients":          '["+639989845492", "+639199745615"]',
}


# ── Core helpers ──────────────────────────────────────────────────────────────

def get_setting(key: str, default: Optional[str] = None, db: Optional[Session] = None) -> str:
    """Read a setting. Falls back to DEFAULTS[key], then to the explicit default."""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        if row and row.value is not None:
            return row.value
        return DEFAULTS.get(key, default if default is not None else "")
    finally:
        if own_db:
            db.close()


def set_setting(key: str, value: str, db: Optional[Session] = None) -> None:
    """Upsert a single setting."""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        if row:
            row.value = value
        else:
            db.add(AppSetting(key=key, value=value))
        db.commit()
    finally:
        if own_db:
            db.close()


def get_all_settings(prefix: Optional[str] = None, db: Optional[Session] = None) -> dict[str, str]:
    """Return every known setting key, with stored value or default."""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        rows = {r.key: r.value for r in db.query(AppSetting).all()}
        out: dict[str, str] = {}
        for k, default in DEFAULTS.items():
            if prefix and not k.startswith(prefix):
                continue
            out[k] = rows.get(k) if rows.get(k) is not None else default
        return out
    finally:
        if own_db:
            db.close()


def seed_defaults() -> None:
    """
    Idempotent on a fresh install — inserts the missing default keys.

    Also runs a one-time migration: any teacher template still referencing
    the now-removed `{department}` placeholder is reset to the new default
    (with `{role}`). User-edited templates that don't mention `{department}`
    are left alone.
    """
    db = SessionLocal()
    try:
        rows = {r.key: r for r in db.query(AppSetting).all()}
        added, migrated = 0, 0
        for k, v in DEFAULTS.items():
            if k not in rows:
                db.add(AppSetting(key=k, value=v))
                added += 1
            elif k.startswith("sms.teacher.") and k.endswith("_template"):
                # Migrate away from legacy {department} placeholder
                if rows[k].value and "{department}" in rows[k].value:
                    rows[k].value = v
                    migrated += 1
        if added or migrated:
            db.commit()
            if added:
                logger.info(f"[Settings] Seeded {added} default value(s)")
            if migrated:
                logger.info(f"[Settings] Migrated {migrated} teacher template(s) from {{department}} to {{role}}")
    finally:
        db.close()


# ── Convenience accessors used by attendance.py ───────────────────────────────

def get_teacher_recipients(db: Optional[Session] = None) -> list[str]:
    """Parse the JSON list of teacher SMS recipient phone numbers."""
    raw = get_setting("sms.teacher.recipients", "[]", db=db)
    try:
        val = json.loads(raw)
        if isinstance(val, list):
            return [str(x).strip() for x in val if str(x).strip()]
    except (json.JSONDecodeError, ValueError):
        logger.warning(f"[Settings] Bad JSON in sms.teacher.recipients: {raw!r}")
    return []


def set_teacher_recipients(numbers: list[str], db: Optional[Session] = None) -> None:
    """Persist the teacher SMS recipient list as JSON."""
    cleaned = [str(n).strip() for n in numbers if str(n).strip()]
    set_setting("sms.teacher.recipients", json.dumps(cleaned), db=db)
