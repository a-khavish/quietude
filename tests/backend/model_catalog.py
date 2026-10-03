#!/usr/bin/env python3
# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Exercises quietude.core.model_catalog against a local HTTP server serving
a fake vosk zip and fake whisper files. Nothing here touches the real
internet: alphacephei and HuggingFace are unreachable from this
container, which is exactly why the catalog's URLs and the catalog
itself are overridable by environment variable.

Run: python3 test_model_catalog.py
"""

import hashlib
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = "/home/claude/quietude/backend"

PASS = FAIL = 0


def check(label, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))


def section(name):
    print(f"\n--- {name} ---")


# ============================================================
# Fake payloads
# ============================================================

def make_vosk_zip(top="fake-wake-model"):
    """A zip shaped like a real vosk model: one top-level directory
    holding am/ and conf/."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{top}/am/final.mdl", b"\x00acoustic model" * 64)
        z.writestr(f"{top}/conf/model.conf", b"--min-active=200\n")
        z.writestr(f"{top}/README", b"fake vosk model for tests\n")
    return buf.getvalue()


def make_evil_zip():
    """A zip that tries to write outside where it is extracted."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../../escaped.txt", b"should never land")
    return buf.getvalue()


WHISPER_FILES = {
    "model.bin": b"\x00ctranslate2 weights" * 500,
    "config.json": json.dumps({"model_type": "whisper"}).encode(),
    "tokenizer.json": b'{"fake": true}',
    "vocabulary.txt": b"hello\nworld\n",
}

GOOD_ZIP = make_vosk_zip("fake-wake-good-1.0")
SECOND_ZIP = make_vosk_zip("fake-wake-second-1.0")
EVIL_ZIP = make_evil_zip()
SLOW_BLOB = b"x" * (6 * 1024 * 1024)

ROUTES = {
    "/vosk/fake-wake-good-1.0.zip": GOOD_ZIP,
    "/vosk/fake-wake-second-1.0.zip": SECOND_ZIP,
    "/vosk/fake-wake-badsum-1.0.zip": GOOD_ZIP,   # served fine, wrong sha in catalog
    "/vosk/fake-wake-badsize-1.0.zip": GOOD_ZIP,  # served fine, absurd size in catalog
    "/vosk/fake-wake-notazip-1.0.zip": b"<html>login required</html>" * 400,
    "/vosk/fake-wake-evil-1.0.zip": EVIL_ZIP,
    "/hf/slow/model.bin": SLOW_BLOB,
}
for name, blob in WHISPER_FILES.items():
    ROUTES[f"/hf/mirror/{name}"] = blob


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = ROUTES.get(self.path)
        if body is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        # Trickled out in small pieces so the cancel test has a window to
        # cancel inside, and so progress callbacks fire more than once.
        slow = "/slow/" in self.path
        step = 64 * 1024 if slow else len(body)
        for off in range(0, len(body), max(step, 1)):
            try:
                self.wfile.write(body[off:off + step])
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            if slow:
                time.sleep(0.05)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ============================================================
# Environment
# ============================================================

ROOT = tempfile.mkdtemp(prefix="quietude-models-", dir=HERE)
PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"

CATALOG = [
    {"key": "fake-wake-good-1.0", "role": "wake", "name": "Fake wake (good)",
     "language": "Test", "size_bytes": len(GOOD_ZIP),
     "sha256": hashlib.sha256(GOOD_ZIP).hexdigest(),
     "description": "a wake model that downloads cleanly"},
    {"key": "fake-wake-second-1.0", "role": "wake", "name": "Fake wake (second)",
     "language": "Test", "size_bytes": len(SECOND_ZIP),
     "description": "a second wake model, so select and remove have a choice"},
    {"key": "fake-wake-badsum-1.0", "role": "wake", "name": "Fake wake (bad sha)",
     "language": "Test", "size_bytes": len(GOOD_ZIP),
     "sha256": "0" * 64,
     "description": "sha256 in the catalog does not match the bytes served"},
    {"key": "fake-wake-badsize-1.0", "role": "wake", "name": "Fake wake (bad size)",
     "language": "Test", "size_bytes": 500 * 1000 * 1000,
     "description": "catalog claims 500MB, server serves a few KB"},
    {"key": "fake-wake-notazip-1.0", "role": "wake", "name": "Fake wake (not a zip)",
     "language": "Test", "size_bytes": len(ROUTES["/vosk/fake-wake-notazip-1.0.zip"]),
     "description": "server returns a proxy login page instead of an archive"},
    {"key": "fake-wake-evil-1.0", "role": "wake", "name": "Fake wake (zip slip)",
     "language": "Test", "size_bytes": len(EVIL_ZIP),
     "description": "archive member tries to write outside the directory"},

    {"key": "Fake/whisper-mirror", "role": "transcribe", "name": "Fake whisper (mirror)",
     "language": "Test",
     "size_bytes": sum(len(b) for b in WHISPER_FILES.values()),
     "description": "a mirrored catalog entry that lists its own files",
     "files": [{"name": name, "url": f"/hf/mirror/{name}", "size_bytes": len(blob)}
               for name, blob in WHISPER_FILES.items()]},
    {"key": "Fake/whisper-slow", "role": "transcribe", "name": "Fake whisper (slow)",
     "language": "Test", "size_bytes": len(SLOW_BLOB),
     "description": "trickled out slowly, for the cancel test",
     "files": [{"name": "model.bin", "url": "/hf/slow/model.bin",
                "size_bytes": len(SLOW_BLOB)}]},
    {"key": "Fake/whisper-hub", "role": "transcribe", "name": "Fake whisper (hub)",
     "language": "Test", "size_bytes": 1234,
     "description": "no files listed, so it goes through huggingface_hub"},
]

CATALOG_PATH = os.path.join(ROOT, "catalog.json")
with open(CATALOG_PATH, "w") as f:
    json.dump({"models": CATALOG}, f)

os.environ["XDG_DATA_HOME"] = os.path.join(ROOT, "data")
os.environ["XDG_CONFIG_HOME"] = os.path.join(ROOT, "cfg")
os.environ["XDG_CACHE_HOME"] = os.path.join(ROOT, "cache")
os.environ["QUIETUDE_MODEL_CATALOG_FILE"] = CATALOG_PATH
os.environ["QUIETUDE_VOSK_BASE_URL"] = f"{BASE}/vosk/"
os.environ["QUIETUDE_MODEL_FILE_BASE_URL"] = f"{BASE}/"

sys.path.insert(0, BACKEND)

server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()

from quietude import config                      # noqa: E402
from quietude.core import model_catalog as mc    # noqa: E402

config.ensure_dirs()


def wait(job_id, timeout=30):
    deadline = time.time() + timeout
    seen = []
    while time.time() < deadline:
        j = mc.job(job_id)
        if j["received"] and (not seen or seen[-1] != j["received"]):
            seen.append(j["received"])
        if j["state"] not in ("queued", "downloading"):
            return j, seen
        time.sleep(0.02)
    return mc.job(job_id), seen


def install(key, timeout=30):
    job, err = mc.start_install(key)
    if err:
        return {"state": "rejected", "error": err}, []
    return wait(job["id"], timeout)


print(f"quietude speech model catalog - test run")
print(f"  data root : {config.DATA_DIR}")
print(f"  fake host : {BASE}")

# ============================================================
section("listing")
# ============================================================

rows = mc.catalog()
check("catalog() returns the overridden list", len(rows) == len(CATALOG),
      f"got {len(rows)}")
check("every row carries the documented fields",
      all(set(("key", "role", "name", "language", "size_bytes", "description",
               "installed", "active")) <= set(r) for r in rows))
check("rows are grouped wake-then-transcribe and sized ascending",
      [r["role"] for r in rows] == ["wake"] * 6 + ["transcribe"] * 3,
      str([(r["role"], r["size_bytes"]) for r in rows]))
check("nothing is installed yet", mc.installed_keys() == set(),
      str(mc.installed_keys()))
print("        " + "\n        ".join(
    f"{r['role']:11} {r['key']:24} {r['size_bytes']:>11,}  installed={r['installed']}"
    for r in rows))

# ============================================================
section("path-traversal-shaped keys are refused")
# ============================================================

for bad in ("../../etc/passwd", "../escape", "..", "Fake/../../../etc/passwd",
            "fake-wake-good-1.0/../../x", "/etc/passwd", ""):
    job, err = mc.start_install(bad)
    check(f"start_install({bad!r}) refused", job is None and bool(err), repr(err))

ok, err, _ = mc.remove("../../etc/passwd")
check("remove() refuses a traversal key as invalid",
      not ok and "valid model name" in (err or ""), repr(err))
ok, err = mc.select("wake", "../../etc/passwd")
check("select() refuses a traversal key as invalid",
      not ok and "valid model name" in (err or ""), repr(err))
check("model_dir() refuses a traversal key",
      mc.model_dir("wake", "../../etc") is None
      and mc.model_dir("transcribe", "a/../../b") is None)

# ============================================================
section("backward compatibility with the installer's flat directory")
# ============================================================

(config.VOSK_MODEL_DIR / "am").mkdir(parents=True, exist_ok=True)
(config.VOSK_MODEL_DIR / "conf").mkdir(parents=True, exist_ok=True)
check("legacy_present('wake') sees the installer's model", mc.legacy_present("wake"))
check("active_model_dir('wake') is the legacy directory",
      mc.active_model_dir("wake") == config.VOSK_MODEL_DIR,
      str(mc.active_model_dir("wake")))
check("active_source('wake') says 'legacy'", mc.active_source("wake") == "legacy")
check("active_key('wake') is None - the legacy directory has no key",
      mc.active_key("wake") is None)
check("active_model_dir('transcribe') is None with nothing installed",
      mc.active_model_dir("transcribe") is None)

# ============================================================
section("install with progress")
# ============================================================

job, progress = install("fake-wake-good-1.0")
check("install finished", job["state"] == "done", json.dumps(job, default=str))
check("progress was reported in bytes as it went",
      len(progress) >= 1 and progress[-1] == job["total"],
      f"steps={progress} total={job['total']}")
dest = mc.model_dir("wake", "fake-wake-good-1.0")
check("the model landed unwrapped (am/ at the top, not nested)",
      (dest / "am" / "final.mdl").is_file() and (dest / "conf").is_dir())
check("installed_keys() reports it", mc.installed_keys() == {"fake-wake-good-1.0"})
check("the first model for a role is selected automatically",
      mc.active_key("wake") == "fake-wake-good-1.0"
      and mc.active_source("wake") == "selected"
      and json.loads((config.DATA_DIR / "speech-models.json").read_text())
      .get("wake") == "fake-wake-good-1.0",
      f"{mc.active_key('wake')} / {mc.active_source('wake')}")
check("active_model_dir('wake') now points at it",
      mc.active_model_dir("wake") == dest, str(mc.active_model_dir("wake")))
check("no staging directories left behind",
      not [p for p in dest.parent.iterdir() if p.name.startswith(".")],
      str(list(dest.parent.iterdir())))

job, _ = install("fake-wake-good-1.0")
check("installing it again is refused",
      job["state"] == "rejected" and "already installed" in job["error"], repr(job))

job, progress = install("Fake/whisper-mirror")
check("a transcribe model installs from a mirrored file list",
      job["state"] == "done", json.dumps(job, default=str))
whisper_dest = mc.model_dir("transcribe", "Fake/whisper-mirror")
check("the slash in the key is encoded, not joined",
      whisper_dest.name == "Fake--whisper-mirror"
      and whisper_dest.parent == config.MODELS_DIR / "transcribe",
      str(whisper_dest))
check("all four files arrived",
      sorted(p.name for p in whisper_dest.iterdir()) == sorted(WHISPER_FILES))
check("progress covered every file",
      progress[-1] == sum(len(b) for b in WHISPER_FILES.values()), str(progress))

# ============================================================
section("verification failures")
# ============================================================

job, _ = install("fake-wake-badsum-1.0")
check("a sha256 mismatch fails the install",
      job["state"] == "failed" and "checksum" in job["error"], repr(job.get("error")))
check("nothing was left installed after the checksum failure",
      "fake-wake-badsum-1.0" not in mc.installed_keys()
      and not mc.model_dir("wake", "fake-wake-badsum-1.0").exists())

job, _ = install("fake-wake-badsize-1.0")
check("a size nothing like the catalog's fails the install",
      job["state"] == "failed" and "nothing like" in job["error"],
      repr(job.get("error")))

job, _ = install("fake-wake-notazip-1.0")
check("a non-archive response fails cleanly rather than crashing",
      job["state"] == "failed" and "archive" in job["error"], repr(job.get("error")))

job, _ = install("fake-wake-evil-1.0")
check("a zip member pointing outside the directory is refused",
      job["state"] == "failed" and "unsafe path" in job["error"],
      repr(job.get("error")))
check("the zip-slip target was never written",
      not (config.MODELS_DIR / "escaped.txt").exists()
      and not (config.MODELS_DIR.parent / "escaped.txt").exists())

job, _ = install("Fake/whisper-hub")
check("a missing huggingface_hub is reported, not raised",
      job["state"] == "failed" and "huggingface_hub" in job["error"],
      repr(job.get("error")))
check("the whisper staging directory was cleaned up",
      not [p for p in (config.MODELS_DIR / "transcribe").iterdir()
           if p.name.startswith(".")])

# ============================================================
section("cancel")
# ============================================================

job, err = mc.start_install("Fake/whisper-slow")
# "queued" or "downloading": start_install returns a snapshot of the
# record, and the thread it just started may already have moved it on.
check("the slow install started",
      err is None and job["state"] in ("queued", "downloading"),
      f"{err!r} {job and job['state']!r}")
time.sleep(1.0)
mid = mc.job(job["id"])
check("it is downloading and has received some bytes",
      mid["state"] == "downloading" and 0 < mid["received"] < mid["total"],
      f"{mid['state']} {mid['received']}/{mid['total']}")
check("cancel() accepts a running job", mc.cancel(job["id"]) is True)
final, _ = wait(job["id"], timeout=15)
check("the job ends up cancelled", final["state"] == "cancelled", repr(final))
check("cancel() refuses an already-finished job", mc.cancel(job["id"]) is False)
check("a cancelled install leaves nothing installed",
      "Fake/whisper-slow" not in mc.installed_keys()
      and not mc.model_dir("transcribe", "Fake/whisper-slow").exists())
check("and no staging directory",
      not [p for p in (config.MODELS_DIR / "transcribe").iterdir()
           if p.name.startswith(".")],
      str(list((config.MODELS_DIR / "transcribe").iterdir())))
check("jobs() lists every job so far", len(mc.jobs()) >= 7, str(len(mc.jobs())))
check("job() on an unknown id is None", mc.job("nosuchjob") is None)

# ============================================================
section("select")
# ============================================================

job, _ = install("fake-wake-second-1.0")
check("a second wake model installs", job["state"] == "done", repr(job.get("error")))
check("the automatic selection did not move",
      mc.active_key("wake") == "fake-wake-good-1.0", mc.active_key("wake"))

ok, err = mc.select("wake", "fake-wake-second-1.0")
check("select() accepts an installed model", ok, repr(err))
check("active_key('wake') followed it",
      mc.active_key("wake") == "fake-wake-second-1.0")
check("active_model_dir('wake') followed it",
      mc.active_model_dir("wake") == mc.model_dir("wake", "fake-wake-second-1.0"))
check("active_source('wake') says 'selected'", mc.active_source("wake") == "selected")

ok, err = mc.select("wake", "fake-wake-badsum-1.0")
check("select() refuses a model that isn't installed",
      not ok and "isn't installed" in (err or ""), repr(err))
ok, err = mc.select("nonsense", "fake-wake-good-1.0")
check("select() refuses an unknown role",
      not ok and "kind of speech model" in (err or ""), repr(err))

saved = json.loads((config.DATA_DIR / "speech-models.json").read_text())
check("the choice is on disk under DATA_DIR",
      saved.get("wake") == "fake-wake-second-1.0", json.dumps(saved))

import importlib                                  # noqa: E402
importlib.reload(mc)
check("the choice survives a restart (module reloaded from disk)",
      mc.active_key("wake") == "fake-wake-second-1.0", mc.active_key("wake"))

# ============================================================
section("remove")
# ============================================================

ok, err, reassigned = mc.remove("Fake/whisper-mirror")
check("removing the only model a role has is refused",
      not ok and "unable to listen" in (err or ""), repr(err))
check("it is still installed after the refusal",
      "Fake/whisper-mirror" in mc.installed_keys())

ok, err, reassigned = mc.remove("fake-wake-good-1.0")
check("removing a non-active model succeeds", ok, repr(err))
check("nothing was reassigned", reassigned is None, repr(reassigned))
check("it is gone from disk",
      not mc.model_dir("wake", "fake-wake-good-1.0").exists())
check("the active model is untouched",
      mc.active_key("wake") == "fake-wake-second-1.0")

job, _ = install("fake-wake-good-1.0")
check("it can be installed again afterwards", job["state"] == "done",
      repr(job.get("error")))
ok, err, reassigned = mc.remove("fake-wake-second-1.0")
check("removing the active model succeeds when there is a fallback", ok, repr(err))
check("the selection was re-pointed at the remaining model",
      reassigned == "fake-wake-good-1.0", repr(reassigned))
check("active_model_dir('wake') is a directory that exists",
      mc.active_model_dir("wake") is not None and mc.active_model_dir("wake").is_dir(),
      str(mc.active_model_dir("wake")))
saved = json.loads((config.DATA_DIR / "speech-models.json").read_text())
check("and the re-pointing was persisted", saved.get("wake") == "fake-wake-good-1.0",
      json.dumps(saved))

ok, err, _ = mc.remove("fake-wake-second-1.0")
check("removing it twice is refused",
      not ok and "isn't installed" in (err or ""), repr(err))

# with the legacy whisper directory present, the last whisper model can go
(config.WHISPER_MODEL_DIR).mkdir(parents=True, exist_ok=True)
(config.WHISPER_MODEL_DIR / "model.bin").write_bytes(b"legacy")
ok, err, _ = mc.remove("Fake/whisper-mirror")
check("the last model may go when the installer's own is still there", ok, repr(err))
check("and the role falls back to the legacy directory",
      mc.active_model_dir("transcribe") == config.WHISPER_MODEL_DIR
      and mc.active_source("transcribe") == "legacy",
      str(mc.active_model_dir("transcribe")))

# ============================================================
print(f"\n{'=' * 52}")
print(f"  {PASS} passed, {FAIL} failed")
print("=" * 52)

server.shutdown()
shutil.rmtree(ROOT, ignore_errors=True)
sys.exit(1 if FAIL else 0)
