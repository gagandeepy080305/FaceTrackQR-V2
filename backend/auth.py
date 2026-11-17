"""
Authentication and abuse-protection helpers.

* Faculty log in once and receive a signed, expiring token (itsdangerous,
  which ships with Flask). Protected routes take it as `Authorization: Bearer`.
* Admin-only routes (adding faculty, students, classes...) need the
  `X-Admin-Key` header to match the ADMIN_API_KEY environment variable. With
  no key configured they are disabled.
* AttemptLimiter slows down password / code guessing.
"""

import functools
import hmac
import os
import secrets
import threading
import time

from flask import g, request
from itsdangerous import URLSafeSerializer, URLSafeTimedSerializer, BadSignature, SignatureExpired

from database.mongodb import db

try:
    TOKEN_MAX_AGE = int(os.getenv("TOKEN_MAX_AGE_SECONDS", str(8 * 60 * 60)))
except ValueError:
    TOKEN_MAX_AGE = 8 * 60 * 60

_SECRET_FILE = os.path.join(os.path.dirname(__file__), ".secret_key")
_serializer = None


def _load_secret_key():
    key = os.getenv("SECRET_KEY", "").strip()
    if key:
        return key

    # Local-development fallback: generate a key once and keep it in a
    # git-ignored file so tokens survive restarts. Set SECRET_KEY in the
    # environment for real deployments.
    try:
        with open(_SECRET_FILE) as handle:
            stored = handle.read().strip()
            if stored:
                return stored
    except FileNotFoundError:
        pass

    generated = secrets.token_hex(32)

    try:
        fd = os.open(_SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(generated)
        print(
            "[auth] SECRET_KEY is not set; generated one in backend/.secret_key. "
            "Set SECRET_KEY in the environment for deployments."
        )
    except FileExistsError:
        with open(_SECRET_FILE) as handle:
            stored = handle.read().strip()
        if stored:
            return stored
    except OSError:
        pass  # read-only filesystem: fall back to a per-process key

    return generated


def _get_serializer():
    global _serializer
    if _serializer is None:
        _serializer = URLSafeTimedSerializer(_load_secret_key(), salt="faculty-auth")
    return _serializer


_pass_serializer = None


def _get_pass_serializer():
    global _pass_serializer
    if _pass_serializer is None:
        _pass_serializer = URLSafeTimedSerializer(_load_secret_key(), salt="checkin-pass")
    return _pass_serializer


def create_checkin_pass(session_id, device_id="", ip=""):
    """
    A signed, short-lived pass that lets one phone submit one check-in. It is
    bound to the phone (server-issued device id) and the address that asked
    for it, so it can't be handed to another device.
    """
    return _get_pass_serializer().dumps({
        "sid": session_id,
        "jti": secrets.token_hex(12),
        "dev": device_id,
        "ip": ip
    })


def read_checkin_pass(token, max_age):
    try:
        data = _get_pass_serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None

    if isinstance(data, dict) and data.get("sid") and data.get("jti"):
        return data
    return None


_device_serializer = None


def _get_device_serializer():
    global _device_serializer
    if _device_serializer is None:
        _device_serializer = URLSafeSerializer(_load_secret_key(), salt="device-id")
    return _device_serializer


def create_device_token():
    """A signed random phone id. Clients can keep it but cannot choose it."""
    return _get_device_serializer().dumps({"d": secrets.token_hex(10)})


def read_device_token(token):
    """The device id inside a token this server issued, or None."""
    if not isinstance(token, str) or not token:
        return None

    try:
        data = _get_device_serializer().loads(token)
    except BadSignature:
        return None

    device_id = data.get("d") if isinstance(data, dict) else None
    return device_id if isinstance(device_id, str) and device_id else None


def create_token(faculty_id):
    return _get_serializer().dumps({"faculty_id": faculty_id})


def verify_token(token):
    try:
        data = _get_serializer().loads(token, max_age=TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None

    faculty_id = data.get("faculty_id") if isinstance(data, dict) else None
    return faculty_id if isinstance(faculty_id, str) and faculty_id else None


def require_faculty(view):
    """Require a valid faculty token; exposes the caller as g.faculty_id."""

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        faculty_id = verify_token(token.strip()) if scheme.lower() == "bearer" else None

        # Re-check the account still exists, so removing a faculty member
        # also revokes their tokens.
        if not faculty_id or not db.faculty.find_one({"faculty_id": faculty_id}, {"_id": 1}):
            return {"message": "Authentication required"}, 401

        g.faculty_id = faculty_id
        return view(*args, **kwargs)

    return wrapper


def require_admin(view):
    """Require the X-Admin-Key header; disabled when ADMIN_API_KEY is unset."""

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        expected = os.getenv("ADMIN_API_KEY", "")

        if not expected:
            return {"message": "Admin API is disabled on this server"}, 403

        supplied = request.headers.get("X-Admin-Key", "")

        if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
            return {"message": "Admin key required"}, 403

        return view(*args, **kwargs)

    return wrapper


def teaches(faculty_id, class_name, subject_code=None, department_code=None):
    """True if this faculty member is assigned to the class (and subject)."""

    query = {"faculty_id": faculty_id, "class_name": class_name}

    if subject_code:
        query["subject_code"] = subject_code

    if department_code:
        query["department_code"] = department_code

    return db.faculty_assignments.find_one(query, {"_id": 1}) is not None


class AttemptLimiter:
    """
    Allows `max_attempts` failures per `window_seconds` for each key, then
    blocks that key until the oldest failure ages out. In-memory, so each
    server process keeps its own counts.
    """

    def __init__(self, max_attempts, window_seconds):
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._failures = {}
        self._lock = threading.Lock()

    def _prune(self, key, now):
        recent = [t for t in self._failures.get(key, []) if now - t < self.window]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def blocked_for(self, key):
        """Seconds until `key` may try again (0 = not blocked)."""
        now = time.monotonic()
        with self._lock:
            recent = self._prune(key, now)
            if len(recent) >= self.max_attempts:
                return max(1, int(self.window - (now - recent[0])) + 1)
            return 0

    def record_failure(self, key):
        now = time.monotonic()
        with self._lock:
            self._prune(key, now)
            self._failures.setdefault(key, []).append(now)

    def reset(self, key):
        with self._lock:
            self._failures.pop(key, None)


# Per (client address, faculty id) and per client address overall.
login_pair_limiter = AttemptLimiter(max_attempts=5, window_seconds=15 * 60)
login_ip_limiter = AttemptLimiter(max_attempts=20, window_seconds=15 * 60)
# Wrong enrollment codes, per client address.
enroll_limiter = AttemptLimiter(max_attempts=10, window_seconds=15 * 60)
# Registration frames rejected for quality (too far, blurry...), per address.
# The hands-free scan sends a frame every second or so, so this is generous.
register_quality_limiter = AttemptLimiter(max_attempts=90, window_seconds=10 * 60)
# Wrong QR tokens when asking for a check-in pass, per client address.
pass_limiter = AttemptLimiter(max_attempts=30, window_seconds=5 * 60)
# Wrong-face check-in attempts, per client address + USN. An auto-scanning
# page retries by itself, so repeated mismatches must not be free.
checkin_fail_limiter = AttemptLimiter(max_attempts=8, window_seconds=10 * 60)

# New phone ids handed out per client address (a generous cap: a whole class
# behind one shared address may all be first-time users).
try:
    _DEVICE_ISSUE_LIMIT = int(os.getenv("DEVICE_TOKEN_ISSUE_LIMIT", "100"))
except ValueError:
    _DEVICE_ISSUE_LIMIT = 100
device_issue_limiter = AttemptLimiter(max_attempts=_DEVICE_ISSUE_LIMIT, window_seconds=10 * 60)

