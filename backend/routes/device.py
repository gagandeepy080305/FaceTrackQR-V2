from flask import Blueprint, request

from auth import create_device_token, read_device_token, device_issue_limiter

device_bp = Blueprint("device", __name__)


@device_bp.route("/device/token", methods=["POST"])
def device_token():
    """
    Give the phone a server-signed id (or confirm the one it already has).
    The id decides which phone a check-in or registration came from, so a
    student can't pick or invent one.
    """

    data = request.get_json(silent=True) or {}
    existing = data.get("device")

    if read_device_token(existing):
        return {"device": existing}

    client_ip = request.remote_addr or "unknown"
    wait = device_issue_limiter.blocked_for(client_ip)

    if wait:
        return {
            "message": "Too many new devices from this network. Please wait a few minutes."
        }, 429, {"Retry-After": str(wait)}

    device_issue_limiter.record_failure(client_ip)

    return {"device": create_device_token()}
