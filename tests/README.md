# Tests

## The interface

```bash
./run-gui-tests.sh                 # from this directory
./tests/run-gui-tests.sh sizes     # or one suite at a time
```

Needs the interface built (`cd frontend && npm run build`) and Playwright
installed:

```bash
pip install playwright && playwright install chromium
```

Nothing here touches your profile. Each run gets an empty XDG sandbox
under `tests/gui/run/`, the speech and voice models are stubs, and the
microphone is a generated WAV file. Screenshots from every check land in
`tests/gui/run/shots/`, which is the first place to look when something
fails.

### What each suite is for

| suite | asks |
|---|---|
| `screens` | can you get past every screen before the console — agreement, setup, login, face registration — at every window size? Specifically: is the button you have to press actually on screen? |
| `sizes` | does every part of the interface fit, at every size the window can be? |
| `states` | does the layout hold still while things happen — a reply playing aloud, voice chat going on and off, the message box growing? |
| `voice` | do spoken words appear in the message box and become a message, including a command spoken in the same breath as the wake word? And does the interface call the assistant by her name? |
| `allviews` | all of the above, on every screen, at three sizes |
| `conversation` | hands-free for longer than one sentence: several in a row on one wake, no interrupting somebody mid-sentence, every answer spoken, and saying her name to cut a long answer short |
| `scroll` | does the log behave like a chat log — follows the newest message, stays put when you scroll up to read, takes hold again when you scroll back, and shows the end again when you return from another page |
| `commands` | the two command pages: that the reference actually lists the commands, that search and the built-in/mine filter narrow it, and that a user command's three spoken lines land when they say they do |
| `electron_window` | the window itself — what the dock files it under, whether it refuses to be dragged below the layout's floor, whether it carries an icon, where it opens |
| `electron_camera` | the camera, in the real window rather than in a browser standing in for it |
| `electron_voice` | voice chat, in the real window: the microphone permission, the worklet module the audio engine fetches on its own, the wake word, the transcription, and the message landing in the chat — each checked separately so a failure names the link that broke |

`electron_window` is a shell script rather than a Python suite because
none of it is about the page. It reads the window's X properties the way
a dock would. Every check in it has been a released bug: the dock icon
alone took three attempts, because the first two fixed real problems
that were not *the* problem.

### Why they exist

Four faults reached a release together, and none of them could have:

- the window could not be dragged smaller than the size it opened at, so
  on a screen that could not give that size the bottom of the window —
  the message box — was off the edge of the display
- `.chat-log` was `flex: 1` without `min-height: 0`, so it refused to
  shrink and pushed everything below it out of the window whenever
  anything appeared above it
- a placeholder still said `Say "Hello Quietude"` after the assistant had
  been given a name of her own
- replies were built from templates like `{wake}`, filled in at some call
  sites and not others, so a literal `{wake}` could reach the screen

A later round found four more of the same kind:

- `.screen` centred its panel with `align-items: center` in a container
  that could not scroll, so a panel taller than the window overflowed
  equally off the **top and the bottom** with no way to reach either.
  That put the agreement's Agree button and face unlock's Retry button
  off screen — two screens you cannot get past
- the command reference had two `white-space: nowrap` columns against a
  container with `overflow-x: hidden`, so below about 935px the entire
  Usage column was not narrower but absent
- WebKitGTK requires a user gesture before media may play, which applies
  to the camera preview too. `play()` was rejected, the rejection was
  swallowed, and the engine painted its own play button over a frozen
  frame
- she said "Yes, I'm listening." aloud with the recogniser suppressed
  for the whole phrase — two seconds of deafness beginning at the exact
  moment people speak

Then one that none of them could have caught, because every suite was
asking whether a page was *well formed* and none was asking whether it
had anything on it. Renaming the user-commands route to stop it
colliding with the reference also repointed the call the reference
makes, so "show commands" listed the user's own commands — of which
there were none — and the shortcut column fell back to a list hard-coded
for an older release. The page opened, nothing overflowed, no
placeholder leaked, and the table was empty. `commands` reads the table.

And one about reading rather than layout: the log opened at the oldest
message every time you came back from another page. The messages are
kept in the store, so returning rebuilds a log that is already full —
nothing is appended, and the thing that scrolled to the bottom was a
watcher on "a message was added". Writing `scroll` turned up a second
one underneath it: making the window smaller fires a scroll event
*before* it reports the resize, because the browser repositions the
scroller to hold your place, and the log read that as the reader walking
away and then stopped following anything for the rest of the session.

