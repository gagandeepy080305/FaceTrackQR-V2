import hmac
import math

from flask import Blueprint, request
from werkzeug.security import generate_password_hash, check_password_hash
from database.mongodb import db
from auth import (
    create_token,
    require_admin,
    login_pair_limiter,
    login_ip_limiter,
)

faculty_bp = Blueprint("faculty", __name__)

# PBKDF2 is available on every Python build; werkzeug's default (scrypt) is not.
HASH_METHOD = "pbkdf2:sha256"


def hash_password(password):
    return generate_password_hash(password, method=HASH_METHOD)


# Prefixes werkzeug puts on the hashes it generates. Anything else in the
# password field is a legacy plain-text password from before hashing existed.
HASH_PREFIXES = ("pbkdf2:", "scrypt:")


def _is_hashed(stored):
    return isinstance(stored, str) and stored.startswith(HASH_PREFIXES)


@faculty_bp.route("/faculty/test")
def faculty_test():
    return {
        "message": "Faculty Route Working"
    }


@faculty_bp.route("/faculty/add", methods=["POST"])
@require_admin
def add_faculty():

    data = request.get_json(silent=True) or {}

    missing = [
        field for field in ("name", "faculty_id", "email", "password")
        if not data.get(field)
    ]

    if missing:
        return {
            "message": f"Missing fields: {', '.join(missing)}"
        }, 400

    faculty = {
        "name": data["name"],
        "faculty_id": data["faculty_id"],
        "email": data["email"],
        "password": hash_password(str(data["password"]))
    }

    db.faculty.insert_one(faculty)

    return {
        "message": "Faculty Added Successfully"
    }


@faculty_bp.route("/faculty/login", methods=["POST"])
def faculty_login():

    data = request.get_json(silent=True) or {}

    faculty_id = data.get("faculty_id")
    password = data.get("password")

    client_ip = request.remote_addr or "unknown"
    pair_key = f"{client_ip}|{faculty_id if isinstance(faculty_id, str) else ''}"

    wait = max(
        login_pair_limiter.blocked_for(pair_key),
        login_ip_limiter.blocked_for(client_ip)
    )

    if wait:
        return {
            "message": f"Too many failed attempts. Try again in {math.ceil(wait / 60)} minute(s)."
        }, 429, {"Retry-After": str(wait)}

    valid = False
    faculty = None

    if isinstance(faculty_id, str) and isinstance(password, str):

        faculty = db.faculty.find_one({
            "faculty_id": faculty_id
        })

        stored = faculty.get("password") if faculty else None

        if isinstance(stored, str) and stored:
            if _is_hashed(stored):
                valid = check_password_hash(stored, password)
            else:
                # Legacy plain-text record: accept it once, then upgrade it to
                # a hash so the plain-text password no longer sits in the
                # database.
                valid = hmac.compare_digest(
                    stored.encode("utf-8"),
                    password.encode("utf-8")
                )

                if valid:
                    db.faculty.update_one(
                        {"_id": faculty["_id"]},
                        {"$set": {"password": hash_password(password)}}
                    )

    if not valid:
        login_pair_limiter.record_failure(pair_key)
        login_ip_limiter.record_failure(client_ip)
        return {
            "message": "Invalid Faculty ID or Password"
        }, 401

    login_pair_limiter.reset(pair_key)

    return {
        "message": "Login Successful",
        "faculty_id": faculty["faculty_id"],
        "name": faculty["name"],
        "token": create_token(faculty["faculty_id"])
    }
