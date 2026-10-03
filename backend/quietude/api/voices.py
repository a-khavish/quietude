# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
voices.py
The voice library: browse the piper catalog, install, remove.

Downloads don't happen in the request. A voice model is 20-110MB over a
link Quietude knows nothing about, and a request that blocks for two minutes
is a request that times out somewhere between here and the browser. So
POST /install starts a background download and returns a job the page
polls - the same shape the rest of Quietude uses for anything that takes
longer than a round trip.
"""

from flask import Blueprint, jsonify, request

from quietude.core import database, tts_engine, voice_catalog

bp = Blueprint("voices", __name__, url_prefix="/api/voices")


def _decorate(voices, installed, in_flight):
    """Adds what the page needs on top of the catalog's own fields, and
    drops the private download metadata - the browser has no use for a
    repo path or an md5, and sending them would only invite the frontend
    to start building URLs of its own."""
    rows = []
    for v in voices:
        row = {k: val for k, val in v.items() if not k.startswith("_")}
        row["installed"] = v["key"] in installed
        row["job"] = in_flight.get(v["key"])
        rows.append(row)
    return rows


@bp.get("")
def list_voices():
    """The catalog, with what's installed and what's downloading.

    `refresh=1` forces a re-fetch; without it a list less than a day old
    is served from disk so opening the page twice doesn't mean two
    requests to the internet.
    """
    refresh = request.args.get("refresh") in ("1", "true", "yes")
    voices, source, error = voice_catalog.catalog(refresh=refresh)

    installed = voice_catalog.installed_keys()
    in_flight = {
        j["key"]: j for j in voice_catalog.jobs()
        if j["state"] in ("queued", "downloading")
    }

    language = (request.args.get("language") or "").strip()
    query = (request.args.get("q") or "").strip().lower()
    rows = _decorate(voices, installed, in_flight)

    if language:
        rows = [r for r in rows if r["language_code"] == language]
    if query:
        rows = [
            r for r in rows
            if query in r["key"].lower()
            or query in r["name"].lower()
            or query in r["language"].lower()
            or query in r["country"].lower()
        ]

    # Every language in the full catalog, not in the filtered rows - the
    # language picker must not lose the option you'd need to get back.
    languages = sorted(
        {(v["language_code"], v["language"], v["country"]) for v in voices},
        key=lambda t: (t[1], t[2]),
    )

    # Installed voices that aren't in the catalog at all: someone's own
    # model dropped into the directory, or a voice since withdrawn.
    # Listing them is what makes "remove" work for them too.
    known = {v["key"] for v in voices}
    extra = [
        {"key": key, "name": key, "quality": "", "quality_note": "",
         "language": "Not in the catalog", "country": "", "language_code": "",
         "num_speakers": 1, "size_bytes": 0, "installed": True, "job": None}
        for key in sorted(installed - known)
    ]

    return jsonify({
        "ok": True,
        "voices": extra + rows,
        "languages": [
            {"code": code, "language": name, "country": country}
            for code, name, country in languages
        ],
        "installed": sorted(installed),
        "total": len(voices),
        "showing": len(rows) + len(extra),
        "source": source,
        "error": error,
    })


@bp.post("/install")
def install_voice():
    payload = request.get_json(silent=True) or {}
    job, error = voice_catalog.start_install((payload.get("key") or "").strip())
    if error:
        return jsonify({"ok": False, "error": error}), 400
    return jsonify({"ok": True, "job": job})


@bp.get("/jobs")
def list_jobs():
    return jsonify({"ok": True, "jobs": voice_catalog.jobs()})


@bp.get("/jobs/<job_id>")
def get_job(job_id):
    job = voice_catalog.job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "No such download."}), 404
    return jsonify({"ok": True, "job": job})


@bp.post("/jobs/<job_id>/cancel")
def cancel_job(job_id):
    if not voice_catalog.cancel(job_id):
        return jsonify({"ok": False, "error": "That download already finished."}), 400
    return jsonify({"ok": True})


@bp.delete("/<key>")
def remove_voice(key):
    """Removes an installed voice.

    If it was the saved voice, the saved setting is moved to another
    installed voice rather than left pointing at a file that no longer
    exists - which would otherwise surface as a failed phrase at some
    arbitrary later moment, with nothing connecting it to this action.
    """
    ok, error = voice_catalog.remove(key)
    if not ok:
        return jsonify({"ok": False, "error": error}), 400

    reassigned = None
    user = database.get_primary_user()
    if user:
        saved = database.get_tts_settings(user) or {}
        if saved.get("voice_id") == voice_catalog.voice_id_for(key):
            remaining = tts_engine.get_voices()
            if remaining:
                reassigned = remaining[0]["id"]
                database.set_tts_settings(
                    user["id"],
                    saved.get("rate", tts_engine.RATE_DEFAULT),
                    saved.get("volume", 1.0),
                    reassigned,
                )

    return jsonify({"ok": True, "reassigned_to": reassigned})


@bp.post("/preload")
def preload_voice():
    """Warms a voice without speaking, so the first phrase in it is as
    quick as any other. Used when a voice is selected in the settings
    page, before anything has been previewed."""
    payload = request.get_json(silent=True) or {}
    return jsonify({"ok": tts_engine.preload((payload.get("voice_id") or "").strip())})
