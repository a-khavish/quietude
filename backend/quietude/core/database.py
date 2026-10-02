# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
database.py
All local persistence for Quietude: the user profile, the one-time agreement
flag, in-progress setup-wizard state (so setup survives a restart), and
encryption/hashing of sensitive fields with a key generated on this
machine. Nothing ever leaves it - no server, no cloud, no telemetry.

Ported from the Windows build with its security properties unchanged:
the backup password is hashed with scrypt (werkzeug) and never
recoverable, the Gemini API key is Fernet-encrypted (reversible, since
it has to be usable for real calls), and the key itself lives in a file
only this user can read. The one change is where those files live - see
config.py for why they moved out of the source tree.
"""

import datetime
import json
import os
import shutil
import uuid

from cryptography.fernet import Fernet
from werkzeug.security import check_password_hash, generate_password_hash

from quietude import config

VALID_FIELDS = ("name", "age")


def _ensure_data_dir():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)


def _get_or_create_key():
    _ensure_data_dir()
    if config.KEY_FILE.exists():
        return config.KEY_FILE.read_bytes()
    key = Fernet.generate_key()
    config.KEY_FILE.write_bytes(key)
    # The whole point of this file is that it never leaves the machine,
    # so it should not be readable by other accounts on it either. The
    # Windows build left this to NTFS inheritance; here it's explicit.
    os.chmod(config.KEY_FILE, 0o600)
    return key


def _fernet():
    return Fernet(_get_or_create_key())


def _load_raw():
    _ensure_data_dir()
    if not config.USERS_FILE.exists():
        return {"users": [], "agreement_accepted": False, "setup_progress": None}
    try:
        with open(config.USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            data.setdefault("setup_progress", None)
            return data
    except (json.JSONDecodeError, OSError):
        return {"users": [], "agreement_accepted": False, "setup_progress": None}


def _save_raw(data):
    _ensure_data_dir()
    # Write to a sibling temp file and rename over the target. os.replace
    # is atomic within a filesystem, so a crash or a power cut mid-write
    # leaves the previous profile intact instead of a half-written file.
    tmp = config.USERS_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, config.USERS_FILE)
    os.chmod(config.USERS_FILE, 0o600)



# ============================================================
# Who the assistant is
# ============================================================
#
# Stored at the top level rather than on the user record: there is one
# assistant, and the people it belongs to are a separate question.
#
# The name starts empty on purpose. The app is called Quietude; the
# assistant inside it has no name until someone gives it one, and
# shipping a default would quietly make that default the answer for
# almost everyone. The setup wizard asks.

ASSISTANT_DEFAULTS = {
    "name": "",
    # she / he / they. Stored as the subject pronoun because that is
    # what the rest of the text is derived from.
    "pronouns": "they",
    "age": None,
    "nationality": "",
}

# Subject, object, possessive determiner, possessive pronoun, reflexive,
# and whether the verb agrees as singular. "They" is singular here - "they
# is listening" is wrong - so the flag is what the copy uses to pick
# between "is" and "are".
PRONOUN_SETS = {
    "she": {"they": "she", "them": "her", "their": "her", "theirs": "hers",
            "themself": "herself", "s": "s", "are": "is"},
    "he":  {"they": "he", "them": "him", "their": "his", "theirs": "his",
            "themself": "himself", "s": "s", "are": "is"},
    "they": {"they": "they", "them": "them", "their": "their", "theirs": "theirs",
             "themself": "themselves", "s": "", "are": "are"},
}


def pronouns_for(key):
    return PRONOUN_SETS.get((key or "they").lower(), PRONOUN_SETS["they"])


def get_assistant():
    """The assistant's identity, with every field present."""
    stored = _load_raw().get("assistant") or {}
    identity = dict(ASSISTANT_DEFAULTS)
    identity.update({k: v for k, v in stored.items() if k in ASSISTANT_DEFAULTS})
    return identity


def set_assistant(**fields):
    """Updates whichever fields were given and returns the whole identity.

    Partial on purpose: the settings page sends one field at a time as
    it is edited, so that changes apply as they are made rather than on
    a Save button - and a partial write must not blank the rest."""
    data = _load_raw()
    identity = dict(ASSISTANT_DEFAULTS)
    identity.update(data.get("assistant") or {})
    for key, value in fields.items():
        if key in ASSISTANT_DEFAULTS:
            identity[key] = value
    data["assistant"] = identity
    _save_raw(data)
    return identity


