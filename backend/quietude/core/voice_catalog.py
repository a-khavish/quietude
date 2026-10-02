# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
voice_catalog.py
Browsing and installing piper voices from inside Quietude.

Until now the only voice Quietude had was whichever one install.sh
downloaded - en_US-amy-medium by default, and changing it meant setting
an environment variable and re-running the installer. That is a strange
thing to ask of someone who just wants a different accent, and it is the
only part of Quietude that needed a terminal to change.

So the catalog moves into the app. There are around a thousand piper
voices across sixty-odd languages; this module lists them, downloads the
two files a voice is made of, and verifies them.

Where the list comes from
-------------------------
rhasspy/piper-voices publishes a voices.json describing every voice -
key, language, quality, and for each file a size and an md5. That is the
authoritative list, so it is fetched rather than restated here: a
hardcoded list would be wrong the first time a voice was added, and it
would have to carry sizes and checksums that nobody would ever update.

It is cached on disk for a day. Quietude is an offline assistant and this is
the one feature that cannot be - but it should be the only thing that
ever reaches the network, it should do so only when you open the voice
library, and having opened it once you should be able to open it again
on a train.

What is downloaded
------------------
A piper voice is a <key>.onnx and a <key>.onnx.json, between about 20MB
(low) and 110MB (high). Both are needed; one without the other is not a
voice, which is why _piper_voices in tts_engine requires the pair.

Downloads are verified and atomic. Each file goes to a temporary name in
the destination directory, is hashed while it streams, is checked
against the size and md5 from the catalog, and only then is moved into
place with os.replace. A download that is interrupted, truncated or
corrupted therefore leaves nothing behind that tts_engine could pick up
and try to load - the failure mode this avoids is a half-written .onnx
that lists as an installed voice and crashes the worker on first use.

