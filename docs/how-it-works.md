# How Quietude works

This is the long version — the design decisions, what was tried, what was
measured, and why things ended up the way they did. If you just want to
install it and use it, the [README](../README.md) is all you need.

---

## Contents

- [What Quietude does](#what-quietude-does)
- [Installing](#installing)
- [Running her](#running-her)
- [The microphone, and why there is no Docker](#the-microphone-and-why-there-is-no-docker)
- [Models, and staying offline](#models-and-staying-offline)
- [Commands](#commands)
- [Voice chat](#voice-chat)
- [Conversation mode](#conversation-mode)
- [Where your data lives](#where-your-data-lives)
- [Uninstalling](#uninstalling)
- [What changed from the Windows build, and why](#what-changed-from-the-windows-build-and-why)
- [Face login and PAM: a recommendation](#face-login-and-pam-a-recommendation)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [Open questions](#open-questions)

---

## What runs where

Every subsystem, what it uses, and whether anything leaves your machine.
Quietude reports this live too — say **`show features`** and she'll tell you
the state of the actual install rather than what the docs hoped for.

| Subsystem | Engine | Leaves this machine? |
|---|---|---|
| Face recognition | OpenCV Haar cascade + LBPH | **No.** Model trained and stored here |
| Wake word | Vosk (small English model) | **No.** Runs on every audio chunk, locally |
| Transcription | faster-whisper, int8 on CPU | **No.** Loaded `local_files_only` — it *cannot* reach the network |
| Speech synthesis | piper (neural), espeak-ng fallback | **No.** Offline CPU inference |
| Microphone capture | Your browser's AudioWorklet | **No.** Resampled in the page, posted to loopback |
| Camera capture | Your browser | **No.** Frames posted to loopback |
| Your profile | JSON + scrypt + Fernet | **No.** Key generated here, file is `0600` |
| Web interface | Vue 3, served by Flask | **No.** Binds `127.0.0.1` only |
| Fonts, icons | Bundled | **No.** Downloaded once at install, then local |
| **Conversation mode** | **Google Gemini** | **YES — this is the only one.** Off by default, needs your own key, separate install |

So: the only network traffic Quietude ever makes after installation is
conversation mode, which is opt-in, clearly indicated in the top bar
while it's on, and whose dependency isn't even installed unless you ask
for it.

Models are downloaded once during `./install.sh`. After that you can
unplug the machine and everything except conversation mode behaves
identically.

## What Quietude does

She runs as a small local server with a browser interface. You set up an
account on first launch, log in with your face (or a backup password),
and then talk to her — typed, or hands-free with a wake word.

Everything is local and offline:

| Feature | How |
|---|---|
| Face login | OpenCV Haar cascade detection + LBPH recognition |
| Wake word | Vosk, streaming, always listening |
| Transcription | faster-whisper, int8 on CPU, once per utterance |
| Her voice | piper (neural TTS), with espeak-ng as a fallback |
| Your profile | A JSON file, scrypt-hashed password, Fernet-encrypted secrets |

The one exception is **conversation mode**, which sends what you say to
Google's Gemini API. It needs your own API key, it is off by default,
and its dependency is in a separate requirements file so that not having
it is a real, enforceable choice rather than a setting you have to
trust.

---

## Installing

```bash
./install.sh
```

Takes a few minutes, mostly Python dependencies and ~250 MB of models.
It will:

1. install system packages via your package manager (`apt`, `dnf`,
   `pacman`, `zypper` or `apk` — it detects which)
2. build a virtualenv at `~/.local/share/quietude/venv` — nothing is
   installed into your system Python
3. download the speech models and a voice
4. build the interface
5. install a `quietude` command, a systemd user service, and a desktop entry

Python dependencies come in two files, and the split is deliberate:

| File | Contents | On failure |
|---|---|---|
| `backend/requirements.txt` | Server, storage, encryption, face login | Fatal — Quietude can't run |
| `backend/requirements-voice.txt` | vosk, faster-whisper, piper | Warns and continues |

Nothing in the voice stack is needed for text chat, face login, the
account flow or the interface, so a wheel that isn't available for your
interpreter costs you voice and nothing else. Quietude then says voice is
unavailable rather than pretending to listen. One requirements file
would abort the whole install over it — which is exactly what used to
happen on Python 3.14.

Version floors rather than exact pins, for the same reason: several of
these ship interpreter-specific wheels, and support for a new Python
arrives in a *new* release, so pinning an old one guarantees failure on
a new interpreter. Verified on Python 3.11, 3.13 and 3.14.

### Useful flags

| Flag | Effect |
|---|---|
| `--no-models` | Skip the ~250 MB of models. Text chat works; voice doesn't. |
| `--models-only` | Download models into an existing install. |
| `--no-system-deps` | Don't touch the package manager. It prints what it needs. |
| `--no-service` | No systemd unit or desktop entry; just the `quietude` command. |
| `--no-fonts` | Skip the webfonts; the UI falls back to your system monospace. |
| `--desktop-only` | Reinstall just the icon and desktop entries, and refresh the desktop's caches. No rebuild, no downloads. |
| `--doctor` | Print what your desktop can actually see of Quietude, and change nothing. |
| `-y`, `--yes` | Don't prompt. |
| `--uninstall` | Remove everything installed, keep your profile. |
| `--purge-data` | With `--uninstall`, erase your profile too. |

`QUIETUDE_PREFIX`, `QUIETUDE_VENV`, `QUIETUDE_WHISPER_MODEL` and `QUIETUDE_PIPER_VOICE`
override the defaults. `./install.sh --help` lists everything.

### If you'd rather install the system packages yourself

```
python3 (with venv and headers), nodejs, npm, espeak-ng, libgomp, libGL, curl, unzip
```

**Node must be 18 or newer** to build the interface. Several distributions
pair a current Node with an old npm, or ship a `nodejs` package that is
years behind — Ubuntu 22.04 and Debian 11 both ship Node 12. The
installer checks this up front rather than letting you discover it after
several hundred megabytes of downloads, and falls back from `npm ci` to
`npm install` if your npm is older than the lockfile.

Then `./install.sh --no-system-deps`.

Note what isn't in that list: **nothing audio-related except espeak-ng.**
No ALSA headers, no PulseAudio or PipeWire libraries, no portaudio. The
backend never opens an audio device. That absence is the single clearest
consequence of the audio architecture, and the next section is about why.

---

## Running her

```bash
quietude                                  # opens in a window of her own
quietude --window tab                     # a tab in your existing browser
quietude --window none                    # nothing opens; open it yourself
systemctl --user start quietude           # in the background, no window
systemctl --user enable --now quietude    # and at every login
quietude --port 8080                      # somewhere else
```

### Her own window

By default Quietude opens in a real window — a GTK window with a WebKit
view in it, run as its own process by the system Python. Not a browser
dressed up as one, which is what it used to be.

That distinction is the whole point, because three things turned out to
be impossible from outside a browser window and are ordinary properties
of a real one:

| | a Chromium `--app` window | a GTK window |
|---|---|---|
| its icon in the dock | derived from the URL's host; not settable | `WM_CLASS` and the Wayland `app_id` are ours |
| a minimum size | no flag exists; CSS can only stop the *layout* collapsing | `set_size_request` — the window manager refuses to go smaller |
| opening centred | absolute coordinates only, and impossible on Wayland | `set_position(CENTER)` |

So she **opens at 1180×760, centred, and cannot be dragged smaller than
that**. Maximise and resize upward freely; there is no size below the
opening size that the interface was designed for, so there is nothing
below it to offer. On a screen too small for that, the window opens as
large as fits, never below 900×620.

The engine is WebKitGTK rather than Chromium, and that was measured
before it was trusted: the two features that define this app both live
in the browser's media stack. Before any of it was written —

| | |
|---|---|
| secure context | yes |
| `getUserMedia` | opens a track |
| `AudioWorklet` | registers and instantiates |
| canvas capture | yes |

If any of that had come back no, the window would still be a browser.

Needs `python3-gi`, GTK 3 and WebKit2GTK 4.1, which `install.sh`
installs. Without them she falls back to a Chromium app window, and
without that to a tab — each rung works, and `./install.sh --doctor`
says which one you are on.

The backend owns the window either way, so the relationship runs both
ways:

| | |
|---|---|
| `shutdown`, the SHUT DOWN button, `Ctrl+C` | the window closes |
| closing the window | Quietude shuts down |

Set it permanently in `~/.config/quietude/settings.json`:

```json
{ "window": "app" }
```

`"app"` (default), `"tab"` or `"none"`. `QUIETUDE_WINDOW` overrides it for
one run.

**It's a browser underneath** — launched with `--app`, its own profile
directory, and its own WM class so the desktop files it under Quietude's
icon rather than the browser's. A native GTK window via PyGObject and
WebKitGTK was the obvious alternative and was tested rather than
assumed: WebKitGTK does support `getUserMedia`, AudioWorklet and canvas,
so Quietude's face login and wake word *could* run in it. It was still the
wrong trade.

Quietude's two defining features both ride on the browser's media stack — an
AudioWorklet resampling microphone audio for the wake word, and canvas
frame capture for face recognition — and both were built and tested
against Chromium. Changing the engine to get a nicer window frame would
put the two things that make Quietude *Quietude* on an engine they have never
run on, for cosmetics. And PyGObject's C extension is built against the
distribution's own Python, so depending on it would force the virtualenv
onto that exact interpreter — the same class of fragility that already
broke an install here once.

The separate profile directory isn't a nicety either. Without it, a
browser you already have open adopts the new window, the process Quietude
launched exits immediately, and she would read that as you closing the
window and shut down a second after starting. It also keeps her out of
your browsing session, extensions and cookies.

If no Chromium-family browser is installed, Quietude says so and opens a tab
instead. If the window fails to start, she notices that it died too
quickly to have been you closing it, says so, falls back to a tab, and
keeps running. Firefox can't do app mode — it dropped site-specific
browsers and `--kiosk` is fullscreen, not a window — so install
`chromium` if you want the window and only have Firefox.

`QUIETUDE_WINDOW_FLAGS` passes extra flags to the browser, for setups that
need one.

### Wayland and X11

Both work, and Quietude does nothing that only works on one of them.

She asks the browser for `--ozone-platform-hint=auto`, so the window
uses the session's native display server — Wayland on a Wayland session,
X11 otherwise. Several distributions' Chromium builds still default to
XWayland on a Wayland session, which costs fractional scaling and crisp
text on HiDPI screens; the hint fixes that. `auto` rather than forcing
Wayland, because forcing it on a build without proper support means no
window at all.

Nothing in Quietude finds, positions or manipulates a window after it
exists. The usual X11 toolbox — `xdotool`, `wmctrl`, raw window-manager
calls — does not work under a Wayland compositor, which deliberately
refuses to let clients manage each other's windows. Everything is asked
for at launch instead (`--app`, `--class`, `--window-size`), so the
browser creates the window it was told to and nobody has to move it
afterwards. That behaves identically on both.

The window's identity is worth recording, because the obvious approach
looks correct and isn't.

`WM_CLASS` has two fields, an instance and a class. Chromium's `--class`
flag sets only the second. The first it derives from the host of the
window's URL and from nothing else — so an app window opened on
`http://127.0.0.1:5000/` reports:

```
WM_CLASS = ("127.0.0.1", "quietude")
```

GNOME tries the instance, then the class, so it found Quietude. KDE's task
manager, Plank and xfce4-panel match on the instance only: they looked
for an application called `127.0.0.1`, found none, and fell back to a
generic gear. The icon was installed correctly the whole time and
resolved correctly the whole time; nothing was ever going to ask for it.

So the window is opened on a name rather than an address, with the
mapping passed explicitly rather than relying on the implicit
`.localhost` rule:

```
--app=http://quietude.localhost:5000/
--host-resolver-rules=MAP quietude.localhost 127.0.0.1
--class=quietude
```

which gives `WM_CLASS = ("quietude.localhost", "quietude")`, and the installer
registers an entry for each field — `quietude.desktop` for the class, and a
`NoDisplay` companion for the instance, so the menu still shows one Quietude.

| | |
|---|---|
| X11 | both `WM_CLASS` fields name Quietude; an entry matches either one |
| Wayland | the `app_id` is `quietude`, matched against `quietude.desktop` by basename |

Lowercase throughout, because some compositors match the app id to the
desktop file case-sensitively. The capitalised **Quietude** you see in the
dock comes from `Name=` in the desktop entry, not from the class.

`quietude.localhost` is loopback by specification and stays a secure
context, so the microphone and camera are unaffected. If it ever causes
trouble, `QUIETUDE_WINDOW_HOST=off` opens the address directly and gives up
only the icon.

An explicit `--host-resolver-rules` mapping was tried first, as belt and
braces in case some build didn't honour the implicit `.localhost` rule.
It was removed: Chromium treats that switch as a testing flag and puts a
permanent *"You are using an unsupported command-line flag"* banner
across the top of the window because of it. A certain, visible,
permanent banner traded against a hypothetical resolution failure is the
wrong way round.

#### She has a minimum size

The window opens at 1440×900, but nothing stops you dragging a corner,
and the layout used to come apart well before the window got small.
Below 780px the two columns collapse into one and the command list is
hidden outright; measured at 680×520, the chat log was 134px — about one
message — and the commands were gone.

So in a window of her own the layout has a floor of **900×620**. Below
it the window scrolls instead of reflowing: the two columns stay two
columns, nothing is hidden, nothing is squeezed into a size it was never
designed for.

This cannot stop the window being dragged smaller. There is no portable
way to hand a window manager a minimum size for a browser window, on
X11 or Wayland. What it does is make a smaller window show *less of* the
interface rather than a broken one.

The floor applies only in her own window, keyed on `display-mode:
standalone` — true for a Chromium `--app` window and for an installed
PWA, false in a tab. Verified against a real window rather than assumed,
which is also the only way to test it: Chrome DevTools cannot emulate
that media feature, so the test drives a genuine `--app` window and
resizes it for real. A browser tab keeps the narrow layout it always
had.

Camera and microphone go through the browser, so permission prompts,
device selection and PipeWire or PulseAudio routing are handled by the
session's existing plumbing rather than by Quietude. There is nothing
display-server-specific to configure.

**If the dock still shows the wrong icon,** ask before guessing:

```bash
./install.sh --doctor
```

It prints what your desktop can actually see — which entries are
installed, whether they validate, whether the name `quietude` resolves to a
file, and what `WM_CLASS` her window will report — and changes nothing.
Desktop integration has about six independent ways to fail and from the
outside they look identical, which is the whole reason this exists.

To reinstall just the icon and entries, with no rebuild and no
downloads:

```bash
./install.sh --desktop-only
```

If everything reports green and the dock still disagrees, it's holding a
cached copy: GNOME on X11 takes <kbd>Alt</kbd>+<kbd>F2</kbd>, `r`,
<kbd>Enter</kbd>; GNOME on Wayland needs a log out and back in; KDE takes
`plasmashell --replace`.

**Or install it as a PWA.** Quietude ships a web manifest, so a
Chromium-family browser will offer "Install" and give her a launcher
entry and her own window that way instead. Equivalent result; it just
puts you in charge of the window rather than the backend.

She listens on `127.0.0.1:5000` — loopback only, not reachable from your
network.

**Stopping her:** the SHUT DOWN button, saying or typing `shutdown`,
`Ctrl+C`, or `systemctl --user stop quietude`. All four take the same path.

Closing her **window** does stop her. Closing a **tab** doesn't — see
[the heartbeat section](#the-heartbeat-watchdog-is-gone) for why those
are different.

---

## The microphone, and why there is no Docker

The rebuild was originally specified as a single `docker compose up`. It
isn't, and the microphone is the reason.

Quietude needs to hear you. In a container that means passing an audio
device through: either bind-mounting `/dev/snd` and adding the container
user to `audio`, or forwarding the PipeWire or PulseAudio socket along
with its authentication cookie. Both are host-specific, both break
differently on each distro and each session type, and both are fragile
in the particular way that matters most here — they produce a container
that *starts perfectly* and simply can't hear anything. That is the
worst possible failure mode for the headline feature.

Natively, there is nothing to pass through, because **the browser holds
the microphone.** The same way it already held the webcam for face
login, in the Windows build and this one.

So the audio path is:

```
your mic
  -> browser getUserMedia (one permission prompt, which you can revoke)
  -> AudioWorklet: resample to 16kHz mono int16, chunk at 250ms
  -> POST /api/speech/audio   (raw octet-stream, no JSON, no base64)
  -> the speech worker's queue -> Vosk and faster-whisper
```

and the reverse, for her voice:

```
POST /api/tts/speak
  -> the TTS worker synthesizes a WAV to the cache directory
  -> returns a clip id
  -> GET /api/tts/clip/<id>  -> an <audio> element in the browser
```

The backend never touches `/dev/snd`, never links against PipeWire, and
holds no audio device open. What this buys:

- **No device passthrough to configure.** The reason there's no Docker.
- **Pause, resume, stop and seek are free.** They're properties of an
  `<audio>` element. The Windows build drove a pygame mixer in a worker
  process through four HTTP routes and a 400ms polling loop, with a
  special case for telling "still synthesizing" apart from "finished" —
  because from outside the worker, the two were indistinguishable. Three
  of the four routes and the entire polling loop are gone.
- **Volume changes don't need re-synthesis.** It's a property, not a
  parameter baked into the audio.
- **The mic permission stays revocable, per-site, in one obvious place.**

What it costs: roughly 10–20ms of added latency versus capturing
directly through PipeWire, and a linear-interpolation resample done in
JS. Both are immaterial against a pipeline whose next step is a Whisper
inference measured in hundreds of milliseconds. The resampler runs on
the audio thread in an AudioWorklet, not the main thread, so a busy UI
can't cause dropouts.

**If you want server-side capture anyway** — a headless install with no
browser, say — `core/speech_engine.py` takes 16kHz mono int16 on a
queue and doesn't care where it came from. A PipeWire capture loop
feeding `push_audio()` would be perhaps 60 lines. It would also make the
backend depend on an audio device, which is the thing this design
deliberately avoids, so it isn't the default.

---

## Models, and staying offline

Three models, downloaded once by `install.sh`, then never touched again
(voices aside — see the voice library below):

| Model | Size | Where | Job |
|---|---|---|---|
| `vosk-model-small-en-us-0.15` | ~40 MB | `~/.local/share/quietude/models/vosk/` | Wake word and the interrupt phrase |
| `faster-whisper-base.en` | ~150 MB | `~/.local/share/quietude/models/whisper/` | Transcribing commands |
| `en_US-amy-medium` (piper) | ~65 MB | `~/.local/share/quietude/models/piper/` | Her voice — and any others you add from the voice library |

They live under your data directory, not in the repo and not in the
install prefix — so updating Quietude never re-downloads them, and
`--uninstall` leaves them alone.

**Why pre-download rather than fetch on first use.** faster-whisper will
happily download its own weights the first time you load it. Quietude
doesn't let it: the model is loaded with `local_files_only=True`, so the
engine *cannot* reach for the network, even if a file is missing. The
only moment Quietude touches the internet is during `install.sh`. After
that, pull the plug and everything except conversation mode works
identically.

**Swapping the transcription model:**

```bash
QUIETUDE_WHISPER_MODEL=Systran/faster-whisper-small.en ./install.sh --models-only
```

`base.en` is the default because these are short fixed commands, not
dictation — it transcribes one in well under a second on modest CPU.
`small.en` is noticeably more accurate and roughly twice the latency.

### The voice library

Say **`voice library`** (or click it in the sidebar). There are around a
thousand piper voices across sixty-odd languages; browse them by
language or name, see what each one costs in megabytes, and download the
ones you want.

Voices used to be the one thing you couldn't change without a terminal —
`QUIETUDE_PIPER_VOICE=... ./install.sh --models-only`, which is a strange
thing to ask of someone who just wants a different accent. That still
works for setting the voice installed at first run; everything after
that happens in the app.

**This is the only screen in Quietude that uses the internet,** and it says
so on the page rather than quietly doing it. It fetches a list of voices
and downloads files from one host. Nothing about you is sent. The list
is cached for a day, so opening the library twice is one request, and
opening it on a train still shows you what's installed.

Downloads are verified and atomic. Each file streams to a temporary name
beside its destination, is hashed as it arrives, is checked against the
size and md5 from the catalog, and only then moved into place. A
download that is interrupted or corrupted leaves nothing behind — which
matters, because a half-written `.onnx` would otherwise list as a
perfectly good voice and then crash the worker the first time she tried
to use it.

Voices are discovered from disk at runtime, so one you just downloaded
appears in `tts settings` immediately, with no restart. It is also
loaded into the running engine the moment it arrives, so the first thing
she says in it isn't the sentence that waits for the model.

If your network can't reach HuggingFace but mirrors it, point Quietude at
the mirror:

```bash
QUIETUDE_VOICE_CATALOG_URL=https://mirror.example/piper-voices/voices.json
QUIETUDE_VOICE_BASE_URL=https://mirror.example/piper-voices/
```

### Models are loaded once

All of them are loaded at startup and kept, so the work at the time you
ask for something is only the work of answering:

| | |
|---|---|
| vosk, faster-whisper | loaded in the speech worker at launch — already the case |
| piper | **now** loaded at launch too, and up to three voices stay resident |

The piper voice used to load lazily on the first phrase, and the loaded
voice was dropped the instant a different one was asked for. That cost
most of a second on the first thing Quietude ever said — during the greeting,
the worst possible moment — and it meant auditioning voices in the
settings page reloaded a model per click, with switching back reloading
it again. Measured, with a stub counting every load:

| | before | after |
|---|---|---|
| first phrase after launch | pays the model load | 9 ms |
| returning to a voice you already heard | full reload | 1 ms |

Three voices stay resident because comparing two or three is the thing
people actually do; past that the least recently used is dropped, so a
library full of downloads can't grow without bound.

If a model is missing, Quietude says so plainly — at boot, in the status
API, and when you try to turn voice chat on. She does not pretend to be
listening.

---

## Who she is

Setup asks what to call her, because she does not have a name until you
give her one. Shipping a default would have quietly made that default
almost everyone's answer.

Say **`who are you`**, or click it in the sidebar, to change any of it
later:

| | |
|---|---|
| **name** | what she is called — and her wake word |
| **pronouns** | she / he / they |
| **age**, **nationality** | colour how she introduces herself and how she speaks in conversation mode |

**Everything applies as you type it.** There is no Save button. Three of
these four only change how she refers to herself, and watching the
example sentence rewrite itself as you type explains the setting better
than any label could. Renaming her relabels every message already on
screen, retitles the window, and rewrites the command reference — in
place, with nothing reloaded.

### Why the name matters more than it looks

The name is what her wake word is built from: call her Ada and you wake
her by saying **"Hello Ada"** (`hey`, `ok` and `hi` work too). Her name
on its own deliberately does not wake her — a name that fired every time
it came up in conversation would be unusable.

This is also the one place the choice can go quietly wrong. **A wake-word
model recognises a fixed vocabulary.** It is not spelling out sounds; it
is choosing among words it was trained on. A name outside that vocabulary
can never be the wake word, however clearly you say it, and the failure
is completely silent — she simply never wakes up, with nothing saying
why.

So the name is checked against the model's own lexicon while you type it,
and the page tells you the answer before you commit:

> ⚠ The wake-word model doesn't know "Zyx", so it will never hear it as a
> wake word. Everything else will still use the name — it is only the
> wake word that needs a word the model was trained on.

Everything else about a name the model doesn't know still works. Only the
wake word needs one it has heard of.

The name changes what she says and what she listens for. It does not
touch your account, your data, or anything on disk.

---

## Speech models

Say **`speech models`**. Two roles, chosen separately:

| | |
|---|---|
| **wake** | Vosk. Listens for the wake word, and captions you live as you speak. |
| **transcribe** | faster-whisper. Decides what you actually said, once you have finished saying it. |

Both ship small and both can be upgraded from inside the app — larger
models hear more and cost more, in megabytes and in latency. Downloads
are verified and atomic, the same as the voice library, and choosing a
different model reloads the engine live rather than asking you to
restart.

Along with the voice library, this is the second and last screen that
touches the network, and it says so on the page.

### When she isn't hearing you

This was a real bug, and worth describing because the fix is the
interesting part.

Deciding whether someone is speaking used to be a single fixed number:
RMS 500 on 16-bit audio, about −36 dBFS. That is wrong in both
directions depending on the microphone.

- **Too high** for a quiet speaker or a low-gain mic. The voiced total
  never reaches its minimum, the utterance never finalises, and nothing
  is ever transcribed. From the outside that is indistinguishable from
  the assistant simply not hearing you — which is exactly how it was
  reported.
- **Too low** in a room with a fan or a desktop PC. Every chunk counts as
  speech, silence never accumulates, and the utterance runs to its
  fifteen-second ceiling before anything is transcribed.

No single number can be right for both, because the thing that varies is
not the speech — it is the floor underneath it. So the floor is now
measured continuously and the gate sits above it. Measured, with the old
gate pinned alongside the new one for comparison:

| | old fixed gate | measured floor |
|---|---|---|
| quiet speaker, quiet room | **nothing transcribed** | transcribed, gate settled at 141 |
| normal speaker, noisy room | — | transcribed, gate settled at 1393 |
| room tone alone | — | correctly ignored |

Two details that took finding. The floor rises slowly and falls quickly,
so a few seconds of speech cannot drag it up and deafen her mid-sentence
while a door slamming does not leave her deaf for the next minute. And
the hold-level — the lower bar that keeps an utterance open between two
words — is never allowed below the room itself, or in a noisy room the
background holds the sentence open forever and nothing is ever
transcribed.

There is also a **sensitivity** setting, 1–10, applied live, for when the
measurement still needs a nudge. `QUIETUDE_VAD_THRESHOLD` pins the gate
to a fixed value if you would rather do it yourself.

**You now also see what she hears.** Vosk runs while she is awake, not
only while she is asleep waiting for the wake word, so your words appear
as you say them and are then replaced by whisper's better transcription.
Waiting in silence for a model to finish is most of what made this feel
like it was not listening.

---

## Commands

The important ones are listed down the left of the console, and they are
buttons: clicking one runs it. The list is built from the same command
reference this table comes from, rather than being a second copy — it
used to be eight hardcoded strings, which meant a command could be
renamed or removed and the sidebar would go on suggesting it.

| Command | What it does |
|---|---|
| `help` | What she can do, in the chat |
| `show commands` | The full reference |
| `show account information` | Your saved details (hashed fields shown as hashes) |
| `change my name` / `change my age` | Asks, then always confirms yes/no |
| `register face` | Set up or update face recognition |
| `reset data` | Erase everything, after a confirmation and a face check |
| `turn on voice chat` / `turn off voice chat` | Hands-free mode |
| `tts settings` | Rate, volume, voice |
| `voice library` | Browse and download more voices — the one screen that goes online |
| `what time is it` / `what's the date` | |
| `show features` | Every subsystem, its engine, and whether anything leaves the machine |
| `clear terminal` | |
| `gemini api key "KEY"` | Register a key for conversation mode |
| `turn on conversation mode` | The one non-local feature |
| `shutdown` | Same as the button |

`reset data`, `change my ...`, `show account information` and setting a
Gemini key all require a registered face. If yours isn't registered, Quietude
explains why and offers to do it then and there.

---

## Voice chat

Say or type `turn on voice chat`. Quietude then has two states:

**Asleep** (green mic) — always listening, but only for **"Hello
Quietude."** Nothing else is transcribed, nothing is sent anywhere. The chat
box is locked and shows a blurred red preview of what she's picking up,
so you can see she's hearing sound without her acting on it.

**Awake** (yellow mic) — she chirps and says she's listening. What you
say is buffered until you pause, then transcribed in one go and treated
as a command — never word by word. Go quiet and she checks in once; stay
quiet and she goes back to sleep with a falling tone.

While she's speaking, say **"stop talking"** to cut her off. Recognition
stays on the whole time she talks specifically to catch that, and
ignores everything else — including her own voice coming back through
the mic.

The two models split the work because they're good at different things.
Vosk is small and streaming and can run on every chunk of audio forever,
but it isn't very accurate — fine for two fixed phrases. faster-whisper
is accurate but transcribes a finished clip in one shot, so it runs once
per utterance, on the thing that actually becomes a command.

An utterance ends when you've said something substantial *and* then
paused — at least 300ms of voiced audio, then 700ms of silence. Both
halves matter: without the voiced minimum a cough would become a
command; without the silence window she'd cut you off mid-sentence.
There's a hard 15-second ceiling so a stuck or noisy mic can't buffer
forever.

Voice activity detection is plain RMS energy thresholding, no VAD
library. If it misfires on your room, tune it:

```bash
QUIETUDE_VAD_THRESHOLD=800 quietude    # higher = less sensitive
```

**You can't reach the main interface until both models confirm they're
loaded.** That gate is deliberately hard: a half-loaded speech engine
can't reliably hear a wake word, and an assistant that *looks* like it's
listening but isn't is worse than one that says it isn't ready. It isn't
infinitely hard, though — after three minutes, or on a definite error, it
gives up and lets you in with voice disabled and an explanation, because
blocking forever would make text chat unreachable too.

---

## Conversation mode

By default every command runs locally. `turn on conversation mode`
switches to free-form chat with a Gemini-backed persona instead — and
this is **the one thing in Quietude that sends what you say to a third
party.**

```bash
~/.local/share/quietude/venv/bin/pip install -r backend/requirements-gemini.txt
```

Then, in Quietude:

```
gemini api key "YOUR_KEY"
```

The key is encrypted at rest (Fernet — reversible, because it has to be
usable) and never displayed. `gemini api key ""` removes it entirely and
turns the mode off.

An indicator in the top bar always shows which mode you're in: cyan
`STATIC` for fully local, purple `CONVERSATION` with a turn counter when
it isn't. `turn off conversation mode` always works immediately, even
mid-conversation.

To change the persona, model or history length, edit
`backend/quietude/core/gemini_tool.py` — it's one small self-contained file.

---

## Where your data lives

XDG directories, not a `data/` folder next to the code:

```
~/.local/share/quietude/      users.json, secret.key, face_model.yml, faces/,
                          uploads/, models/, venv/        (mode 700)
~/.config/quietude/           settings.json (host and port; nothing private)
~/.cache/quietude/            synthesized speech clips — safe to delete anytime
```

The Windows build kept everything in `data/` inside the source tree,
which meant your private data and the code were the same directory. The
practical payoff of moving it: `--uninstall` can remove every byte of
installed code without touching your profile, and `--purge-data` can
erase your profile deliberately, as two separate decisions.

Security properties, unchanged from the Windows build:

- Your backup password is **hashed** with scrypt (per-field salt) and is
  not recoverable, by Quietude or by anyone reading the file.
- The Gemini key is **encrypted**, not hashed — it has to be usable.
- The encryption key is generated on this machine, stored `0600`, and
  never leaves it.
- `users.json` is written to a temp file and atomically renamed, so a
  crash mid-write leaves the previous profile intact rather than a
  truncated one.
- Four wrong backup-password attempts wipe all local data. That's a
  brute-force guard on a path that bypasses face recognition entirely.

---

## Uninstalling

```bash
./install.sh --uninstall
```

Removes the virtualenv, the `quietude` command, the systemd unit, the
desktop entry and the icon. **Your profile, face model and downloaded
models are kept** — reinstalling picks up exactly where you left off.

```bash
./install.sh --uninstall --purge-data
```

Also erases `~/.local/share/quietude`, `~/.config/quietude` and
`~/.cache/quietude` — your account, encryption key and face model included.
Irreversible, and it asks first.

Neither touches the repo; delete the directory to finish.

---

## What changed from the Windows build, and why

### The shutdown machinery mostly disappeared

This is the single biggest simplification, so it's worth being precise
about what went and why.

The Windows build had four independent mechanisms for stopping
reliably:

1. an in-process failsafe timer
2. a PowerShell kill-watcher, spawned at startup and polling a flag file
3. an external `cmd.exe` kill-switch running `taskkill /F /T`
4. a Win32 call to close the console window

That was not over-engineering. `Ctrl+C` genuinely isn't delivered
reliably in some Windows console configurations, a Python process can end
up unable to kill itself, and `taskkill /T` was the only thing certain to
take the worker processes down with the parent.

Linux removes the need for all four:

- **SIGINT and SIGTERM are delivered reliably**, and a Python handler
  actually runs — so (1) and (2) have nothing to do.
- **SIGKILL cannot be caught or ignored**, so escalation is one syscall
  rather than an external process — (3) collapses into eight lines in
  `process_utils.py`.
- **Nothing owns a console window** — (4) doesn't exist.

What's left is the part that was always the real work, and the one
principle worth keeping: close things in a known order, wait a bounded
time for each, then stop waiting.

```
1. the HTTP server    so nothing can hand the workers new jobs
                      while we're closing them
2. the speech worker  releases the audio queue; holds the most memory
3. the TTS worker     may be mid-synthesis; its clips are cache files,
                      so losing one costs nothing
4. the Managers       last, because both workers write to their shared
                      status dicts right up until they exit
```

Each step waits a bounded time, then sends SIGKILL. No platform
branches, no retry ladders, no watchdog watching the watchdog. It's all
in `lifecycle.py` and `process_utils.py`, and both are short enough to
read in full.

A second `Ctrl+C` while shutting down exits immediately, because someone
pressing it twice wants it gone now rather than politely.

### The heartbeat watchdog is gone

The Windows build had the page ping `/api/heartbeat` every 3 seconds and
shut the server down when pings stopped — because the UI was a Chrome
app-window, and closing it had to mean "Quietude is closed", or an orphaned
Python process sat in the terminal forever.

That no longer describes how Quietude runs. Started from `quietude` or a systemd
user service, she's a process with her own lifetime and a browser tab is
a client that connects to her. Tying her life to a tab would mean a
refresh looks like a quit, a second tab is ambiguous, and a laptop
suspending its browser kills the assistant.

**Her own window is a different case, and she does quit when you close
it.** That isn't the heartbeat coming back — it's the opposite kind of
signal. The watchdog *inferred* the window was gone from pings stopping,
which is why a refresh or a sleeping laptop fooled it. The app window
*is* a process Quietude started, with its own profile so nothing else can
adopt it: the process exiting means the window closed, and nothing else
makes it exit. A refresh doesn't, and there are no tabs to be ambiguous
about.

So: closing her window quits her, closing a tab doesn't, and neither
involves guessing.

### No Chrome hunting, no app-window juggling

The Windows build searched Program Files for `chrome.exe`, launched it
with `--app=` for a frameless window, held the process handle, and fell
back to the default browser if Chrome wasn't found.

That's gone for a practical reason and a Wayland one. Practically, a
native window isn't needed — opening the URL in whatever browser you
already have is one call and keeps your extensions and settings. And
under Wayland the approach wouldn't have survived anyway: anything that
finds, positions or manipulates another application's window (the usual
`xdotool`/`wmctrl` toolbox) simply doesn't work, because a Wayland
compositor deliberately doesn't let clients manage each other's windows.

A browser's own app/kiosk mode *is* Wayland-compatible, because the
browser creates the window itself. So if you want a chrome-less window,
`QUIETUDE_BROWSER_APP_MODE=1` asks the browser for one — and it stays the
browser's business, not ours.

### pyttsx3 → piper

On Linux, pyttsx3 is a thin wrapper that shells out to espeak-ng. Keeping
it would have meant paying for an abstraction layer and still getting
espeak's robotic output.

piper is a neural TTS that runs fully offline on CPU, sounds markedly
more natural, and ships voices as two files you can drop in a directory.
espeak-ng is kept as an automatic fallback — it's in every distro's repos
and installs in seconds, so Quietude can always say *something* even before
anyone downloads a voice.

The worker-process architecture was kept, but the reason for it changed.
On Windows it was COM: SAPI5 behaves far better with a process to itself
than sharing threads with Flask. Here it's the model — piper loads tens
of megabytes of ONNX weights under a native runtime with its own thread
pool, and a crash or a stuck inference in that native code shouldn't be
able to take Flask with it. Keeping it warm is also the whole performance
story: loading costs most of a second, synthesizing a sentence costs a
fraction of one.

One thing did go: the per-phrase synthesis watchdog thread. The Windows
version wrapped every `runAndWait()` in its own thread with a 15-second
join, because pyttsx3 could hang indefinitely on a COM call with no way
to interrupt it. piper's synthesize call is ordinary Python into a native
library — it returns or it raises.

`rate` is still stored on the same 50–400 scale, so settings carry over;
it now maps onto piper's `length_scale` with 200 as 1.0.

### Web Speech API → Vosk + faster-whisper

The old voice input used the browser's Web Speech API, which needed an
internet connection, sent every utterance to Google for transcription,
and was unreliable in practice. For an assistant whose premise is that
nothing leaves your machine, that was the largest hole in the story.

It also needed constant babysitting: the Web Speech recognizer ends on
its own every few seconds, so `onend` scheduled a restart 80ms later with
guards against overlapping `start()` calls — and every millisecond in
that gap was audio simply never heard. The local engine doesn't stop, so
all of that is gone.

"Ignore what you hear while Quietude is speaking" also moved. It used to be a
frontend concern: a `ttsSpeaking` flag that the result handler checked, so
her own voice still reached the recognizer and was filtered after the
fact. Now suppression happens in the engine — the recognizer is told to
watch only for the interrupt phrase, so her voice is never transcribed at
all.

The frontend↔backend event pattern was kept exactly: three
monotonically-increasing counters (`wake_seq`, `stop_seq`,
`transcript_seq`) that the frontend diffs against its own last-seen
values. A counter moving means no event is ever missed, even if a poll is
slow or two events land between polls. A boolean flag would be.

### Jinja2 templates and a 1,700-line main.js → a Vue 3 SPA

The old frontend was three server-rendered templates plus one
`static/js/main.js` doing imperative DOM manipulation. Where it went:

| Was | Now |
|---|---|
| `showScreen()` toggling a `.hidden` class on 7 divs | `router/index.js`, with a guard that decides entry from backend state |
| Voice state machine (module-level flags) | `composables/useVoiceChat.js` |
| Web Speech API + restart juggling | `composables/useSpeechEngine.js` |
| Two camera loops | `composables/useCamera.js` + the two face views |
| TTS: 4 routes and a polling loop | `composables/useTts.js` — an `<audio>` element |
| ~90 lines of `createElement` per chat bubble | `stores/chat.js` (data) + `ChatBubble.vue` (view) |
| Boot overlay callbacks | `composables/useBoot.js` |
| `/commands` and `/tts-settings` as separate browser windows | Routes in the SPA |
| `/api/heartbeat` | Deleted |

`main.js` is now four lines plus a comment explaining where everything
went.

Two screens became routes rather than pop-up windows, which means they
have real URLs you can reload and bookmark, the back button works, and
they get the same styling as everything else instead of a second
template kept in visual sync by hand.

Pinia is used for exactly two stores — the session and the chat log —
because that's the state that genuinely outlives a component. Everything
else is a composable, because everything else is behaviour.

A few things were preserved deliberately because the old code had learned
them the hard way:

- **The camera loops are sequential and self-scheduling** (call, await,
  schedule the next) rather than `setInterval`. `setInterval` fires on a
  fixed clock whether or not the previous request finished, so one slow
  request puts several in flight at once, each able to independently
  "succeed". That was the traced cause of samples overshooting their
  count and of duplicate welcome messages on login. The status polling
  uses a chained timeout for the same reason.
- **The boot overlay outlives the transition it covers.** It awaits the
  handover before hiding, so there's never a frame where the overlay is
  gone but the screen underneath hasn't changed — which let the previous
  screen flash back into view.
- **Face registration always starts from zero samples.** There's no
  guarantee the person at the camera now is the one who started an
  earlier, abandoned attempt, so resuming one would be a security hole
  rather than a convenience.
- **Three consecutive matching frames** are required to unlock. LBPH
  isn't bank-vault-grade and a stray low-confidence frame is possible.

### The fonts are local now

The old stylesheet opened with an `@import` from `fonts.googleapis.com`
— so a "fully local, nothing leaves your machine" assistant made a
request to Google on every single load, and rendered in a fallback serif
with no internet.

`install.sh` downloads the four faces into `frontend/public/fonts/`.
They're in `public/` rather than `src/assets/` on purpose: Vite validates
asset imports at build time, so a font that failed to download would
break the whole build. Files under `public/` are copied verbatim, so a
missing one is a runtime 404 the browser recovers from by falling back to
your system monospace. That fallback is a real code path, not decoration.

### Flask stayed

The brief allowed swapping it. There was nothing to gain: the API is
about 30 small synchronous endpoints, none long-lived, none streaming.
The one thing that might have argued for an async framework — a websocket
for the speech pipeline — isn't there by design. Audio is one-way and
fire-and-forget, and events are picked up by comparing counters, which
can't miss an event the way a boolean can and needs none of the
reconnection logic a socket would.

There's also no nginx and no second container, because there's no Docker:
Flask serves the built SPA from `frontend/dist` directly. For a
single-user assistant on loopback, a reverse proxy would be a process to
supervise and a config to get wrong in exchange for throughput nobody
needs.

---

## Face login and PAM: a recommendation

The brief asked whether Quietude's from-scratch OpenCV face auth should stay,
or whether a [Howdy](https://github.com/boltgolt/howdy)-style PAM
integration would be worth offering alongside it.

**Recommendation: keep the in-app flow, and don't build a PAM module.**
Reasoning, honestly:

**They solve different problems.** PAM authenticates you to the
*operating system* — `sudo`, your display manager's lock screen, `su`.
Quietude's face check authenticates you to *Quietude*, deciding whether to show
your profile and allow account changes. A PAM module would not let Quietude
gate her own `reset data` command. The thing you'd be building isn't a
replacement for what's there; it's a different feature that happens to
use a camera.

**The security benefit is close to zero here.** If Howdy is already set
up on your machine, your session is already behind a face unlock. Quietude's
in-app check is a second gate on an already-unlocked session — useful
against someone walking up to your unlocked laptop, which is exactly what
it's for. Routing it through PAM wouldn't make that stronger.

**The cost is high and ongoing.** A PAM module means a shared object
loaded into `sudo`, `login` and your display manager. Bugs there don't
produce a stack trace; they produce a machine you can't log into. It
needs root to install, writes to `/etc/pam.d/`, is distro-specific, and
in the worst case locks you out of your own system. That is a serious
thing to take on for a personal assistant, and it is not reversible by
`./install.sh --uninstall`.

**Howdy already exists and does this well.** If you want Windows
Hello-style face unlock for your Linux session, install Howdy. It's
packaged, maintained, and focused on exactly that. Quietude reimplementing
it would be strictly worse, and Quietude shunting her own login through it
would mean she can't run on a machine that doesn't have it.

**They compose fine as they are.** Howdy unlocks your session; Quietude
unlocks Quietude. Both use the same camera, neither knows about the other,
and nothing needs writing to make that work.

**What I'd reconsider it for:** if you wanted `sudo`-style elevation
*inside* Quietude — a genuinely destructive action asking for OS-level
credentials rather than her own — then `pam_authenticate` against the
existing stack would be the right call, and would pick up Howdy for free
if it's installed. That's a different feature from face login, and worth
having only if you want that specific thing.

The existing `core/face_auth.py` ported unchanged. It never had any
OS-specific code — the one Windows-flavoured detail was a defensive
fallback around `cv2.data.haarcascades`, which is kept, not because Linux
has that problem but because shipping our own cascade and not trusting
the installed wheel's layout is better practice anywhere.

---

## Development

```
quietude/
├── install.sh                   install, uninstall, models, fonts
├── branding/                    icon, logo, motto - see branding/README.md
├── backend/
│   ├── requirements.txt         core - a failure here is fatal
│   ├── requirements-voice.txt   speech + synthesis - optional, best-effort
│   ├── requirements-gemini.txt  the one non-local feature, kept separate
│   └── quietude/
│       ├── __main__.py          entry point, browser launch
│       ├── config.py            XDG paths
│       ├── server.py            Flask app, SPA serving
│       ├── lifecycle.py         signals, ordered shutdown
│       ├── process_utils.py     bounded-wait-then-SIGKILL
│       ├── api/                 the JSON API, one blueprint per area
│       └── core/
│           ├── assistant.py     the command interpreter
│           ├── database.py      local JSON, hashing, encryption
│           ├── setup_wizard.py  first-run state machine
│           ├── face_auth.py     OpenCV detection + LBPH
│           ├── gemini_tool.py   optional conversation mode
│           ├── tts_engine.py    piper/espeak worker
│           └── speech_engine.py vosk + faster-whisper worker
└── frontend/
    └── src/
        ├── router/              screens as routes, with an entry guard
        ├── stores/              session + chat (the only shared state)
        ├── composables/         speech, tts, camera, voice, boot, chimes
        ├── components/          HUD panel, chat bubble, camera frame, ...
        ├── views/               one per screen
        ├── worklets/            the PCM resampler
        └── styles/              the HUD design system
```

Backend edits take effect on the next launch — `install.sh` installs the
package editable, so the repo stays the source of truth.

For the frontend, run Vite's dev server with hot reload against a live
Quietude:

```bash
QUIETUDE_NO_BROWSER=1 quietude              # terminal 1
cd frontend && npm run dev          # terminal 2 -> http://localhost:5173
```

Vite proxies `/api` to port 5000. Dev and production differ only in who
serves the static files. `QUIETUDE_BACKEND` overrides the proxy target.

```bash
cd frontend && npm run build        # rebuild for production
```

### Verifying the interface

The GUI is tested by rendering it, not by assuming it works. An earlier
pass checked HTTP status codes and backend logic and shipped a main
console that rendered as a floating box in the middle of the page,
because nothing had ever looked at it.

`npm run build`, then with Quietude running:

```bash
python3 scripts/screenshot_ui.py shots/          # every screen, driving the real flows
python3 scripts/record_ui.py walkthrough/        # a video of the whole walkthrough
```

Both use Playwright against headless Chromium, with fake media devices
so the camera screens actually run rather than erroring on a missing
webcam. The screenshot script is also an end-to-end functional test: it
drives first-run setup, the console, the command reference, voice
settings, face registration and both login paths, asserting at each
step, and it measures the console's layout (topbar full width and at the
top, side panel on the left, input row pinned near the bottom) rather
than trusting it by eye.

A useful check when changing styles, which would have caught that bug
immediately:

```bash
python3 scripts/check_css_selectors.py           # CSS rules matching no markup
```

### Adding a command

`core/assistant.py` is the single place a message becomes a reply. Most
new commands need a branch in `respond()` and an entry in
`COMMANDS_REFERENCE` — which is also what the `/commands` screen renders,
so there's one source of truth. If the command needs the UI to do
something, return an `action` and add a handler to the `ACTIONS` map in
`ConsoleView.vue`.

---

## Troubleshooting

**"NO BACKEND" in the browser.** She isn't running. `quietude`, or
`systemctl --user status quietude`.

**`quietude: command not found`.** `~/.local/bin` isn't on your `PATH`:

```bash
export PATH="$HOME/.local/bin:$PATH"     # add to ~/.bashrc or ~/.zshrc
```

**"VOICE UNAVAILABLE" at boot.** The speech models aren't there. Text
chat still works. `./install.sh --models-only`.

**Voice chat won't turn on.** Quietude says why in the chat. Usually a
missing model, or the mic permission being denied — check the camera/mic
icon in your browser's address bar.

**She doesn't hear the wake word.** Check the chat box shows a red
preview while asleep; if it's empty, no audio is arriving — check the
browser's mic permission and that the right input device is selected. If
it shows text but the wrong words, that's Vosk's accuracy; say "hello
quietude" a little more distinctly. If it triggers on nothing, raise
`QUIETUDE_VAD_THRESHOLD`.

**She's silent.** Check `tts settings` lists a voice. If it lists none,
either install `espeak-ng` or run `./install.sh --models-only`.

**"core dependency installation failed" during install.** Run the `pip
install` line the error prints to see the real cause. Usually a package
needing a compiler or headers — install the system packages (drop
`--no-system-deps`) and retry.

**"the voice stack couldn't be installed".** Not fatal: Quietude installs
and text chat, face login and account management all work. It means no
wheel was available for your Python for one of vosk, faster-whisper or
piper. To see which:

```bash
~/.local/share/quietude/venv/bin/pip install -r backend/requirements-voice.txt
```

If you're on a very new Python (3.14+) and a package has no wheel yet,
either wait for one or build the venv against an older interpreter:

```bash
QUIETUDE_VENV=~/.local/share/quietude/venv-313 ./install.sh      # with python3.13 first on PATH
```

**`cv2.face` is unavailable / face login fails.** `opencv-contrib` is
required (plain `opencv-python` has no `cv2.face`). If `cv2` won't import
at all, a missing `libGL` is the usual cause:

```bash
sudo apt-get install libgl1        # or mesa-libGL on Fedora
```

**Camera or mic blocked in Firefox.** Both need a secure context.
`http://127.0.0.1` counts as one; `http://<your-hostname>:5000` does
not. Use the loopback address.

**I forgot my backup password.** There's no recovery — it's hashed with
scrypt, which is the point. Log in with your face. If you can't do
either, `./install.sh --uninstall --purge-data` and start over.

**Port 5000 is taken.** `quietude --port 8080`, or set `port` in
`~/.config/quietude/settings.json`.

---

## Open questions

Things I decided but could reasonably go the other way. Flagged rather
than buried.

**Nothing here has been tested against a real microphone.** The state
machine, the VAD arithmetic, the resampler and the sequence counters are
all covered by tests, and the resampler is verified exact over two
seconds of audio with no drift. But wake-word accuracy, the VAD threshold
and real-world latency need validation on actual hardware with an actual
voice. `QUIETUDE_VAD_THRESHOLD` is the first thing to reach for if it
misbehaves, and the threshold constant is the single value in the speech
engine most likely to need tuning.

**Returning to sleep after each command** is a change from the reference,
which left her awake for a follow-up. Going back to sleep keeps the
"nothing is heard unless you woke her" guarantee simple to reason about,
at the cost of saying "Hello Quietude" again for a second command. If you'd
rather chain commands, it's one line in `useVoiceChat.js`.

**The awake-silence timers are frontend-driven**, as in the reference —
she checks in at 10s and sleeps 6s later. The backend could own them
instead, which would be more robust to a closed tab, but would mean the
engine deciding things the UI is better placed to know about. The
`sleep` control carries the wake counter it was responding to, so a
timer firing at the same moment as a wake can't cut the new session
short.

**`base.en` over `small.en`** trades accuracy for latency, which I think
is right for short fixed commands and wrong for anything approaching
dictation. One env var either way.

**Conversation-mode history is in memory only** and starts fresh each
time the mode is turned on, as before. Persisting it would be easy; not
persisting it means a mode that sends data to Google leaves nothing
behind on disk, which seemed the better default to keep.

**The 100 MB per-file upload cap** is inherited from the reference and is
arbitrary. Uploads are saved and acknowledged but not otherwise used, so
nothing depends on the number.

**`settings.json` only holds host and port.** It's a natural place for
`QUIETUDE_VAD_THRESHOLD` and the model choices too, so they could be changed
without environment variables. I left them as env vars because they're
tuning knobs rather than settings, but that's a judgement call.
