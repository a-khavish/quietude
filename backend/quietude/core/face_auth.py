# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
face_auth.py
Local, fully offline facial recognition: OpenCV's Haar cascade for
detection, LBPH for recognition. No cloud, no accounts, no external
calls - the trained model is a file in Quietude's data directory next to the
encrypted profile.

Ported essentially unchanged from the Windows build, which is the point:
this module never had any OS-specific code in it. The one Windows-
flavoured detail was a defensive fallback around cv2.data.haarcascades,
and it's kept - not because Linux has that problem, but because shipping
our own copy of the cascade and not trusting the installed wheel's
layout is just better practice on any platform.
"""

import base64

import cv2
import numpy as np

from quietude import config

FACE_SIZE = (200, 200)
MIN_SAMPLES_REQUIRED = 18

# LBPH's "confidence" is really a distance - lower means a closer match.
# Kept strict, since LBPH can struggle to tell apart people with similar
# facial structure (close relatives, say). A stray low-confidence frame
# is still possible, which is why the frontend requires several
# consecutive matching frames before it unlocks anything.
CONFIDENCE_THRESHOLD = 55

_cascade = None


class CascadeLoadError(RuntimeError):
    """No usable Haar cascade file could be found or loaded."""


def _get_cascade():
    global _cascade
    if _cascade is not None:
        return _cascade

    candidates = [config.CASCADE_PATH]
    # Fall back to whatever cv2 ships with, in case the bundled file is
    # missing from a stripped-down copy of the install.
    try:
        from pathlib import Path
        candidates.append(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
    except Exception:
        pass

    for path in candidates:
        if path.is_file():
            cascade = cv2.CascadeClassifier(str(path))
            # An unreadable file loads "empty" rather than raising, and
            # then detectMultiScale fails with an assertion later, a long
            # way from the actual cause. Check here instead.
            if not cascade.empty():
                _cascade = cascade
                return _cascade

    raise CascadeLoadError(
        "Could not load the face-detection model. Expected it at "
        f"{config.CASCADE_PATH} - if that file is missing, reinstall Quietude or restore "
        "backend/quietude/assets/haarcascades/haarcascade_frontalface_default.xml."
    )


def _decode_image(data_url: str):
    """Decode a base64 data URL (a <canvas>.toDataURL capture) to BGR."""
    if not data_url:
        return None
    if "," in data_url:
        data_url = data_url.split(",", 1)[1]
    try:
        raw = base64.b64decode(data_url)
    except Exception:
        return None
    arr = np.frombuffer(raw, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def _largest_face(gray):
    faces = _get_cascade().detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(80, 80)
    )
    if len(faces) == 0:
        return None
    faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
    return faces[0]


def extract_face(data_url: str):
    """A normalized 200x200 grayscale, histogram-equalized crop, or None."""
    img = _decode_image(data_url)
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    box = _largest_face(gray)
    if box is None:
        return None
    x, y, w, h = box
    face = gray[y:y + h, x:x + w]
    face = cv2.resize(face, FACE_SIZE)
    return cv2.equalizeHist(face)


def _sample_dir(user_id: str):
    d = config.FACES_DIR / user_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_sample(user_id: str, face_img) -> int:
    """Saves a crop as a new training sample. Returns the new count."""
    d = _sample_dir(user_id)
    idx = len(list(d.glob("*.png")))
    cv2.imwrite(str(d / f"{idx:03d}.png"), face_img)
    return idx + 1


def sample_count(user_id: str) -> int:
    return len(list(_sample_dir(user_id).glob("*.png")))


def clear_samples(user_id: str):
    d = _sample_dir(user_id)
    for p in d.glob("*.png"):
        p.unlink(missing_ok=True)


def train_model(user_id: str) -> bool:
    """Trains the LBPH recognizer on every saved sample for this user."""
    d = _sample_dir(user_id)
    images, labels = [], []
    for p in sorted(d.glob("*.png")):
        img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if img is not None:
            images.append(img)
            labels.append(0)  # single-user model - the registered face is label 0

    if len(images) < MIN_SAMPLES_REQUIRED:
        return False

    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.train(images, np.array(labels))
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    recognizer.save(str(config.FACE_MODEL_PATH))
    return True


def model_exists() -> bool:
    return config.FACE_MODEL_PATH.exists()


def verify(data_url: str):
    """Returns (matched: bool, confidence: float | None, detected: bool)."""
    if not model_exists():
        return False, None, False

    face = extract_face(data_url)
    if face is None:
        return False, None, False

    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.read(str(config.FACE_MODEL_PATH))
    label, confidence = recognizer.predict(face)
    matched = (label == 0) and (confidence <= CONFIDENCE_THRESHOLD)
    return matched, float(confidence), True
