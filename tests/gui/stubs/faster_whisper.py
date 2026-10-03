# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Whisper stub that returns whatever the test said was spoken.

It also *listens*. The first version ignored the audio entirely and
returned the scripted line whatever it was handed - which meant every
test passed even if the engine had handed it an empty buffer, a buffer
of silence, or a tenth of a second of nothing. The one class of bug
these tests exist to catch was the one they could not see.

So it does what the real model does with the two inputs that matter:
nothing to transcribe, and nothing audible to transcribe both come back
empty.
"""
import os
import pathlib

import numpy as np

SCRIPT = pathlib.Path(os.environ.get("QUIETUDE_VOSK_SCRIPT", "/tmp/quietude-test-spoken"))

# Below these, the real model returns nothing useful. A tenth of a
# second is shorter than any word, and a peak this low is a room rather
# than a person.
MIN_SAMPLES = 1600
MIN_PEAK = 0.01


class _Seg:
    def __init__(self, text):
        self.text = text


class WhisperModel:
    def __init__(self, path, **kw):
        self.path = path

    def transcribe(self, audio, **kw):
        if audio is None or len(audio) < MIN_SAMPLES:
            return ([], {})
        if float(np.max(np.abs(np.asarray(audio)))) < MIN_PEAK:
            return ([], {})
        try:
            said = SCRIPT.read_text().strip().rstrip(".")
        except Exception:
            said = ""
        return ([_Seg(said or "stub transcript")], {})
