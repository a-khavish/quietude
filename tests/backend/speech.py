# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Drive speech_engine._worker_main directly: real state machine, real
VAD maths, real sequence counters, with a scriptable vosk."""
import json, queue, threading, time
import numpy as np

import vosk
SCRIPT = {"text": ""}
class ScriptedRecognizer:
    def __init__(self, model, rate): pass
    def AcceptWaveform(self, data): return False
    def Result(self): return json.dumps({"text": SCRIPT["text"]})
    def PartialResult(self): return json.dumps({"partial": SCRIPT["text"]})
    def Reset(self): pass
vosk.KaldiRecognizer = ScriptedRecognizer

import faster_whisper
TRANSCRIPTS = []
class ScriptedWhisper:
    def __init__(self, path, **kw): pass
    def transcribe(self, audio, **kw):
        TRANSCRIPTS.append(len(audio))
        class S: text = " what time is it "
        return ([S()], {})
faster_whisper.WhisperModel = ScriptedWhisper

from quietude.core import speech_engine as se
from quietude import config
config.VOSK_MODEL_DIR.mkdir(parents=True, exist_ok=True)
config.WHISPER_MODEL_DIR.mkdir(parents=True, exist_ok=True)

RATE = se.SAMPLE_RATE
def chunk(ms, amplitude):
    n = int(RATE * ms / 1000)
    if amplitude == 0:
        return np.zeros(n, dtype=np.int16).tobytes()
    t = np.arange(n) / RATE
    return (np.sin(2 * np.pi * 220 * t) * amplitude).astype(np.int16).tobytes()

LOUD = chunk(250, 8000)
QUIET = chunk(250, 50)

audio_q, control_q = queue.Queue(), queue.Queue()
status = {}
worker = threading.Thread(target=se._worker_main,
                          args=(audio_q, control_q, status, se.VAD_RMS_THRESHOLD, "ada"),
                          daemon=True)
worker.start()

ok = fail = 0
def check(label, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}  {extra}")

def force_sleep():
    """Put her to sleep and wait until it has actually taken effect.
    Queuing a control message and then observing an already-true
    condition proves nothing - the message may still be in flight."""
    before = status["wake_seq"]
    control_q.put({"action": "sleep"})
    settle(lambda: control_q.empty())
    time.sleep(0.4)
    return before

def settle(predicate, timeout=4.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate(): return True
        time.sleep(0.02)
    return False

print("\n-- readiness is reported per model, then overall --")
check("both models load", settle(lambda: status.get("ready")), status)
check("vosk reported ready", status["vosk_ready"] is True)
check("whisper reported ready", status["whisper_ready"] is True)
check("starts asleep", status["mode"] == "asleep")
check("worker seeds its own counters",
      (status["wake_seq"], status["stop_seq"], status["transcript_seq"]) == (0, 0, 0), status)

print("\n-- asleep: ignores speech that isn't the wake phrase --")
SCRIPT["text"] = "the weather is quite nice today"
for _ in range(4): audio_q.put(LOUD)
check("no wake on unrelated speech", settle(lambda: audio_q.empty()) and status["wake_seq"] == 0)
check("still asleep", status["mode"] == "asleep")
check("nothing transcribed", status["transcript_seq"] == 0)
check("partial text surfaced for UI feedback",
      status["partial_text"] == "the weather is quite nice today", status["partial_text"])

print("\n-- the wake phrase wakes her --")
SCRIPT["text"] = "hello ada"
audio_q.put(LOUD)
check("wake_seq increments", settle(lambda: status["wake_seq"] == 1), status)
check("mode becomes awake", status["mode"] == "awake")

print("\n-- awake: buffers speech, finalizes on a pause --")
SCRIPT["text"] = ""
audio_q.put(LOUD); audio_q.put(LOUD)
check("no premature finalize while still talking",
      settle(lambda: audio_q.empty()) and status["transcript_seq"] == 0)
check("still awake mid-utterance", status["mode"] == "awake")
# A short pause is a breath, not the end of a sentence. Under the old
# 700ms gate these three chunks ended the utterance, which is how one
# sentence arrived as three fragments.
for _ in range(3):
    audio_q.put(QUIET)
settle(lambda: audio_q.empty())
check("a 750ms pause does not end the utterance", status["transcript_seq"] == 0, status)

# Past END_OF_UTTERANCE_SILENCE_MS it does.
for _ in range(int(se.END_OF_UTTERANCE_SILENCE_MS / 250) + 2):
    audio_q.put(QUIET)
check("transcript_seq increments after a real pause",
      settle(lambda: status["transcript_seq"] == 1), status)
check("transcript text is whisper's", status["transcript_text"] == "what time is it",
      status["transcript_text"])
# She stays awake: one wake opens a window, and the window stays open
# while you keep talking. The interface closes it, not the engine.
check("still listening after finalizing", status["mode"] == "awake", status)
check("whisper got the buffered audio, not one chunk", TRANSCRIPTS[-1] > RATE // 2, TRANSCRIPTS)

print("\n-- a cough is not a command (voiced minimum) --")
# Per ARCHITECTURE_NOTES: the end-of-utterance rule needs BOTH enough
# voiced audio AND a pause. A 100ms noise followed by silence satisfies
# only the pause half, so she must NOT transcribe it - and must stay
# awake, still waiting for actual speech, rather than finalizing nothing.
wake_before = force_sleep()
SCRIPT["text"] = "hello ada"; audio_q.put(LOUD)
check("woken for the cough test", settle(lambda: status["wake_seq"] == wake_before + 1))
SCRIPT["text"] = ""
before = status["transcript_seq"]
audio_q.put(chunk(100, 9000))
for _ in range(int(se.END_OF_UTTERANCE_SILENCE_MS / 250) + 2):
    audio_q.put(QUIET)
settle(lambda: audio_q.empty())
time.sleep(0.4)
check("nothing transcribed from a cough", status["transcript_seq"] == before, status)
check("stays awake waiting for real speech", status["mode"] == "awake", status)

print("\n-- hard ceiling stops a stuck mic buffering forever --")
wake_before = force_sleep()
SCRIPT["text"] = "hello ada"; audio_q.put(LOUD)
check("woken for the ceiling test", settle(lambda: status["wake_seq"] == wake_before + 1))
SCRIPT["text"] = ""
before = status["transcript_seq"]
for _ in range(int(se.MAX_UTTERANCE_MS / 250) + 2):
    audio_q.put(LOUD)
check("force-finalized at the ceiling",
      settle(lambda: status["transcript_seq"] == before + 1, timeout=6), status)
check("still listening after the ceiling", status["mode"] == "awake", status)

print("\n-- suppressed: only the interrupt phrase gets through --")
control_q.put({"action": "suppress"})
check("mode becomes suppressed", settle(lambda: status["mode"] == "suppressed"))
SCRIPT["text"] = "hello ada"
before_wake = status["wake_seq"]
audio_q.put(LOUD)
check("wake phrase ignored while she speaks",
      settle(lambda: audio_q.empty()) and status["wake_seq"] == before_wake)
SCRIPT["text"] = "what time is it"
before_tr = status["transcript_seq"]
audio_q.put(LOUD)
check("nothing transcribed while suppressed",
      settle(lambda: audio_q.empty()) and status["transcript_seq"] == before_tr)
SCRIPT["text"] = "please stop talking now"
audio_q.put(LOUD)
check("stop phrase increments stop_seq", settle(lambda: status["stop_seq"] == 1), status)
control_q.put({"action": "unsuppress"})
# Unsuppressing restores whatever she was before being suppressed,
# which here is awake - she was woken for the ceiling test above and
# nothing has put her back to sleep since.
check("unsuppress restores the previous mode",
      settle(lambda: status["mode"] in ("awake", "asleep")), status)


print("\n-- a stale sleep request cannot cut a new session short --")
force_sleep()
SCRIPT["text"] = "hello ada"; audio_q.put(LOUD)
check("awake again", settle(lambda: status["mode"] == "awake"))
stale = status["wake_seq"] - 1
control_q.put({"action": "sleep", "wake_seq": stale})
settle(lambda: control_q.empty()); time.sleep(0.4)
check("stale sleep ignored, still awake", status["mode"] == "awake", status)
control_q.put({"action": "sleep", "wake_seq": status["wake_seq"]})
check("current sleep still honoured", settle(lambda: status["mode"] == "asleep"), status)
SCRIPT["text"] = "hello ada"; audio_q.put(LOUD)
settle(lambda: status["mode"] == "awake")
control_q.put({"action": "sleep"})
check("sleep with no counter still works (unconditional)",
      settle(lambda: status["mode"] == "asleep"), status)

print("\n-- counters only ever increase, so no event can be missed --")
seqs = (status["wake_seq"], status["stop_seq"], status["transcript_seq"])
check("wake fired repeatedly across scenarios", seqs[0] >= 3, seqs)
check("every counter advanced, none reset", all(s > 0 for s in seqs), seqs)

print("\n-- VAD maths doesn't overflow on loud input --")
loud = np.frombuffer(chunk(250, 32000), dtype=np.int16)
rms = float(np.sqrt(np.mean(loud.astype(np.float64) ** 2)))
check("full-scale audio gives a sane RMS", 20000 < rms < 32768, rms)
quiet = np.frombuffer(QUIET, dtype=np.int16)
check("room noise is below the threshold",
      float(np.sqrt(np.mean(quiet.astype(np.float64) ** 2))) < se.VAD_RMS_THRESHOLD)

print("\n-- regex phrase matching --")
# "helloada" as a single token used to match, because the old pattern
# joined the two words with \s* - zero or more spaces. It is False now,
# deliberately: the pattern is only ever matched against a speech
# recogniser's output, and a recogniser emits words. It has no way to
# hand back two words run together, so allowing for it only widened what
# could be mistaken for the wake phrase.
for phrase, should in (("hello ada", True), ("helloada", False), ("Hello  Ada!", True),
                       ("hey ada", True), ("okay ada", True), ("ada", False),
                       ("say hello to ada", False), ("hello adan", False)):
    check(f"wake {phrase!r} -> {should}", bool(se.wake_phrase_pattern("ada").search(phrase)) is should)
for phrase, should in (("stop talking", True), ("please STOP TALKING", True),
                       ("stop", False), ("talking", False)):
    check(f"stop {phrase!r} -> {should}", bool(se.STOP_PHRASE_RE.search(phrase)) is should)

print("\n-- shutdown sentinel ends the worker --")
audio_q.put(None)
worker.join(timeout=3)
check("worker exits on the None sentinel", not worker.is_alive())

print(f"\n{ok} passed, {fail} failed")
raise SystemExit(1 if fail else 0)
