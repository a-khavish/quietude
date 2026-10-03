# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
speech_engine.py
Local, fully offline speech recognition: a wake word that's always
listening, and accurate transcription once it's been woken.

This replaces the browser's Web Speech API, which needed an internet
connection, sent every utterance to Google for transcription, and was
unreliable in practice. Built to the design in ARCHITECTURE_NOTES.md,
deliberately mirroring tts_engine.py's worker-process shape - that
consistency is worth more than a marginally better structure for one of
the two, since anyone reading one engine can then read the other.

Why two models
--------------
Vosk is small, streaming and cheap enough to run on every audio chunk
forever, but not very accurate. faster-whisper is an int8-quantized
Whisper that is accurate but transcribes a finished clip in one shot
rather than a live stream. So each does the job it suits:

    Vosk            always on. Watches for two short fixed phrases -
                    "hello quietude" to wake, "stop talking" to interrupt.
                    Low accuracy doesn't matter for a known phrase.
    faster-whisper   runs once per utterance, on buffered audio, after a
                    pause is detected. This is the transcription that
                    actually becomes a command, so it's the one that has
                    to be right.

Both live in one worker process, loaded once at startup. Same reasoning
as the TTS worker: model memory and native-library threads stay out of
the Flask process, and a crash in either model doesn't take Quietude down.