Trusting the catalog
--------------------
The catalog is data fetched over the network, so none of it is used to
decide where a file lands. The destination filename is built from the
voice key, and the key is checked against a strict pattern before
anything else happens. The paths in the catalog are used only to build
the URL to fetch. A catalog entry cannot name a file outside the voice
directory even if the catalog said to.
"""

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid

from quietude import config

# The repo the voices live in. Both of these are the same host, and it
# is the only host Quietude ever contacts for a voice.
#
# Overridable because some networks cannot reach HuggingFace and keep a
# mirror of it, and because an installation that is genuinely air-gapped
# can point these at a copy on the LAN and have the library work
# normally rather than not at all.
CATALOG_URL = (
    os.environ.get("QUIETUDE_VOICE_CATALOG_URL")
    or "https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json"
)
DOWNLOAD_BASE = (
    os.environ.get("QUIETUDE_VOICE_BASE_URL")
    or "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
)

CATALOG_CACHE = config.CACHE_DIR / "piper-voices.json"
CATALOG_TTL_SECONDS = 24 * 60 * 60

NETWORK_TIMEOUT = 30
CHUNK = 256 * 1024

# A voice key names a file on disk, so it is checked rather than
# trusted. Real keys look like en_US-amy-medium or pt_BR-faber-medium.
VALID_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")

# Rough guidance shown beside each quality, because "medium" means
# nothing without knowing what it costs.
QUALITY_NOTE = {
    "x_low": "smallest and fastest, noticeably synthetic",
    "low": "small and fast, a little rough",
    "medium": "the usual choice - good quality, moderate size",
    "high": "best quality, largest and slowest to synthesize",
}


# ============================================================
# The catalog
# ============================================================

def _fetch_catalog_json():
    req = urllib.request.Request(
        CATALOG_URL,
        headers={"User-Agent": "quietude-voice-library"},
    )
    with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _read_cache():
    try:
        raw = json.loads(CATALOG_CACHE.read_text())
        return raw["voices"], float(raw["fetched_at"])
    except Exception:
        return None, 0.0


def _write_cache(voices):
    try:
        CATALOG_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CATALOG_CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"fetched_at": time.time(), "voices": voices}))
        os.replace(tmp, CATALOG_CACHE)
    except OSError:
        pass  # a cache that can't be written is slower, not broken


def _normalize(key, entry):
    """One catalog entry, reduced to what the interface needs.

    The two files are picked out by extension rather than by position,
    because the catalog also lists a MODEL_CARD that is of no use here.
    """
    model_path = config_path = None
    model_meta = config_meta = {}
    size = 0
    files = entry.get("files") or {}
    for path, meta in files.items():
        if path.endswith(".onnx"):
            model_path, model_meta = path, meta
            size += int(meta.get("size_bytes") or 0)
        elif path.endswith(".onnx.json"):
            config_path, config_meta = path, meta
            size += int(meta.get("size_bytes") or 0)

    if not model_path or not config_path:
        return None

    language = entry.get("language") or {}
    return {
        "key": key,
        "name": entry.get("name") or key,
        "quality": entry.get("quality") or "",
        "quality_note": QUALITY_NOTE.get(entry.get("quality") or "", ""),
        "num_speakers": entry.get("num_speakers") or 1,
        "language_code": language.get("code") or "",
        "language": language.get("name_english") or language.get("code") or "",
        "country": language.get("country_english") or "",
        "size_bytes": size,
        # Kept for the download, not shown.
        "_model": {"path": model_path,
                   "size": int(model_meta.get("size_bytes") or 0),
                   "md5": model_meta.get("md5_digest") or ""},
        "_config": {"path": config_path,
                    "size": int(config_meta.get("size_bytes") or 0),
                    "md5": config_meta.get("md5_digest") or ""},
    }


def catalog(refresh=False):
    """Returns (voices, source, error).

    source is "network", "cache" or "none". An error alongside a cached
    list means the refresh failed but there is still something to show -
    which the interface says out loud rather than quietly presenting a
    day-old list as current.
    """
    cached, fetched_at = _read_cache()
    fresh = cached is not None and (time.time() - fetched_at) < CATALOG_TTL_SECONDS

    if fresh and not refresh:
        return cached, "cache", None

    try:
        raw = _fetch_catalog_json()
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
        reason = _network_reason(e)
        if cached is not None:
            return cached, "cache", reason
        return [], "none", reason

    voices = []
    for key, entry in raw.items():
        if not VALID_KEY.match(key):
            continue
        norm = _normalize(key, entry)
        if norm:
            voices.append(norm)
    voices.sort(key=lambda v: (v["language"], v["country"], v["name"], v["quality"]))
    _write_cache(voices)
    return voices, "network", None


def _network_reason(exc):
    """A sentence someone can act on, rather than a traceback.

    Quietude is deliberately offline, so a user hitting this is quite likely
    on a machine with no route out at all, and "URLError" would leave
    them wondering whether Quietude was broken."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"The voice catalog returned HTTP {exc.code}."
    if isinstance(exc, urllib.error.URLError):
        return (f"Couldn't reach the voice catalog ({exc.reason}). "
                "Everything else in Quietude works offline; this is the one "
                "thing that needs the internet.")
    return f"Couldn't read the voice catalog: {exc}"


# ============================================================
# What's on disk
# ============================================================

def installed_keys():
    """Keys of voices fully present on disk - both files, or neither."""
    directory = config.PIPER_VOICE_DIR
    if not directory.is_dir():
        return set()
    keys = set()
    for onnx in directory.glob("*.onnx"):
        if onnx.with_suffix(".onnx.json").is_file():
            keys.add(onnx.stem)
    return keys


def voice_id_for(key):
    """The id tts_engine and the settings page use for an installed voice."""
    return f"piper:{key}.onnx"


def remove(key):
    """Deletes an installed voice. Returns (ok, error)."""
    if not VALID_KEY.match(key or ""):
        return False, "That isn't a valid voice name."
    directory = config.PIPER_VOICE_DIR
    removed = 0
    for suffix in (".onnx", ".onnx.json"):
        path = directory / f"{key}{suffix}"
        if path.is_file():
            try:
                path.unlink()
                removed += 1
            except OSError as e:
                return False, f"Couldn't remove {path.name}: {e}"
    if not removed:
        return False, "That voice isn't installed."
    return True, None


# ============================================================
# Downloading
# ============================================================

_jobs = {}
_jobs_lock = threading.Lock()


def _job_set(job_id, **fields):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.update(fields)


def job(job_id):
    with _jobs_lock:
        found = _jobs.get(job_id)
        return dict(found) if found else None


def jobs():
    with _jobs_lock:
        return [dict(j) for j in _jobs.values()]


def cancel(job_id):
    with _jobs_lock:
        found = _jobs.get(job_id)
        if not found or found["state"] not in ("queued", "downloading"):
            return False
        found["cancelled"] = True
        return True


