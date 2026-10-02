# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
tts_engine.py
Quietude's voice. A persistent worker process, started once at launch and
kept warm for the whole session, that synthesizes a phrase to a WAV file
and hands back an id the frontend can fetch and play.

What stayed from the Windows build
----------------------------------
The architecture: an own OS process (not a thread), a
multiprocessing.Manager dict for shared status, a command queue, and a
bounded-escalation shutdown. The reason for a separate process changed
but didn't go away. On Windows it was COM: pyttsx3 drives SAPI5, and COM
apartments behave far better with a process to themselves than sharing
threads with Flask's request handling. Here it's the voice model: piper
loads a few tens of megabytes of ONNX weights and runs inference under a
native runtime that keeps its own thread pool, and a crash or a stuck
inference in that native code should not be able to take Flask with it.
Keeping it warm is also the whole performance story - loading the model
costs most of a second, synthesizing a sentence costs a fraction of one.

What changed
------------
1. The engine is piper, not pyttsx3. On Linux pyttsx3 is a thin wrapper
   that shells out to espeak-ng anyway, so keeping it would have meant
   paying for an abstraction layer and still getting espeak's robotic
   output. piper is a neural TTS that runs fully offline on CPU, sounds
   markedly more natural, and ships voices as two files you can drop in
   a directory. espeak-ng is kept as an automatic fallback, because it is
   in every distro's repos and installs in seconds - so Quietude can always
   speak something, even before anyone downloads a voice model.

2. This module no longer plays audio. The Windows build owned a pygame
   mixer in the worker and exposed pause/resume/stop routes to drive it,
   which is why it needed an audio device, a playback state machine, and
   careful file-handle juggling to stop pygame serving a stale clip. Now
   the backend's entire job is text in, WAV path out; the browser plays
   it with an <audio> element. Three things fell out of that:
     - no audio device is touched by the backend at all,
     - pause/resume/stop/seek are properties of the audio element, so
       the playback state machine and its polling disappeared,
     - the whole class of "did pygame release the file handle" bugs is
       gone, since nothing holds a handle after the file is written.
   The per-phrase unique filename is kept anyway - it makes the clips
   cache-friendly and means a replay never races a rewrite.

3. No synthesis-watchdog thread. The Windows version wrapped each
   runAndWait() in its own thread with a 15s join, because pyttsx3 could
   hang indefinitely on a COM call and there was no way to interrupt it.
   piper's synthesize call is ordinary Python calling into a native
   library and returns or raises; if the worker ever does wedge, the
   bounded shutdown in process_utils handles it the same way it handles
   everything else.