Audio arrives as 16kHz mono 16-bit PCM, already downsampled in the
browser by an AudioWorklet (see frontend/src/worklets/pcm-processor.js).
Capture stayed browser-side rather than moving to PipeWire: it keeps the
mic behind the browser's permission prompt, means this process never
opens an audio device, and the DSP involved is a linear-interpolation
resample that costs nothing on the main thread. The latency difference
against a direct PipeWire capture is a few tens of milliseconds, against
a pipeline whose next step is a Whisper inference measured in hundreds.
"""

import json
import multiprocessing
import re
import time

import numpy as np

from quietude import config
from quietude.core import model_catalog
from quietude import process_utils

SAMPLE_RATE = 16000

# Phrase detection: regex on the recognizer's text. Fast, predictable,
# and entirely sufficient for two fixed short phrases.
STOP_PHRASE_RE = re.compile(r"\bstop\s*talking\b", re.IGNORECASE)

# The words that can precede the assistant's name to wake her. Kept
# short and deliberately not including the bare name on its own: a name
# like "Sam" or "Ada" occurs in ordinary conversation, and a wake word
# that fires on it would have her interrupting constantly.
WAKE_PREFIXES = ("hello", "hey", "ok", "okay", "hi")


def wake_phrase_pattern(name):
    """A regex matching "hello <name>", for whatever name the user chose.

    Built rather than hardcoded because the assistant's name is the
    user's to pick, and the wake word is the single place that choice
    has a mechanical consequence rather than a cosmetic one.

    Vosk returns lowercase words with no punctuation, so matching is
    case-insensitive and tolerant of the spacing a recogniser puts
    between words. A multi-word name is matched with flexible spacing
    for the same reason."""
    clean = " ".join((name or "").split())
    if not clean:
        return None
    words = [re.escape(w) for w in clean.split(" ")]
    name_re = r"\s+".join(words)
    prefixes = "|".join(WAKE_PREFIXES)
    return re.compile(rf"\b(?:{prefixes})\s+{name_re}\b", re.IGNORECASE)

# An utterance ends when the speaker has said something substantial and
# then paused. Both halves matter: the voiced minimum stops a cough or a
# door closing from being transcribed as a command, and the silence
# window is what "stopped talking" actually means.
MIN_UTTERANCE_VOICED_MS = 300

# Three seconds, where this was 700 milliseconds.
#
# 700ms is not a pause, it is a breath. People pause mid-sentence to
# think, to find a word, to say a number - and every one of those ended
# the utterance, so a single sentence arrived as three fragments, each
# transcribed separately and sent as its own command. Fragments shorter
# than MIN_UTTERANCE_VOICED_MS were dropped on the floor without a
# trace. From the outside: "not everything I say gets through, and what
# does get through is cut up."
#
# Three seconds is long enough that no natural pause reaches it and
# short enough that nobody waits for it. The cost is three seconds of
# latency on every command, which is the right thing to pay: a command
# that arrives late is better than half a command that arrives quickly.
END_OF_UTTERANCE_SILENCE_MS = 3000

# The ceiling, and the thing that keeps her sending when the gate never
# closes.
#
# It was thirty seconds, on the grounds that an utterance with a couple
# of thinking pauses in it legitimately runs long. True - but it is also
# the only way an utterance ends when the room never goes quiet enough
# for the gate to find a gap, which is the common case with a fan, a
# desktop tower, or a microphone with its gain wound up. Thirty seconds
# of that is thirty seconds of saying things and watching nothing
# happen, and the interface gives up before it ever arrives.
#
# Fifteen seconds is longer than anybody's sentence and short enough
# that something comes back while you are still in the conversation. A
# monologue arrives as several messages rather than one, which is the
# right trade: continuous and slightly chopped beats whole and absent.
MAX_UTTERANCE_MS = 15000

# How long suppression may last before the worker lifts it itself.
#
# Suppression is "stop listening, she is talking", and it is lifted by
# the interface when the audio finishes. Which means her hearing was
# tied to a promise resolving in a browser - and when that promise did
# not resolve, she stayed deaf. Not for a moment: until the application
# was restarted. Every reply after that point was spoken into a
# microphone that had been switched off, and nothing the user said went
# anywhere. It is the worst shape a bug can have, because the symptom is
# silence and silence looks like not listening hard enough.
#
# So the worker keeps its own clock. The interface says how long it
# expects to be speaking for when it asks, and that becomes a deadline
# here; past it, she goes back to listening whether or not anyone said
# she could. A phrase cut off a second early is a small cost. Being deaf
# for the rest of the session is not.
SUPPRESS_CEILING_MS = 20000
SUPPRESS_MAX_MS = 180000

# ---------------- deciding when someone is speaking ----------------
#
# This was a single fixed number - RMS 500 on int16, about -36 dBFS -
# and it was wrong in both directions depending on the microphone.
#
# Too high for a quiet speaker or a low-gain mic: voiced_ms never
# reaches MIN_UTTERANCE_VOICED_MS, the utterance never finalises, and
# nothing is ever transcribed. From the outside that is indistinguishable
# from the assistant simply not hearing you, which is exactly how it was
# reported. Too low in a room with a fan or a desktop PC: every chunk
# counts as speech, silence never accumulates, and the utterance runs to
# the 15-second ceiling before anything is transcribed.
#
# No single number can be right for both, because the thing that varies
# is not the speech - it is the floor underneath it. So the floor is
# measured continuously and the threshold sits above it.
#
# The floor rises slowly and falls quickly on purpose: rising slowly
# means a few seconds of speech cannot drag the floor up and deafen her
# mid-sentence, and falling quickly means a door slamming does not leave
# her deaf for the next minute.
NOISE_FLOOR_INITIAL = 120.0
NOISE_FLOOR_RISE = 0.015     # per chunk, towards a louder room
NOISE_FLOOR_FALL = 0.200     # per chunk, towards a quieter one

# The slow rise above is right for steady-state, and wrong for the first
# couple of seconds: starting from a guess of 120 in a room that is
# actually at 400, it would take the better part of a minute to catch
# up, and until it did the gate would sit under the room and every chunk
# would count as speech. So the first few chunks are learned quickly and
# the slow behaviour takes over once there is an estimate worth
# protecting.
NOISE_FLOOR_WARMUP_CHUNKS = 10
NOISE_FLOOR_WARMUP_ALPHA = 0.35
NOISE_FLOOR_MIN = 25.0       # a digital-silence mic shouldn't make the gate paranoid
NOISE_FLOOR_MAX = 4000.0

# Speech has to be this many times the measured floor. Scaled by the
# user's sensitivity setting, which is the one knob worth exposing.
VAD_MARGIN = 2.6

# An absolute floor under all of it. Below this it is not speech on any
# microphone, however quiet the room is.
VAD_ABSOLUTE_MIN = 70.0

# Once someone is speaking, they stay speaking down to this fraction of
# the threshold. Without the hysteresis, the quiet part between two words
# dips below the gate and gets counted as the end of the sentence.
VAD_RELEASE_RATIO = 0.55

# ...but never below the room itself. In a noisy room the released gate
# can land *under* the background level, and then the background holds
# the utterance open: the speaker stops, the room doesn't, silence never
# accumulates and the sentence runs to the fifteen-second ceiling before
# anything is transcribed. Found by testing a normal speaker in a noisy
# room, which is the case that exposes it.
VAD_RELEASE_FLOOR_MARGIN = 1.25

# Kept only as the fallback for QUIETUDE_VAD_THRESHOLD, which pins the
# gate to a fixed value for anyone who needs to.
VAD_RMS_THRESHOLD = 500

SENSITIVITY_MIN = 1
SENSITIVITY_MAX = 10
SENSITIVITY_DEFAULT = 5


def margin_for_sensitivity(level):
    """Turns the 1-10 setting into a multiple of the noise floor.

    Higher sensitivity means a smaller multiple, so quieter sound counts
    as speech. 5 is VAD_MARGIN exactly; the ends are roughly half and
    double that."""
    try:
        level = int(level)
    except (TypeError, ValueError):
        level = SENSITIVITY_DEFAULT
    level = max(SENSITIVITY_MIN, min(SENSITIVITY_MAX, level))
    # 1 -> 2x the margin (least sensitive), 10 -> 0.5x (most)
    return VAD_MARGIN * (2.0 - 1.5 * (level - 1) / (SENSITIVITY_MAX - 1))

_process = None
_audio_queue = None
_control_queue = None
_status_dict = None
_manager = None


def is_available() -> bool:
    try:
        import vosk  # noqa: F401
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def models_present() -> tuple:
    """(wake, transcription) - whether each model is actually on disk.

    Asks the model chooser rather than looking at the legacy directories
    directly: a model installed from inside the app lives under its own
    key, and checking only the installer's flat directory would report
    "not downloaded" for a model the user had just downloaded and
    selected. active_model_dir falls back to the legacy directory, so an
    install that predates the chooser keeps working untouched."""
    return (bool(model_catalog.active_model_dir("wake")),
            bool(model_catalog.active_model_dir("transcribe")))




# ============================================================
# Worker process
# ============================================================

def _worker_main(audio_queue, control_queue, status_dict, vad_threshold,
                 wake_name="", sensitivity=SENSITIVITY_DEFAULT):
    """Runs entirely in the child process.

    Loads both models before accepting audio, publishing each one's
    readiness separately so the frontend's boot screen can say which one
    it's still waiting on rather than showing an undifferentiated spinner
    for what can be a minute on slow hardware."""
    try:
        import vosk
        vosk.SetLogLevel(-1)  # its default chatter is substantial and tells us nothing
        vosk_model = vosk.Model(str(model_catalog.active_model_dir("wake")))
        recognizer = vosk.KaldiRecognizer(vosk_model, SAMPLE_RATE)
        status_dict["vosk_ready"] = True
    except Exception as e:
        status_dict["error"] = f"wake-word model failed to load: {e!r}"
        status_dict["vosk_ready"] = False
        return

    try:
        from faster_whisper import WhisperModel
        whisper = WhisperModel(
            str(model_catalog.active_model_dir("transcribe")),
            device="cpu",
            compute_type="int8",
            local_files_only=True,  # never reach for the network, even if a file is missing
        )
        status_dict["whisper_ready"] = True
    except Exception as e:
        status_dict["error"] = f"transcription model failed to load: {e!r}"
        status_dict["whisper_ready"] = False
        return

    # The worker seeds every field it owns rather than trusting the
    # parent to have done it. start() does seed them, but then the
    # contract would depend on two places agreeing - and a missing
    # counter here would read as "no events yet" forever rather than
    # failing loudly.
    status_dict.update({
        "ready": True,
        "mode": "asleep",
        "error": None,
        "wake_seq": status_dict.get("wake_seq") or 0,
        "stop_seq": status_dict.get("stop_seq") or 0,
        "transcript_seq": status_dict.get("transcript_seq") or 0,
        "transcript_text": status_dict.get("transcript_text") or "",
        "partial_text": "",
        "level": 0.0,
        "noise_floor": NOISE_FLOOR_INITIAL,
        "vad_threshold": 0.0,
        "voiced": False,
        "sensitivity": sensitivity,
    })

    # The wake phrase is built from the assistant's name, which the user
    # chooses and can change while she is running - so it lives in a
    # variable the control loop can replace, not in a module constant.
    wake_re = wake_phrase_pattern(wake_name)
    status_dict["wake_name"] = wake_name
    status_dict["wake_phrase"] = f"{WAKE_PREFIXES[0]} {wake_name}" if wake_name else ""

    # Voice-activity state. noise_floor is the running estimate of the
    # room; was_voiced carries the hysteresis across chunks.
    noise_floor = NOISE_FLOOR_INITIAL
    quiet_chunks = 0          # how much silence the floor has been learned from
    was_voiced = False
    margin = margin_for_sensitivity(sensitivity)
    # A pinned threshold overrides all of it, for anyone who needs it.
    pinned = vad_threshold if vad_threshold else None

    mode = "asleep"
    # Suppression is a temporary overlay, not a state change: Quietude speaks
    # for a couple of seconds in the middle of a session and then has to
    # go back to whatever she was doing. So the mode underneath it is
    # remembered, because the common case is being suppressed *while
    # awake* - she acknowledges the wake word, and the user's actual
    # command comes next. Returning to "asleep" unconditionally would
    # end the session she just opened, and the user would be talking to
    # something that had stopped listening.
    mode_before_suppress = "asleep"
    # When the worker stops waiting to be told she has finished
    # speaking. See SUPPRESS_CEILING_MS.
    suppress_until = 0.0
    # Whether her name counts as an interrupt during this phrase. Off
    # when the phrase being spoken contains her name, so that reading
    # out "say Hello Ada to wake me" does not wake her.
    suppress_wake_ok = False
    buffer = []        # raw int16 bytes for the utterance in progress
    buffered_ms = 0.0
    voiced_ms = 0.0
    silence_ms = 0.0
    # The second opinion on whether anyone is speaking.
    #
    # voiced_ms is the RMS gate's view, and the gate can be wrong: a
    # quiet speaker, a low-gain microphone, a room the noise floor
    # settled too high in. When it is wrong it is wrong silently - the
    # utterance never reaches MIN_UTTERANCE_VOICED_MS, nothing is ever
    # transcribed, and she simply stops answering with no way to tell
    # why. words_ms is how long the wake-word recogniser has been
    # producing new words for, which is evidence of speech arrived at a
    # completely different way. Either one is enough to carry an
    # utterance, so one of them being miscalibrated costs accuracy
    # rather than everything.
    words_ms = 0.0
    last_live = ""

    def reset_utterance():
        nonlocal buffer, buffered_ms, voiced_ms, silence_ms, words_ms, last_live
        buffer = []
        buffered_ms = voiced_ms = silence_ms = words_ms = 0.0
        last_live = ""
        publish_utterance()

    def publish_utterance():
        """How much of a sentence is currently in hand.

        The interface needs this, and not having it is what made her
        interrupt people. From outside the worker, "awake and nothing
        has been transcribed for twelve seconds" looked exactly the same
        whether the room was silent or somebody was halfway through a
        long sentence - so she would ask "did you want to say
        something?" over the top of them, and then go to sleep eight
        seconds later having thrown the sentence away.

        speech_ms is the evidence that a person is talking, by either
        route: the level gate, or the wake-word model producing words.
        Either one is enough, for the same reason finalize_utterance
        takes the larger of the two."""
        status_dict["utterance_ms"] = round(buffered_ms)
        status_dict["utterance_speech_ms"] = round(max(voiced_ms, words_ms))
        status_dict["utterance_silence_ms"] = round(silence_ms)

    def set_mode(new_mode):
        nonlocal mode, mode_before_suppress
        if new_mode == "suppressed" and mode != "suppressed":
            mode_before_suppress = mode
        mode = new_mode
        status_dict["mode"] = new_mode

    def bump(counter):
        status_dict[counter] = (status_dict.get(counter) or 0) + 1

    def dropped(reason):
        """Somebody spoke and it did not become a message.

        Every one of these used to be a bare `return`. From the user's
        side that is not a failure, it is silence - she simply stops
        answering, with nothing on screen and nothing in a log to say
        why. It is the worst possible way for this to fail, and it is
        how it failed for a long time.

        Only called when there was real speech in the utterance.
        A cough, a door, a chair - those are supposed to be dropped
        without comment, and saying "I didn't catch that" after every
        one of them would be worse than saying nothing."""
        status_dict["dropped_reason"] = reason
        bump("dropped_seq")

    def finalize_utterance():
        """Hand the buffered audio to Whisper and listen for the next
        thing.

        It used to go back to asleep here, so every sentence needed the
        wake word in front of it. It no longer does: one wake opens a
        window and the window stays open while you keep talking, which
        is the difference between issuing commands and having a
        conversation.

        Who closes it, then. The interface does, from
        useVoiceChat.checkAwakeSilence - it asks after a stretch of
        nothing and sleeps after that, and it sleeps outright when voice
        chat is turned off. Keeping that decision on one side rather
        than splitting it means there is one place to look when she is
        listening and should not be.

        The audio and the voiced total are both read out before
        reset_utterance() clears them, since the decision about whether
        this utterance is worth transcribing depends on the counter it is
        about to zero."""
        pcm = b"".join(buffer)
        voiced = max(voiced_ms, words_ms)
        reset_utterance()
        # Still awake. The recogniser is reset so the next utterance
        # starts clean, but the mode does not change - see above.
        recognizer.Reset()
        status_dict["partial_text"] = ""

        if not pcm:
            return
        if voiced < MIN_UTTERANCE_VOICED_MS:
            # Too short to be speech. Silent on purpose - see dropped().
            return

        try:
            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            segments, _info = whisper.transcribe(
                audio,
                language="en",
                # Was greedy, on the grounds that this is one short
                # command rather than prose. It is - and greedy decoding
                # is exactly where a short command suffers, because
                # there is no surrounding sentence to recover from a
                # wrong first guess. Five beams costs a fraction of a
                # second on an utterance this size and is the single
                # cheapest accuracy there is here.
                beam_size=5,
                # Each utterance stands alone. Conditioning on the last
                # one makes Whisper repeat it when the next is unclear,
                # which turns a misheard command into the previous
                # command run twice.
                condition_on_previous_text=False,
                vad_filter=False,  # the RMS gate above already decided where it ends
            )
            text = " ".join(seg.text.strip() for seg in segments).strip()
        except Exception as e:
            status_dict["error"] = f"transcription failed: {e!r}"
            dropped("Something went wrong turning that into words. "
                    f"({e.__class__.__name__})")
            return

        if text:
            status_dict["error"] = None
            status_dict["transcript_text"] = text
            bump("transcript_seq")
        else:
            # Heard, long enough to be speech, and the model made
            # nothing of it. Usually too quiet, too far from the
            # microphone, or a model too small for the room.
            dropped("I heard you, but I couldn't make out the words. "
                    "Try speaking a little closer to the microphone.")

    while True:
        # Control messages are drained first and completely: a suppress
        # that arrives while audio is backed up has to take effect before
        # the next chunk is judged, or Quietude hears her own voice.
        while True:
            try:
                msg = _nowait(control_queue)
            except Exception:
                break
            if msg is None:
                break
            action = msg.get("action")
            if action == "suppress":
                asked = msg.get("max_ms")
                try:
                    asked = float(asked) if asked else SUPPRESS_CEILING_MS
                except (TypeError, ValueError):
                    asked = SUPPRESS_CEILING_MS
                asked = max(1000.0, min(float(SUPPRESS_MAX_MS), asked))
                suppress_until = time.monotonic() + asked / 1000.0
                suppress_wake_ok = bool(msg.get("wake_ok"))
                set_mode("suppressed")
                reset_utterance()
                recognizer.Reset()
                status_dict["partial_text"] = ""
            elif action == "unsuppress":
                if mode == "suppressed":
                    # Back to whatever she was doing before she spoke -
                    # which is usually "awake", waiting for the command
                    # that follows the wake word she just acknowledged.
                    # The utterance buffer is reset either way: anything
                    # captured while suppressed was her own voice.
                    set_mode(mode_before_suppress)
                    reset_utterance()
                    recognizer.Reset()
                    status_dict["partial_text"] = ""
            elif action == "identity":
                # The assistant's name changed while she was running.
                # Rebuilt here rather than requiring a restart, because
                # the settings page applies everything else live and a
                # wake word that needed a restart would be the one thing
                # that didn't.
                wake_name = msg.get("name") or ""
                wake_re = wake_phrase_pattern(wake_name)
                status_dict["wake_name"] = wake_name
                status_dict["wake_phrase"] = (
                    f"{WAKE_PREFIXES[0]} {wake_name}" if wake_name else "")
                recognizer.Reset()
                status_dict["partial_text"] = ""
            elif action == "sensitivity":
                margin = margin_for_sensitivity(msg.get("level"))
                status_dict["sensitivity"] = msg.get("level")
                # Re-learn the room rather than carrying an estimate made
                # under the old setting, and re-enter the fast warm-up so
                # that takes a second rather than a minute.
                noise_floor = NOISE_FLOOR_INITIAL
                quiet_chunks = 0
            elif action == "sleep":
                # The frontend decides when an awake-but-silent session
                # should lapse, so this arrives on its timer - which can
                # fire at the same moment the user finally says "hello
                # quietude". Without a guard, the stale request lands just
                # after the wake and puts her straight back to sleep: she
                # chirps, then goes dead, and the user has no idea why.
                #
                # So a sleep request carries the wake_seq its sender had
                # last seen. If she has woken since, the request was
                # about a session that no longer exists and is dropped.
                # Same counter-comparison idea as the event polling, just
                # pointed the other way down the wire.
                seen_wake = msg.get("wake_seq")
                current_wake = status_dict.get("wake_seq") or 0
                if seen_wake is not None and seen_wake < current_wake:
                    continue
                # Whatever was said before the lights went out still
                # gets through. A sleep can land on top of a sentence -
                # the interface times out while somebody is still
                # talking, or voice chat is switched off mid-word - and
                # throwing the audio away means the speaker watched
                # their words go nowhere with no way to tell why.
                # finalize_utterance drops anything too short to be
                # speech, so this costs nothing when the buffer really
                # is just room noise.
                finalize_utterance()
                set_mode("asleep")
                recognizer.Reset()
                status_dict["partial_text"] = ""

        # Has she been quiet for longer than she said she would be?
        #
        # Checked here rather than on a timer because this loop runs at
        # least every 300ms anyway, and a second thread for one
        # comparison is a second thing to reason about.
        if mode == "suppressed" and time.monotonic() > suppress_until:
            status_dict["error"] = None
            set_mode(mode_before_suppress)
            reset_utterance()
            recognizer.Reset()
            status_dict["partial_text"] = ""

        try:
            chunk = audio_queue.get(timeout=0.3)
        except Exception:
            continue

        if chunk is None:  # shutdown sentinel
            break
        if not chunk:
            continue

        samples = np.frombuffer(chunk, dtype=np.int16)
        if samples.size == 0:
            continue
        chunk_ms = (samples.size / SAMPLE_RATE) * 1000.0
        # float64 before squaring: int16 squared overflows, and a silent
        # overflow here shows up as a VAD that fires on quiet rooms.
        rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))

        if pinned is not None:
            threshold = pinned
            is_voiced = rms >= threshold
        else:
            threshold = max(VAD_ABSOLUTE_MIN, noise_floor * margin)
            # Hysteresis: it takes more to start speaking than to keep
            # speaking, so the dip between two words isn't read as the
            # end of the sentence.
            release = max(threshold * VAD_RELEASE_RATIO,
                          noise_floor * VAD_RELEASE_FLOOR_MARGIN)
            gate = release if was_voiced else threshold
            is_voiced = rms >= gate
            if not is_voiced:
                # The floor is only ever learned from silence. Learning
                # it from speech would let a long sentence raise the bar
                # until she stopped hearing the end of it.
                if quiet_chunks < NOISE_FLOOR_WARMUP_CHUNKS:
                    alpha = NOISE_FLOOR_WARMUP_ALPHA
                else:
                    alpha = NOISE_FLOOR_FALL if rms < noise_floor else NOISE_FLOOR_RISE
                quiet_chunks += 1
                noise_floor = (1 - alpha) * noise_floor + alpha * rms
                noise_floor = min(NOISE_FLOOR_MAX, max(NOISE_FLOOR_MIN, noise_floor))
        was_voiced = is_voiced

        # Published every chunk: this is what drives the level meter, and
        # it is the difference between "she isn't hearing me" being a
        # mystery and being a number you can look at.
        status_dict["level"] = round(rms, 1)
        status_dict["noise_floor"] = round(noise_floor, 1)
        status_dict["vad_threshold"] = round(threshold, 1)
        status_dict["voiced"] = bool(is_voiced)

        if mode == "asleep":
            heard = ""
            if recognizer.AcceptWaveform(chunk):
                heard = json.loads(recognizer.Result()).get("text", "")
                status_dict["partial_text"] = ""
            else:
                partial = json.loads(recognizer.PartialResult()).get("partial", "")
                status_dict["partial_text"] = partial
                heard = partial

            if heard and wake_re is not None and wake_re.search(heard):
                recognizer.Reset()
                reset_utterance()
                status_dict["partial_text"] = ""
                set_mode("awake")
                bump("wake_seq")

        elif mode == "awake":
            buffer.append(chunk)
            buffered_ms += chunk_ms

            # Vosk runs here too, purely so there is something on screen
            # while you are still talking. Whisper is still what decides
            # what you actually said - it is the accurate one, and it
            # needs the whole utterance before it can do anything - but
            # waiting for it in silence is what made this feel like it
            # wasn't listening. Vosk is streaming and costs almost
            # nothing, so the words appear as they are spoken and are
            # then replaced by the better transcription.
            if recognizer.AcceptWaveform(chunk):
                live = json.loads(recognizer.Result()).get("text", "")
            else:
                live = json.loads(recognizer.PartialResult()).get("partial", "")
            if live:
                status_dict["partial_text"] = live

            # A *change* in what it has heard, not merely having heard
            # something: a partial result stays on the wire after you
            # stop talking, so treating a non-empty partial as activity
            # would mean the utterance never ends.
            words_arrived = bool(live) and live != last_live
            last_live = live

            if is_voiced or words_arrived:
                if is_voiced:
                    voiced_ms += chunk_ms
                if words_arrived:
                    words_ms += chunk_ms
                silence_ms = 0.0
            else:
                silence_ms += chunk_ms

            publish_utterance()

            long_enough = (voiced_ms >= MIN_UTTERANCE_VOICED_MS
                           or words_ms >= MIN_UTTERANCE_VOICED_MS)
            ended = long_enough and silence_ms >= END_OF_UTTERANCE_SILENCE_MS
            if ended or buffered_ms >= MAX_UTTERANCE_MS:
                finalize_utterance()

        elif mode == "suppressed":
            # Quietude is speaking. Only an interrupt matters; everything else
            # heard here is her own voice bleeding through the mic.
            heard = ""
            if recognizer.AcceptWaveform(chunk):
                heard = json.loads(recognizer.Result()).get("text", "")
            else:
                heard = json.loads(recognizer.PartialResult()).get("partial", "")

            if heard and STOP_PHRASE_RE.search(heard):
                recognizer.Reset()
                bump("stop_seq")

            # Her name, said over the top of her, means the same as
            # "stop talking" and then some: stop, and listen.
            #
            # A long answer read out in full is thirty or forty seconds
            # of her talking into a microphone she has switched off,
            # and until this there was no way back into the
            # conversation except waiting her out. Saying her name is
            # what a person does to interrupt another person, so it is
            # what this listens for.
            #
            # Only when the phrase being spoken does not contain her
            # name - otherwise reading out "say Hello Ada to wake me"
            # wakes her, every time, which is a worse bug than the one
            # this fixes. The interface works that out and says so when
            # it asks for suppression.
            elif (suppress_wake_ok and heard and wake_re is not None
                  and wake_re.search(heard)):
                recognizer.Reset()
                reset_utterance()
                status_dict["partial_text"] = ""
                # Stop first, so the audio is cut before she is told
                # anyone is listening.
                bump("stop_seq")
                set_mode("awake")
                bump("wake_seq")


def _nowait(queue):
    """get_nowait() that returns None on an empty queue instead of
    raising, so the drain loop above reads as a drain loop."""
    from queue import Empty
    try:
        return queue.get_nowait()
    except Empty:
        return None


# ============================================================
# Parent-side API
# ============================================================

def start(wake_name="", sensitivity=SENSITIVITY_DEFAULT):
    """Starts the worker and returns immediately.

    `wake_name` is the assistant's name, which is what the wake phrase is
    built from. It is passed in rather than read here because the name
    lives in the database, and a speech engine has no business opening
    one. Both it and the sensitivity can be changed afterwards without a
    restart - see set_identity and set_sensitivity.
 Loading both models
    takes real time - which is exactly why this is kicked off at launch,
    so it happens behind the agreement screen, setup wizard and login
    rather than in front of someone staring at a progress message."""
    global _process, _audio_queue, _control_queue, _status_dict, _manager
    if _process is not None:
        return

    _manager = multiprocessing.Manager()
    _status_dict = _manager.dict()
    _status_dict.update({
        "ready": False, "vosk_ready": False, "whisper_ready": False, "error": None,
        "mode": "asleep", "wake_seq": 0, "stop_seq": 0, "transcript_seq": 0,
        "transcript_text": "", "partial_text": "",
        "dropped_seq": 0, "dropped_reason": "",
        "utterance_ms": 0, "utterance_speech_ms": 0, "utterance_silence_ms": 0,
        "level": 0.0, "noise_floor": NOISE_FLOOR_INITIAL, "vad_threshold": 0.0,
        "voiced": False, "sensitivity": sensitivity,
        "wake_name": wake_name,
        "wake_phrase": f"{WAKE_PREFIXES[0]} {wake_name}" if wake_name else "",
    })

    if not is_available():
        _status_dict["error"] = (
            "speech packages not installed - run install.sh, or pip install "
            "vosk faster-whisper in the virtualenv"
        )
        return

    vosk_ok, whisper_ok = models_present()
    if not (vosk_ok and whisper_ok):
        missing = ", ".join(
            n for n, ok in (("wake-word (vosk)", vosk_ok), ("transcription (whisper)", whisper_ok))
            if not ok
        )
        _status_dict["error"] = (
            f"speech models not downloaded: {missing}. Run ./install.sh --models-only "
            "to fetch them."
        )
        return

    # A pinned threshold is an escape hatch, not the normal path: unset,
    # the gate measures the room and sits above it. See the constants.
    import os
    pinned = os.environ.get("QUIETUDE_VAD_THRESHOLD")
    try:
        threshold = float(pinned) if pinned else 0.0
    except ValueError:
        threshold = 0.0

    _audio_queue = multiprocessing.Queue(maxsize=256)
    _control_queue = multiprocessing.Queue()
    _process = multiprocessing.Process(
        target=_worker_main,
        args=(_audio_queue, _control_queue, _status_dict, threshold,
              wake_name, sensitivity),
        daemon=True,
        name="quietude-speech",
    )
    _process.start()


def is_ready() -> bool:
    return bool(_status_dict and _status_dict.get("ready"))


def readiness() -> tuple:
    """(ready, human-readable detail) for the assistant's "turn on voice
    chat" check and for the boot gate's progress text."""
    if not _status_dict:
        return False, "The speech engine was never started."
    if _status_dict.get("ready"):
        return True, "Speech engine ready."
    error = _status_dict.get("error")
    if error:
        return False, error
    if not _status_dict.get("vosk_ready"):
        return False, "Loading wake-word model..."
    if not _status_dict.get("whisper_ready"):
        return False, "Loading speech-to-text model..."
    return False, "Starting speech engine..."


