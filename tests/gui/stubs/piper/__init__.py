# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal piper stub."""
class SynthesisConfig:
    def __init__(self, **kw): self.__dict__.update(kw)
class PiperVoice:
    @classmethod
    def load(cls, model, config_path=None): return cls()
    def synthesize_wav(self, text, wav, syn_config=None):
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(22050)
        wav.writeframes(b"\x00\x00" * 2205)
