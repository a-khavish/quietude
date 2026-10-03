#!/usr/bin/env python3
# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Writes the audio the tests speak into the microphone.

Chromium's built-in fake microphone is a constant tone. The voice
activity gate correctly files a constant tone as room noise, so with it
the tests can show that the live preview works but never that an
utterance *ends* - which is half of what they are for.

This writes something with a shape instead: a quiet room, a burst of
speech-band energy, a quiet room again. Chromium loops the file, so each
pass through it is one spoken command.
"""
import math, pathlib, random, struct, wave

RATE = 16000
OUT = pathlib.Path(__file__).resolve().parent / "run" / "speech.wav"


def segment(seconds, amplitude, voiced):
    n = int(RATE * seconds)
    out = []
    for i in range(n):
        if voiced:
            # Several tones plus noise - broadband enough to sit well
            # above a measured noise floor, which a pure tone is not.
            v = (math.sin(2 * math.pi * 180 * i / RATE) * 0.5
                 + math.sin(2 * math.pi * 700 * i / RATE) * 0.3
                 + math.sin(2 * math.pi * 2300 * i / RATE) * 0.15
                 + random.uniform(-0.2, 0.2))
            # An envelope, so it starts and stops rather than switching.
            v *= min(1.0, min(i, n - i) / (RATE * 0.08))
        else:
            v = random.uniform(-1, 1) * 0.004   # a quiet room, not silence
        out.append(int(max(-1.0, min(1.0, v)) * amplitude))
    return out


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # The silent tail has to be longer than END_OF_UTTERANCE_SILENCE_MS
    # in speech_engine.py, or the loop starts talking again before the
    # gate has decided the utterance ended - and no utterance ever
    # finishes. It was 1.6s against a 700ms gate; the gate is 3s now.
    samples = segment(1.0, 300, False) + segment(1.6, 9000, True) \
        + segment(4.5, 300, False)
    with wave.open(str(OUT), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", v) for v in samples))
    print(f"{OUT}  {len(samples) / RATE:.1f}s per loop")


if __name__ == "__main__":
    main()
