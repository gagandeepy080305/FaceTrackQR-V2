from datetime import datetime

from flask import Blueprint, request, g, send_file
from database.mongodb import db
from auth import enroll_limiter, register_quality_limiter, require_faculty, teaches, read_device_token
from face_quality import QualityError, judge, measure
from network_guard import client_in_lock, lock_is_private_lan, is_loopback
from routes.enrollment import find_enrollment_code
from face_settings import face_model, match_threshold, effective_threshold
from werkzeug.utils import secure_filename
from deepface import DeepFace
import os
import shutil
import tempfile

face_bp = Blueprint("face", __name__)

UPLOAD_FOLDER = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "uploads", "faces")
)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


@face_bp.route("/face/test")
def face_test():
    return {
        "message": "Face Route Working"
    }


NO_FACE_MESSAGE = (
    "No face detected in the photo. Use a clear, front-facing photo "
    "with good lighting."
)


SPOOF_MESSAGE = (
    "This looks like a photo of a screen or a print. "
    "Take a live selfie with your camera."
)


def anti_spoofing_enabled():
    # Opt-in (ANTI_SPOOFING=1): needs DeepFace's anti-spoofing model files.
    return os.getenv("ANTI_SPOOFING", "0") == "1"


def _save_upload_to_temp(uploaded_image):
    with tempfile.NamedTemporaryFile(
        delete=False,
        suffix=".jpg"
    ) as temp:
        uploaded_image.save(temp.name)
        return temp.name


# Two frames sent together must show the same, still person.
FRAME_PAIR_RATIO = 0.85


def assess_frame(path):
    """Quality-check one registration frame; returns its sharpness."""

    try:
        faces = DeepFace.extract_faces(img_path=path, enforce_detection=True)
    except ValueError:
        raise QualityError("no_face", NO_FACE_MESSAGE)

    main = max(
        faces,
        key=lambda f: (f.get("facial_area") or {}).get("w", 0)
        * (f.get("facial_area") or {}).get("h", 0)
    )

    return judge(faces, measure(path, main.get("facial_area") or {}))