def push_audio(pcm_bytes: bytes) -> bool:
    """Hands one chunk of 16kHz mono int16 PCM to the worker.

    Drops the chunk rather than blocking if the queue is full. A full
    queue means recognition has fallen behind real time, and in that
    situation the newest audio is worth more than a backlog of old audio
    - blocking here would stall the HTTP request handler and make the
    backlog worse."""
    if not _audio_queue or not is_ready():
        return False
    try:
        _audio_queue.put_nowait(pcm_bytes)
        return True
    except Exception:
        return False


def control(action: str, wake_seq=None, max_ms=None, wake_ok=False):
    """suppress (Quietude started speaking) / unsuppress (she finished) /
    sleep (back to waiting for the wake word).

    wake_seq is only meaningful for "sleep": pass the last wake_seq the
    caller saw, and a request made stale by a newer wake is dropped
    rather than cutting the new session short.

    max_ms and wake_ok are only meaningful for "suppress". max_ms is
    how long the caller expects to be speaking: a deadline, not a
    schedule - the worker lifts suppression itself if nobody comes back
    to lift it (see SUPPRESS_CEILING_MS). wake_ok says whether her name
    counts as an interrupt during this phrase, which it does not when
    the phrase being spoken contains her name."""
    if not _control_queue or action not in ("suppress", "unsuppress", "sleep"):
        return
    try:
        _control_queue.put_nowait({
            "action": action, "wake_seq": wake_seq,
            "max_ms": max_ms, "wake_ok": wake_ok,
        })
    except Exception:
        pass