def has_users():
    return len(_load_raw().get("users", [])) > 0


def agreement_accepted():
    return _load_raw().get("agreement_accepted", False)


def accept_agreement():
    data = _load_raw()
    data["agreement_accepted"] = True
    _save_raw(data)


# ============================================================
# Setup-wizard progress (pause / resume across restarts)
# ============================================================

def get_setup_progress():
    return _load_raw().get("setup_progress")


def save_setup_progress(progress: dict):
    data = _load_raw()
    data["setup_progress"] = progress
    _save_raw(data)


def clear_setup_progress():
    data = _load_raw()
    data["setup_progress"] = None
    _save_raw(data)


# ============================================================
# User creation / lookup
# ============================================================

def create_user(full_name, preferred_name, age, login_password):
    data = _load_raw()

    user = {
        "id": str(uuid.uuid4()),
        "full_name": full_name.strip(),
        "name": preferred_name.strip(),  # what Quietude calls them, used throughout
        "age": age,
        "login_password_hash": generate_password_hash(login_password),
        "face_registered": False,
        "created_at": datetime.datetime.now().isoformat(),
    }
    data.setdefault("users", []).append(user)
    data["setup_progress"] = None
    _save_raw(data)
    return user


def get_primary_user():
    users = _load_raw().get("users", [])
    return users[0] if users else None


def verify_login_password(user, password):
    return check_password_hash(user.get("login_password_hash", ""), password or "")


def set_gemini_api_key(user_id, api_key):
    """Encrypted rather than hashed - reversible on purpose, since it has
    to be usable for real API calls. Never stored or shown in plain text."""
    data = _load_raw()
    encrypted = _fernet().encrypt(api_key.encode()).decode()
    for user in data.get("users", []):
        if user["id"] == user_id:
            user["gemini_api_key_enc"] = encrypted
            break
    _save_raw(data)


def clear_gemini_api_key(user_id):
    """Removes the key entirely rather than blanking it - backs the
    `gemini api key ""` command."""
    data = _load_raw()
    for user in data.get("users", []):
        if user["id"] == user_id:
            user.pop("gemini_api_key_enc", None)
            break
    _save_raw(data)


def get_gemini_api_key(user):
    enc = user.get("gemini_api_key_enc") if user else None
    if not enc:
        return None
    try:
        return _fernet().decrypt(enc.encode()).decode()
    except Exception:
        return None


def set_tts_settings(user_id, rate, volume, voice_id):
    """Voice preferences aren't sensitive, so unlike the password and API
    key these are stored as plain values."""
    data = _load_raw()
    for user in data.get("users", []):
        if user["id"] == user_id:
            user["tts_settings"] = {"rate": rate, "volume": volume, "voice_id": voice_id}
            break
    _save_raw(data)


def get_tts_settings(user):
    if not user:
        return None
    return user.get("tts_settings")


def update_user_field(user_id, field, value):
    """Updates one account field (name / age). Age is stored as an int."""
    if field not in VALID_FIELDS:
        raise ValueError(f"Unknown field: {field}")

    data = _load_raw()
    for user in data.get("users", []):
        if user["id"] == user_id:
            if field == "age":
                user["age"] = int(value)
            else:
                user[field] = str(value).strip()
            user["updated_at"] = datetime.datetime.now().isoformat()
            break
    _save_raw(data)


def set_face_registered(flag: bool):
    data = _load_raw()
    users = data.get("users", [])
    if users:
        users[0]["face_registered"] = bool(flag)
    _save_raw(data)


def face_registered():
    user = get_primary_user()
    return bool(user and user.get("face_registered"))


def factory_reset():
    """Wipes everything Quietude has ever saved about this person - profile,
    encryption key, face samples and trained model, uploads - so the next
    launch starts as though she'd never been set up. Irreversible.

    Deliberately does NOT touch models/, which lives under the same data
    directory: those are large read-only downloads with nothing personal
    in them, and re-downloading several hundred megabytes is not what
    anyone means by "reset my data". The Windows build removed its whole
    data folder because models didn't live there."""
    _ensure_data_dir()

    for path in (config.USERS_FILE, config.KEY_FILE, config.FACE_MODEL_PATH):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    for directory in (config.FACES_DIR, config.UPLOADS_DIR):
        shutil.rmtree(directory, ignore_errors=True)

    config.FACES_DIR.mkdir(parents=True, exist_ok=True)
    config.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
