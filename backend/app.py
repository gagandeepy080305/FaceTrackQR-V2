import os
from dotenv import load_dotenv
from flask import Flask
from werkzeug.exceptions import HTTPException
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

from routes.student import student_bp
from routes.faculty import faculty_bp
from routes.department import department_bp
from routes.classroom import classroom_bp
from routes.subject import subject_bp
from routes.faculty_assignment import assignment_bp
from routes.session import session_bp
from routes.attendance import attendance_bp
from routes.face import face_bp
from routes.enrollment import enrollment_bp
from routes.device import device_bp

app = Flask(__name__)

# When running behind a hosting proxy, trust its X-Forwarded-For so that
# request.remote_addr is the real client address (used by the network check
# on /attendance/mark). 0 = no proxy in front of the app.
try:
    TRUST_PROXY_HOPS = int(os.environ.get("TRUST_PROXY_HOPS", "0") or 0)
except ValueError:
    TRUST_PROXY_HOPS = 0

if TRUST_PROXY_HOPS > 0:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=TRUST_PROXY_HOPS)

CORS(app)

# Register Blueprints
app.register_blueprint(student_bp)
app.register_blueprint(faculty_bp)
app.register_blueprint(department_bp)
app.register_blueprint(classroom_bp)
app.register_blueprint(subject_bp)
app.register_blueprint(assignment_bp)
app.register_blueprint(session_bp)
app.register_blueprint(attendance_bp)
app.register_blueprint(face_bp)
app.register_blueprint(enrollment_bp)
app.register_blueprint(device_bp)


from network_guard import server_lan_ip


@app.errorhandler(Exception)
def handle_error(error):
    """Always answer with JSON, and never leak a stack trace to clients."""

    if isinstance(error, HTTPException):
        return {"message": error.description}, error.code

    # With FLASK_DEBUG=1 (local use only) let Flask show the full traceback.
    if app.debug:
        raise error

    app.logger.exception("Unhandled error")
    return {"message": "Internal Server Error"}, 500


@app.route("/")
def home():
    return {
        "message": "Backend Working"
    }


@app.route("/server/info")
def server_info():
    lan_ip = server_lan_ip()

    port = int(os.environ.get("PORT", 5001))
    return {
        "lan_ip": lan_ip,
        "frontend_url": f"http://{lan_ip}:5173",
        "backend_url": f"http://{lan_ip}:{port}"
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))

    # Flask's debugger lets anyone who can reach the port run code and read
    # source, so it is opt-in (FLASK_DEBUG=1) and then bound to this machine
    # only. Normal runs stay reachable on the LAN, which phones need.
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"

    # HOST=127.0.0.1 keeps the API reachable only through the https dev proxy.
    host = os.environ.get("HOST") or ("127.0.0.1" if debug else "0.0.0.0")

    app.run(host=host, port=port, debug=debug)