def status() -> dict:
    """The single payload the frontend polls. The three *_seq counters are
    the important part: the frontend compares them against its own
    last-seen values, so an event is never missed even if a poll is slow
    or two events land between polls. A boolean flag would be."""
    if not _status_dict:
        return {
            "ready": False, "vosk_ready": False, "whisper_ready": False,
            "error": "not started", "mode": "asleep",
            "wake_seq": 0, "stop_seq": 0, "transcript_seq": 0,
            "transcript_text": "", "partial_text": "", "available": False,
            "level": 0.0, "noise_floor": 0.0, "vad_threshold": 0.0,
            "voiced": False, "sensitivity": SENSITIVITY_DEFAULT,
            "sensitivity_min": SENSITIVITY_MIN, "sensitivity_max": SENSITIVITY_MAX,
            "wake_name": "", "wake_phrase": "",
            "utterance_ms": 0, "utterance_speech_ms": 0,
            "utterance_silence_ms": 0,
            "dropped_seq": 0, "dropped_reason": "",
        }
    return {
        "ready": bool(_status_dict.get("ready")),
        "vosk_ready": bool(_status_dict.get("vosk_ready")),
        "whisper_ready": bool(_status_dict.get("whisper_ready")),
        "error": _status_dict.get("error"),
        "mode": _status_dict.get("mode", "asleep"),
        "wake_seq": _status_dict.get("wake_seq", 0),
        "stop_seq": _status_dict.get("stop_seq", 0),
        "transcript_seq": _status_dict.get("transcript_seq", 0),
        "transcript_text": _status_dict.get("transcript_text", ""),
        "partial_text": _status_dict.get("partial_text", ""),
        "available": is_available(),
        # What she is hearing right now, so "it isn't picking me up" can
        # be looked at instead of guessed at.
        "level": _status_dict.get("level", 0.0),
        "noise_floor": _status_dict.get("noise_floor", 0.0),
        "vad_threshold": _status_dict.get("vad_threshold", 0.0),
        "voiced": bool(_status_dict.get("voiced")),
        "sensitivity": _status_dict.get("sensitivity", SENSITIVITY_DEFAULT),
        "sensitivity_min": SENSITIVITY_MIN,
        "sensitivity_max": SENSITIVITY_MAX,
        # What the interface needs to say "this is how long a pause has
        # to be" without hard-coding the number in two places.
        "end_of_utterance_ms": END_OF_UTTERANCE_SILENCE_MS,
        "min_speech_ms": MIN_UTTERANCE_VOICED_MS,
        "wake_name": _status_dict.get("wake_name", ""),
        "wake_phrase": _status_dict.get("wake_phrase", ""),
        # The sentence in progress. The interface uses this to tell
        # silence from somebody still talking, so that it stops
        # interrupting the second one.
        "utterance_ms": _status_dict.get("utterance_ms", 0),
        "utterance_speech_ms": _status_dict.get("utterance_speech_ms", 0),
        "utterance_silence_ms": _status_dict.get("utterance_silence_ms", 0),
        # Something was said and did not become a message. A counter
        # rather than a flag, for the same reason as the other three.
        "dropped_seq": _status_dict.get("dropped_seq", 0),
        "dropped_reason": _status_dict.get("dropped_reason", ""),
    }


