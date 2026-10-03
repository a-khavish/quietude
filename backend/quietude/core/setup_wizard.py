# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
setup_wizard.py
Drives the first-run account setup as a small conversational state
machine, mirroring how Quietude's chat itself works. Each step validates its
own answer, gives a clear pass/fail signal the frontend can turn into a
green "saved" or red "try again" outline, and persists progress after
every successful step - so setup can be paused anytime and resumed right
where it left off, even after fully closing and reopening Quietude.
"""

import re

from quietude.core import database

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z\-' ]{1,49}$")
PREFERRED_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z ]{0,29}$")
# Same shape as a person's name, and for the same reason: this one
# becomes a wake word, and a recogniser returns words - it has no way
# to return a digit or a symbol, so a name containing one could never
# be matched however clearly it was said.
ASSISTANT_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z '-]{0,31}$")
PASSWORD_RE = re.compile(
    r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()_\-+=\[\]{}|;:'\",.<>/?`~]).{10,}$"
)
YES_WORDS = ("yes", "y", "confirm", "yeah", "yep", "correct", "sure", "ok", "okay")
NO_WORDS = ("no", "n", "cancel", "nevermind", "never mind", "nope", "not now", "skip")

STEP_ORDER = [
    "full_name", "preferred_name", "age",
    # Asked here rather than buried in a settings page, because the
    # assistant has no name until someone gives her one and because the
    # name is what her wake word is built from. Shipping a default would
    # have quietly made that default almost everyone's answer.
    "assistant_name",
    "login_password", "register_face_prompt", "done",
]

REQUIREMENTS = {
    "full_name": "Letters only (spaces, hyphens, and apostrophes allowed) - at least 2 characters. No numbers or symbols.",
    "preferred_name": "Letters only, 1-30 characters - this is just what I'll call you day to day.",
    "age": "A whole number between 1 and 130.",
    "assistant_name": (
        "Letters only (hyphens and apostrophes are fine), up to 32 characters. "
        "Pick something you'd be happy saying out loud, and something ordinary "
        "enough that the wake-word model knows it - an invented word can't be "
        "recognised, however clearly you say it. You can change it whenever "
        'you like with "who are you".'
    ),
    "login_password": (
        "At least 10 characters, with at least one uppercase letter, one lowercase letter, "
        "one number, and one special character (e.g. ! @ # $ % ^ & *). "
        "This is your backup login if face recognition ever doesn't work "
        "(bad lighting, camera trouble, etc.)."
    ),
}

PROMPTS = {
    "full_name": "What is your full name?",
    "preferred_name": "Nice to meet you! What would you like me to call you day to day?",
    "age": "How old are you?",
    "assistant_name": (
        "Now - what would you like to call me?\n\n"
        "I don't have a name yet. This application is called Quietude; who I am "
        "inside it is up to you.\n\n"
        "Worth knowing before you choose: this is also my wake word. With voice "
        'chat on, you\'ll get my attention by saying "Hello" and then this name, '
        "so pick something you won't mind saying out loud."
    ),
    "login_password": (
        "Now let's set up a backup password. In case face recognition ever isn't reliable "
        "for you - bad lighting, camera issues, or you just can't show your face right now - "
        "you'll be able to log in with this instead. What would you like it to be?"
    ),
    "register_face_prompt": (
        "One last thing before we're done: face recognition registration is required for "
        "logging in without your password, and for account changes later on - like updating "
        "your details or resetting your data.\n\n"
        "Would you like to register your face now? (yes/no)"
    ),
}


class SetupWizard:
    def __init__(self):
        progress = database.get_setup_progress()
        if progress:
            self.step = progress.get("step", STEP_ORDER[0])
            self.answers = progress.get("answers", {})
        else:
            self.step = STEP_ORDER[0]
            self.answers = {}

    # ---------------- persistence ----------------

    def _save(self):
        database.save_setup_progress({"step": self.step, "answers": self.answers})

    def _advance(self):
        idx = STEP_ORDER.index(self.step)
        self.step = STEP_ORDER[idx + 1]
        self._save()

    # ---------------- state for the frontend ----------------

    def state(self):
        if self.step == "done":
            return {"step": "done", "done": True}

        expect = "password" if self.step == "login_password" else \
                 "confirm" if self.step == "register_face_prompt" else "text"
        return {
            "step": self.step,
            "done": False,
            "expect": expect,
            "prompt": PROMPTS[self.step],
            "requirements": REQUIREMENTS.get(self.step, ""),
        }

    # ---------------- submission handling ----------------

    def submit_text(self, message):
        text = (message or "").strip()
        step = self.step

        if step == "full_name":
            if not text or not NAME_RE.match(text):
                return self._error("That doesn't look like a valid full name. " + REQUIREMENTS["full_name"])
            self.answers["full_name"] = text

        elif step == "preferred_name":
            if not text or not PREFERRED_NAME_RE.match(text):
                return self._error("That doesn't look right. " + REQUIREMENTS["preferred_name"])
            self.answers["preferred_name"] = text

        elif step == "age":
            if not text.isdigit() or not (0 < int(text) <= 130):
                return self._error("That doesn't look like a valid age. " + REQUIREMENTS["age"])
            self.answers["age"] = int(text)

        elif step == "assistant_name":
            if not text or not ASSISTANT_NAME_RE.match(text):
                return self._error("That won't work as a name. " + REQUIREMENTS["assistant_name"])
            self.answers["assistant_name"] = " ".join(text.split())

        elif step == "login_password":
            if not text or not PASSWORD_RE.match(text):
                return self._error("That doesn't meet the requirements. " + REQUIREMENTS["login_password"])
            self.answers["login_password"] = text

        elif step == "register_face_prompt":
            if text.lower() in YES_WORDS:
                self.answers["register_face"] = True
            elif text.lower() in NO_WORDS:
                self.answers["register_face"] = False
            else:
                return self._error('Please choose one of the valid options: "yes" or "no".')

        else:
            return self._error("Unexpected step - please refresh and try again.")

        self._advance()
        return {"ok": True, "state": self.state()}

    def is_complete(self):
        return self.step == "done"

    def wants_face_registration(self):
        return self.answers.get("register_face", True)

    def finalize(self):
        """Called once every step is done - creates the real user record
        and clears the in-progress wizard state."""
        a = self.answers
        user = database.create_user(
            full_name=a["full_name"],
            preferred_name=a["preferred_name"],
            age=a["age"],
            login_password=a["login_password"],
        )
        # Her name, and with it her wake word. Saved before the user
        # record is handed back, so the first thing she says is already
        # said by someone with a name.
        if a.get("assistant_name"):
            database.set_assistant(name=a["assistant_name"])
            try:
                from quietude.core import speech_engine
                speech_engine.set_identity(a["assistant_name"])
            except Exception:
                # The engine may not be running (no models, no mic). The
                # name is saved either way and is read at the next start.
                pass
        return user

    def _error(self, message):
        return {"ok": False, "error": message, "state": self.state()}
