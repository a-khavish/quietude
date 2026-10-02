# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
model_catalog.py
Browsing and installing the two speech models from inside Quietude.

The voice library solved this for piper voices; this is the same problem
one layer down. Until now the only wake-word model Quietude had was the
40MB vosk-model-small-en-us-0.15 that install.sh fetches, and the only
transcription model was Systran/faster-whisper-base.en - and changing
either meant an environment variable and a re-run of the installer.

That default pair is a reasonable one, but it is a compromise nobody got
to make. The small vosk model mishears names; the big one hears them and
costs 1.8GB. base.en transcribes a sentence in half a second on a laptop
and takes four on a Pi. Neither of those is the right answer for
everyone, so this module makes the choice available in the app.

Why the list is hardcoded here
------------------------------
voice_catalog deliberately does *not* restate the piper catalog: rhasspy
publishes a voices.json with a size and an md5 for every file, so the
authoritative list is fetched and a hardcoded copy would be wrong the
first time a voice was added.

There is no equivalent here. The vosk models are rows in a hand-written
HTML table on alphacephei.com with sizes like "1.8G"; the faster-whisper
models are HuggingFace repos with no index tying them together. Scraping
that table to populate a dropdown would be a liability - it would break
on a page redesign, with no way to tell a redesign from "there are no
models". So the curated list below is the catalog, with sizes and
descriptions checked by hand, and `catalog()` never touches the network.

The practical cost is that a model added upstream is not offered until
this file is edited. For a list of six wake models and five transcription
models that someone picks from once, that is the right trade.

Where models go
---------------
The app has always had exactly one directory per role -
config.VOSK_MODEL_DIR and config.WHISPER_MODEL_DIR - which is fine for
exactly one model and not at all fine for a chooser. So an installed
model now lives in its own directory under its role:

    MODELS_DIR/wake/<key>/
    MODELS_DIR/transcribe/<key>/

and `active_model_dir(role)` says which one the engine should load.

Those are deliberately *not* subdirectories of the legacy paths. Putting
them at MODELS_DIR/vosk/<key>/ would mean speech_engine's
models_present() - which only asks whether VOSK_MODEL_DIR has anything
in it - started answering "yes" the moment a subdirectory appeared, and
the worker would then hand that directory to vosk.Model and die on it.
Role-named siblings leave both legacy directories exactly as they were,
so nothing downstream changes behaviour until it is changed on purpose.

Backward compatibility: a machine that has run install.sh has a model in
the legacy flat directory and nothing in the new layout. In that state
`active_model_dir` returns the legacy directory, so an existing install
keeps working untouched and nobody has to re-download 150MB to get back
to where they were.

Trusting the catalog
--------------------
Same rule as voice_catalog: nothing fetched over the network decides
where a file lands. A key is matched against a strict per-role pattern,
the destination is built from the key alone, and the finished path is
checked to be a direct child of its role directory before anything is
written. A transcription key contains a slash (Systran/faster-whisper-
base.en), which is exactly the shape that would walk out of the
directory if it were pasted into a path, so it is encoded rather than
joined.

