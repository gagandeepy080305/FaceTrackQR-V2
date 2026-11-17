from flask import Blueprint, request, Response, g, send_file
from database.mongodb import db
from network_guard import is_allowed_client, client_in_lock, network_of, lock_is_private_lan
from auth import (
    require_faculty,
    teaches,
    create_checkin_pass,
    read_checkin_pass,
    create_device_token,
    read_device_token,
    device_issue_limiter,
    pass_limiter,
    checkin_fail_limiter,
)
from checkin_tokens import qr_token_is_valid, pass_seconds
from face_settings import review_distance, hold_borderline
from evidence import (
    absolute_selfie_path,
    relative_selfie_path,
)
from pymongo.errors import DuplicateKeyError
from routes.face import verify_student_face, FaceCheckError
from datetime import datetime
import csv
import io
import ipaddress
import os
import re

attendance_bp = Blueprint("attendance", __name__)


@attendance_bp.route("/attendance/test")
def attendance_test():
    return {
        "message": "Attendance Route Working"
    }


def _owned_session(session_id):
    """(session, None) if it exists and belongs to the caller, else (None, error)."""

    session = db.attendance_sessions.find_one({
        "session_id": session_id
    })

    if not session:
        return None, ({"message": "Session Not Found"}, 404)

    if session.get("faculty_id") != g.faculty_id:
        return None, ({"message": "You do not have access to this session"}, 403)

    return session, None


def _not_assigned():
    return {"message": "You are not assigned to this class and subject"}, 403


NETWORK_MESSAGE = (
    "Not connected to the class network. Join the same Wi-Fi as your "
    "classroom and scan the QR code again."
)


def _note_blocked_network(session_id, client_ip):
    """Remember that someone off the session's network tried to get in."""

    db.attendance_sessions.update_one(
        {"session_id": session_id},
        {"$inc": {"blocked_count": 1}}
    )

    network = network_of(client_ip)

    if network:
        # Keep at most 10 distinct blocked networks per session.
        db.attendance_sessions.update_one(
            {"session_id": session_id, "blocked_networks.9": {"$exists": False}},
            {"$addToSet": {"blocked_networks": network}}
        )


def _review_flags(record, device_users, review_at):
    """Things worth a second look on a check-in (shown to the faculty)."""

    flags = []
    distance = record.get("distance")

    if record.get("device_id") and len(device_users.get(record["device_id"], ())) > 1:
        flags.append("Shared device")

    if isinstance(distance, (int, float)) and distance >= review_at:
        flags.append("Borderline match")

    if record.get("capture_mode") and record["capture_mode"] != "live":
        flags.append("Not live camera")

    return flags


DEVICE_MESSAGE = (
    "This phone has already checked in another student in this session. "
    "Each student must use their own phone."
)


def _enforce_device_rules(session):
    """One USN per phone per session (off for 'any network' testing sessions)."""
    if os.getenv("ENFORCE_ONE_CHECKIN_PER_DEVICE", "1") == "0":
        return False

    return (session.get("network_lock") or {}).get("mode") != "any"


def _lock_is_private_lan(session):
    return lock_is_private_lan(session.get("network_lock"))


def _device_conflict(session, usn, device_id, client_ip):
    """
    True if this phone (or, on a private LAN where every phone has its own
    address, this address) already checked in a DIFFERENT student here.
    """
    session_id = session["session_id"]

    if db.attendance_records.find_one(
        {"session_id": session_id, "device_id": device_id, "usn": {"$ne": usn}},
        {"_id": 1}
    ):
        return True

    try:
        loopback = ipaddress.ip_address(client_ip).is_loopback
    except ValueError:
        loopback = False

    if _lock_is_private_lan(session) and not loopback:
        return db.attendance_records.find_one(
            {"session_id": session_id, "client_ip": client_ip, "usn": {"$ne": usn}},
            {"_id": 1}
        ) is not None

    return False


