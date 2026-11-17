"""
Face-match settings.

Model: Facenet512 (cosine distance, DeepFace's own pass mark 0.30). It was
chosen after replaying real phone check-ins: the old default, VGG-Face, scored
genuine matches at 0.40-0.45 and a different person at 0.55, so its default
pass mark (0.68) accepted strangers and even a stricter 0.50 left almost no
margin. Facenet512 scored the same genuine check-ins at about 0.25 and the
strangers at 0.43-0.59.

The model's weight file (~94 MB) is downloaded by DeepFace on first use if it
is not already in ~/.deepface/weights.

Everything is adjustable without code changes:
  FACE_MODEL            DeepFace model name (e.g. VGG-Face, ArcFace)
  FACE_MATCH_THRESHOLD  pass mark (cosine distance; lower = stricter)
  HOLD_BORDERLINE       1 (default): a match between the "Borderline" line and
                        the pass mark is held for the faculty to confirm instead
                        of counting as present. 0 = count it, with a flag.
"""

import os

DEFAULT_MODEL = "Facenet512"

# VGG-Face's library default (0.68) lets strangers through; 0.50 is the strictest
# value that still accepted genuine phone check-ins in testing.
VGG_FACE_THRESHOLD = 0.50

# Check-ins at or above this share of the pass mark are flagged "Borderline"
# for the faculty (genuine matches normally sit well below it).
REVIEW_RATIO = 0.85


def face_model():
    return os.getenv("FACE_MODEL", "").strip() or DEFAULT_MODEL


def match_threshold():
    """Pass mark to hand to DeepFace, or None to use that model's own default."""
    raw = os.getenv("FACE_MATCH_THRESHOLD", "").strip()

    if raw:
        try:
            return float(raw)
        except ValueError:
            print(f"[face_settings] Ignoring invalid FACE_MATCH_THRESHOLD: {raw!r}")

    return VGG_FACE_THRESHOLD if face_model() == "VGG-Face" else None


def effective_threshold():
    """The pass mark actually in force (resolving the library default if needed)."""
    configured = match_threshold()

    if configured is not None:
        return configured

    try:
        from deepface.modules.verification import find_threshold
        return float(find_threshold(face_model(), "cosine"))
    except Exception:
        return 0.68


def review_distance():
    """Matches at or above this are 'Borderline' on the faculty sheet."""
    return round(effective_threshold() * REVIEW_RATIO, 3)


def hold_borderline():
    """Whether borderline matches wait for the faculty's confirmation."""
    return os.getenv("HOLD_BORDERLINE", "1").strip() != "0"

