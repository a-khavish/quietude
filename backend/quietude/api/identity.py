# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
identity.py
Who the assistant is.

Quietude is the application. The assistant inside it has no name until
someone gives it one, and no pronouns, age or nationality either. This
is where those are set.

Applied live, not on save
-------------------------
Every field here takes effect the moment it changes. Most of them are
cosmetic - they change how the assistant refers to itself - and applying
those live is simply better behaviour.

The name is not cosmetic. It is what the wake word is built from, so
changing it re-arms the speech worker with a new phrase to listen for,
without a restart. That is the one place the user's choice has a
mechanical consequence, and the one place it can go quietly wrong: a
wake-word model recognises a fixed vocabulary, so a name it has never
heard of can never wake the assistant, however clearly it is said, and
nothing about the failure says why. So the name is checked against the
model's own lexicon and the answer is reported before the user commits
to it.
"""

from flask import Blueprint, jsonify, request

from quietude.core import database, speech_engine

bp = Blueprint("identity", __name__, url_prefix="/api/identity")

# Long enough for a double-barrelled name, short enough that it still
# fits in a chat bubble label and a window title.
MAX_NAME = 32
MAX_NATIONALITY = 48
AGE_MIN, AGE_MAX = 1, 200


def _payload(identity, wake=None):
    """The identity plus everything derived from it, so the interface
    never has to re-derive any of it and the two can't disagree."""
    pronouns = database.pronouns_for(identity.get("pronouns"))
    name = identity.get("name") or ""
    return {
        "ok": True,
        "identity": identity,
        "pronouns": pronouns,
        "named": bool(name),
        # What the user would actually say out loud. Built here because
        # the speech engine builds the matching pattern from the same
        # name, and two places inventing the phrasing separately is how
        # the hint and the behaviour drift apart.
        "wake_phrase": f"{speech_engine.WAKE_PREFIXES[0]} {name}" if name else "",
        "wake_prefixes": list(speech_engine.WAKE_PREFIXES),
        "wake_check": wake,
        "pronoun_options": sorted(database.PRONOUN_SETS),
    }


def _validate(payload):
    values, errors = {}, {}

    if "name" in payload:
        name = " ".join(str(payload.get("name") or "").split())
        if len(name) > MAX_NAME:
            errors["name"] = f"Keep the name to {MAX_NAME} characters or fewer."
        elif name and not all(part.replace("-", "").replace("'", "").isalpha()
                              for part in name.split(" ")):
            # Letters, hyphens and apostrophes. Not pedantry: this name
            # becomes a wake word, and a recogniser returns words - it
            # has no way to return a digit or a symbol, so a name
            # containing one could never be matched.
            errors["name"] = (
                "Letters only (hyphens and apostrophes are fine). The name "
                "becomes the wake word, and the recogniser only ever returns "
                "words - it has no way to hear a digit or a symbol."
            )
        else:
            values["name"] = name

    if "pronouns" in payload:
        pronouns = str(payload.get("pronouns") or "").lower().strip()
        if pronouns not in database.PRONOUN_SETS:
            errors["pronouns"] = "Choose she, he or they."
        else:
            values["pronouns"] = pronouns

    if "age" in payload:
        age = payload.get("age")
        if age in (None, ""):
            values["age"] = None
        else:
            try:
                age = int(age)
            except (TypeError, ValueError):
                errors["age"] = "Age must be a whole number, or left blank."
            else:
                if not (AGE_MIN <= age <= AGE_MAX):
                    errors["age"] = f"Age must be between {AGE_MIN} and {AGE_MAX}."
                else:
                    values["age"] = age

    if "nationality" in payload:
        nationality = " ".join(str(payload.get("nationality") or "").split())
        if len(nationality) > MAX_NATIONALITY:
            errors["nationality"] = f"Keep this to {MAX_NATIONALITY} characters or fewer."
        else:
            values["nationality"] = nationality

    return values, errors


@bp.get("")
def get_identity():
    identity = database.get_assistant()
    wake = None
    if identity.get("name"):
        known, detail = speech_engine.wake_name_is_recognisable(identity["name"])
        wake = {"known": known, "detail": detail}
    return jsonify(_payload(identity, wake))


@bp.post("")
def save_identity():
    """Saves whatever fields were sent and applies them immediately.

    Partial updates are the normal case: the settings page sends one
    field as it is edited, so there is no Save button to forget to
    press, and a partial write must not blank the fields it wasn't
    given.
    """
    payload = request.get_json(silent=True) or {}
    values, errors = _validate(payload)
    if errors:
        return jsonify({"ok": False, "field_errors": errors}), 400

    before = database.get_assistant()
    identity = database.set_assistant(**values)

    # The name is the only field with a mechanical consequence, so it is
    # the only one that has to reach the speech worker. Pushed rather
    # than polled: the wake word should change the moment the name does,
    # not at the next restart.
    wake = None
    if identity.get("name") != before.get("name"):
        speech_engine.set_identity(identity.get("name"))
        if identity.get("name"):
            known, detail = speech_engine.wake_name_is_recognisable(identity["name"])
            wake = {"known": known, "detail": detail}

    return jsonify(_payload(identity, wake))


@bp.get("/wake-check")
def wake_check():
    """Can the wake-word model hear this name?

    Asked while the user is still typing, so the answer arrives before
    they commit rather than as a surprise the first time they try to
    use their voice.
    """
    name = " ".join((request.args.get("name") or "").split())
    known, detail = speech_engine.wake_name_is_recognisable(name)
    return jsonify({
        "ok": True,
        "name": name,
        "known": known,
        "detail": detail,
        "wake_phrase": f"{speech_engine.WAKE_PREFIXES[0]} {name}" if name else "",
    })
