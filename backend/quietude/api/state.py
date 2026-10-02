# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
state.py
The handful of objects that live for the length of a session, and the
one place they get rebuilt when a factory reset wipes the ground out
from under them.

The Windows build kept these as module-level globals in app.py and
rebound them with `global` statements from inside three different route
handlers. That worked, but it meant "what does a reset actually reset"
was spread across the file. Collecting it here keeps the blueprints
stateless and makes the reset path a single method you can read.
"""

import threading

from quietude.core import database, speech_engine
from quietude.core.assistant import Assistant
from quietude.core.setup_wizard import SetupWizard

MAX_WRONG_PASSWORD_ATTEMPTS = 3


class PasswordLoginFlow:
    """The 'Login via Password' gate: the backup password created during
    setup. Because this bypasses face recognition entirely, repeated
    wrong attempts are limited - after MAX_WRONG_PASSWORD_ATTEMPTS
    failures, the next wrong attempt wipes all local data, same as the
    "reset data" command."""

    def __init__(self):
        self.stage = "password"  # "password" | "done"
        self.wrong_attempts = 0

    def state(self, _user=None):
        if self.stage == "password":
            return {
                "stage": self.stage, "done": False, "expect": "password",
                "prompt": (
                    "Please enter your backup password. For your security, "
                    f"{MAX_WRONG_PASSWORD_ATTEMPTS} incorrect attempts are allowed - "
                    "after that, all data on this device will be automatically reset."
                ),
            }
        return {"stage": "done", "done": True}

    def submit(self, user, message):
        if self.stage != "password":
            return {"ok": False, "error": "Unexpected stage - please refresh and try again.",
                    "state": self.state(user)}

        if database.verify_login_password(user, message):
            self.stage = "done"
            self.wrong_attempts = 0
            return {"ok": True, "state": self.state(user)}

        self.wrong_attempts += 1
        if self.wrong_attempts > MAX_WRONG_PASSWORD_ATTEMPTS:
            database.factory_reset()
            return {
                "ok": False,
                "error": ("Too many incorrect attempts. For your security, all data on "
                          "this device has been reset."),
                "reset_triggered": True,
                "state": self.state(user),
            }

        remaining = MAX_WRONG_PASSWORD_ATTEMPTS + 1 - self.wrong_attempts
        return {
            "ok": False,
            "error": (
                f"Incorrect password. {remaining} attempt{'s' if remaining != 1 else ''} "
                "remaining before all data is automatically reset."
            ),
            "state": self.state(user),
        }


class SessionState:
    def __init__(self):
        self._lock = threading.Lock()
        self.assistant = Assistant(speech_ready=speech_engine.readiness,
                                   system_summary=system_summary,
                                   identity=database.get_assistant())
        self.setup_wizard = SetupWizard()
        self.password_login = PasswordLoginFlow()

    def reset(self):
        """Everything a factory reset has to put back to first-run state.
        Held under a lock because the reset route and an in-flight chat
        request can genuinely overlap - the frontend reloads immediately
        after, but 'immediately' is not 'atomically'."""
        with self._lock:
            self.assistant.set_user(None)
            self.assistant.pending = None
            self.assistant.conversation_mode = False
            self.assistant.gemini_history = []
            self.setup_wizard = SetupWizard()
            self.password_login = PasswordLoginFlow()

    def refresh_identity(self):
        """Re-reads who she is.

        Called at the top of each chat turn rather than cached at
        startup, because the settings page applies changes as they are
        typed and the next thing she says should already know her new
        name. It is a read of a small JSON file, not a reason to build
        an invalidation protocol."""
        self.assistant.identity = database.get_assistant()
        return self.assistant.identity

    def adopt_existing_user(self):
        """Lazily attach the stored profile to Quietude. Covers the case where
        the server was restarted while the browser kept its session - the
        UI is already past login, so nothing else would set the user."""
        if self.assistant.user is None:
            existing = database.get_primary_user()
            if existing:
                self.assistant.set_user(existing)


def system_summary():
    """Rows for the "show features" command: what each subsystem is, and
    whether it leaves this machine.

    Read live from the engines rather than written out as a fixed list,
    because a privacy claim you can't check against the running system
    is just marketing. If a model failed to load or a voice is missing,
    this says so instead of describing the install someone hoped for.
    """
    from quietude.core import face_auth, gemini_tool, speech_engine, tts_engine

    speech = speech_engine.status()
    tts = tts_engine.status()
    user = database.get_primary_user()
    vosk_ok, whisper_ok = speech_engine.models_present()

    def state(ok, yes, no):
        return yes if ok else no

    wake = state(speech["ready"], "Vosk - running, local",
                 state(vosk_ok, "Vosk - model present, not loaded",
                       "Vosk - model not downloaded"))
    transcribe = state(speech["ready"], "faster-whisper (int8, CPU) - running, local",
                       state(whisper_ok, "faster-whisper - model present, not loaded",
                             "faster-whisper - model not downloaded"))

    engine = tts.get("engine")
    if tts["ready"] and engine == "piper":
        voice = "piper (neural) - running, local"
    elif tts["ready"] and engine == "espeak":
        voice = "espeak-ng - running, local"
    else:
        voice = "unavailable - no voice model installed"

    gemini_installed = gemini_tool.is_available()
    has_key = bool(database.get_gemini_api_key(user)) if user else False
    if not gemini_installed:
        conversation = "NOT INSTALLED - cannot send anything anywhere"
    elif not has_key:
        conversation = "installed, no API key - inactive"
    elif session.assistant.conversation_mode:
        conversation = "ON - SENDING TO GOOGLE right now"
    else:
        conversation = "installed and keyed, currently OFF"

    return [
        ["Face recognition", "OpenCV Haar + LBPH - local, offline"
            if face_auth.model_exists() else
            "OpenCV Haar + LBPH - local, offline (not registered yet)"],
        ["Wake word", wake],
        ["Transcription", transcribe],
        ["Speech synthesis", voice],
        ["Microphone capture", "Your browser - audio is resampled in the page and "
                               "posted to this machine only"],
        ["Camera capture", "Your browser - frames are posted to this machine only"],
        ["Your profile", "JSON on this machine. Password hashed (scrypt), "
                         "API key encrypted (Fernet), key generated here"],
        ["Network access", "Loopback only (127.0.0.1) - not reachable from your network"],
        ["Model downloads", "Once, during install. Transcription is loaded with "
                            "local_files_only, so it cannot reach out later"],
        ["Conversation mode", conversation],
        ["Leaves this machine", "Nothing, unless conversation mode is ON (above)"],
    ]


# Constructed last, because SessionState wires system_summary into Quietude
# and a module body runs top to bottom.
session = SessionState()
