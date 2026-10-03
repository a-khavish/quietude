# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
models.py
The speech model chooser: browse, install, select, remove.

The voice library's shape, one layer down. Same reasoning for the job
polling: a transcription model is between 78MB and 1.5GB over a link
Quietude knows nothing about, and a request that blocks for four minutes
is a request that times out somewhere between here and the browser. So
POST /install starts a background download and returns a job the page
polls.

One thing differs from /api/voices, and it is worth stating plainly.
Choosing a voice takes effect on the next sentence, because tts_engine
loads a voice per phrase. Choosing a speech *model* does not: both
models are loaded once, at launch, inside the speech worker process, and
there is no way to swap them in a running worker that wouldn't mean
dropping audio mid-utterance and reloading 1.5GB while someone is
talking. So select and remove report `restart_required`, and the page
says so rather than letting someone wonder why the new model doesn't
seem to be doing anything.
"""

from flask import Blueprint, jsonify, request

from quietude.core import model_catalog, speech_engine

bp = Blueprint("models", __name__, url_prefix="/api/models")


def _public(row):
    """One catalog row as the browser gets it.

    Private fields are dropped the way /api/voices drops them: a sha256
    and a file list are the download's business, and sending them would
    only invite the frontend to start building URLs of its own."""
    return {k: v for k, v in row.items() if not k.startswith("_")}


def _role_state():
    """What each role is actually going to load, for the page's header.

    `legacy` is the interesting one: it means this machine has the model
    install.sh downloaded and nothing from the chooser, so there is a
    model in use that has no catalog entry to highlight. Saying "no
    model selected" there would be alarming and wrong.
    """
    states = {}
    for role in model_catalog.ROLES:
        directory = model_catalog.active_model_dir(role)
        states[role] = {
            "role": role,
            "label": model_catalog.ROLE_LABEL[role],
            "active_key": model_catalog.active_key(role),
            "source": model_catalog.active_source(role),
            "active_dir": str(directory) if directory else None,
            "installed": model_catalog.installed_for(role),
        }
    return states


@bp.get("")
def list_models():
    """The catalog, with what's installed, what's active and what's
    downloading.

    No `refresh`, unlike /api/voices: this catalog is curated in
    model_catalog itself rather than fetched, so there is nothing to
    refresh and nothing that can be stale. See that module's header for
    why it isn't fetched.
    """
    rows = [_public(r) for r in model_catalog.catalog()]

    in_flight = {
        j["key"]: j for j in model_catalog.jobs()
        if j["state"] in ("queued", "downloading")
    }
    for row in rows:
        row["job"] = in_flight.get(row["key"])

    role = (request.args.get("role") or "").strip()
    query = (request.args.get("q") or "").strip().lower()
    total = len(rows)

    if role in model_catalog.ROLES:
        rows = [r for r in rows if r["role"] == role]
    if query:
        rows = [
            r for r in rows
            if query in r["key"].lower()
            or query in r["name"].lower()
            or query in r["language"].lower()
            or query in r["description"].lower()
        ]

    # Whether the engine could run at all, so the page can say "nothing
    # is installed yet" in the same words the boot screen uses rather
    # than inventing its own.
    vosk_ok, whisper_ok = speech_engine.models_present()

    return jsonify({
        "ok": True,
        "models": rows,
        "roles": _role_state(),
        "role_order": list(model_catalog.ROLES),
        "installed": sorted(model_catalog.installed_keys()),
        "total": total,
        "showing": len(rows),
        "engine": {"wake_present": vosk_ok, "transcribe_present": whisper_ok},
    })


@bp.post("/install")
def install_model():
    payload = request.get_json(silent=True) or {}
    job, error = model_catalog.start_install((payload.get("key") or "").strip())
    if error:
        return jsonify({"ok": False, "error": error}), 400
    return jsonify({"ok": True, "job": job})


@bp.get("/jobs")
def list_jobs():
    return jsonify({"ok": True, "jobs": model_catalog.jobs()})


@bp.get("/jobs/<job_id>")
def get_job(job_id):
    job = model_catalog.job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "No such download."}), 404
    return jsonify({"ok": True, "job": job})


@bp.post("/jobs/<job_id>/cancel")
def cancel_job(job_id):
    if not model_catalog.cancel(job_id):
        return jsonify({"ok": False, "error": "That download already finished."}), 400
    return jsonify({"ok": True})



def _restart_engine():
    """Reloads the speech worker on whatever models are now selected.

    The assistant's name and the sensitivity are read back out and
    handed over, because a restart that forgot them would silently
    leave the wake word unarmed - which is the failure that looks
    exactly like the model being broken.
    """
    try:
        from quietude.core import database
        status = speech_engine.status()
        name = status.get("wake_name") or ""
        if not name:
            name = (database.get_assistant() or {}).get("name") or ""
        sensitivity = status.get("sensitivity", speech_engine.SENSITIVITY_DEFAULT)
        speech_engine.restart(wake_name=name, sensitivity=sensitivity)
        return True
    except Exception:
        return False


@bp.post("/select")
def select_model():
    """Makes an installed model the one its role will load.

    Takes effect at the next launch, for the reason in this module's
    header, so the response says so instead of leaving the page to guess.
    `restart_required` is False when the choice happens to match what is
    already loaded - re-selecting the active model is a no-op worth
    reporting as one.
    """
    payload = request.get_json(silent=True) or {}
    role = (payload.get("role") or "").strip()
    key = (payload.get("key") or "").strip()

    already = model_catalog.active_key(role) == key and model_catalog.active_source(role)

    ok, error = model_catalog.select(role, key)
    if not ok:
        return jsonify({"ok": False, "error": error}), 400

    # The worker is replaced rather than reconfigured. Loading a
    # different model means reading up to 1.5GB from disk, which cannot
    # be done to a running worker without dropping audio mid-utterance -
    # so the engine is restarted. It takes a few seconds and the
    # interface says so, but the alternative was telling someone who had
    # just chosen a model to go and restart the application.
    restarted = False
    if not already:
        restarted = _restart_engine()

    return jsonify({
        "ok": True,
        "roles": _role_state(),
        "restart_required": not already,
        "restarted": restarted,
    })


@bp.delete("/<path:key>")
def remove_model(key):
    """Removes an installed model.

    `path:` rather than the default converter because a transcription key
    is a HuggingFace owner/name pair. The frontend sends it
    percent-encoded, but the server decodes %2F back into a slash before
    routing, so the default converter - which stops at a slash - would
    never match and every whisper model would be un-removable.

    model_catalog refuses to remove the last model a role has and
    re-points the selection when it removes the active one, so what
    cannot happen here is the speech worker being left pointing at a
    directory that no longer exists. What it *can* leave is a worker
    already running with the removed model loaded in memory, which keeps
    working for this session - vosk and faster-whisper both read their
    weights at load time - and loads the replacement at the next launch.
    Hence restart_required.
    """
    ok, error, reassigned = model_catalog.remove(key)
    if not ok:
        return jsonify({"ok": False, "error": error}), 400
    return jsonify({
        "ok": True,
        "reassigned_to": reassigned,
        "roles": _role_state(),
        "restart_required": True,
    })