@face_bp.route("/face/register", methods=["POST"])
def register_face():
    """
    Register a student's face. Requires a live enrollment code from a faculty
    member, a USN that is on the class roster, and no existing registration
    (only faculty can reset one), so a face can't be registered on someone
    else's behalf or swapped out later.
    """

    usn = (request.form.get("usn") or "").strip().upper()
    code = (request.form.get("enrollment_code") or "").strip().upper()

    if not usn:
        return {
            "message": "USN is required"
        }, 400

    if "image" not in request.files:
        return {
            "message": "No Image Uploaded"
        }, 400

    if not code:
        return {
            "message": "Enrollment code is required. Ask your faculty for the code shown in class."
        }, 400

    client_ip = request.remote_addr or "unknown"
    wait = enroll_limiter.blocked_for(client_ip)

    if wait:
        return {
            "message": "Too many wrong codes. Please wait a few minutes and try again."
        }, 429, {"Retry-After": str(wait)}

    enrollment, state = find_enrollment_code(code)

    if state == "invalid":
        enroll_limiter.record_failure(client_ip)
        return {
            "message": "Invalid enrollment code"
        }, 403

    if state == "expired":
        return {
            "message": "Enrollment code expired. Ask your faculty for a new one."
        }, 403

    # Registration only works from the network the faculty opened it on, so a
    # shared code can't be used from somewhere else.
    if not client_in_lock(enrollment.get("network_lock"), request.remote_addr):
        return {
            "message": "Not connected to the class network. Join the same Wi-Fi as your classroom and try again."
        }, 400

    device_id = read_device_token((request.form.get("device") or "").strip())

    if not device_id:
        return {
            "message": "This phone isn't recognised. Reload the page and try again."
        }, 403

    # One student per phone per enrollment window, and on a private LAN one
    # per address too (the in-app browser a QR scan opens forgets the phone id,
    # so the address is what stops a phone from registering classmates).
    client_ip = request.remote_addr
    lock = enrollment.get("network_lock") or {}
    one_per_phone = (
        os.getenv("ENFORCE_ONE_CHECKIN_PER_DEVICE", "1") != "0"
        and lock.get("mode") != "any"
    )
    by_address = lock_is_private_lan(lock) and not is_loopback(client_ip)

    def same_phone(entry):
        return entry.get("device") == device_id or (by_address and entry.get("ip") == client_ip)

    if one_per_phone and any(
        entry.get("usn") != usn and same_phone(entry)
        for entry in enrollment.get("registered", [])
    ):
        return {
            "message": "This phone has already registered another student. Each student must use their own phone."
        }, 403

    student = db.students.find_one({
        "usn": usn
    })

    if not student:
        return {
            "message": "USN not found in the class roster"
        }, 404

    if student.get("class_name") != enrollment["class_name"]:
        return {
            "message": f"This enrollment code is for class {enrollment['class_name']}"
        }, 403

    if db.student_faces.find_one({"usn": usn}):
        return {
            "message": "A face is already registered for this USN. Ask your faculty to reset it."
        }, 409

    # The registered photo is what every later check-in is compared against,
    # so refuse anything unclear: no face, too small, dark, blurry, tilted or
    # more than one person. The hands-free page sends two frames about a
    # second apart; they must show the same person, and the sharper is kept.
    wait = register_quality_limiter.blocked_for(client_ip)

    if wait:
        return {
            "message": "Too many unclear photos. Wait a minute, then try again.",
            "code": "slow_down"
        }, 429, {"Retry-After": str(wait)}

    temp_paths = [_save_upload_to_temp(request.files["image"])]

    if "image2" in request.files:
        temp_paths.append(_save_upload_to_temp(request.files["image2"]))

    temp_path = temp_paths[0]

    try:
        try:
            sharpness = [assess_frame(path) for path in temp_paths]

            if len(temp_paths) == 2:
                pair = DeepFace.verify(
                    img1_path=temp_paths[0],
                    img2_path=temp_paths[1],
                    model_name=face_model(),
                    distance_metric="cosine",
                    threshold=match_threshold(),
                    enforce_detection=True
                )

                if pair["distance"] > effective_threshold() * FRAME_PAIR_RATIO:
                    raise QualityError(
                        "unstable",
                        "Hold still and make sure only you are in the picture."
                    )

                temp_path = temp_paths[sharpness.index(max(sharpness))]
        except (QualityError, ValueError) as error:
            register_quality_limiter.record_failure(client_ip)

            if isinstance(error, QualityError):
                return {"message": error.message, "code": error.code}, 422

            return {"message": NO_FACE_MESSAGE, "code": "no_face"}, 422

        filepath = os.path.join(
            UPLOAD_FOLDER,
            secure_filename(f"{usn}.jpg")
        )

        # Claim the USN atomically so two simultaneous registrations can't
        # both succeed.
        claim = db.student_faces.update_one(
            {"usn": usn},
            {"$setOnInsert": {
                "image_path": filepath,
                # Not usable for check-in until the faculty approves it.
                "status": "pending",
                "registered_at": datetime.utcnow(),
                "device_id": device_id,
                "client_ip": request.remote_addr,
                "capture_mode": "live" if request.form.get("capture_mode") == "live" else "file"
            }},
            upsert=True
        )

        if claim.upserted_id is None:
            return {
                "message": "A face is already registered for this USN. Ask your faculty to reset it."
            }, 409

        try:
            shutil.move(temp_path, filepath)
        except OSError:
            db.student_faces.delete_one({"usn": usn})
            raise
    finally:
        # The kept frame was moved into place; remove whatever is left.
        for path in temp_paths:
            if os.path.exists(path):
                os.remove(path)

    entry = {"usn": usn, "device": device_id, "ip": client_ip}

    # Record it atomically; if another registration from this phone slipped in
    # at the same moment, undo this one.
    conflict_filter = {"code": code}
    if one_per_phone:
        conflict_filter["registered"] = {"$not": {"$elemMatch": {
            "usn": {"$ne": usn},
            "$or": [{"device": device_id}] + ([{"ip": client_ip}] if by_address else [])
        }}}

    recorded = db.enrollment_codes.update_one(conflict_filter, {"$push": {"registered": entry}})

    if recorded.matched_count == 0:
        db.student_faces.delete_one({"usn": usn})
        undo_path = os.path.join(UPLOAD_FOLDER, secure_filename(f"{usn}.jpg"))
        if os.path.isfile(undo_path):
            os.remove(undo_path)
        return {
            "message": "This phone has already registered another student. Each student must use their own phone."
        }, 403

    return {
        "message": "Face Registered Successfully. Your faculty will approve it, then you can check in.",
        "status": "pending"
    }


class FaceCheckError(Exception):
    """A face check that cannot proceed; carries the HTTP status to return."""

    def __init__(self, message, status):
        super().__init__(message)
        self.message = message
        self.status = status


