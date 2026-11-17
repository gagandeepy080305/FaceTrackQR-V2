"""
Quality gate for the photo a student registers with.

The registered photo is the reference every later check-in is compared
against, so a small, dark, blurry or tilted one makes the whole semester's
check-ins less accurate. These checks run on the server for every frame the
hands-free registration sends, and say what to fix ("Move closer", "More
light") so the student can correct it without pressing anything.

All limits can be tuned with environment variables (see .env.example).
"""

import math
import os

try:
    import cv2
except ImportError:  # DeepFace depends on OpenCV, so this only guards odd setups
    cv2 = None


def _number(name, default):
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return float(default)


class QualityError(Exception):
    """A frame that can't be used; `code` lets the page show a matching hint."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def limits():
    return {
        # face width as a share of the picture's width
        "min_face_ratio": _number("REGISTER_MIN_FACE_RATIO", 0.22),
        "min_face_px": _number("REGISTER_MIN_FACE_PX", 110),
        "min_brightness": _number("REGISTER_MIN_BRIGHTNESS", 70),
        "max_brightness": _number("REGISTER_MAX_BRIGHTNESS", 200),
        "min_sharpness": _number("REGISTER_MIN_SHARPNESS", 45),
        "max_tilt_degrees": _number("REGISTER_MAX_TILT", 18),
        # a second face this big (relative to the main one) means a crowd
        "other_face_ratio": 0.5,
    }


def measure(path, area):
    """
    Brightness and sharpness of the face region, plus the picture's size.
    Returns None when the image can't be read here (then only the
    face-position checks apply).
    """

    if cv2 is None:
        return None

    image = cv2.imread(path)

    if image is None:
        return None

    height, width = image.shape[:2]
    x, y = max(0, int(area.get("x", 0))), max(0, int(area.get("y", 0)))
    w, h = int(area.get("w", 0)), int(area.get("h", 0))
    crop = image[y:y + h, x:x + w]

    if crop.size == 0:
        crop = image

    # Compare faces at one size so a close-up isn't "sharper" than a far shot.
    gray = cv2.cvtColor(cv2.resize(crop, (160, 160)), cv2.COLOR_BGR2GRAY)

    return {
        "width": width,
        "height": height,
        "brightness": float(gray.mean()),
        "sharpness": float(cv2.Laplacian(gray, cv2.CV_64F).var()),
    }


def _tilt(area):
    """Head roll in degrees from the eye line, when the detector gave eyes."""
    left, right = area.get("left_eye"), area.get("right_eye")

    if not left or not right:
        return 0.0

    # Eye points are reported as seen in the picture; either order is fine.
    dx, dy = right[0] - left[0], right[1] - left[1]

    if dx == 0 and dy == 0:
        return 0.0

    angle = abs(math.degrees(math.atan2(dy, dx)))
    return min(angle, 180 - angle)


def judge(faces, metrics):
    """
    Decide whether the frame is good enough. `faces` is DeepFace.extract_faces'
    result. Returns the frame's sharpness (so the better of two frames can be
    kept), or raises QualityError.
    """

    rules = limits()

    def box(face):
        return face.get("facial_area") or {}

    ranked = sorted(faces, key=lambda f: box(f).get("w", 0) * box(f).get("h", 0), reverse=True)

    if not ranked:
        raise QualityError("no_face", "No face detected. Look straight at the camera.")

    main = box(ranked[0])

    if len(ranked) > 1 and box(ranked[1]).get("w", 0) >= main.get("w", 0) * rules["other_face_ratio"]:
        raise QualityError("multiple", "Only your face should be in the picture.")

    width = (metrics or {}).get("width")

    if width:
        face_w = main.get("w", 0)

        if face_w < rules["min_face_px"] or face_w < width * rules["min_face_ratio"]:
            raise QualityError("too_far", "Move closer so your face fills the circle.")

        centre = main.get("x", 0) + face_w / 2

        if not width * 0.2 <= centre <= width * 0.8:
            raise QualityError("off_centre", "Move your face to the middle of the circle.")

    if _tilt(main) > rules["max_tilt_degrees"]:
        raise QualityError("tilted", "Keep your head straight.")

    if not metrics:
        return 0.0

    if metrics["brightness"] < rules["min_brightness"]:
        raise QualityError("too_dark", "Too dark. Face a light or move somewhere brighter.")

    if metrics["brightness"] > rules["max_brightness"]:
        raise QualityError("too_bright", "Too bright. Turn away from direct light.")

    if metrics["sharpness"] < rules["min_sharpness"]:
        raise QualityError("blurry", "Hold still. The picture is blurry.")

    return metrics["sharpness"]
