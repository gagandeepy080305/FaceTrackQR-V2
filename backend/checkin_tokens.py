"""
Rotating QR tokens.

Each attendance session has a secret. The QR shown by faculty carries a token
derived from that secret and the current time window (QR_ROTATE_SECONDS,
default 15). The server accepts the current and the previous window, so a
screenshot that is shared even half a minute later no longer works.

Scanning the QR does not check anyone in by itself: the student's page trades
the fresh token for a short-lived, single-use check-in pass
(see /attendance/pass), and only a pass can submit a check-in.
"""

import hashlib
import hmac
import math
import os
import time


def _int_env(name, default, minimum):
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def rotate_seconds():
    return _int_env("QR_ROTATE_SECONDS", 8, 5)


def pass_seconds():
    """How long a check-in pass stays valid. The hands-free scan takes ~3s, so
    45s leaves room for typing a USN once; a QR relayed to another room
    would have to be scanned and checked in within this window."""
    return _int_env("CHECKIN_PASS_SECONDS", 45, 20)


def _now():
    return time.time()


def _token_for(secret, session_id, window):
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{session_id}:{window}".encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return digest[:16]


def current_qr_token(secret, session_id):
    """(token, seconds until it rotates)."""
    seconds = rotate_seconds()
    now = _now()
    window = int(now // seconds)
    return (
        _token_for(secret, session_id, window),
        max(1, math.ceil(seconds - (now % seconds)))
    )


def qr_token_is_valid(secret, session_id, supplied):
    if not isinstance(supplied, str) or not supplied or not secret:
        return False

    window = int(_now() // rotate_seconds())

    # Evaluate both so timing doesn't reveal which window matched.
    matches = [
        hmac.compare_digest(_token_for(secret, session_id, w), supplied)
        for w in (window, window - 1)
    ]

    return any(matches)