"""

import multiprocessing
import shutil
import subprocess
import time
import uuid
import wave

from quietude import config
from quietude import process_utils

_process = None
_command_queue = None
_result_queue = None
_status_dict = None
_manager = None

# Kept from the Windows build so saved settings and the settings UI carry
# over unchanged. The number is interpreted differently now - see
# _length_scale_for below - but the range and the 200 default still mean
# "normal speaking pace" in the middle.
RATE_MIN = 50
RATE_MAX = 400
RATE_DEFAULT = 200

# How many synthesized clips to keep in the cache before pruning the
# oldest. The browser may still be fetching the clip it was just handed,
# and a user may replay a message from a while back, so this is a few
# deep rather than one.
MAX_CACHED_CLIPS = 24

_ESPEAK_BIN = "espeak-ng"


def _piper_available() -> bool:
    try:
        import piper  # noqa: F401
        return True
    except ImportError:
        return False


def _espeak_available() -> bool:
    return shutil.which(_ESPEAK_BIN) is not None


def is_available() -> bool:
    """True if Quietude can actually speak - which is not the same as having
    a TTS package installed.

    piper being importable means nothing on its own: it needs a voice
    model on disk, and the models are downloaded rather than bundled, so
    "piper installed, no voices" is the normal state of a fresh install
    that couldn't reach the network. espeak-ng needs no model, so its
    binary being present is sufficient by itself.

    Getting this distinction wrong is worse than it sounds: reporting
    ready when there is no usable voice makes the readiness gate pass,
    and the failure resurfaces later as a 503 on the first thing Quietude
    tries to say - long after the point where it could have been
    explained."""
    return bool(_piper_voices()) or _espeak_available()


# How many loaded piper voices to keep in the worker at once.
#
# Each one is a few tens of megabytes of ONNX weights and most of a
# second to load, so the old behaviour - keep exactly one, drop it the
# moment a different voice is asked for - meant that auditioning voices
# on the settings page reloaded a model per click, and switching back to
# one you had already heard reloaded it again. Three covers the
# comparing-two-or-three case that actually happens, and caps what a
# voice library full of downloads can cost in memory.
MAX_LOADED_VOICES = 3

_voices_cache = None
_voices_cache_key = None


def _piper_voices():
    """A piper voice is a <name>.onnx next to a <name>.onnx.json. Both
    have to be present for the voice to be usable, so a half-finished
    download simply doesn't show up in the list.

    Cached against the directory's mtime rather than re-globbed every
    time. This is called on the path of every phrase, and a voice
    library the user has been downloading into is a directory with real
    contents - but the mtime check means a voice that appears while Quietude
    is running is still picked up on the next phrase, with no restart."""
    global _voices_cache, _voices_cache_key
    if not config.PIPER_VOICE_DIR.is_dir():
        _voices_cache, _voices_cache_key = None, None
        return []
    try:
        key = config.PIPER_VOICE_DIR.stat().st_mtime_ns
    except OSError:
        key = None
    if key is not None and key == _voices_cache_key and _voices_cache is not None:
        return list(_voices_cache)

    voices = []
    for onnx in sorted(config.PIPER_VOICE_DIR.glob("*.onnx")):
        if not onnx.with_suffix(".onnx.json").is_file():
            continue
        voices.append({
            "id": f"piper:{onnx.name}",
            "name": onnx.stem.replace("_", " "),
            "engine": "piper",
        })
    _voices_cache, _voices_cache_key = voices, key
    return list(voices)


def _espeak_voices():
    """The English voices espeak can actually produce.

    `espeak-ng --voices=en` prints six columns:

        Pty  Language  Age/Gender  VoiceName  File  Other Languages

    The identifier has to come from **File**, not Language. Language
    repeats heavily - of 29 English voices, 16 share the code `en` and
    four share `en-us` - so using it gave sixteen different voices the
    same id, and the settings page dutifully showed every one of them as
    selected when you clicked a single entry. File is unique per voice
    and `-v gmw/en` is a form espeak accepts, so it serves as both the
    key and the thing passed back to the engine.

    MBROLA voices (File beginning `mb/`) are listed by espeak whether or
    not the separate `mbrola` binary is installed, and synthesis simply
    fails without it. Offering a voice that cannot speak is worse than
    offering fewer, so they're filtered out unless mbrola is present."""
    if not _espeak_available():
        return []
    try:
        out = subprocess.run(
            [_ESPEAK_BIN, "--voices=en"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []

    have_mbrola = shutil.which("mbrola") is not None

    voices, seen = [], set()
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 5:
            continue
        name, voice_file = parts[3], parts[4]

        if voice_file.startswith("mb/") and not have_mbrola:
            continue

        voice_id = f"espeak:{voice_file}"
        if voice_id in seen:
            continue  # belt and braces: ids must be unique
        seen.add(voice_id)

        voices.append({
            "id": voice_id,
            "name": f"{name.replace('_', ' ')} (espeak)",
            "engine": "espeak",
        })
    return voices


def get_voices():
    """Every voice the settings page can offer, piper first. A one-off
    query that doesn't touch the worker - listing files and running
    `espeak-ng --voices` are both cheap and side-effect free, unlike the
    Windows build where this had to spin up a throwaway pyttsx3 engine."""
    return _piper_voices() + _espeak_voices()


def get_property_defaults():
    voices = get_voices()
    return {
        "rate": RATE_DEFAULT,
        "volume": 1.0,
        "voice_id": voices[0]["id"] if voices else None,
    }


def _length_scale_for(rate) -> float:
    """piper's speed control is length_scale: seconds of audio per unit
    of text, so bigger is slower. Map the stored words-per-minute-style
    rate onto it with RATE_DEFAULT as 1.0, which keeps every saved
    setting from the Windows build meaningful and keeps the slider's
    direction intuitive (right = faster)."""
    try:
        rate = float(rate)
    except (TypeError, ValueError):
        return 1.0
    rate = max(RATE_MIN, min(RATE_MAX, rate))
    return RATE_DEFAULT / rate


# ============================================================
# Worker process
# ============================================================

def _load_piper(voice_file):
    from piper import PiperVoice

    path = config.PIPER_VOICE_DIR / voice_file
    if not path.is_file():
        raise FileNotFoundError(f"piper voice not found: {path}")
    return PiperVoice.load(str(path), config_path=str(path.with_suffix(".onnx.json")))


_piper_api = None


def _detect_piper_api():
    """Work out once which call shape this piper install wants.

    piper's API has moved across releases: the options argument changed
    from loose keywords to a SynthesisConfig object, and in current
    versions `synthesize()` returns an iterable of audio chunks instead
    of writing to a wave file. So the shape has to be discovered rather
    than assumed.

    Discovered by inspecting signatures, deliberately, rather than by
    calling and catching TypeError. A try/except ladder around the real
    synthesis call looks equivalent but isn't: a TypeError raised from
    *inside* a correct call - bad text, a malformed voice config - is
    indistinguishable from a signature mismatch, so it would be swallowed
    and the next (wrong) shape tried, turning a clear error into silence
    or wrong audio. Here a genuine failure propagates."""
    global _piper_api
    if _piper_api is not None:
        return _piper_api

    import inspect

    from piper import PiperVoice

    config_cls = None
    try:
        from piper import SynthesisConfig
        config_cls = SynthesisConfig
    except ImportError:
        pass

    if hasattr(PiperVoice, "synthesize_wav"):
        params = inspect.signature(PiperVoice.synthesize_wav).parameters
        if "syn_config" in params and config_cls is not None:
            _piper_api = ("wav_config", config_cls)
        else:
            _piper_api = ("wav_kwargs", None)
    else:
        # Very old: synthesize() wrote into a wave file.
        _piper_api = ("legacy_synthesize", None)

    return _piper_api


def _synthesize_piper(voice, text, wav_path, length_scale):
    shape, config_cls = _detect_piper_api()

    with wave.open(str(wav_path), "wb") as wav:
        if shape == "wav_config":
            # Current piper. Note that SynthesisConfig also exposes
            # `volume`, which is deliberately not set: volume is applied
            # to the <audio> element in the browser instead, so changing
            # it takes effect on the next phrase without re-synthesizing,
            # and doesn't bake a level into the cached clip.
            voice.synthesize_wav(
                text, wav, syn_config=config_cls(length_scale=length_scale)
            )
        elif shape == "wav_kwargs":
            voice.synthesize_wav(text, wav, length_scale=length_scale)
        else:
            voice.synthesize(text, wav, length_scale=length_scale)


def _synthesize_espeak(text, wav_path, rate, voice_code):
    cmd = [_ESPEAK_BIN, "-w", str(wav_path)]
    try:
        cmd += ["-s", str(int(max(80, min(450, float(rate or RATE_DEFAULT)))))]
    except (TypeError, ValueError):
        pass
    if voice_code:
        cmd += ["-v", voice_code]
    cmd += ["--", text]
    subprocess.run(cmd, capture_output=True, timeout=30, check=True)


def _prune_clips(clip_dir):
    clips = sorted(clip_dir.glob("clip_*.wav"), key=lambda p: p.stat().st_mtime)
    for stale in clips[:-MAX_CACHED_CLIPS]:
        try:
            stale.unlink()
        except OSError:
            pass


def _worker_main(command_queue, result_queue, status_dict, clip_dir_str,
                 preload_voice_id=None):
    """Runs entirely in the child process.

    Voice models are loaded once and kept. Two things changed here after
    the first version shipped, both of which were costing time on every
    single phrase:

    The saved voice is now loaded at startup rather than on the first
    phrase. Loading an ONNX voice costs most of a second; paying that on
    the first thing Quietude says meant her first sentence was always the
    slow one, during the greeting, which is the worst possible moment
    for it. The load happens here while the rest of the app is still
    booting, against a readiness flag that is already soft.

    And up to MAX_LOADED_VOICES stay loaded, rather than exactly one.
    The old code dropped the loaded voice the instant a different one
    was asked for, so auditioning voices in the settings page reloaded a
    model per click and switching back reloaded it again."""
    from pathlib import Path
    clip_dir = Path(clip_dir_str)
    clip_dir.mkdir(parents=True, exist_ok=True)

    have_piper = _piper_available()
    have_espeak = _espeak_available()
    piper_voices = _piper_voices()

    # Readiness is decided by whether a voice can actually be produced,
    # not by which packages imported. piper with no voice model on disk
    # cannot say anything, so it does not count towards being ready.
    if have_piper and piper_voices:
        default_engine = "piper"
    elif have_espeak:
        default_engine = "espeak"
    else:
        status_dict["ready"] = False
        status_dict["engine"] = None
        if have_piper and not piper_voices:
            status_dict["error"] = (
                "piper is installed but no voice model was found in "
                f"{config.PIPER_VOICE_DIR}. Say \"voice library\" to download "
                "one, or install espeak-ng for a basic fallback voice."
            )
        else:
            status_dict["error"] = (
                "no speech engine available - install espeak-ng for a basic "
                "voice, or re-run ./install.sh to install piper."
            )
        return

    # file name -> PiperVoice. Ordered, so the first key is the least
    # recently used and eviction is a popitem on the front.
    loaded = {}

    def load_voice(voice_file):
        """Returns a loaded voice, from the cache where possible."""
        existing = loaded.pop(voice_file, None)
        if existing is not None:
            loaded[voice_file] = existing      # re-inserted: now newest
            return existing
        voice = _load_piper(voice_file)
        loaded[voice_file] = voice
        while len(loaded) > MAX_LOADED_VOICES:
            loaded.pop(next(iter(loaded)))
        status_dict["loaded_voices"] = list(loaded)
        return voice

    # Warm the voice that is actually going to be used, before announcing
    # readiness. Which voice that is comes from the parent, because the
    # saved setting lives in the database and the worker has no business
    # opening it.
    if default_engine == "piper":
        warm = ""
        if preload_voice_id and preload_voice_id.startswith("piper:"):
            warm = preload_voice_id.split(":", 1)[1]
        elif piper_voices:
            warm = piper_voices[0]["id"].split(":", 1)[1]
        if warm:
            started = time.monotonic()
            try:
                load_voice(warm)
                status_dict["warm_voice"] = warm
                status_dict["warm_ms"] = round((time.monotonic() - started) * 1000)
            except Exception as e:
                # Not fatal, and not worth refusing to start over: the
                # phrase path will try again and fall back to espeak if
                # it has to. Recorded so the settings page can say so.
                status_dict["warm_error"] = repr(e)

    status_dict["ready"] = True
    status_dict["engine"] = default_engine
    status_dict["error"] = None

    while True:
        try:
            cmd = command_queue.get(timeout=0.5)
        except Exception:
            continue

        if cmd is None or cmd.get("action") == "shutdown":
            break

        # Load a voice without saying anything. Sent when a voice is
        # downloaded from the library, so the first phrase in a
        # brand-new voice is as quick as any other.
        if cmd.get("action") == "preload":
            want = (cmd.get("voice_id") or "")
            if want.startswith("piper:"):
                try:
                    load_voice(want.split(":", 1)[1])
                except Exception as e:
                    status_dict["warm_error"] = repr(e)
            continue

        if cmd.get("action") != "speak":
            continue

        text = (cmd.get("text") or "").strip()
        clip_id = cmd.get("clip_id")
        if not text or not clip_id:
            continue

        wav_path = clip_dir / f"clip_{clip_id}.wav"
        voice_id = cmd.get("voice_id") or ""
        rate = cmd.get("rate")

        # Resolve which engine this request wants. An explicitly chosen
        # voice wins; with nothing chosen, prefer piper if a voice model
        # is actually on disk, else espeak.
        engine, voice_ref = "", ""
        if voice_id.startswith("piper:"):
            engine, voice_ref = "piper", voice_id.split(":", 1)[1]
        elif voice_id.startswith("espeak:"):
            engine, voice_ref = "espeak", voice_id.split(":", 1)[1]
        elif default_engine == "piper":
            # Re-listed per request rather than cached from startup, so a
            # voice downloaded while Quietude is running gets picked up
            # without a restart.
            available = _piper_voices()
            if available:
                engine, voice_ref = "piper", available[0]["id"].split(":", 1)[1]
            elif have_espeak:
                engine, voice_ref = "espeak", ""
        elif have_espeak:
            engine, voice_ref = "espeak", ""

        try:
            if engine == "piper":
                _synthesize_piper(load_voice(voice_ref), text, wav_path,
                                  _length_scale_for(rate))
            elif engine == "espeak":
                _synthesize_espeak(text, wav_path, rate, voice_ref)
            else:
                raise RuntimeError("no usable voice")

            status_dict["clip_id"] = clip_id
            status_dict["clip_state"] = "ready"
            status_dict["engine"] = engine
            status_dict["error"] = None
            _prune_clips(clip_dir)
            result_queue.put({"clip_id": clip_id, "ok": True})

        except Exception as e:
            # One failed phrase is never fatal. Fall back to espeak if
            # piper was the thing that broke (a corrupt or half-downloaded
            # voice model is the likely cause, and espeak needs no model),
            # and only then give up on this phrase.
            if engine == "piper" and have_espeak:
                try:
                    _synthesize_espeak(text, wav_path, rate, "")
                    status_dict["clip_id"] = clip_id
                    status_dict["clip_state"] = "ready"
                    status_dict["engine"] = "espeak"
                    status_dict["error"] = f"piper failed, used espeak: {e!r}"
                    _prune_clips(clip_dir)
                    result_queue.put({"clip_id": clip_id, "ok": True})
                    continue
                except Exception as e2:
                    e = e2
            status_dict["clip_id"] = clip_id
            status_dict["clip_state"] = "failed"
            status_dict["error"] = repr(e)
            result_queue.put({"clip_id": clip_id, "ok": False, "error": repr(e)})


# ============================================================
# Parent-side API
# ============================================================

def _saved_voice_id():
    """The voice the user last saved, or None.

    Read here in the parent rather than in the worker: the worker's job
    is synthesis, and giving a child process a reason to open the
    database would be giving it a reason to care about users, settings
    and encryption keys. Failures are swallowed on purpose - on a first
    run there is no user yet, and "which voice to warm" is never worth
    failing a startup over."""
    try:
        from quietude.core import database
        user = database.get_primary_user()
        if not user:
            return None
        return (database.get_tts_settings(user) or {}).get("voice_id")
    except Exception:
        return None


def start():
    """Starts the worker. Non-blocking: nothing else in Quietude's startup
    needs to wait on a voice, and the readiness gate for TTS is a soft
    one (unlike the speech-recognition gate, which is hard - you can use
    Quietude perfectly well while she's still finding her voice, but you
    cannot use a wake word that isn't listening yet)."""
    global _process, _command_queue, _result_queue, _status_dict, _manager
    if _process is not None:
        return

    if not is_available():
        # No usable voice, so there's nothing to start - but the reason
        # still has to reach the UI. Without this the status would read
        # "not ready, no error", which tells the user nothing and sends
        # them looking in the wrong place. Mirrors what
        # speech_engine.start does for a missing model.
        _manager = multiprocessing.Manager()
        _status_dict = _manager.dict()
        _status_dict.update({
            "ready": False,
            "engine": None,
            "clip_id": None,
            "clip_state": "idle",
            "error": (
                "piper is installed but no voice model was found in "
                f"{config.PIPER_VOICE_DIR}. Say \"voice library\" to download "
                "one, or install espeak-ng for a basic fallback voice."
                if _piper_available()
                else "no speech engine available - install espeak-ng for a basic "
                     "voice, or re-run ./install.sh to install piper."
            ),
        })
        return

    config.TTS_CLIP_DIR.mkdir(parents=True, exist_ok=True)
    # Clear anything left from a previous session - a clip id from a dead
    # session is never requested again.
    for leftover in config.TTS_CLIP_DIR.glob("clip_*.wav"):
        try:
            leftover.unlink()
        except OSError:
            pass

    _manager = multiprocessing.Manager()
    _status_dict = _manager.dict()
    _status_dict.update({
        "ready": False, "engine": None, "error": None,
        "clip_id": None, "clip_state": "idle",
        # Seeded by the parent so the shape of the status dict never
        # depends on how far the worker got before something went wrong.
        "loaded_voices": [], "warm_voice": None, "warm_ms": None,
        "warm_error": None,
    })
    _command_queue = multiprocessing.Queue()
    _result_queue = multiprocessing.Queue()

    _process = multiprocessing.Process(
        target=_worker_main,
        args=(_command_queue, _result_queue, _status_dict,
              str(config.TTS_CLIP_DIR), _saved_voice_id()),
        daemon=True,
        name="quietude-tts",
    )
    _process.start()


def is_ready() -> bool:
    return bool(_status_dict and _status_dict.get("ready"))


def synthesize(text, rate=None, volume=None, voice_id=None, timeout=20.0):
    """Enqueue a phrase and wait for the worker to confirm the WAV is on
    disk. Returns the clip id to fetch, or None.

    Waits on a result queue rather than polling the shared status dict.
    The polling version slept 30ms between checks, which meant every
    phrase - however short - took at least 30ms before the HTTP response
    could even start. espeak synthesizes a sentence in under a
    millisecond, so that sleep *was* the backend's entire latency: short
    and long phrases both measured 31ms, which is the tell. Blocking on
    a queue wakes the moment the worker is done.

    Synchronous on purpose: the caller is an HTTP request about to tell
    the browser what to play, and a clip id for a file that doesn't
    exist yet is worse than a slightly slower response."""
    if not _command_queue or not is_ready() or not (text or "").strip():
        return None

    clip_id = uuid.uuid4().hex
    _command_queue.put({
        "action": "speak", "text": text, "clip_id": clip_id,
        "rate": rate, "volume": volume, "voice_id": voice_id,
    })

    deadline = time.time() + timeout
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            return None
        try:
            result = _result_queue.get(timeout=remaining)
        except Exception:
            return None
        # Results from an earlier, abandoned request (a phrase
        # interrupted by a newer one) are discarded rather than mistaken
        # for this one.
        if result.get("clip_id") != clip_id:
            continue
        return clip_id if result.get("ok") else None


def preload(voice_id):
    """Asks the worker to load a voice without speaking.

    Sent after a voice is downloaded from the library, so the first
    phrase in it isn't the one that pays the load. Fire-and-forget: the
    worker warms it when it gets to the message, and if it never does,
    the phrase path loads it as it always would."""
    if not voice_id or not str(voice_id).startswith("piper:"):
        return False
    if _process is None or _command_queue is None or not _process.is_alive():
        return False
    try:
        _command_queue.put({"action": "preload", "voice_id": voice_id})
        return True
    except Exception:
        return False


def clip_path(clip_id: str):
    """Resolve a clip id to its file, refusing anything that isn't a
    plain hex id - this value arrives from a URL, so it is not trusted to
    stay inside the cache directory on its own."""
    if not clip_id or not all(c in "0123456789abcdef" for c in clip_id):
        return None
    path = config.TTS_CLIP_DIR / f"clip_{clip_id}.wav"
    return path if path.is_file() else None


def status() -> dict:
    """available means "a voice exists"; ready means "the worker has it
    loaded". They differ only briefly at startup, or permanently when
    there's no voice at all - in which case error says why."""
    if not _status_dict:
        return {"ready": False, "engine": None, "error": None,
                "available": is_available()}
    return {
        "ready": bool(_status_dict.get("ready")),
        "engine": _status_dict.get("engine"),
        "error": _status_dict.get("error"),
        "available": is_available(),
        # What the worker is actually holding. Reported so the settings
        # page can say a voice is already warm instead of leaving you to
        # wonder why one preview starts instantly and another doesn't.
        "loaded_voices": list(_status_dict.get("loaded_voices") or []),
        "warm_voice": _status_dict.get("warm_voice"),
        "warm_ms": _status_dict.get("warm_ms"),
        "warm_error": _status_dict.get("warm_error"),
    }


def shutdown():
    """Called from the single ordered shutdown path in lifecycle.py."""
    global _process, _command_queue, _result_queue, _status_dict, _manager
    process_utils.stop_process(
        _process, sentinel_queue=_command_queue, sentinel=None,
        timeout=4.0, label="tts worker",
    )
    process_utils.stop_manager(_manager, label="tts manager")
    _process = _command_queue = _result_queue = _status_dict = _manager = None