def _send(message):
    if _control_queue is None:
        return False
    try:
        _control_queue.put(message)
        return True
    except Exception:
        return False


def set_identity(name):
    """Rebuilds the wake phrase for a new assistant name, live.

    The name is the one setting with a mechanical consequence rather
    than a cosmetic one: everything else about the assistant's identity
    changes what she says, and this changes what she listens for."""
    return _send({"action": "identity", "name": name or ""})


def set_sensitivity(level):
    """Changes how much louder than the room speech has to be."""
    return _send({"action": "sensitivity", "level": level})


def wake_name_is_recognisable(name):
    """Can the wake-word model actually hear this name?

    Vosk recognises a fixed vocabulary - it is not spelling out sounds,
    it is choosing among words it knows. A name outside that vocabulary
    can never be the wake word, no matter how clearly it is said, and
    the failure is completely silent: she simply never wakes up.

    So the name is checked against the model's own lexicon before the
    user commits to it. Returns (known, detail) where known is True,
    False, or None when it could not be checked - which is not the same
    as False and must not be reported as it.
    """
    words = [w for w in (name or "").split() if w]
    if not words:
        return None, "No name set yet."
    if not models_present()[0]:
        return None, "The wake-word model isn't downloaded yet, so I can't check."
    try:
        import vosk
        vosk.SetLogLevel(-1)
        model = vosk.Model(str(model_catalog.active_model_dir("wake")))
    except Exception as e:
        return None, f"Couldn't open the wake-word model to check: {e}"

    missing = []
    for word in words:
        try:
            if model.find_word(word.lower()) is None or model.find_word(word.lower()) < 0:
                missing.append(word)
        except Exception:
            return None, "This wake-word model can't be asked about its vocabulary."

    if not missing:
        return True, "The wake-word model knows this name."
    if len(missing) == len(words):
        return False, (
            f"The wake-word model doesn't know \"{' '.join(missing)}\", so it will "
            "never hear it as a wake word. Everything else will still use the name - "
            "it is only the wake word that needs a word the model was trained on. "
            "Try a common given name, or a different spelling."
        )
    return False, (
        f"The wake-word model doesn't know {', '.join(missing)}, so "
        f"\"{WAKE_PREFIXES[0]} {' '.join(words)}\" won't be heard reliably."
    )