def _note_device_block(session_id, usn):
    db.attendance_sessions.update_one(
        {"session_id": session_id},
        {
            "$inc": {"device_blocked_count": 1},
            "$push": {"device_blocked_usns": {"$each": [usn], "$slice": -10}}
        }
    )


def _require_live_capture():
    return os.getenv("REQUIRE_LIVE_CAPTURE", "0") == "1"


@attendance_bp.route("/attendance/pass", methods=["POST"])
def issue_checkin_pass():
    """
    Trade a freshly scanned QR token for a short-lived check-in pass.

    The QR token rotates every few seconds, so a screenshot shared with
    someone outside the room stops working almost immediately. Only a pass
    can submit a check-in (see /attendance/mark).
    """

    client_ip = request.remote_addr or "unknown"

    if not is_allowed_client(request.remote_addr):
        return {
            "message": "Not Connected To College WiFi"
        }, 400

    wait = pass_limiter.blocked_for(client_ip)

    if wait:
        return {
            "message": "Too many invalid QR codes. Please wait a moment and scan again."
        }, 429, {"Retry-After": str(wait)}

    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id") or "").strip()
    token = str(data.get("token") or "").strip()

    if not session_id or not token:
        return {
            "message": "Session ID and QR token are required"
        }, 400

    session = db.attendance_sessions.find_one({
        "session_id": session_id
    })

    if not session:
        return {
            "message": "Invalid Session"
        }, 400

    now = datetime.utcnow()

    if now > session["expires_at"]:
        return {
            "message": "Session Expired"
        }, 400

    if not client_in_lock(session.get("network_lock"), request.remote_addr):
        _note_blocked_network(session_id, request.remote_addr)
        pass_limiter.record_failure(client_ip)
        return {
            "message": NETWORK_MESSAGE
        }, 400

    if not qr_token_is_valid(session.get("qr_secret"), session_id, token):
        pass_limiter.record_failure(client_ip)
        return {
            "message": "This QR code has expired. Scan the current code shown by your faculty."
        }, 403

    remaining = int((session["expires_at"] - now).total_seconds())

    # The pass belongs to one phone: keep the server-issued id it already has,
    # or hand it a new one.
    device_token = data.get("device")

    if not read_device_token(device_token):
        wait = device_issue_limiter.blocked_for(client_ip)

        if wait:
            return {
                "message": "Too many new devices from this network. Please wait a few minutes."
            }, 429, {"Retry-After": str(wait)}

        device_issue_limiter.record_failure(client_ip)
        device_token = create_device_token()

    return {
        "pass": create_checkin_pass(session_id, read_device_token(device_token), client_ip),
        "device": device_token,
        "expires_in": max(1, min(pass_seconds(), remaining))
    }


