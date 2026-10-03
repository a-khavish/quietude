#!/usr/bin/env python3
# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""The voice library, against a local stand-in for the catalog host.

HuggingFace isn't reachable from here, but every line of this code is:
parsing the catalog, caching it, streaming a download, verifying size
and checksum, writing atomically, cancelling, and cleaning up after a
corrupted file. Those are the parts that can be wrong. Pointing the two
URLs at a local server exercises all of them."""
import hashlib, http.server, json, os, pathlib, shutil, sys
import tempfile, threading, time

ROOT = pathlib.Path(tempfile.mkdtemp())
os.environ["XDG_DATA_HOME"] = str(ROOT / "data")
os.environ["XDG_CONFIG_HOME"] = str(ROOT / "config")
os.environ["XDG_CACHE_HOME"] = str(ROOT / "cache")

from quietude import config                           # noqa: E402
from quietude.core import voice_catalog as vc         # noqa: E402

ok = fail = 0
def check(label, got, want=True):
    global ok, fail
    if got == want:
        ok += 1; print(f"  pass  {label}")
    else:
        fail += 1; print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")

# ---- a local stand-in for the catalog host ----
SERVE = ROOT / "repo"
(SERVE / "en/en_US/amy/medium").mkdir(parents=True)
(SERVE / "en/en_GB/alba/low").mkdir(parents=True)

def put(path, data):
    p = SERVE / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return len(data), hashlib.md5(data).hexdigest()

amy_model = os.urandom(300_000)
amy_cfg   = json.dumps({"audio": {"sample_rate": 22050}}).encode()
alba_model = os.urandom(120_000)
alba_cfg   = json.dumps({"audio": {"sample_rate": 16000}}).encode()

am_s, am_h = put("en/en_US/amy/medium/en_US-amy-medium.onnx", amy_model)
ac_s, ac_h = put("en/en_US/amy/medium/en_US-amy-medium.onnx.json", amy_cfg)
bm_s, bm_h = put("en/en_GB/alba/low/en_GB-alba-low.onnx", alba_model)
bc_s, bc_h = put("en/en_GB/alba/low/en_GB-alba-low.onnx.json", alba_cfg)

CATALOG = {
  "en_US-amy-medium": {
    "key": "en_US-amy-medium", "name": "amy", "quality": "medium", "num_speakers": 1,
    "language": {"code": "en_US", "family": "en", "region": "US",
                 "name_native": "English", "name_english": "English",
                 "country_english": "United States"},
    "files": {
      "en/en_US/amy/medium/en_US-amy-medium.onnx": {"size_bytes": am_s, "md5_digest": am_h},
      "en/en_US/amy/medium/en_US-amy-medium.onnx.json": {"size_bytes": ac_s, "md5_digest": ac_h},
      "en/en_US/amy/medium/MODEL_CARD": {"size_bytes": 284, "md5_digest": "x"}}},
  "en_GB-alba-low": {
    "key": "en_GB-alba-low", "name": "alba", "quality": "low", "num_speakers": 1,
    "language": {"code": "en_GB", "name_english": "English",
                 "country_english": "United Kingdom"},
    "files": {
      "en/en_GB/alba/low/en_GB-alba-low.onnx": {"size_bytes": bm_s, "md5_digest": bm_h},
      "en/en_GB/alba/low/en_GB-alba-low.onnx.json": {"size_bytes": bc_s, "md5_digest": bc_h}}},
  # A corrupted entry: the catalog's checksum does not match the bytes
  # served. This must be caught and must leave nothing behind.
  "xx_XX-broken-medium": {
    "key": "xx_XX-broken-medium", "name": "broken", "quality": "medium",
    "language": {"code": "xx_XX", "name_english": "Test"},
    "files": {
      "en/en_US/amy/medium/en_US-amy-medium.onnx": {"size_bytes": am_s,
                                                    "md5_digest": "0"*32},
      "en/en_US/amy/medium/en_US-amy-medium.onnx.json": {"size_bytes": ac_s,
                                                         "md5_digest": ac_h}}},
  # No .onnx at all - must be skipped, not crash the parse.
  "yy_YY-nofiles-low": {
    "key": "yy_YY-nofiles-low", "name": "nofiles", "quality": "low",
    "language": {"code": "yy_YY", "name_english": "Test"},
    "files": {"readme.txt": {"size_bytes": 1, "md5_digest": "x"}}},
}
(SERVE / "voices.json").write_text(json.dumps(CATALOG))

class Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw): super().__init__(*a, directory=str(SERVE), **kw)
    def log_message(self, *a): pass

httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
vc.CATALOG_URL = f"http://127.0.0.1:{PORT}/voices.json"
vc.DOWNLOAD_BASE = f"http://127.0.0.1:{PORT}/"

def wait(job_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        j = vc.job(job_id)
        if j and j["state"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.05)
    return vc.job(job_id)

print("reading the catalog")
voices, source, err = vc.catalog(refresh=True)
check("fetched from the network", source, "network")
check("no error", err, None)
check("entries without a model file are skipped", len(voices), 3)
amy = next(v for v in voices if v["key"] == "en_US-amy-medium")
check("size is model + config, not the MODEL_CARD", amy["size_bytes"], am_s + ac_s)
check("language is readable", (amy["language"], amy["country"]),
      ("English", "United States"))
check("quality carries a note", bool(amy["quality_note"]))

print("\nthe catalog is cached, so opening the page twice is one request")
LIVE, DEAD = vc.CATALOG_URL, "http://127.0.0.1:1/voices.json"
vc.CATALOG_URL = DEAD                    # the host is now unreachable
cached, source, err = vc.catalog()
check("still served", len(cached), 3)
check("from the cache", source, "cache")
check("and it didn't go to the network to do it", err, None)
vc.CATALOG_URL = LIVE

print("\noffline, with a forced refresh, it says so instead of emptying the list")
vc.CATALOG_URL = DEAD
stale, source, err = vc.catalog(refresh=True)
check("the cached list is still returned", len(stale), 3)
check("and the failure is reported alongside it", bool(err))
check("in words someone can act on", "offline" in (err or "").lower()
      or "reach" in (err or "").lower())
vc.CATALOG_URL = LIVE

print("\ninstalling a voice")
check("nothing installed yet", vc.installed_keys(), set())
job, err = vc.start_install("en_GB-alba-low")
check("download started", err, None)
done = wait(job["id"])
check("it finished", done["state"], "done")
check("both files are on disk", vc.installed_keys(), {"en_GB-alba-low"})
check("the model is byte-for-byte what was served",
      (config.PIPER_VOICE_DIR / "en_GB-alba-low.onnx").read_bytes(), alba_model)
check("progress reached the total", done["received"], done["total"])
check("no .part files left behind",
      list(config.PIPER_VOICE_DIR.glob(".*part")), [])

print("\ninstalling the same voice twice is refused rather than re-downloaded")
_, err = vc.start_install("en_GB-alba-low")
check("refused", "already installed" in (err or ""))

print("\na corrupted download is caught and leaves nothing behind")
job, err = vc.start_install("xx_XX-broken-medium")
check("it starts", err, None)
done = wait(job["id"])
check("it fails", done["state"], "failed")
check("saying why", "checksum" in (done["error"] or "").lower())
check("the voice is NOT listed as installed", "xx_XX-broken-medium" in vc.installed_keys(), False)
check("and neither file was left on disk",
      sorted(p.name for p in config.PIPER_VOICE_DIR.glob("xx_XX*")), [])

print("\ncancelling a download cleans up after itself")
job, err = vc.start_install("en_US-amy-medium")
time.sleep(0.05)
vc.cancel(job["id"])
done = wait(job["id"])
check("it ends cancelled or done (it is a small file, so it may win the race)",
      done["state"] in ("cancelled", "done"))
if done["state"] == "cancelled":
    check("nothing half-written survives",
          sorted(p.name for p in config.PIPER_VOICE_DIR.glob("en_US-amy*")), [])

print("\nremoving a voice")
if "en_US-amy-medium" not in vc.installed_keys():
    wait(vc.start_install("en_US-amy-medium")[0]["id"])
ok_, err = vc.remove("en_US-amy-medium")
check("removed", ok_)
check("gone from disk", "en_US-amy-medium" in vc.installed_keys(), False)
check("removing it again is refused", vc.remove("en_US-amy-medium")[0], False)

print("\nthe catalog is data, not instructions")
for bad in ("../../etc/passwd", "/etc/passwd", "a/b", "", "x" * 200, "a;rm -rf /"):
    j, e = vc.start_install(bad)
    if j is not None or not e:
        check(f"rejected {bad!r}", False); break
else:
    check("every path-shaped voice name is rejected before anything is fetched", True)

httpd.shutdown(); httpd.server_close()
shutil.rmtree(ROOT, ignore_errors=True)
print(f"\n{ok}/{ok + fail} checks passed")
sys.exit(1 if fail else 0)
