# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Blueprint registration for Quietude's JSON API."""

from quietude.api import (auth, chat, identity, models, setup, speech,
                          system, tts, voices)

BLUEPRINTS = (system.bp, setup.bp, auth.bp, chat.bp, tts.bp, speech.bp,
              voices.bp, identity.bp, models.bp)


def register(app):
    for bp in BLUEPRINTS:
        app.register_blueprint(bp)
