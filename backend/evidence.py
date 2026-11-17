"""
Check-in evidence: the selfie taken at check-in, kept so faculty can review
who actually checked in. Photos are biometric data, so they are only served
to the session's own faculty and are deleted after CHECKIN_PHOTO_DAYS
(default 30; 0 keeps them forever).
"""

import os
from datetime import datetime, timedelta

from werkzeug.utils import secure_filename

from database.mongodb import db

CHECKIN_FOLDER = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "uploads", "checkins")
)

def retention_days():
    try:
        return int(os.getenv("CHECKIN_PHOTO_DAYS", "30"))
    except ValueError:
        return 30


def relative_selfie_path(session_id, usn):
    return f"{secure_filename(session_id)}/{secure_filename(usn)}.jpg"


def absolute_selfie_path(relative):
    """Absolute path inside CHECKIN_FOLDER, or None if it would escape it."""
    if not relative:
        return None

    path = os.path.abspath(os.path.join(CHECKIN_FOLDER, *relative.split("/")))

    try:
        inside = os.path.commonpath([path, CHECKIN_FOLDER]) == CHECKIN_FOLDER
    except ValueError:
        return None

    return path if inside else None


def purge_old_evidence():
    """Delete expired selfies and stale used-pass markers. Never raises."""
    try:
        now = datetime.utcnow()

        db.used_passes.delete_many({"created_at": {"$lt": now - timedelta(hours=1)}})

        days = retention_days()
        if days <= 0:
            return

        cutoff = now - timedelta(days=days)

        for record in db.attendance_records.find(
            {"selfie": {"$type": "string"}, "marked_at": {"$lt": cutoff}},
            {"selfie": 1}
        ):
            path = absolute_selfie_path(record["selfie"])
            if path and os.path.isfile(path):
                os.remove(path)
            db.attendance_records.update_one(
                {"_id": record["_id"]},
                {"$unset": {"selfie": ""}}
            )
    except Exception as error:  # housekeeping must never break a request
        print("[evidence] cleanup skipped:", error)
