import os
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, request, g

from database.mongodb import db
from auth import require_faculty, teaches
from network_guard import make_session_lock, network_of

enrollment_bp = Blueprint("enrollment", __name__)

try:
    ENROLLMENT_MINUTES = max(1, int(os.getenv("ENROLLMENT_MINUTES", "5")))
except ValueError:
    ENROLLMENT_MINUTES = 5

# No 0/O or 1/I: easy to read off a screen and type.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6


def _new_code():
    for _ in range(20):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if not db.enrollment_codes.find_one({"code": code}):
            return code
    raise RuntimeError("Could not generate a unique enrollment code")


def find_enrollment_code(code):
    """
    Look a code up. Returns (document, "ok"), (None, "invalid") or
    (None, "expired"). Used by /face/register.
    """

    document = db.enrollment_codes.find_one({"code": code})

    if not document:
        return None, "invalid"

    if datetime.utcnow() > document["expires_at"]:
        return None, "expired"

    return document, "ok"


@enrollment_bp.route("/enrollment/create", methods=["POST"])
@require_faculty
def create_enrollment_code():
    """
    A faculty member opens a short enrollment window for one of their
    classes. Students can only register a face while a valid code is shown
    in front of them.
    """

    data = request.get_json(silent=True) or {}
    class_name = (data.get("class_name") or "").strip()

    if not class_name:
        return {
            "message": "class_name is required"
        }, 400

    if not teaches(g.faculty_id, class_name):
        return {
            "message": "You are not assigned to this class"
        }, 403

    now = datetime.utcnow()

    # Housekeeping, and only one live code per faculty + class.
    db.enrollment_codes.delete_many({"expires_at": {"$lt": now}})
    db.enrollment_codes.delete_many({
        "faculty_id": g.faculty_id,
        "class_name": class_name
    })

    expires_at = now + timedelta(minutes=ENROLLMENT_MINUTES)
    code = _new_code()

    # Registration is limited to the network the faculty is on, and each
    # phone may register only one student per window (see /face/register).
    network_lock = make_session_lock(
        request.remote_addr,
        allow_any=bool(data.get("allow_any_network"))
    )

    db.enrollment_codes.insert_one({
        "code": code,
        "faculty_id": g.faculty_id,
        "class_name": class_name,
        "created_at": now,
        "expires_at": expires_at,
        "network_lock": network_lock,
        # Who registered from which phone/address in this window.
        "registered": []
    })

    return {
        "code": code,
        "class_name": class_name,
        "expires_at": expires_at.isoformat() + "Z",
        "expires_in": int((expires_at - now).total_seconds()),
        "network_lock": network_lock
    }


@enrollment_bp.route("/enrollment/status/<class_name>", methods=["GET"])
@require_faculty
def enrollment_status(class_name):
    """
    Who has registered a face for this class, and who is waiting for the
    faculty's approval. Text only; photos load one at a time on request.
    """

    if not teaches(g.faculty_id, class_name):
        return {
            "message": "You are not assigned to this class"
        }, 403

    roster = {
        student["usn"]: student.get("name")
        for student in db.students.find(
            {"class_name": class_name},
            {"_id": 0, "usn": 1, "name": 1}
        )
    }

    approved = 0
    pending = []

    for face in db.student_faces.find({"usn": {"$in": list(roster)}}):
        if face.get("status", "approved") == "pending":
            registered_at = face.get("registered_at")
            pending.append({
                "usn": face["usn"],
                "name": roster.get(face["usn"]),
                "registered_at": registered_at.isoformat() + "Z" if hasattr(registered_at, "isoformat") else None,
                "capture_mode": face.get("capture_mode"),
                "network": network_of(face.get("client_ip"))
            })
        else:
            approved += 1

    pending.sort(key=lambda item: item["registered_at"] or "")

    return {
        "total": len(roster),
        "approved": approved,
        "pending": pending
    }