Then the one that made hands-free unusable, and it is the best example
of why these are written against behaviour rather than units. She stops
listening while she speaks — she has to, or she transcribes her own
voice — and that was lifted by a promise resolving in the browser when
the audio ended. Nothing bounded it. A phrase paused from the playback
bar, a clip that stalled, an element torn down between the request and
the first frame, and she was deaf: not for a moment, for the rest of the
session, still answering into a microphone that had been switched off.
Alongside it the clock that asks "did you want to say something?" and
then sleeps counted only *finished* transcriptions as activity, so it
ran while somebody was talking and threw the sentence away at twenty
seconds. Neither is visible in a unit test of either half.

And then the round where the suites were the problem. "The words I say
never reach the chat box" was reported three times while every voice
test passed, because the scripted models were too obliging: the
recogniser returned words whether or not the audio had any sound in it,
and the transcriber returned its line whatever buffer it was handed —
including an empty one. So the tests proved the plumbing between the
parts and nothing about the part that was failing. Both stubs listen
now, and the voice suite failed the moment they did.

Each is a one-line fault with no visible stack trace, and each was
reported by someone using the app rather than caught here. The suites are
written to fail on the *kind* of thing each one was, not just on the
exact case: `sizes` and `states` measure every element against the
window rather than checking known trouble spots, and `allviews` checks
every screen for both naming faults at once.

### Writing one

`drive.py` holds the shared parts: `setup()` takes a fresh profile
through first-run to the console, `overflow()` reports anything outside
the window, and `say()` puts words in the microphone — a trailing full
stop ends the utterance.

Two things to know. Some commands (`help`, `who are you`, `tts settings`)
open a page of their own and take the message box off screen, correctly —
if a test needs the box afterwards, it has to come back. And the message
box is disabled while a reply is in flight, so wait for it rather than
racing it; `states.py` has a `ready()` worth copying.

## The engine, and why there is a test for it

The interface is a web page, so almost all of it can be tested in a
plain browser. One thing cannot: the window's own engine.

The app normally runs on Chromium via Electron. It falls back to
WebKitGTK — the system's engine, and what it used to use — and the
difference is not academic. WebKit requires a user gesture before any
media may play, and that rule applies to a `<video>` fed from
`getUserMedia`. The camera preview was rejected by it, the rejection was
swallowed, and the engine painted its own play button over a still
frame. A browser test would never have found it, because the browser
*is* Chromium.

So `electron_camera.py` drives the real Electron window over its
debugging port and asks the video element whether `currentTime` is
advancing — not whether a stream exists, which it did throughout the
bug. Run it with the window suite; it needs `Xvfb`.

## One that was only caught because the test drives the real thing

The server refuses any request that doesn't carry the window's secret.
The window attaches it as a header — and that covered everything the
*page* requests, which looked complete.

It wasn't. An AudioWorklet module is fetched by the browser's audio
engine, not by the page, and arrives with none of the page's headers. So
the wake word could not start — `Unable to load a worklet's module` —
while every visible part of the interface worked perfectly. The secret
is sent as a cookie as well now, because a cookie is attached by the
engine to all of it.

The voice suite found this within a minute of the change going in. A
test that only checked pages loaded would have passed.

## A recording of the whole app

```bash
./tests/run-walkthrough.sh              # writes tests/gui/run/quietude-walkthrough.mp4
```

Walks every screen and every feature and records it: first launch,
naming her, the chat, the command reference, identity, voice settings
with a live preview, the voice library, the speech models, voice chat
with a spoken command, face registration, and the login screens — then
the same application again in the smallest window it allows. Two takes,
joined, because the video canvas is fixed when recording starts and
resizing mid-run records a small picture inside a large grey one.

Nothing in it asserts anything. It is for looking at before a release,
and for comparing against after one.

## The Python side

```bash
./tests/run-backend-tests.sh            # everything
./tests/run-backend-tests.sh speech     # one suite
```

No window, no browser, no network. The heavy native dependencies are
stubbed and everything runs against a throwaway profile.

| suite | covers |
|---|---|
| `launcher` | which port it lands on when the usual one is taken, which of Chromium's two sandboxes it can use, that the server refuses anything but the window, and that starting at login writes and removes its entry |
| `user_commands` | commands people write themselves: that one name cannot become two, that two cannot run at once, that one which never ends is stopped, that a semicolon is an argument rather than a second command, and that starting one returns while it is still running so the three spoken lines can land in order |
| `speech` | the wake/awake/suppressed state machine, the voice-activity gate, where an utterance starts and ends, the wake and stop phrase patterns |
| `model_catalog` | which models are installed, which is active, downloading, removing, and the installer's own directory |
| `voice_library` | the piper catalogue, and that a voice name can never become a path |

`speech` is the one to read if you change anything about listening. It
drives the real worker through a queue with stubbed Vosk and Whisper, so
it tests the decisions rather than the models.