def restart(wake_name="", sensitivity=SENSITIVITY_DEFAULT):
    """Stops the worker and starts it again on the current models.

    Needed because choosing a different speech model is the one setting
    that cannot be applied to a running worker: the models are loaded
    into the child process at startup and swapping one means loading
    anything up to 1.5GB from disk. Doing that in place would mean
    dropping audio mid-utterance with no way to tell the user why.

    So the worker is replaced rather than reconfigured. It takes a few
    seconds and voice chat is unavailable while it happens, which the
    interface says - but it is live, and the alternative was telling
    someone who just downloaded a model to restart the application.
    """
    global _process, _audio_queue, _control_queue, _status_dict, _manager
    if _process is None:
        start(wake_name=wake_name, sensitivity=sensitivity)
        return True
    shutdown()
    start(wake_name=wake_name, sensitivity=sensitivity)
    return True


def shutdown():
    """Called from the single ordered shutdown path in lifecycle.py."""
    global _process, _audio_queue, _control_queue, _status_dict, _manager
    process_utils.stop_process(
        _process, sentinel_queue=_audio_queue, sentinel=None,
        timeout=5.0, label="speech worker",
    )
    process_utils.stop_manager(_manager, label="speech manager")
    _process = _audio_queue = _control_queue = _status_dict = _manager = None
