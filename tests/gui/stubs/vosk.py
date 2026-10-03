# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Vosk stub that can be driven from the test.

The real recognizer streams partial results as you speak. The test needs
to make that happen on demand, and the recogniser runs in a forked
worker process - so the channel between them is a file on disk, which is
the one thing both processes can see without any setup.

It only produces words when the audio it is given has something in it.
That matters more than it looks: a recogniser that returns words during
silence keeps resetting the end-of-utterance timer, so an utterance
never ends and nothing is ever transcribed - and a stub that did that
would hide exactly that bug rather than catch it.
"""
import json
import os
import pathlib

import numpy as np

SCRIPT = pathlib.Path(os.environ.get("QUIETUDE_VOSK_SCRIPT", "/tmp/quietude-test-spoken"))

# Roughly where speech sits above a quiet room in the generated test
# audio. Not the engine's gate - this is the recogniser's own "is there
# anything here", which is a separate judgement in the real one too.
MIN_RMS = 200.0


class Model:
    def __init__(self, path):
        self.path = path


def SetLogLevel(level):
    pass


class KaldiRecognizer:
    def __init__(self, model, rate):
        self._final = ""
        self._heard = ""

    def _spoken(self):
        try:
            return SCRIPT.read_text().strip()
        except Exception:
            return ""

    def _audible(self, data):
        if not data:
            return False
        samples = np.frombuffer(data, dtype=np.int16)
        if samples.size == 0:
            return False
        return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2))) >= MIN_RMS

    def AcceptWaveform(self, data):
        # Final results are what the test asks for by ending the line
        # with a full stop; everything else stays a partial, which is the
        # state the live preview in the message box renders.
        if not self._audible(data):
            # Silence does not retract what has already been heard -
            # the real recogniser keeps its partial until the segment
            # ends - but it does not add to it either.
            return False
        text = self._spoken()
        self._heard = text[:-1].strip() if text.endswith(".") else text
        if text.endswith("."):
            self._final = self._heard
            return True
        return False

    def Result(self):
        out, self._final = self._final, ""
        self._heard = ""
        return json.dumps({"text": out})

    def PartialResult(self):
        return json.dumps({"partial": self._heard})

    def Reset(self):
        self._final = ""
        self._heard = ""
