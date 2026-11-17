from flask import Blueprint, request, g
from database.mongodb import db
from auth import require_faculty, teaches
from checkin_tokens import current_qr_token, rotate_seconds
from network_guard import make_session_lock, valid_network
from evidence import purge_old_evidence
import secrets
import uuid
from datetime import datetime, timedelta

session_bp = Blueprint("session", __name__)


@session_bp.route("/session/test")
def session_test():
    return {
        "message": "Session Route Working"
    }


@session_bp.route("/session/create", methods=["POST"])
@require_faculty
def create_session():

    data = request.get_json(silent=True) or {}

    required = ("department_code", "class_name", "subject_code")

    if any(not data.get(field) for field in required):
        return {
            "message": "department_code, class_name and subject_code are required"
        }, 400

    # The faculty member comes from the login token, never from the request,
    # and may only open sessions for classes they are assigned to.
    if not teaches(
        g.faculty_id,
        data["class_name"],
        data["subject_code"],
        data["department_code"]
    ):
        return {
            "message": "You are not assigned to this class and subject"
        }, 403

    created_at = datetime.utcnow()
    expires_at = created_at + timedelta(minutes=2)

    session = {
        "session_id": str(uuid.uuid4()),
        "faculty_id": g.faculty_id,
        "department_code": data["department_code"],
        "class_name": data["class_name"],
        "subject_code": data["subject_code"],
        "status": "active",
        "created_at": created_at,
        "expires_at": expires_at,
        # Key for the rotating QR tokens (never sent to any client).
        "qr_secret": secrets.token_hex(16),
        # Students must check in from the network the faculty started the
        # session on (see network_guard.py).
        "network_lock": make_session_lock(
            request.remote_addr,
            allow_any=bool(data.get("allow_any_network"))
        ),
        "blocked_count": 0,
        "blocked_networks": []
    }

    db.attendance_sessions.insert_one(session)

    purge_old_evidence()

    # expires_in lets the dashboard run a countdown without hard-coding the
    # session length or trusting the browser's clock.
    return {
        "message": "Session Created",
        "session_id": session["session_id"],
        "expires_at": expires_at.isoformat() + "Z",
        "expires_in": int((expires_at - created_at).total_seconds()),
        "network_lock": session["network_lock"]
    }


@session_bp.route("/session/<session_id>/qr", methods=["GET"])
@require_faculty
def session_qr_token(session_id):
    """The QR token to show right now; the dashboard refreshes it as it rotates."""

    session = db.attendance_sessions.find_one({"session_id": session_id})

    if not session:
        return {"message": "Session Not Found"}, 404

    if session.get("faculty_id") != g.faculty_id:
        return {"message": "You do not have access to this session"}, 403

    if datetime.utcnow() > session["expires_at"]:
        return {"message": "Session Expired"}, 410

    secret = session.get("qr_secret")

    if not secret:
        return {"message": "This session predates rotating QR codes. Generate a new session."}, 409

    token, expires_in = current_qr_token(secret, session_id)

    return {
        "token": token,
        "expires_in": expires_in,
        "rotate_seconds": rotate_seconds()
    }


@session_bp.route("/session/<session_id>/network/allow", methods=["POST"])
@require_faculty
def allow_blocked_network(session_id):
    """
    Let students from a network that was blocked get in. Only networks that
    actually showed up in this session's blocked attempts can be added, so
    this can't be used to open the session to arbitrary ranges by hand.
    """

    session = db.attendance_sessions.find_one({"session_id": session_id})

    if not session:
        return {"message": "Session Not Found"}, 404

    if session.get("faculty_id") != g.faculty_id:
        return {"message": "You do not have access to this session"}, 403

    data = request.get_json(silent=True) or {}
    network = valid_network(data.get("network"))

    if not network or network not in session.get("blocked_networks", []):
        return {"message": "That network has not been blocked in this session"}, 400

    db.attendance_sessions.update_one(
        {"session_id": session_id},
        {
            "$addToSet": {"network_lock.networks": network},
            "$pull": {"blocked_networks": network}
        }
    )

    updated = db.attendance_sessions.find_one(
        {"session_id": session_id},
        {"network_lock": 1, "blocked_networks": 1}
    )

    return {
        "network_lock": updated.get("network_lock"),
        "blocked_networks": updated.get("blocked_networks", [])
    }


@session_bp.route(
    "/session/history/<faculty_id>",
    methods=["GET"]
)
@require_faculty
def session_history(faculty_id):

    if faculty_id != g.faculty_id:
        return {
            "message": "You can only view your own sessions"
        }, 403

    sessions = db.attendance_sessions.find({
        "faculty_id": faculty_id
    }).sort("created_at", -1)

    session_list = []

    for session in sessions:

        if datetime.utcnow() > session["expires_at"]:
            status = "Expired"
        else:
            status = "Active"

        created_at_val = session.get("created_at")
        if hasattr(created_at_val, "isoformat"):
            created_at_str = created_at_val.isoformat() + "Z"
        else:
            created_at_str = str(created_at_val)

        session_list.append({
            "session_id": session["session_id"],
            "department_code": session["department_code"],
            "class_name": session["class_name"],
            "subject_code": session["subject_code"],
            "status": status,
            "created_at": created_at_str
        })

    return session_list