def verify_student_face(usn, uploaded_image):
    """
    Compare an uploaded selfie against the face registered for `usn`.

    Returns {"verified": bool, "distance": float}.
    Raises FaceCheckError for unknown students, missing registrations and
    unreadable images. Shared by /face/verify and /attendance/mark so that
    attendance can never be recorded without a server-side face match.
    """

    student = db.students.find_one({
        "usn": usn
    })

    if not student:
        raise FaceCheckError("Invalid USN", 404)

    face_record = db.student_faces.find_one({
        "usn": usn
    })

    if not face_record:
        raise FaceCheckError("Face Not Registered", 404)

    if face_record.get("status", "approved") == "pending":
        raise FaceCheckError(
            "Your face registration is waiting for your faculty's approval.",
            403
        )

    registered_image = face_record["image_path"]

    if not os.path.exists(registered_image):
        raise FaceCheckError(
            "Registered face image is missing. Please register your face again.",
            409
        )

    temp_path = None
    try:
        temp_path = _save_upload_to_temp(uploaded_image)

        # Liveness check on the selfie only (never the registered photo, which
        # may legitimately be a scan of an ID photo).
        if anti_spoofing_enabled():
            faces = DeepFace.extract_faces(
                img_path=temp_path,
                enforce_detection=True,
                anti_spoofing=True
            )

            # extract_faces reports spoofing per face instead of raising, so
            # judge the main (largest) face. Anything other than a clear
            # "real" verdict is refused.
            main_face = max(
                faces,
                key=lambda f: (f.get("facial_area") or {}).get("w", 0)
                * (f.get("facial_area") or {}).get("h", 0)
            )

            print("[anti-spoofing]", main_face.get("is_real"), main_face.get("antispoof_score"))

            if main_face.get("is_real") is not True:
                raise ValueError("Spoof detected in the given image.")

        # enforce_detection=True: a photo without a detectable face is
        # rejected instead of being compared as-is (a blank image can
        # otherwise land under the match threshold).
        # model + pass mark come from face_settings (stricter than DeepFace's
        # default, which let a different person through in testing).
        result = DeepFace.verify(
            img1_path=registered_image,
            img2_path=temp_path,
            model_name=face_model(),
            distance_metric="cosine",
            threshold=match_threshold(),
            enforce_detection=True
        )
    except ValueError as error:
        message = str(error).lower()

        if "spoof" in message:
            raise FaceCheckError(SPOOF_MESSAGE, 422)

        if "img1_path" in message:
            raise FaceCheckError(
                "No face could be detected in your registered photo. "
                "Please register your face again with a clear photo.",
                409
            )
        raise FaceCheckError(NO_FACE_MESSAGE, 422)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

    print(result)

    return {
        "verified": bool(result["verified"]),
        "distance": float(result["distance"])
    }


@face_bp.route("/face/verify", methods=["POST"])
@require_faculty
def verify_face():
    """
    Faculty-only match test. It used to be public, which let anyone try
    unlimited photos against any classmate's USN and read the match score.
    """

    usn = (request.form.get("usn") or "").strip().upper()

    student = db.students.find_one({
        "usn": usn
    })

    if not student:
        return {
            "message": "Invalid USN"
        }, 404

    if not teaches(g.faculty_id, student.get("class_name")):
        return {
            "message": "You can only test students in your classes"
        }, 403

    if "image" not in request.files:
        return {
            "message": "No Image Uploaded"
        }, 400

    try:
        return verify_student_face(usn, request.files["image"])
    except FaceCheckError as error:
        return {
            "message": error.message
        }, error.status


@face_bp.route(
    "/face/status/<usn>",
    methods=["GET"]
)
def face_status(usn):
    usn = usn.strip().upper()

    face = db.student_faces.find_one({
        "usn": usn
    })

    status = None

    if face is not None:
        status = face.get("status", "approved")

    return {
        "registered": face is not None,
        "status": status
    }


def _student_in_my_class(usn):
    """(face_record, error_response) for a faculty member's own student."""

    usn = usn.strip().upper()
    face = db.student_faces.find_one({"usn": usn})

    if not face:
        return None, ({"message": "Face Not Found"}, 404)

    student = db.students.find_one({"usn": usn}, {"class_name": 1})

    if not student or not teaches(g.faculty_id, student.get("class_name")):
        return None, ({"message": "You can only manage students in your classes"}, 403)

    return face, None


@face_bp.route("/face/photo/<usn>", methods=["GET"])
@require_faculty
def face_photo(usn):
    """The registered photo, for the faculty to check before approving it."""

    face, error = _student_in_my_class(usn)

    if error:
        return error

    path = face.get("image_path", "")

    if not path or not os.path.isfile(path):
        return {"message": "Photo file is missing"}, 404

    response = send_file(path, mimetype="image/jpeg", max_age=0)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@face_bp.route("/face/approve/<usn>", methods=["POST"])
@require_faculty
def approve_face(usn):
    """Approve a registration after checking the photo matches the student."""

    face, error = _student_in_my_class(usn)

    if error:
        return error

    db.student_faces.update_one(
        {"usn": face["usn"]},
        {"$set": {
            "status": "approved",
            "approved_by": g.faculty_id,
            "approved_at": datetime.utcnow()
        }}
    )

    return {"usn": face["usn"], "status": "approved"}


@face_bp.route(
    "/face/delete/<usn>",
    methods=["DELETE"]
)
@require_faculty
def delete_face(usn):
    """Reset a student's face so they can register again (faculty only)."""

    usn = usn.strip().upper()

    face = db.student_faces.find_one({
        "usn": usn
    })

    if not face:
        return {
            "message": "Face Not Found"
        }, 404

    student = db.students.find_one({"usn": usn}, {"class_name": 1})

    if not student or not teaches(g.faculty_id, student.get("class_name")):
        return {
            "message": "You can only reset faces for students in your classes"
        }, 403

    db.student_faces.delete_one({
        "usn": usn
    })

    # Also remove the stored photo, but only if it lives in our own uploads
    # folder (never touch files elsewhere on disk).
    image_path = os.path.abspath(face.get("image_path", ""))

    if (
        os.path.commonpath([image_path, UPLOAD_FOLDER]) == UPLOAD_FOLDER
        and os.path.isfile(image_path)
    ):
        os.remove(image_path)

    return {
        "message": "Face Deleted Successfully"
    }