def _download_file(url, dest, expect_size, expect_md5, on_bytes, is_cancelled):
    """Streams one file to `dest`, verified, atomically.

    Written beside the destination rather than in /tmp, so the final move
    is a rename within one filesystem and therefore atomic. A voice model
    is 60MB; a cross-device copy at the last moment would be both slow
    and interruptible at exactly the wrong point."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{dest.name}.{uuid.uuid4().hex[:8]}.part"
    digest = hashlib.md5()
    received = 0

    req = urllib.request.Request(url, headers={"User-Agent": "quietude-voice-library"})
    try:
        with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT) as r, \
                open(tmp, "wb") as out:
            while True:
                if is_cancelled():
                    raise InterruptedError("cancelled")
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                on_bytes(len(chunk))

        if expect_size and received != expect_size:
            raise OSError(
                f"{dest.name} is {received} bytes, the catalog says {expect_size}"
            )
        if expect_md5 and digest.hexdigest() != expect_md5:
            raise OSError(f"{dest.name} failed its checksum - the download was corrupted")

        os.replace(tmp, dest)
    finally:
        # Whatever went wrong, no partial file is left where tts_engine
        # could list it as a voice and try to load it.
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _run_install(job_id, voice):
    key = voice["key"]
    directory = config.PIPER_VOICE_DIR
    total = voice["_model"]["size"] + voice["_config"]["size"]
    received = 0

    def is_cancelled():
        current = job(job_id)
        return bool(current and current.get("cancelled"))

    def on_bytes(n):
        nonlocal received
        received += n
        _job_set(job_id, received=received)

    _job_set(job_id, state="downloading", total=total, received=0)

    try:
        # The config file first. It is 5KB against the model's 60MB, so
        # a failure that is going to happen for a boring reason - no
        # route, a proxy, a 404 - happens in a second rather than after
        # a long download.
        _download_file(DOWNLOAD_BASE + voice["_config"]["path"],
                       directory / f"{key}.onnx.json",
                       voice["_config"]["size"], voice["_config"]["md5"],
                       on_bytes, is_cancelled)
        _download_file(DOWNLOAD_BASE + voice["_model"]["path"],
                       directory / f"{key}.onnx",
                       voice["_model"]["size"], voice["_model"]["md5"],
                       on_bytes, is_cancelled)
    except InterruptedError:
        # The .json may already be in place; without its model it is not
        # a voice, so clear it rather than leave a stub behind.
        remove(key)
        _job_set(job_id, state="cancelled", finished_at=time.time())
        return
    except Exception as e:
        remove(key)
        _job_set(job_id, state="failed", error=str(e), finished_at=time.time())
        return

    _job_set(job_id, state="done", received=total, finished_at=time.time())

    # Load it now, so the first phrase in a voice someone just chose to
    # download isn't the one that pays for loading it.
    try:
        from quietude.core import tts_engine
        tts_engine.preload(voice_id_for(key))
    except Exception:
        pass


def start_install(key):
    """Begins a download in the background. Returns (job, error)."""
    if not VALID_KEY.match(key or ""):
        return None, "That isn't a valid voice name."

    if key in installed_keys():
        return None, "That voice is already installed."

    with _jobs_lock:
        for existing in _jobs.values():
            if existing["key"] == key and existing["state"] in ("queued", "downloading"):
                return dict(existing), None

    voices, _, error = catalog()
    found = next((v for v in voices if v["key"] == key), None)
    if not found:
        return None, error or "No voice by that name is in the catalog."

    job_id = uuid.uuid4().hex[:12]
    record = {
        "id": job_id, "key": key, "name": found["name"],
        "quality": found["quality"], "language": found["language"],
        "state": "queued", "received": 0,
        "total": found["_model"]["size"] + found["_config"]["size"],
        "error": None, "cancelled": False,
        "started_at": time.time(), "finished_at": None,
    }
    with _jobs_lock:
        _jobs[job_id] = record
        # Finished jobs are kept so the page can report an outcome after
        # a reload, but not forever.
        if len(_jobs) > 30:
            done = sorted(
                (j for j in _jobs.values() if j["state"] in ("done", "failed", "cancelled")),
                key=lambda j: j.get("finished_at") or 0,
            )
            for stale in done[:len(_jobs) - 30]:
                _jobs.pop(stale["id"], None)

    threading.Thread(target=_run_install, args=(job_id, found),
                     daemon=True, name=f"quietude-voice-{key}").start()
    return dict(record), None
