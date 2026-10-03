# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
assistant.py
Quietude's "brain" - a lightweight, rule-based command interpreter for the
text-only phase of the project. Also handles a small multi-turn state
machine for account-management commands (change name / email / password
/ age), always asking for a yes/no confirmation before saving anything.

respond() returns a dict rather than a plain string so the frontend can
adapt the input field (e.g. switch to a password field, or hint that a
yes/no answer is expected) and react to shutdown/restart signals.

Ported from the Windows build with the command logic intact - it never
had any UI or OS coupling to strip. Two things did change:

  * "show commands" and "tts settings" used to pop separate browser
    windows rendered by their own Flask templates. They're routes in the
    SPA now, so the action names stay the same but the wording no longer
    promises a new window.
  * "turn on voice chat" can now genuinely fail. The old build leaned on
    the browser's Web Speech API, which is always there in Chrome; voice
    input is local models now, and a model that didn't load is a real
    state Quietude has to be able to explain. Hence speech_ready.
"""

import re
import datetime

from quietude.core import database
from quietude.core import gemini_tool

FIELD_ALIASES = {
    "name": ["name"],
    "age": ["age"],
}

FIELD_LABELS = {
    "name": "name",
    "age": "age",
}

CHANGE_TRIGGER_RE = re.compile(
    r"\b(change|update|edit|set)\b.*\b(name|age)\b"
)
GEMINI_KEY_RE = re.compile(r'gemini\s+api\s+key\s+"?([^"\s]+)"?', re.IGNORECASE)
GEMINI_KEY_CLEAR_RE = re.compile(r'gemini\s+api\s+key\s+""\s*$', re.IGNORECASE)

CONVERSATION_ON_PHRASES = ("turn on conversation mode", "start conversation mode",
                           "enable conversation mode", "conversation mode on")
CONVERSATION_OFF_PHRASES = ("turn off conversation mode", "stop conversation mode",
                            "disable conversation mode", "conversation mode off")

YES_WORDS = ("yes", "y", "confirm", "yeah", "yep", "correct", "sure", "ok", "okay")
NO_WORDS = ("no", "n", "cancel", "nevermind", "never mind", "nope", "not now", "skip")

# Single source of truth for the "show commands" reference page.
# The wake prefix shown in help text. The speech engine accepts
# several (hello/hey/ok/okay/hi); this is the one she suggests.
WAKE_PREFIX = "Hello"

# speak "..." and its curly-quote and single-quote spellings, because a
# phone keyboard and a desktop one do not agree about what a quote is.
SPEAK_RE = re.compile(
    r'^\s*(?:speak|say)\s*[\u201c\u2018"\']'
    r'(?P<text>.*?)'
    r'[\u201d\u2019"\']\s*$',
    re.S,
)

# The placeholders _text fills. Anything else in braces is left exactly
# as it was written.
_PLACEHOLDER = re.compile(
    r"\{(name|wake|they_are|they|them|their|theirs|themself)\}"
)

COMMANDS_REFERENCE = [
    {
        "name": "help",
        "primary": True,
        "description": "Lists what {name} can currently do, right in the chat.",
        "usage": 'help',
    },
    {
        "name": "show commands",
        "primary": True,
        "description": "Opens the full command reference.",
        "usage": 'show commands',
    },
    {
        "name": "show account information",
        "primary": True,
        "description": "Shows your saved account details as a table. Hashed/encrypted "
                        "fields are shown as previews, never in plain text. Requires a "
                        "registered face.",
        "usage": 'show account information',
    },
    {
        "name": "change my name / age",
        "short": "change my name",
        "description": "Updates a single account field. {name} asks for the new value, then "
                        "always confirms yes/no before saving. Requires a registered face.",
        "usage": 'change my name',
    },
    {
        "name": "register face",
        "primary": True,
        "description": "Sets up face recognition if it isn't registered yet (with a "
                        "well-lit-room reminder and countdown first), or offers to update "
                        "it if it's already registered.",
        "usage": 'register face',
    },
    {
        "name": "reset data",
        "description": "Permanently wipes your account and starts over from scratch. Asks "
                        "for confirmation, then a live face verification. Requires a "
                        "registered face.",
        "usage": 'reset data',
    },
    {
        "name": "turn on voice chat / turn off voice chat",
        "short": "turn on voice chat",
        "primary": True,
        "description": 'Enables or disables hands-free voice control. Once on, say '
                        '"{wake}" to wake {them} up before giving a spoken command.',
        "usage": 'turn on voice chat',
    },
    {
        "name": "what time is it / what's the date",
        "short": "what time is it",
        "primary": True,
        "description": "Tells you the current time or date.",
        "usage": 'what time is it',
    },
    {
        "name": 'gemini api key "YOUR_KEY"',
        "short": "gemini api key",
        "description": "Registers or updates your own Gemini API key, required before "
                       "conversation mode can be used. Stored encrypted, requires a "
                       'registered face to set. Use gemini api key "" (empty quotes) to '
                       "remove it entirely.",
        "usage": 'gemini api key "AIzaSy..."',
    },
    {
        "name": "turn on conversation mode / turn off conversation mode",
        "short": "turn on conversation mode",
        "description": "Switches between static commands and free-form conversation with "
                        "{name}'s Gemini-powered persona. Unlike everything else here, "
                        "conversation mode sends what you say to Google's servers.",
        "usage": 'turn on conversation mode',
    },
    {
        "name": "tts settings",
        "primary": True,
        "description": "Opens voice customization (rate, volume, voice). Changes "
                        "apply as you make them.",
        "usage": 'tts settings',
    },
    {
        "name": "who are you",
        "description": "Opens the assistant's own settings - name, pronouns, age and "
                       "nationality. The name is the one that matters mechanically: it "
                       "is what the wake word is built from.",
        "usage": 'who are you',
        "primary": True,
        "short": "who are you",
    },
    {
        "name": "speech models",
        "description": "Choose which model listens for the wake word and which one "
                       "transcribes what you say, and download better ones. Larger "
                       "models hear more and cost more.",
        "usage": 'speech models',
        "primary": True,
        "short": "speech models",
    },
    {
        "name": 'speak "..."',
        "description": "Reads whatever is inside the quotes out loud. The playback "
                       "bar at the top gives you pause and stop.",
        "usage": 'speak "the kettle has boiled"',
        "primary": True,
        "short": 'speak "hello"',
    },
    {
        "name": "voice check",
        "description": "Shows what {name} is hearing right now, step by step, so "
                       "\"she can't hear me\" becomes a line you can point at. "
                       "Has the sensitivity control on it.",
        "usage": 'voice check',
        "primary": True,
        "short": "voice check",
    },
    {
        "name": "user commands",
        "description": "Your own commands: a name you type or say, something to run "
                       "on this machine, and what {name} says while it runs.",
        "usage": 'user commands',
        "primary": True,
        "short": "user commands",
    },
    {
        "name": "app settings",
        "description": "What closing the window does - quit, or keep running in "
                       "the tray - and whether to start when you log in.",
        "usage": 'app settings',
        "primary": True,
        "short": "app settings",
    },
    {
        "name": "voice library",
        "primary": True,
        "description": "Browse every piper voice - around a thousand of them across "
                       "sixty-odd languages - and download the ones you want. The only "
                       "part of this app that uses the internet, and only while the page is "
                       "open.",
        "usage": 'voice library',
    },
    {
        "name": "show features",
        "primary": True,
        "description": "Lists every subsystem - face recognition, wake word, "
                       "transcription, voice - with the engine it uses and whether "
                       "anything leaves this machine. Reports live state, not a "
                       "fixed list.",
        "usage": 'show features',
    },
    {
        "name": "clear terminal",
        "primary": True,
        "description": "Clears the chat log completely, resetting it to a blank slate.",
        "usage": 'clear terminal',
    },
    {
        "name": "shutdown",
        "primary": True,
        "description": "Shuts {name} down cleanly - same as the Shut Down button.",
        "usage": 'shutdown',
    },
]


class Assistant:
    def __init__(self, user=None, speech_ready=None, system_summary=None,
                 identity=None):
        self.user = user
        # Who she is, as the user defined her. Injected rather than read,
        # for the same reason speech_ready is: this module stays free of
        # imports it would otherwise need only to look something up.
        # Refreshed by the API layer on every request, so a name changed
        # in settings is in effect by the next thing she says.
        self.identity = identity or {}
        self.pending = None  # multi-turn state for an in-progress account change
        self.conversation_mode = False  # True while Gemini conversation mode is active
        self.gemini_history = []  # conversation-mode chat history, reset each time it's turned on
        # Callable returning (ready: bool, detail: str) for the local
        # speech pipeline, injected by the API layer so this module stays
        # free of any engine imports. None means "don't check" - which is
        # what the unit tests and any text-only use of Quietude want.
        self.speech_ready = speech_ready
        # Callable returning the rows for "show features" - what each
        # subsystem is and whether it leaves the machine. Injected, like
        # speech_ready, so this module stays free of engine imports.
        self.system_summary = system_summary


    # ---------------- who she is ----------------

    @property
    def name(self):
        """What to call her. Empty until the user names her, which the
        setup wizard asks for - so the fallback is a description rather
        than a name, and never a name of our choosing."""
        return (self.identity or {}).get("name") or ""

    @property
    def display_name(self):
        return self.name or "your assistant"

    @property
    def pronouns(self):
        from quietude.core import database
        return database.pronouns_for((self.identity or {}).get("pronouns"))

    def _text(self, template):
        """Fills in the assistant's own details.

        Every sentence she says about herself goes through here, so that
        renaming her renames her everywhere at once rather than in the
        places someone remembered. Placeholders:

            {name}      what she is called
            {wake}      the wake phrase to say out loud
            {they} {them} {their} {theirs} {themself}
            {they_are}  "she is" / "he is" / "they are", as one phrase

        {they_are} is a whole phrase rather than a bare verb on purpose.
        A separate {are} looked tidier and was a trap: it agrees with
        the pronoun, so writing "{name} {are} listening" produced "Sam
        are listening" for anyone whose pronoun was they. A name is
        always singular, so after a name the word is simply "is", and
        there is now no placeholder that can get that wrong.
        Substituted by name rather than with str.format, so that a reply
        containing a brace for any other reason - a JSON example, a shell
        snippet, something a user typed and we are quoting back - passes
        through untouched instead of raising KeyError. That matters
        because every reply goes through here now, not just the ones
        someone remembered to wrap.
        """
        p = self.pronouns
        values = {
            "name": self.display_name,
            "wake": f"{WAKE_PREFIX} {self.name}" if self.name else "the wake phrase",
            "they": p["they"], "them": p["them"], "their": p["their"],
            "theirs": p["theirs"], "themself": p["themself"],
            "they_are": f"{p['they']} {p['are']}",
        }
        return _PLACEHOLDER.sub(lambda m: values[m.group(1)], template)

    def _greetings(self):
        """"hello <her name>", for whatever she is called.

        These were the application's name after the rename, which was
        wrong in a way that would have been easy to miss: saying hello
        to Quietude is saying hello to the program, and the program is
        not who you are talking to."""
        her = self.name.lower()
        if not her:
            return ()
        return tuple(f"{prefix} {her}" for prefix in ("hello", "hi", "hey"))

    def set_user(self, user):
        self.user = user

    def _field_from_text(self, text):
        for field, keywords in FIELD_ALIASES.items():
            if any(k in text for k in keywords):
                return field
        return None

    def respond(self, message: str) -> dict:
        text = (message or "").strip()
        lower = text.lower()

        if not lower:
            return self._reply("I didn't quite catch that. Could you say it again?")

        # ---- registering/updating the Gemini API key always works, in
        # any mode, so a bad or expiring key can always be fixed ----
        if GEMINI_KEY_CLEAR_RE.search(text):
            if not self.user:
                return self._reply("I don't have an account on file yet.")
            gate = self._face_required_gate("remove your Gemini API key")
            if gate:
                return gate
            database.clear_gemini_api_key(self.user["id"])
            self.set_user(database.get_primary_user())
            was_on = self.conversation_mode
            self.conversation_mode = False
            self.gemini_history = []
            return self._reply(
                "Gemini API key removed. You'll need to register a new one before using "
                "conversation mode again.",
                status="warning",
                action="conversation_mode_off" if was_on else None,
            )

        key_match = GEMINI_KEY_RE.search(text)
        if key_match:
            if not self.user:
                return self._reply("I don't have an account on file yet.")
            gate = self._face_required_gate("update your Gemini API key")
            if gate:
                return gate
            api_key = key_match.group(1).strip()
            database.set_gemini_api_key(self.user["id"], api_key)
            self.set_user(database.get_primary_user())
            return self._reply("Gemini API key saved.", status="success")

        # ---- conversation mode: "turn it off" always works, even mid-mode ----
        if self.conversation_mode and any(p in lower for p in CONVERSATION_OFF_PHRASES):
            self.conversation_mode = False
            self.gemini_history = []
            return self._reply("Conversation mode is now OFF.", action="conversation_mode_off")

        if self.conversation_mode:
            return self._gemini_reply(text)

        # Not in conversation mode - "turn it off" doesn't make sense yet.
        if any(p in lower for p in CONVERSATION_OFF_PHRASES):
            if not self.user or not database.get_gemini_api_key(self.user):
                return self._reply(
                    "Conversation mode needs a Gemini API key first. Register one with:\n\n"
                    'gemini api key "YOUR_API_KEY"',
                    status="error",
                )
            return self._reply(
                "You're not in conversation mode right now, so there's nothing to turn off. "
                'Use "turn on conversation mode" first if you\'d like to start one.',
                status="error",
            )

        if any(p in lower for p in CONVERSATION_ON_PHRASES):
            if not self.user or not database.get_gemini_api_key(self.user):
                return self._reply(
                    "Conversation mode needs a Gemini API key first. Register one with:\n\n"
                    'gemini api key "YOUR_API_KEY"',
                    status="error",
                )
            self.conversation_mode = True
            self.gemini_history = []
            self.pending = None
            return self._reply(
                "Conversation mode is now ON.",
                action="conversation_mode_on",
                conversation_turns=self._conversation_turns(),
            )

        # ---- an account-change conversation is already in progress ----
        if self.pending:
            return self._handle_pending(text, lower)

        name = self.user["name"] if self.user else "there"

        # ---- checked before the generic "account info" catch-all below,
        # since that would otherwise swallow this more specific phrase ----
        if any(
            p in lower
            for p in ("show account information", "account information", "show my account",
                      "my account info", "account details", "show account info", "show account details")
        ):
            if not self.user:
                return self._reply("I don't have an account on file yet.")
            gate = self._face_required_gate("show your account information")
            if gate:
                return gate
            return self._reply("Here's your account information:", table=self._account_table())

        # ---- account management (checked early so "change my name" isn't
        # swallowed by the more general "my name" keyword check below) ----
        if "account" in lower and ("info" in lower or "setting" in lower or "detail" in lower):
            return self._reply(
                'You can say things like "change my name" or "change my age" '
                "and I'll walk you through it."
            )

        if CHANGE_TRIGGER_RE.search(lower):
            field = self._field_from_text(lower)
            if field and self.user:
                gate = self._face_required_gate(f"change your {FIELD_LABELS[field]}")
                if gate:
                    return gate
                return self._start_change(field)

        if lower in ("hi", "hey", "hello", "yo") or any(
            g in lower for g in self._greetings()
        ):
            return self._reply(f"Hello, {name}! How can I help you today?")

        if "your name" in lower or "who are you" in lower:
            return self._reply(
                self._text(
                    "I'm {name} - your personal assistant, running entirely on "
                    "this machine.\n\n"
                    "Opening my settings, in case you'd like to change any of that. "
                    "The name is also my wake word, so it's the one that changes what "
                    "I listen for."
                ),
                action="show_identity",
            )

        if "my name" in lower or "who am i" in lower:
            return self._reply(f"You're {name}, of course!")

        if "how old are you" in lower:
            return self._reply("I'm ageless. Just logic, code, and a bit of personality.")

        if "how old am i" in lower and self.user:
            return self._reply(f"You told me you're {self.user.get('age')} years old.")

        if "time" in lower:
            now = datetime.datetime.now().strftime("%I:%M %p").lstrip("0")
            return self._reply(f"It's currently {now}.")

        if "date" in lower or "today" in lower:
            today = datetime.datetime.now().strftime("%A, %B %d, %Y")
            return self._reply(f"Today is {today}.")

        if "thank" in lower:
            return self._reply("You're very welcome!")

        if any(
            p in lower
            for p in ("clear terminal", "clear chat", "clear screen", "clear the chat",
                      "clear the terminal", "clear the screen")
        ):
            return self._reply("Terminal cleared.", action="clear_terminal")

        if any(
            p in lower
            for p in ("tts settings", "voice settings", "speech settings")
        ):
            return self._reply(
                "Opening voice settings.\n\n"
                "Remember: nothing is applied until you click Save - leaving without "
                "saving keeps everything as it was.",
                action="show_tts_settings",
                status="warning",
            )

        if any(
            p in lower
            for p in ("who are you", "your name", "assistant settings", "identity",
                      "rename you", "change your name", "what should i call you",
                      "your identity", "who you are", "personality")
        ):
            return self._reply(
                self._text(
                    "Opening my settings.\n\n"
                    "The name matters more than it looks: it's what the wake word is "
                    "built from, so changing it changes what I listen for. Everything "
                    "else - pronouns, age, nationality - changes how I talk about "
                    "myself, and takes effect as you type it."
                ),
                action="show_identity",
            )

        if any(
            p in lower
            for p in ("speech model", "speech models", "recognition model",
                      "wake word model", "transcription model", "change model",
                      "listening model", "download model", "download models",
                      "speech engine", "hearing")
        ):
            return self._reply(
                "Opening the speech models.\n\n"
                "One model listens for the wake word and captions you live; another "
                "transcribes what you said once you've finished saying it. Bigger "
                "models hear more and take longer.",
                action="show_speech_models",
                status="warning",
            )

        if any(
            p in lower
            # "a" in the middle is how people actually say these, and
            # a substring match doesn't see it: "install voice" is not
            # in "install a voice".
            for p in ("voice library", "voice catalog", "more voices",
                      "browse voices", "get voices", "get more voices",
                      "download voice", "download voices", "download a voice",
                      "install voice", "install voices", "install a voice",
                      "add voice", "add voices", "add a voice",
                      "new voice", "other voices", "different voice")
        ):
            return self._reply(
                "Opening the voice library.\n\n"
                "Heads up: this is one of only two screens that use the internet - it fetches "
                "the list of available voices and downloads the ones you pick. Nothing "
                "about you is sent, and nothing else here goes online.",
                action="show_voice_library",
                status="warning",
            )

        if any(
            p in lower
            for p in ("show commands", "show command list", "list commands",
                      "commands list", "open commands", "commands page")
        ):
            return self._reply(
                "Opening the full command reference...",
                action="show_commands",
            )

        if any(
            p in lower
            for p in ("show features", "system info", "show system", "what runs locally",
                      "what is local", "what's local", "feature summary", "show privacy",
                      "privacy report", "what do you send")
        ):
            if self.system_summary is None:
                return self._reply("I can't read my own system state right now.",
                                   status="error")
            return self._reply(
                "Here's everything I run, and where it runs. Anything marked as leaving "
                "this machine is listed explicitly - there is nothing else.",
                table=self.system_summary(),
            )

        if any(
            p in lower
            for p in ("voice check", "check my microphone", "check the microphone",
                      "why can't you hear me", "why cant you hear me",
                      "you can't hear me", "you cant hear me",
                      "microphone test", "test my microphone")
        ):
            return self._reply(
                "Opening the voice check.\n\n"
                "Turn voice chat on and talk with that page open. Each line is one "
                "step between your microphone and the message box - the first one "
                "that stays dark is the one to fix.",
                action="show_voice_check",
            )

        if any(
            p in lower
            for p in ("user commands", "my commands", "custom commands",
                      "own commands", "create a command", "add a command",
                      "new command")
        ):
            return self._reply(
                "Opening your own commands.\n\n"
                "A command is a name you type or say, something to run on this "
                "machine, and what I say while it runs. Only one runs at a time.",
                action="show_user_commands",
            )

        # Deliberately last of the command-word matches. It catches the
        # bare word "commands", which is a reasonable thing to answer
        # "help" for and a terrible thing to put ahead of "show
        # commands" or "user commands" - both of which contain it, and
        # both of which were being answered with the help text instead
        # of opening.
        if any(k in lower for k in ("help", "what can you do", "commands")):
            return self._reply(self._text(
                "Here's what I can do so far: tell you the time and date, "
                'chat with you, update your account info (try "change my name"), '
                'and remember who you are. Say or type "turn on voice chat", then say '
                '"{wake}" to wake me up before giving a spoken command. Say "show account '
                'information" to see your saved details, "register face" to set up or update '
                'face recognition, "show commands" for the full command reference, "voice '
                'library" to download more voices, "speech models" to change what I listen '
                'with, "who are you" to give me a different name, "turn on conversation '
                'mode" to chat freely (after registering a Gemini API key with "gemini api '
                'key"), or "reset data" to wipe everything and start over from scratch.'
            ))

        # Deliberately not `name`: that is the *user's* name, bound
        # earlier in this method and used by the greeting above.
        her = self.name.lower()
        farewells = ["shutdown", "power off", "shut down"]
        if her:
            farewells += [f"goodbye {her}", f"bye {her}", f"exit {her}"]
        if any(k in lower for k in farewells):
            return self._reply("Shutting down. Goodbye!", shutdown=True)

        if any(
            p in lower
            for p in ("turn on voice chat", "enable voice chat", "start voice chat",
                      "voice chat on", "turn on voice mode", "start listening")
        ):
            if self.speech_ready is not None:
                ready, detail = self.speech_ready()
                if not ready:
                    return self._reply(
                        "I can't turn voice chat on - my local speech models aren't "
                        f"loaded.\n\n{detail}\n\nText chat works normally in the meantime.",
                        status="error",
                    )
            return self._reply(
                "Voice chat is on. I'll only respond to spoken commands after you say "
                '"{wake}" first - no need to say that when typing here in the chat though.',
                voice="start",
            )

        if any(
            p in lower
            for p in ("app settings", "application settings", "window settings",
                      "startup settings", "tray settings", "start at login",
                      "start on login", "minimize to tray", "minimise to tray")
        ):
            return self._reply(
                "Opening the application's settings.\n\n"
                "Closing the window can either shut everything down or leave "
                "{them} running in the tray, and {they} can start when you log "
                "in. Saying \"shutdown\" always stops everything either way.",
                action="show_app_settings",
            )

        if any(
            p in lower
            for p in ("turn off voice chat", "disable voice chat", "stop voice chat",
                      "voice chat off", "turn off voice mode", "stop listening")
        ):
            return self._reply("Voice chat is off.", voice="stop")

        if any(
            p in lower
            for p in ("register face", "register my face", "set up face", "setup face",
                      "re-register face", "reregister face", "update my face", "update face")
        ):
            if self.user and self.user.get("face_registered"):
                self.pending = {"type": "face_reregister_prompt"}
                return self._reply(
                    "Your face is already registered.\n\n"
                    "Would you like to update it instead? (yes/no)",
                    expect="confirm",
                    status="error",
                )
            return self._reply(
                "Let's get your face registered.",
                action="register_face",
            )

        if any(
            p in lower
            for p in ("reset data", "reset my data", "reset everything", "factory reset",
                      "erase my data", "erase everything", "wipe my data", "start over")
        ):
            gate = self._face_required_gate("reset data")
            if gate:
                return gate
            self.pending = {"type": "reset_confirm"}
            return self._reply(
                "This will permanently erase everything - your account and your face data - "
                "and you'll need to set up again from scratch. This can't be undone.\n\n"
                "Do you want to proceed? (yes/no)",
                expect="confirm",
            )

        # speak "..." - read back whatever is in the quotes.
        #
        # Matched on the original text rather than the lowercased copy,
        # because what is inside the quotes is the thing being said and
        # casing is part of it.
        spoken = SPEAK_RE.match(text)
        if spoken:
            words = (spoken.group("text") or "").strip()
            if not words:
                return self._reply(
                    'Give me something to say: speak "the kettle has boiled"',
                    status="warning")
            # The reply *is* the text, so the ordinary "speak her reply
            # aloud" path reads it - no second mechanism, and the
            # playback bar it already puts at the top of the window is
            # the pause and stop this needs.
            return self._reply(words, action="speak_aloud")

        # The user's own commands, checked after the built-ins so that
        # nobody can shadow "shutdown" with something of their own, and
        # before giving up so that a command they wrote is a command.
        own = self._run_user_command(lower)
        if own is not None:
            return own

        return self._reply(
            'That command is not in my list of commands. Say "show commands" to see what I can do.',
            status="error",
        )

    # ---------------- helpers ----------------

    def _run_user_command(self, said):
        """Starts one of the user's own commands, if they said one.

        Returns a reply, or None if they did not - so the caller can go
        on to say it does not recognise the command.

        The three lines a command carries land when they describe:
        starting and running go out with this reply, as the command is
        launched; finished arrives later, from the console watching
        /api/user-commands/activity, under whatever the command printed.
        The first version of this waited for the command and said all
        three at once, which made "this takes a moment" a report on a
        moment that had already passed.
        """
        from quietude.core import user_commands

        asked = said.strip().strip(".!?")
        if not asked:
            return None

        match = None
        for command in user_commands.all_commands():
            name = command["name"].strip().lower()
            if asked == name or asked == f"run {name}":
                match = command
                break
        if match is None:
            return None

        result = user_commands.start(match["slug"])

        if result.get("busy"):
            return self._reply(result["error"], status="warning")
        if not result.get("ok"):
            return self._reply(result.get("error") or "I couldn't start it.",
                               status="error")

        lines = [line for line in (match.get("say_starting"),
                                   match.get("say_running")) if line]
        if not lines:
            lines = [f"Running “{match['name']}”."]
        return self._reply(
            "\n\n".join(lines),
            # No status: the bubble colours are success, warning and
            # error, and a command that has only just started is none
            # of the three yet. The one that does carry a colour is the
            # finishing line, which knows how it went.
            action="user_command_started",
        )

    def _reply(self, text, expect="text", shutdown=False, restart=False, voice=None,
               action=None, table=None, status=None, conversation_turns=None):
        # Filled in here rather than at each call site. Doing it at the
        # call sites meant one reply in forty went out with a literal
        # "{wake}" in it, and the only way to find those was to read
        # every one of them.
        return {
            "text": self._text(text), "expect": expect, "shutdown": shutdown, "restart": restart,
            "voice": voice, "action": action, "table": table, "status": status,
            "conversation_turns": conversation_turns,
        }

    def _conversation_turns(self):
        return {"used": len(self.gemini_history), "cap": gemini_tool.MAX_HISTORY_TURNS}

    def _gemini_reply(self, text):
        api_key = database.get_gemini_api_key(self.user) if self.user else None
        if not api_key:
            self.conversation_mode = False
            return self._reply(
                "I lost access to your Gemini API key, so I've switched back to static "
                'commands. Register one again with: gemini api key "YOUR_API_KEY"',
                status="error",
                action="conversation_mode_off",
            )

        reply_text, new_history, reason = gemini_tool.chat(api_key, self.gemini_history, text)
        if reply_text is None:
            messages = {
                "rate_limited": "Gemini is rate-limiting me right now - too many requests too "
                                 "quickly. Give it a moment and try again.",
                "auth": "Your Gemini API key looks invalid, expired, or unauthorized. Double "
                        'check it with: gemini api key "YOUR_API_KEY"',
                "server": "Gemini's servers are having trouble right now - this usually clears "
                          "up on its own. Try again shortly.",
                "blocked": "That got blocked or came back empty - possibly Gemini's safety "
                           "filters. Try rephrasing it.",
                "unknown": "I couldn't reach Gemini just now - check your API key and internet "
                           'connection, or say "turn off conversation mode" to go back to '
                           "normal commands.",
            }
            return self._reply(messages.get(reason, messages["unknown"]), status="error")

        self.gemini_history = new_history
        return self._reply(reply_text, conversation_turns=self._conversation_turns())

    def _face_required_gate(self, action_label):
        """Call at the top of any command that requires a registered face.
        Returns a reply blocking the action and offering to register now
        if face isn't registered, or None if the caller should proceed
        normally."""
        if self.user and self.user.get("face_registered"):
            return None
        self.pending = {"type": "face_register_prompt"}
        return self._reply(
            f"Face recognition isn't registered yet, so I can't {action_label} - "
            "this requires a registered face first.\n\n"
            "Would you like to register your face now? (yes/no)",
            expect="confirm",
            status="error",
        )

    def post_login_prompt(self):
        """Called once right after a successful login (face or password).
        Returns the face-registration nag if face isn't registered yet -
        repeated every login until it is - or None if it's already set up."""
        if not self.user or self.user.get("face_registered"):
            return None
        self.pending = {"type": "face_register_prompt"}
        return self._reply(
            "One more thing - your face still isn't registered.\n\n"
            "Would you like to register it now? (yes/no)",
            expect="confirm",
        )

    def _account_table(self):
        u = self.user

        def preview(value, length=32):
            value = value or ""
            return (value[:length] + "...") if len(value) > length else value

        return [
            ["Full Name", u.get("full_name", "-")],
            ["Preferred Name", u.get("name", "-")],
            ["Age", str(u.get("age", "-"))],
            ["Backup Password", f"Hashed: {preview(u.get('login_password_hash'))}"],
            ["Gemini API Key", f"Encrypted: {preview(u.get('gemini_api_key_enc'))}" if u.get("gemini_api_key_enc") else "Not set"],
            ["Face Registered", "Yes" if u.get("face_registered") else "No"],
            ["Account Created", u.get("created_at", "-")],
        ]

    def _start_change(self, field):
        self.pending = {"type": "account_change", "field": field, "stage": "await_value"}
        label = FIELD_LABELS[field]
        return self._reply(f"Sure - what would you like your new {label} to be?")

    def _handle_pending(self, text, lower):
        p = self.pending

        if p.get("type") == "face_reregister_prompt":
            if lower in YES_WORDS:
                self.pending = None
                return self._reply(
                    "Okay - let's update your face registration.",
                    action="register_face",
                )
            if lower in NO_WORDS:
                self.pending = None
                return self._reply("Okay, no changes made.")
            return self._reply('Please choose one of the valid options: "yes" or "no".',
                                expect="confirm", status="error")

        if p.get("type") == "face_register_prompt":
            if lower in YES_WORDS:
                self.pending = None
                return self._reply(
                    "Great - let's get your face registered now.",
                    action="register_face",
                )
            if lower in NO_WORDS:
                self.pending = None
                return self._reply(
                    "No problem. Just know that until your face is registered, some features - "
                    "like changing account details or resetting data - won't be available.",
                    status="warning",
                )
            return self._reply('Please choose one of the valid options: "yes" or "no".',
                                expect="confirm", status="error")

        if p.get("type") == "reset_confirm":
            if lower in YES_WORDS:
                self.pending = None
                return self._reply(
                    "Okay - let's verify it's really you first. Look at the camera.",
                    action="reset_confirm",
                )
            if lower in NO_WORDS:
                self.pending = None
                return self._reply("Okay, cancelled. Nothing was changed.")
            return self._reply('Please choose one of the valid options: "yes" or "no".',
                                expect="confirm", status="error")

        if lower in ("cancel", "nevermind", "never mind"):
            self.pending = None
            return self._reply("Okay, no changes made.")

        if p["stage"] == "await_value":
            if not text:
                return self._reply(
                    'That doesn\'t look valid - please enter a value, or say "cancel".',
                )

            field = p["field"]
            if field == "age":
                if not text.isdigit() or not (0 < int(text) <= 130):
                    return self._reply("Please enter a valid age as a number.")

            p["new_value"] = text
            p["stage"] = "await_confirm"
            label = FIELD_LABELS[field]
            return self._reply(
                f'Just to confirm - change your {label} to "{text}"? (yes/no)',
                expect="confirm",
            )

        if p["stage"] == "await_confirm":
            if lower in YES_WORDS:
                field = p["field"]
                value = p["new_value"]
                database.update_user_field(self.user["id"], field, value)
                self.set_user(database.get_primary_user())
                self.pending = None
                label = FIELD_LABELS[field]
                return self._reply(
                    f"Your {label} has been updated. "
                    + self._text("Restarting {name} to apply the changes..."),
                    restart=True,
                    status="success",
                )
            elif lower in NO_WORDS:
                self.pending = None
                return self._reply("Okay, no changes made.")
            else:
                return self._reply('Please choose one of the valid options: "yes" or "no".',
                                    expect="confirm", status="error")

        # safety net - should never be reached
        self.pending = None
        return self._reply("Something went sideways there - let's start over. How can I help?")