Zip archives get the same treatment. A vosk model arrives as a zip, and
a zip member can name ../../anywhere; every member is checked before
extraction rather than trusted to be well-behaved.
"""

import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile

from quietude import config

# The two hosts Quietude will contact for a model, and nothing else.
#
# Overridable for the same reasons the voice catalog's URLs are: a
# network that mirrors these, and an air-gapped install that would rather
# serve them off the LAN than not have the feature at all. The test
# suite uses the same door.
VOSK_BASE_URL = (
    os.environ.get("QUIETUDE_VOSK_BASE_URL")
    or "https://alphacephei.com/vosk/models/"
)
# Only used by catalog entries that list their files explicitly - see
# _install_transcribe. The curated entries don't, so by default this is
# unused and the HuggingFace path below is what runs.
MODEL_FILE_BASE_URL = (
    os.environ.get("QUIETUDE_MODEL_FILE_BASE_URL")
    or "https://huggingface.co/"
)
# A JSON file that *replaces* the curated list. Replaces rather than
# extends: a mirror holds what it holds, and a list that silently mixed
# in entries pointing at hosts the mirror can't reach would offer
# downloads that cannot work.
CATALOG_FILE = os.environ.get("QUIETUDE_MODEL_CATALOG_FILE") or ""

SELECTION_FILE_NAME = "speech-models.json"

NETWORK_TIMEOUT = 30
CHUNK = 256 * 1024

ROLES = ("wake", "transcribe")

ROLE_LABEL = {
    "wake": "Wake word and live captions",
    "transcribe": "Transcription",
}

# A key names a directory, so it is checked rather than trusted.
#
# Two patterns because the two roles name things differently: a vosk
# model is a single token with dots in it, a faster-whisper model is a
# HuggingFace owner/name pair. Dots are allowed because every real name
# has them (0.15, tiny.en); ".." is refused separately below, because a
# pattern that allows dots allows that too.
VALID_KEY = {
    "wake": re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$"),
    "transcribe": re.compile(
        r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}/[A-Za-z0-9][A-Za-z0-9._-]{0,60}$"
    ),
}

# The slash in a transcription key becomes this on disk. A real
# HuggingFace name could in principle contain it, which would make two
# keys share a directory - no such name exists among the entries below,
# and the alternative (hashing the key) would make the models directory
# unreadable to a human looking for what is installed.
SLASH = "--"


# ============================================================
# The catalog
# ============================================================
#
# Sizes: the wake entries are the figures alphacephei.com's own table
# lists, which are rounded to two digits - hence size_approx, and hence
# no exact-size check on the download. The transcribe entries are the
# sum of the files the installer actually fetches, read from
# HuggingFace's API, so those are exact to the byte.
#
# Descriptions say what the trade costs, not what the model is. "small"
# and "medium" mean nothing to someone choosing between them.

CURATED = [
    # ---- wake word / live partials (vosk) ----
    {
        "key": "vosk-model-small-en-us-0.15",
        "role": "wake",
        "name": "English (US) - small",
        "language": "English (US)",
        "size_bytes": 40 * 1000 * 1000,
        "size_approx": True,
        "default": True,
        "description": "The default. Fast enough to run on every audio chunk "
                       "forever on a Raspberry Pi, and only has to recognise "
                       "two fixed phrases - but it mishears unusual names.",
    },
    {
        "key": "vosk-model-en-us-0.22",
        "role": "wake",
        "name": "English (US) - full",
        "language": "English (US)",
        "size_bytes": 1800 * 1000 * 1000,
        "size_approx": True,
        "description": "Far more accurate, which mostly shows up in the live "
                       "captions and in hearing an unusual wake name. Forty "
                       "times the size and needs about 2GB of RAM to sit in "
                       "while it listens.",
    },
    {
        "key": "vosk-model-small-en-in-0.4",
        "role": "wake",
        "name": "English (Indian) - small",
        "language": "English (India)",
        "size_bytes": 36 * 1000 * 1000,
        "size_approx": True,
        "description": "Same size and speed as the US small model, trained on "
                       "Indian English. Worth the swap if the US one keeps "
                       "missing your wake word.",
    },
    {
        "key": "vosk-model-small-fr-0.22",
        "role": "wake",
        "name": "French - small",
        "language": "French",
        "size_bytes": 41 * 1000 * 1000,
        "size_approx": True,
        "description": "French wake word and captions, at the small model's "
                       "speed. The wake phrase has to be something it can "
                       "hear in French.",
    },
    {
        "key": "vosk-model-small-de-0.15",
        "role": "wake",
        "name": "German - small",
        "language": "German",
        "size_bytes": 45 * 1000 * 1000,
        "size_approx": True,
        "description": "German wake word and captions, at the small model's "
                       "speed.",
    },
    {
        "key": "vosk-model-small-es-0.42",
        "role": "wake",
        "name": "Spanish - small",
        "language": "Spanish",
        "size_bytes": 39 * 1000 * 1000,
        "size_approx": True,
        "description": "Spanish wake word and captions, at the small model's "
                       "speed.",
    },

    # ---- transcription (faster-whisper / CTranslate2) ----
    {
        "key": "Systran/faster-whisper-tiny.en",
        "role": "transcribe",
        "name": "Whisper tiny (English only)",
        "language": "English",
        "size_bytes": 78_090_594,
        "description": "The fastest, and the only one worth trying on very "
                       "old hardware. Drops words and invents short ones; "
                       "fine for \"what time is it\", not for a sentence that "
                       "matters.",
    },
    {
        "key": "Systran/faster-whisper-base.en",
        "role": "transcribe",
        "name": "Whisper base (English only)",
        "language": "English",
        "size_bytes": 147_769_510,
        "default": True,
        "description": "The default, and the balance point: about half a "
                       "second per utterance on a laptop, accurate enough "
                       "that misheard commands are rare.",
    },
    {
        "key": "Systran/faster-whisper-small.en",
        "role": "transcribe",
        "name": "Whisper small (English only)",
        "language": "English",
        "size_bytes": 486_098_798,
        "description": "Noticeably better on proper nouns, technical words "
                       "and anything said quickly. Roughly three times base's "
                       "time per utterance.",
    },
    {
        "key": "Systran/faster-whisper-medium.en",
        "role": "transcribe",
        "name": "Whisper medium (English only)",
        "language": "English",
        "size_bytes": 1_530_457_748,
        "description": "The most accurate English option here. Slow enough on "
                       "a CPU that you will wait for it after speaking - "
                       "worth it on a desktop, not on a Pi.",
    },
    {
        "key": "Systran/faster-whisper-small",
        "role": "transcribe",
        "name": "Whisper small (multilingual)",
        "language": "Multilingual",
        "size_bytes": 486_212_372,
        "description": "The only non-English-only option here: handles about "
                       "a hundred languages at small's speed, and is a little "
                       "worse at English than small.en in exchange.",
    },
]


def _load_curated():
    """The curated list, or the contents of QUIETUDE_MODEL_CATALOG_FILE.

    A broken override is a hard failure rather than a silent fall back to
    the built-in list: someone who pointed this at a mirror wants to know
    the mirror is wrong, not to be quietly handed download URLs their
    network can't reach."""
    if not CATALOG_FILE:
        return CURATED
    with open(CATALOG_FILE, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    entries = loaded["models"] if isinstance(loaded, dict) else loaded
    for entry in entries:
        if entry.get("role") not in ROLES:
            raise ValueError(f"catalog entry {entry.get('key')!r} has no valid role")
    return entries


def catalog():
    """Every model Quietude knows how to install, in role order.

    Each entry carries `installed` and `active` so a caller doesn't have
    to cross-reference the disk itself. Entries whose key doesn't pass
    validation are dropped rather than raising - that only happens with
    an overridden catalog, and one bad row shouldn't cost the other
    twenty.
    """
    installed = installed_keys()
    active = {role: active_key(role) for role in ROLES}

    rows = []
    for entry in _load_curated():
        role = entry.get("role")
        key = entry.get("key") or ""
        if role not in ROLES or not _key_ok(role, key):
            continue
        row = dict(entry)
        # Anything only the download needs is renamed to the underscore
        # convention the API layer strips on. An overridden catalog is
        # written by hand, so it carries the readable names; renaming
        # here means the rule that private fields start with an
        # underscore holds for every row, not just curated ones.
        for public, private in (("sha256", "_sha256"), ("files", "_files")):
            if public in row:
                row[private] = row.pop(public)
        row.setdefault("size_approx", False)
        row.setdefault("default", False)
        row.setdefault("language", "")
        row.setdefault("description", "")
        row.setdefault("name", key)
        row["installed"] = key in installed
        row["active"] = active.get(role) == key
        rows.append(row)

    rows.sort(key=lambda r: (ROLES.index(r["role"]), r["size_bytes"], r["key"]))
    return rows


def _entry(key):
    for row in catalog():
        if row["key"] == key:
            return row
    return None


# ============================================================
# Where things live
# ============================================================

def _key_ok(role, key):
    """Shape check, before the key is used to build a path.

    `..` is refused explicitly: the patterns allow dots because every
    real name has them, and that means they allow a key made entirely of
    them."""
    pattern = VALID_KEY.get(role)
    if not pattern or not key or not pattern.match(key):
        return False
    return ".." not in key


def _any_key_ok(key):
    """Shape check when the role isn't known yet - which is every call
    that arrives from the API, since the key is all the browser sends.

    Checked before the catalog is consulted, so a key shaped like a path
    is answered with "that isn't a valid model name" rather than with
    "there's no such model", which would be a confusing thing to say
    about ../../etc/passwd and would also mean the only thing standing
    between that string and a path join was a dictionary lookup."""
    return any(_key_ok(role, key) for role in ROLES)


def _role_root(role):
    return config.MODELS_DIR / role


def _legacy_dir(role):
    """The single directory install.sh writes into, which is all there
    was before this module existed."""
    return config.VOSK_MODEL_DIR if role == "wake" else config.WHISPER_MODEL_DIR


def model_dir(role, key):
    """Where `key` is or would be installed, or None if the key is not
    one we'd accept.

    The resolved path is checked to be a direct child of the role
    directory. The slash encoding already rules out the obvious escape,
    but this is the check that holds regardless of what the encoding
    does, and it costs one stat."""
    if role not in ROLES or not _key_ok(role, key):
        return None
    root = _role_root(role)
    candidate = root / key.replace("/", SLASH)
    try:
        if candidate.resolve().parent != root.resolve(strict=False):
            return None
    except OSError:
        return None
    return candidate


def _key_from_dirname(role, name):
    return name.replace(SLASH, "/") if role == "transcribe" else name


def _is_complete(role, directory):
    """Whether a directory holds a loadable model rather than the
    wreckage of an interrupted install.

    Every vosk model, small or large, has am/ and conf/; `am` alone is
    checked because that is the acoustic model and nothing loads without
    it. For whisper it is model.bin, which is both what install.sh
    checks and what speech_engine's models_present() checks - three
    places agreeing on one marker.
    """
    if not directory or not directory.is_dir():
        return False
    if role == "wake":
        return (directory / "am").is_dir() or (directory / "conf").is_dir()
    return (directory / "model.bin").is_file()


def installed_keys():
    """Keys of models fully present under the per-model layout.

    Deliberately excludes the legacy flat directories: they have no key
    to report, and reporting them as some assumed key would be a guess -
    install.sh's defaults are overridable, so a whisper directory could
    be any of five repos and there is nothing on disk that says which.
    `legacy_present()` answers that question honestly instead.
    """
    keys = set()
    for role in ROLES:
        root = _role_root(role)
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if child.name.startswith("."):
                continue  # an in-flight install's staging directory
            if _is_complete(role, child):
                keys.add(_key_from_dirname(role, child.name))
    return keys


def legacy_present(role):
    """Whether the directory install.sh writes into holds a model."""
    return _is_complete(role, _legacy_dir(role))


def installed_for(role):
    """Installed keys for one role, smallest first - so a fallback picked
    off the front of this list is the cheapest thing that works.

    Built from the raw curated entries rather than from `catalog()`,
    because `catalog()` needs `active_key` needs this, and going through
    the decorated list would be a cycle."""
    installed = installed_keys()
    entries = sorted(
        (e for e in _load_curated()
         if e.get("role") == role and e.get("key") in installed),
        key=lambda e: (e.get("size_bytes") or 0, e["key"]),
    )
    ordered = [e["key"] for e in entries]
    # Anything installed that the catalog no longer lists still belongs
    # in this list, or it could never be selected or removed.
    extra = sorted(k for k in installed - set(ordered)
                   if model_dir(role, k) and _is_complete(role, model_dir(role, k)))
    return ordered + extra


# ============================================================
# Which one is active
# ============================================================

def _selection_file():
    return config.DATA_DIR / SELECTION_FILE_NAME


def _read_selection():
    """config.load_settings' habits, for the same reason: a missing or
    corrupt file means "no choice recorded yet", which is a state the
    app already handles, not a reason to fail."""
    try:
        with open(_selection_file(), "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            return {role: loaded.get(role) for role in ROLES
                    if isinstance(loaded.get(role), str)}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _write_selection(selection):
    path = _selection_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(selection, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def active_key(role):
    """The key of the model this role will load, or None.

    None means either nothing is installed, or the thing that will load
    is the legacy directory - which has no key. `active_source` tells
    those two apart.
    """
    if role not in ROLES:
        return None
    chosen = _read_selection().get(role)
    installed = installed_for(role)
    if chosen and chosen in installed:
        return chosen
    if installed:
        # A recorded choice that is no longer installed falls through to
        # the first installed model rather than leaving the role with
        # nothing. remove() normally rewrites the selection itself, so
        # this is the path for a directory deleted behind Quietude's back.
        return installed[0]
    return None


def active_source(role):
    """Why `active_model_dir` is returning what it is: "selected",
    "only", "legacy" or None. The interface says which, because
    "Quietude is using the model the installer downloaded" and "Quietude is
    using the model you picked" need to look different."""
    if role not in ROLES:
        return None
    chosen = _read_selection().get(role)
    installed = installed_for(role)
    if chosen and chosen in installed:
        return "selected"
    if installed:
        return "only"
    if legacy_present(role):
        return "legacy"
    return None


def active_model_dir(role):
    """The directory speech_engine should load this role's model from.

    This is the whole point of the module as far as the rest of Quietude is
    concerned. Returns None when there is no model at all, which is the
    state models_present() already reports and start() already refuses
    to run in.
    """
    if role not in ROLES:
        return None
    key = active_key(role)
    if key:
        directory = model_dir(role, key)
        if _is_complete(role, directory):
            return directory
    if legacy_present(role):
        return _legacy_dir(role)
    return None


def select(role, key):
    """Makes an installed model the active one. Returns (ok, error)."""
    if role not in ROLES:
        return False, "That isn't a kind of speech model."
    if not _key_ok(role, key or ""):
        return False, "That isn't a valid model name."
    if key not in installed_for(role):
        return False, "That model isn't installed."

    selection = _read_selection()
    selection[role] = key
    try:
        _write_selection(selection)
    except OSError as e:
        return False, f"Couldn't save that choice: {e}"
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


def _download_file(url, dest, expect_sha256, expect_size, on_bytes, is_cancelled):
    """Streams one file to `dest`, verified as far as it can be.

    Three checks, in decreasing order of strength:

      sha256, when the catalog carries one. None of the curated entries
      do - neither alphacephei nor the HuggingFace file listing publishes
      a digest for these - so in practice this is for a mirrored catalog,
      which is also the one case where the files could have been
      tampered with in transit by someone other than the origin.

      Content-Length, when the server sends one. This is what actually
      catches the common failure: a connection cut at 80%, which
      otherwise lands as a perfectly readable truncated file.

      A loose sanity check against the catalog's size. The vosk figures
      are rounded to two digits so equality is impossible, but an order
      of magnitude off means the URL served something that isn't the
      model - a proxy's login page, say, which is 4KB of HTML and would
      otherwise be unzipped and reported as a broken archive.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    received = 0

    req = urllib.request.Request(url, headers={"User-Agent": "quietude-model-library"})
    with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT) as r, \
            open(dest, "wb") as out:
        declared = r.headers.get("Content-Length")
        declared = int(declared) if (declared or "").isdigit() else 0
        while True:
            if is_cancelled():
                raise InterruptedError("cancelled")
            chunk = r.read(CHUNK)
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
            received += len(chunk)
            on_bytes(len(chunk), declared)

    if declared and received != declared:
        raise OSError(
            f"{dest.name} stopped at {received} of {declared} bytes - "
            "the download was cut short"
        )
    if expect_sha256 and digest.hexdigest() != expect_sha256:
        raise OSError(
            f"{dest.name} failed its checksum - the download was corrupted "
            "or the file isn't what the catalog describes"
        )
    if expect_size and not (expect_size * 0.4 <= received <= expect_size * 2.5):
        raise OSError(
            f"{dest.name} is {received} bytes, nothing like the {expect_size} "
            "the catalog describes - that URL served something else"
        )
    return received


def _safe_zip_members(archive):
    """Every member's name, refused outright if any of them would write
    outside the directory it is extracted into.

    extractall() does sanitise paths in current Pythons, but it does so
    by silently rewriting them - an archive with a ../ member would be
    extracted to a *different* shape than it describes, and this code
    then goes looking for a single top-level directory that may no
    longer be where it was. Refusing is both safer and more honest about
    what happened."""
    names = archive.namelist()
    for name in names:
        if name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/"):
            raise OSError(f"the archive contains an unsafe path ({name!r})")
    return names


def _install_wake(entry, staging, progress):
    """A vosk model: one zip holding one top-level directory.

    The zip is written beside the final destination rather than into
    CACHE_DIR, so the extraction and the move that follows are both
    within one filesystem. The big model is 1.8GB, and a cross-device
    copy of that at the last moment would be slow and interruptible at
    precisely the worst moment.
    """
    key = entry["key"]
    zip_path = staging.parent / f"{staging.name}.zip"
    url = VOSK_BASE_URL + key + ".zip"

    progress.download(url, zip_path, entry)

    staging.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        _safe_zip_members(archive)
        archive.extractall(staging)
    zip_path.unlink(missing_ok=True)

    # The archive holds one top-level directory and the engine wants its
    # *contents*, not that directory nested inside it - the same unwrap
    # install.sh does.
    inner = [p for p in staging.iterdir() if p.is_dir()]
    if len(inner) != 1:
        raise OSError("the model archive didn't look like a vosk model")
    return inner[0]


# The files install.sh asks snapshot_download for. Restated rather than
# fetching everything in the repo, because several of these repos also
# carry an onnx or a safetensors copy that CTranslate2 will never read
# and that would double the download.
HF_PATTERNS = ["*.bin", "*.json", "*.txt", "*.model"]


def _install_transcribe(entry, staging, progress):
    """A faster-whisper model from HuggingFace.

    Two routes. If the catalog entry lists its files, they are fetched
    directly from MODEL_FILE_BASE_URL - which is what a mirrored catalog
    looks like, and needs no dependency at all.

    Otherwise it goes through huggingface_hub.snapshot_download, exactly
    as install.sh does. Not by letting faster-whisper fetch on first use:
    downloading now is what lets the engine be loaded with
    local_files_only=True, which is what makes the running app
    genuinely offline rather than offline-until-a-file-is-missing. The
    files are copied out of the hub cache rather than symlinked, because
    that cache is fair game for pruning and a dangling symlink would
    break Quietude long after this install was forgotten.
    """
    staging.mkdir(parents=True, exist_ok=True)

    files = entry.get("_files") or entry.get("files")
    if files:
        for spec in files:
            name = spec["name"]
            if "/" in name or name in ("", ".", ".."):
                raise OSError(f"the catalog named a file it shouldn't ({name!r})")
            progress.download(
                MODEL_FILE_BASE_URL + spec["url"].lstrip("/"),
                staging / name,
                {"size_bytes": spec.get("size_bytes") or 0,
                 "_sha256": spec.get("sha256") or ""},
            )
        return staging

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise OSError(
            "huggingface_hub isn't installed in this virtualenv, and it is "
            "what downloads a transcription model. It normally arrives with "
            "faster-whisper - reinstall the voice stack with "
            "pip install -r backend/requirements-voice.txt"
        ) from None

    if progress.cancelled():
        raise InterruptedError("cancelled")

    # No byte progress through here. snapshot_download draws its own
    # progress bars to a terminal nobody is looking at and offers no
    # callback worth relying on across versions, so the job is marked
    # indeterminate and the interface shows that rather than a bar
    # frozen at zero pretending to be a bar.
    progress.indeterminate()
    try:
        cached = snapshot_download(repo_id=entry["key"], allow_patterns=HF_PATTERNS)
    except Exception as e:
        raise OSError(f"HuggingFace wouldn't give us that model: {e}") from e

    if progress.cancelled():
        raise InterruptedError("cancelled")

    from pathlib import Path
    for item in Path(cached).iterdir():
        if item.is_file():
            shutil.copy2(item, staging / item.name)
    return staging


class _Progress:
    """The two things an installer needs from its job: somewhere to put
    byte counts, and a way to ask whether it has been cancelled.

    A small object rather than three closures because both installers
    need all of it, and passing three callbacks through two call layers
    was worse to read than this is."""

    def __init__(self, job_id, total):
        self.job_id = job_id
        self.total = total
        self.received = 0
        self._declared_total = 0

    def cancelled(self):
        current = job(self.job_id)
        return bool(current and current.get("cancelled"))

    def indeterminate(self):
        _job_set(self.job_id, indeterminate=True)

    def download(self, url, dest, entry):
        base = self.received

        def on_bytes(n, declared):
            self.received += n
            fields = {"received": self.received}
            # Content-Length is the truth about this download; the
            # catalog's size was only ever an estimate used to draw a bar
            # before the first byte arrived. Correct it once, the first
            # time a server tells us.
            if declared and not self._declared_total:
                self._declared_total = declared
                if abs(declared - self.total) > self.total * 0.02:
                    self.total = base + declared
                    fields["total"] = self.total
            _job_set(self.job_id, **fields)

        try:
            _download_file(url, dest, entry.get("_sha256") or entry.get("sha256") or "",
                           entry.get("size_bytes") or 0, on_bytes, self.cancelled)
        except BaseException:
            # The partial file is inside a staging directory that the
            # caller is about to delete, but a failed download that is
            # retried immediately shouldn't resume onto the old bytes.
            dest.unlink(missing_ok=True)
            raise


def _run_install(job_id, entry):
    role, key = entry["role"], entry["key"]
    dest = model_dir(role, key)
    staging = dest.parent / f".{dest.name}.{job_id}.part"
    progress = _Progress(job_id, entry.get("size_bytes") or 0)

    _job_set(job_id, state="downloading", received=0)
    shutil.rmtree(staging, ignore_errors=True)

    try:
        if role == "wake":
            finished = _install_wake(entry, staging, progress)
        else:
            finished = _install_transcribe(entry, staging, progress)

        if not _is_complete(role, finished):
            raise OSError("what arrived doesn't look like a usable model")

        # The move is the moment the model exists. Everything before it
        # happened inside a dot-prefixed staging directory that
        # installed_keys() skips, so an install that dies at any earlier
        # point leaves nothing that could be selected or loaded.
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(finished, dest)
    except InterruptedError:
        shutil.rmtree(staging, ignore_errors=True)
        _job_set(job_id, state="cancelled", finished_at=time.time())
        return
    except Exception as e:
        shutil.rmtree(staging, ignore_errors=True)
        _job_set(job_id, state="failed", error=_reason(e), finished_at=time.time())
        return
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    _job_set(job_id, state="done", received=progress.total or progress.received,
             total=progress.total or progress.received, finished_at=time.time())

    # First model for this role: select it, because otherwise someone
    # downloads a model, nothing visibly changes, and the reason is that
    # they also had to pick it. With one installed there is nothing to
    # pick between.
    if len(installed_for(role)) == 1:
        select(role, key)


def _reason(exc):
    """A sentence someone can act on, in the register voice_catalog uses.

    Quietude is deliberately offline, so somebody hitting this is quite
    likely on a machine with no route out at all, and "URLError" would
    leave them wondering whether Quietude itself was broken."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"The download server returned HTTP {exc.code}."
    if isinstance(exc, urllib.error.URLError):
        return (f"Couldn't reach the model download ({exc.reason}). "
                "Everything else in Quietude works offline; downloading a "
                "model is the one thing that needs the internet.")
    if isinstance(exc, zipfile.BadZipFile):
        return "The downloaded archive was corrupt or wasn't an archive at all."
    return str(exc) or exc.__class__.__name__


def start_install(key):
    """Begins a download in the background. Returns (job, error)."""
    key = (key or "").strip()
    if not _any_key_ok(key):
        return None, "That isn't a valid model name."
    entry = _entry(key)
    if not entry:
        return None, "No speech model by that name is in the catalog."
    role = entry["role"]
    if model_dir(role, key) is None:
        return None, "That isn't a valid model name."
    if key in installed_keys():
        return None, "That model is already installed."

    with _jobs_lock:
        for existing in _jobs.values():
            if existing["key"] == key and existing["state"] in ("queued", "downloading"):
                return dict(existing), None

    job_id = uuid.uuid4().hex[:12]
    record = {
        "id": job_id, "key": key, "role": role, "name": entry["name"],
        "language": entry.get("language") or "",
        "state": "queued", "received": 0,
        "total": entry.get("size_bytes") or 0,
        "indeterminate": False,
        "error": None, "cancelled": False,
        "started_at": time.time(), "finished_at": None,
    }
    with _jobs_lock:
        _jobs[job_id] = record
        # Finished jobs are kept so the page can report an outcome after
        # a reload, but not forever.
        if len(_jobs) > 30:
            done = sorted(
                (j for j in _jobs.values()
                 if j["state"] in ("done", "failed", "cancelled")),
                key=lambda j: j.get("finished_at") or 0,
            )
            for stale in done[:len(_jobs) - 30]:
                _jobs.pop(stale["id"], None)

    threading.Thread(target=_run_install, args=(job_id, entry),
                     daemon=True, name=f"quietude-model-{job_id}").start()
    return dict(record), None


# ============================================================
# Removing
# ============================================================

def remove(key):
    """Deletes an installed model. Returns (ok, error, reassigned_to).

    Removing the model a role is currently using is allowed, but only if
    there is another one to fall back to - and the selection is rewritten
    to that one here rather than left to resolve itself later. The
    failure this avoids is a selection pointing at a deleted directory,
    which would surface as the speech engine refusing to start on some
    later launch with nothing connecting it to this action.

    Refusing the last model for a role is the other half of the same
    decision: it would leave Quietude unable to listen at all, and that is
    not something to discover at the next restart either.
    """
    key = (key or "").strip()
    if not _any_key_ok(key):
        return False, "That isn't a valid model name.", None
    role = next((r for r in ROLES if key in installed_for(r)), None)
    if role is None:
        return False, "That model isn't installed.", None

    directory = model_dir(role, key)
    if directory is None:
        return False, "That isn't a valid model name.", None

    remaining = [k for k in installed_for(role) if k != key]
    was_active = active_key(role) == key
    if was_active and not remaining and not legacy_present(role):
        label = ROLE_LABEL[role].lower()
        return False, (f"That's the only model Quietude has for {label}, so "
                       "removing it would leave her unable to listen. "
                       "Download another one first."), None

    try:
        shutil.rmtree(directory)
    except OSError as e:
        return False, f"Couldn't remove it: {e}", None

    reassigned = None
    selection = _read_selection()
    if selection.get(role) == key:
        if remaining:
            selection[role] = remaining[0]
            reassigned = remaining[0]
        else:
            selection.pop(role, None)  # back to the legacy directory
        try:
            _write_selection(selection)
        except OSError:
            pass  # active_key falls through to the first installed model anyway
    elif was_active and remaining:
        reassigned = active_key(role)

    return True, None, reassigned