@attendance_bp.route("/attendance/mark", methods=["POST"])
def mark_attendance():
    """
    Mark attendance. Expects multipart form data: session_id, usn, the selfie
    as `image`, and the check-in `pass` obtained from /attendance/pass.
    Optional: device_id and capture_mode ("live" when the selfie came from the
    in-page camera). The face is verified here, on the server.
    """

    client_ip = request.remote_addr

    print("Client IP:", client_ip)

    # Only devices on the college network (or localhost for local testing)
    # may check in. Ranges are configurable, see network_guard.py.
    if not is_allowed_client(client_ip):
        return {
            "message": "Not Connected To College WiFi"
        }, 400

    session_id = (request.form.get("session_id") or "").strip()
    usn = (request.form.get("usn") or "").strip().upper()

    if not session_id or not usn:
        return {
            "message": "Session ID and USN are required"
        }, 400

    image = request.files.get("image")

    if image is None:
        return {
            "message": "Face Image Required"
        }, 400

    # The pass proves this device scanned a live QR for this session moments
    # ago; it can be used for one successful check-in only.
    check_in_pass = read_checkin_pass(
        (request.form.get("pass") or "").strip(),
        pass_seconds()
    )

    if not check_in_pass:
        return {
            "message": "Check-in link expired or missing. Scan the QR code shown by your faculty again."
        }, 403

    if check_in_pass["sid"] != session_id:
        return {
            "message": "This check-in link belongs to a different session. Scan the QR code again."
        }, 403

    if db.used_passes.find_one({"_id": check_in_pass["jti"]}):
        return {
            "message": "This check-in link was already used. Scan the QR code again."
        }, 409

    capture_mode = "live" if request.form.get("capture_mode") == "live" else "file"

    if _require_live_capture() and capture_mode != "live":
        return {
            "message": "Live camera capture is required. Allow camera access and take the selfie in the page."
        }, 400

    # The phone id comes from a token only this server can issue.
    device_id = read_device_token((request.form.get("device") or "").strip())

    if not device_id:
        return {
            "message": "This phone isn't recognised. Scan the QR code again."
        }, 403

    session = db.attendance_sessions.find_one({
        "session_id": session_id
    })

    if not session:
        return {
            "message": "Invalid Session"
        }, 400

    if datetime.utcnow() > session["expires_at"]:
        return {
            "message": "Session Expired"
        }, 400

    if not client_in_lock(session.get("network_lock"), client_ip):
        _note_blocked_network(session_id, client_ip)
        return {
            "message": NETWORK_MESSAGE
        }, 400

    # The pass must be used by the phone and address it was issued to.
    if check_in_pass.get("dev") != device_id or check_in_pass.get("ip") != client_ip:
        return {
            "message": "This check-in link was issued to a different phone. Scan the QR code again on this phone."
        }, 403

    # Only students of the session's class may check in to it.
    student = db.students.find_one({"usn": usn}, {"class_name": 1, "name": 1})

    if student and student.get("class_name") != session["class_name"]:
        return {
            "message": f"This session is for class {session['class_name']}"
        }, 403

    existing = db.attendance_records.find_one({
        "session_id": session_id,
        "usn": usn
    })

    if existing and existing.get("status") == "Review":
        return {
            "message": "Your check-in is waiting for your faculty to confirm it."
        }, 400

    if existing and existing.get("status") == "Rejected":
        return {
            "message": "Your check-in for this session was not accepted by your faculty. Please speak to them."
        }, 403

    if existing:
        return {
            "message": "Attendance Already Marked"
        }, 400

    # One student per phone: a phone that already checked in someone else in
    # this session can't check in a second USN (the proxy-attendance case).
    enforce_device = _enforce_device_rules(session)

    if enforce_device and _device_conflict(session, usn, device_id, client_ip):
        _note_device_block(session_id, usn)
        return {
            "message": DEVICE_MESSAGE
        }, 403

    fail_key = f"{client_ip}|{usn}"
    wait = checkin_fail_limiter.blocked_for(fail_key)

    if wait:
        return {
            "message": f"Too many failed face matches for this USN. Try again in {max(1, wait // 60)} minute(s), or ask your faculty."
        }, 429, {"Retry-After": str(wait)}

    try:
        face = verify_student_face(usn, image)
    except FaceCheckError as error:
        return {
            "message": error.message
        }, error.status

    if not face["verified"]:
        checkin_fail_limiter.record_failure(fail_key)
        return {
            "message": "Face Verification Failed",
            "verified": False,
            "distance": face["distance"]
        }, 403

    checkin_fail_limiter.reset(fail_key)

    # Burn the pass now that the face matched (a wrong face can retry).
    try:
        db.used_passes.insert_one({
            "_id": check_in_pass["jti"],
            "session_id": session_id,
            "usn": usn,
            "created_at": datetime.utcnow()
        })
    except DuplicateKeyError:
        return {
            "message": "This check-in link was already used. Scan the QR code again."
        }, 409

    # Keep the selfie as evidence for the faculty.
    selfie = relative_selfie_path(session_id, usn)
    selfie_path = absolute_selfie_path(selfie)

    try:
        os.makedirs(os.path.dirname(selfie_path), exist_ok=True)
        image.stream.seek(0)
        image.save(selfie_path)
    except Exception as error:
        print("[checkin] could not store selfie:", error)
        selfie = None

    # A match that only just passed (close to the pass mark) is the kind a
    # look-alike or sibling produces, so it waits for the faculty to confirm
    # it (the selfie is stored for that) instead of counting straight away.
    held = hold_borderline() and face["distance"] >= review_distance()

    attendance = {
        "subject_code": session["subject_code"],
        "department_code": session["department_code"],
        "class_name": session["class_name"],
        "status": "Review" if held else "Present",
        "marked_at": datetime.utcnow(),
        "client_ip": client_ip,
        "distance": face["distance"],
        "capture_mode": capture_mode,
        "device_id": device_id,
        "user_agent": (request.headers.get("User-Agent") or "")[:200],
        "selfie": selfie
    }

    # Upsert so two simultaneous requests cannot both insert a record.
    outcome = db.attendance_records.update_one(
        {
            "session_id": session_id,
            "usn": usn
        },
        {
            "$setOnInsert": attendance
        },
        upsert=True
    )

    if outcome.upserted_id is None:
        return {
            "message": "Attendance Already Marked"
        }, 400

    # Two check-ins from one phone can race past the check above; if both got
    # in, undo this one.
    if enforce_device and db.attendance_records.count_documents(
        {"session_id": session_id, "device_id": device_id}
    ) > 1:
        db.attendance_records.delete_one({"session_id": session_id, "usn": usn})
        if selfie_path and os.path.isfile(selfie_path):
            os.remove(selfie_path)
        _note_device_block(session_id, usn)
        return {
            "message": DEVICE_MESSAGE
        }, 403

    if held:
        return {
            "message": "Your face matched, but not clearly. Your faculty will confirm your attendance.",
            "verified": True,
            "held": True,
            "distance": face["distance"],
            "name": student.get("name") if student else None
        }

    return {
        "message": "Attendance Marked Successfully",
        "verified": True,
        "held": False,
        "distance": face["distance"],
        "name": student.get("name") if student else None
    }


@attendance_bp.route("/attendance/live/<session_id>", methods=["GET"])
@require_faculty
def live_checkins(session_id):
    """
    Light, text-only snapshot for the faculty dashboard while a session is
    open: who has checked in, anything worth reviewing, and blocked network
    attempts. No images are sent; photos load one at a time on request.
    """

    session, error = _owned_session(session_id)

    if error:
        return error

    records = list(
        db.attendance_records.find(
            {"session_id": session_id},
            {
                "_id": 0, "usn": 1, "marked_at": 1, "distance": 1,
                "device_id": 1, "capture_mode": 1, "selfie": 1,
                "status": 1, "client_ip": 1
            }
        ).sort("marked_at", -1).limit(300)
    )

    names = {
        student["usn"]: student.get("name")
        for student in db.students.find(
            {"usn": {"$in": [record["usn"] for record in records]}},
            {"_id": 0, "usn": 1, "name": 1}
        )
    }

    device_users = {}
    for record in records:
        if record.get("device_id"):
            device_users.setdefault(record["device_id"], set()).add(record["usn"])

    review_at = review_distance()
    items = []

    for record in records:
        rejected = record.get("status") == "Rejected"
        held = record.get("status") == "Review"
        flags = _review_flags(record, device_users, review_at)

        if rejected:
            flags.insert(0, "Rejected by faculty")

        if held:
            flags.insert(0, "Needs your review")

        marked_at = record.get("marked_at")

        items.append({
            "usn": record["usn"],
            "name": names.get(record["usn"]),
            "marked_at": marked_at.isoformat() + "Z" if hasattr(marked_at, "isoformat") else None,
            "flags": flags,
            "has_selfie": bool(record.get("selfie")),
            "rejected": rejected,
            "held": held,
            "network": network_of(record.get("client_ip"))
        })

    total = db.students.count_documents({
        "department_code": session["department_code"],
        "class_name": session["class_name"]
    })

    return {
        "check_ins": items,
        "present": sum(1 for record in records if record.get("status") == "Present"),
        "held": sum(1 for record in records if record.get("status") == "Review"),
        "total": total,
        "expired": datetime.utcnow() > session["expires_at"],
        "network_lock": session.get("network_lock"),
        "blocked": {
            "count": session.get("blocked_count", 0),
            "networks": session.get("blocked_networks", [])
        },
        "device_blocked": {
            "count": session.get("device_blocked_count", 0),
            "usns": session.get("device_blocked_usns", [])
        }
    }


@attendance_bp.route("/attendance/reject/<session_id>/<usn>", methods=["POST"])
@require_faculty
def reject_checkin(session_id, usn):
    """
    Mark a check-in as not accepted (the student isn't in the room), or
    accept it with {"rejected": false} (this also confirms a check-in that was
    held for review). The record and selfie are kept.
    """

    _, error = _owned_session(session_id)

    if error:
        return error

    data = request.get_json(silent=True) or {}
    rejected = data.get("rejected", True) is not False
    usn = usn.strip().upper()
    status = "Rejected" if rejected else "Present"

    result = db.attendance_records.update_one(
        {"session_id": session_id, "usn": usn},
        {"$set": {
            "status": status,
            "reviewed_by": g.faculty_id,
            "reviewed_at": datetime.utcnow()
        }}
    )

    if result.matched_count == 0:
        return {"message": "No check-in found for this student"}, 404

    return {"usn": usn, "status": status}


@attendance_bp.route(
    "/attendance/selfie/<session_id>/<usn>",
    methods=["GET"]
)
@require_faculty
def checkin_selfie(session_id, usn):
    """The selfie a student took at check-in (that session's faculty only)."""

    _, error = _owned_session(session_id)

    if error:
        return error

    record = db.attendance_records.find_one(
        {"session_id": session_id, "usn": usn.strip().upper()},
        {"selfie": 1}
    )

    path = absolute_selfie_path(record.get("selfie")) if record else None

    if not path or not os.path.isfile(path):
        return {"message": "No selfie stored for this check-in"}, 404

    response = send_file(path, mimetype="image/jpeg", max_age=0)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@attendance_bp.route(
    "/attendance/session/<session_id>",
    methods=["GET"]
)
@require_faculty
def get_attendance_by_session(session_id):

    _, error = _owned_session(session_id)

    if error:
        return error

    records = db.attendance_records.find({
        "session_id": session_id
    })

    attendance_list = []

    for record in records:
        attendance_list.append({
            "usn": record["usn"],
            "status": record["status"]
        })

    return attendance_list


@attendance_bp.route(
    "/attendance/export/<session_id>",
    methods=["GET"]
)
@require_faculty
def export_attendance(session_id):

    _, error = _owned_session(session_id)

    if error:
        return error

    records = db.attendance_records.find({
        "session_id": session_id
    })

    output = io.StringIO()

    writer = csv.writer(output)

    writer.writerow([
        "USN",
        "Status",
        "Marked At"
    ])

    for record in records:
        writer.writerow([
            record["usn"],
            record["status"],
            record.get("marked_at", "")
        ])

    output.seek(0)

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":
            f"attachment; filename=attendance_{session_id}.csv"
        }
    )


@attendance_bp.route(
    "/attendance/final/<session_id>",
    methods=["GET"]
)
@require_faculty
def final_attendance(session_id):

    session, error = _owned_session(session_id)

    if error:
        return error

    department_code = session["department_code"]
    class_name = session["class_name"]

    students = list(
        db.students.find(
            {
                "department_code": department_code,
                "class_name": class_name
            },
            {
                "_id": 0,
                "usn": 1,
                "name": 1
            }
        )
    )

    attendance_records = list(
        db.attendance_records.find(
            {
                "session_id": session_id
            },
            {
                "_id": 0,
                "usn": 1,
                "marked_at": 1,
                "distance": 1,
                "client_ip": 1,
                "device_id": 1,
                "capture_mode": 1,
                "selfie": 1,
                "status": 1
            }
        )
    )

    # Rejected check-ins stay on record but do not count as present.
    present_usns = {
        record["usn"]
        for record in attendance_records
        if record.get("status", "Present") == "Present"
    }

    records_by_usn = {
        record["usn"]: record
        for record in attendance_records
    }

    review_at = review_distance()

    # Which device ids were used by more than one student in this session.
    device_users = {}
    for record in attendance_records:
        if record.get("device_id"):
            device_users.setdefault(record["device_id"], set()).add(record["usn"])

    final_list = []

    present_count = 0
    absent_count = 0

    for student in students:

        if student["usn"] in present_usns:
            status = "Present"
            present_count += 1
        else:
            status = "Absent"
            absent_count += 1

        row = {
            "usn": student["usn"],
            "name": student["name"],
            "status": status
        }

        record = records_by_usn.get(student["usn"])

        if record:
            marked_at = record.get("marked_at")
            distance = record.get("distance")
            flags = _review_flags(record, device_users, review_at)

            if record.get("status") == "Rejected":
                flags.insert(0, "Rejected by faculty")

            if record.get("status") == "Review":
                flags.insert(0, "Held for review (not counted)")

            row.update({
                "marked_at": marked_at.isoformat() + "Z" if hasattr(marked_at, "isoformat") else None,
                "distance": round(distance, 3) if isinstance(distance, (int, float)) else None,
                "client_ip": record.get("client_ip"),
                "capture_mode": record.get("capture_mode"),
                "has_selfie": bool(record.get("selfie")),
                "flags": flags
            })

        final_list.append(row)

    total_students = len(students)

    attendance_percentage = 0

    if total_students > 0:
        attendance_percentage = round(
            (present_count / total_students) * 100,
            2
        )

    return {
        "total_students": total_students,
        "present": present_count,
        "absent": absent_count,
        "attendance_percentage": attendance_percentage,
        "students": final_list
    }


def _student_class(usn):
    student = db.students.find_one({"usn": usn}, {"class_name": 1})
    return student.get("class_name") if student else None


def _subject_attendance(usn, subject_code, class_name=None):
    """
    (attended, total, percentage) for one student in one subject.

    Totals only count sessions held for the student's own class, and only
    attendance marked in those sessions counts, so subjects shared between
    sections do not inflate the denominator. Without a class it falls back to
    counting by subject code alone.
    """

    query = {"subject_code": subject_code}

    if class_name:
        query["class_name"] = class_name

    session_ids = db.attendance_sessions.distinct("session_id", query)

    total = len(session_ids)
    attended = 0

    if total:
        attended = db.attendance_records.count_documents({
            "usn": usn,
            "session_id": {"$in": session_ids},
            "status": "Present"
        })

    percentage = round((attended / total) * 100, 2) if total else 0

    return attended, total, percentage


@attendance_bp.route(
    "/attendance/percentage/<usn>/<subject_code>",
    methods=["GET"]
)
def attendance_percentage(usn, subject_code):

    usn = usn.strip().upper()

    attended_classes, total_classes, percentage = _subject_attendance(
        usn,
        subject_code,
        _student_class(usn)
    )

    return {
        "usn": usn,
        "subject_code": subject_code,
        "attended": attended_classes,
        "total": total_classes,
        "percentage": percentage
    }

@attendance_bp.route(
    "/attendance/defaulters/<subject_code>/<class_name>",
    methods=["GET"]
)
@require_faculty
def defaulters(subject_code, class_name):

    if not teaches(g.faculty_id, class_name, subject_code):
        return _not_assigned()

    students = list(
        db.students.find(
            {
                "class_name": class_name
            },
            {
                "_id": 0,
                "usn": 1,
                "name": 1
            }
        )
    )

    total_classes = db.attendance_sessions.count_documents({
        "subject_code": subject_code,
        "class_name": class_name
    })

    defaulters_list = []

    for student in students:

        attended = db.attendance_records.count_documents({
            "usn": student["usn"],
            "subject_code": subject_code,
            "status": "Present"
        })

        percentage = 0

        if total_classes > 0:
            percentage = round(
                (attended / total_classes) * 100,
                2
            )

        if percentage < 85:

            defaulters_list.append({
                "usn": student["usn"],
                "name": student["name"],
                "percentage": percentage
            })

    return defaulters_list


@attendance_bp.route(
    "/attendance/student/<usn>",
    methods=["GET"]
)
def student_attendance(usn):

    usn = usn.strip().upper()

    student = db.students.find_one({
        "usn": usn
    })

    if not student:
        return {
            "message": "Student Not Found"
        }, 404

    class_name = student.get("class_name")

    # Every subject the student's class has held a session for, so a subject
    # with zero attendance still shows up (as 0%) instead of being hidden.
    if class_name:
        subject_codes = db.attendance_sessions.distinct(
            "subject_code",
            {"class_name": class_name}
        )
    else:
        subject_codes = db.attendance_records.distinct(
            "subject_code",
            {"usn": usn}
        )

    attendance_data = []

    for subject in sorted(subject_codes):

        attended, total_classes, percentage = _subject_attendance(
            usn,
            subject,
            class_name
        )

        attendance_data.append({
            "subject_code": subject,
            "attended": attended,
            "total": total_classes,
            "percentage": percentage
        })

    return {
        "usn": usn,
        "name": student["name"],
        "attendance": attendance_data
    }

@attendance_bp.route(
    "/attendance/analytics/<subject_code>/<class_name>",
    methods=["GET"]
)
@require_faculty
def attendance_analytics(subject_code, class_name):

    if not teaches(g.faculty_id, class_name, subject_code):
        return _not_assigned()

    students = list(
        db.students.find(
            {"class_name": class_name},
            {
                "_id": 0,
                "usn": 1
            }
        )
    )

    total_classes = db.attendance_sessions.count_documents({
        "subject_code": subject_code,
        "class_name": class_name
    })

    total_students = len(students)

    if total_students == 0:
        return {
            "message": "No Students Found"
        }

    total_percentage = 0
    above_85 = 0
    below_85 = 0

    for student in students:

        attended = db.attendance_records.count_documents({
            "usn": student["usn"],
            "subject_code": subject_code,
            "status": "Present"
        })

        percentage = 0

        if total_classes > 0:
            percentage = round(
                (attended / total_classes) * 100,
                2
            )

        total_percentage += percentage

        if percentage >= 85:
            above_85 += 1
        else:
            below_85 += 1

    average_attendance = round(
        total_percentage / total_students,
        2
    )

    return {
        "subject_code": subject_code,
        "class_name": class_name,
        "total_students": total_students,
        "average_attendance": average_attendance,
        "above_85": above_85,
        "below_85": below_85
    }

@attendance_bp.route(
    "/attendance/trend/<subject_code>/<class_name>",
    methods=["GET"]
)
@require_faculty
def attendance_trend(subject_code, class_name):

    if not teaches(g.faculty_id, class_name, subject_code):
        return _not_assigned()

    sessions = list(
        db.attendance_sessions.find(
            {
                "subject_code": subject_code,
                "class_name": class_name
            }
        ).sort("created_at", 1)
    )

    students_count = db.students.count_documents({
        "class_name": class_name
    })

    trend = []

    for session in sessions:

        present_count = db.attendance_records.count_documents({
            "session_id": session["session_id"],
            "status": "Present"
        })

        percentage = 0

        if students_count > 0:
            percentage = round(
                (present_count / students_count) * 100,
                2
            )

        trend.append({
            "session_id": session["session_id"],
            "date": str(session["created_at"]),
            "attendance_percentage": percentage
        })

    return trend
@attendance_bp.route(
    "/attendance/eligibility/<usn>/<subject_code>",
    methods=["GET"]
)
def attendance_eligibility(usn, subject_code):

    usn = usn.strip().upper()

    attended, total_classes, percentage = _subject_attendance(
        usn,
        subject_code,
        _student_class(usn)
    )

    eligible = percentage >= 85

    return {
        "usn": usn,
        "subject_code": subject_code,
        "attendance_percentage": percentage,
        "eligible": eligible
    }
