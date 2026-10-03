# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Hands-free, for longer than one sentence.

`voice` checks that a spoken command gets through. This checks that the
*next* one does, and the one after that - which is what "turn it on and
talk to her" actually means and is where it was falling over.

Three things, each of which was broken in its own way:

  it keeps sending     one wake, several sentences, every one of them
                       arriving as its own message
  it does not
  interrupt            the clock that asks "did you want to say
                       something?" and then goes to sleep used to run
                       while somebody was mid-sentence, because the only
                       thing that counted as activity was a *finished*
                       transcription. Twenty seconds of talking and she
                       slept on it, throwing the sentence away
  it speaks            every reply that can be spoken is, not just the
                       first
  you can cut in       saying her name over the top of a long answer
                       stops it and puts her back to listening. The
                       recogniser is switched off while she talks, so
                       before this a forty-second answer was forty
                       seconds with no way back into the conversation
"""
import re
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from playwright.sync_api import sync_playwright
from drive import FAKE, ASSISTANT, setup, say, wait_for_state

OUT = pathlib.Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)

PASSED = FAILED = 0


def check(label, ok, detail=""):
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  pass  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label}  {detail}")


def bubbles(page):
    return page.eval_on_selector_all(
        ".bubble", "els => els.map(e => e.innerText.split('\\n')[0])")


def wait_for(page, needle, since=0, seconds=30):
    """Waits for a bubble containing `needle`, ignoring the ones already
    on screen when `since` was taken.

    Searching the whole log is how this first passed by accident: the
    welcome message ends with 'or say "help"', so waiting for the word
    "help" to arrive found it in a message from before the test started
    and moved on while the real one was still in flight.
    """
    for _ in range(seconds * 4):
        if any(needle in b.lower() for b in bubbles(page)[since:]):
            return True
        page.wait_for_timeout(250)
    return False


def engine_mode(page):
    return page.evaluate("""async () => {
        const d = await (await fetch('/api/speech/status')).json()
        return d.mode
    }""")


def speaking(page, phrase=None):
    """Whether she is reading something out, and optionally what.

    The bar carries the text, which is the only way to be sure the
    phrase being interrupted is the one the test meant. Without it this
    was interrupting whatever happened to still be playing from the
    section before, and the result depended on how busy the machine
    was.
    """
    bar = page.query_selector(".tts-playback-bar")
    if bar is None:
        return False
    if phrase is None:
        return True
    return phrase.lower() in bar.inner_text().lower()


def wait_until(page, predicate, seconds=20):
    for _ in range(seconds * 4):
        if predicate():
            return True
        page.wait_for_timeout(250)
    return False


def quiet(page, seconds=60):
    """Waits until she has finished speaking.

    Worth stating plainly: the recogniser really is switched off while
    she talks, so a test that starts saying things over her is testing
    a microphone that is off by design. Everything here that is about
    listening waits for her to finish first - except the one section
    that is about interrupting her, which does the opposite on purpose.
    """
    for _ in range(seconds * 4):
        if not page.query_selector(".tts-playback-bar"):
            return True
        page.wait_for_timeout(250)
    return False


def speak(page, words, settle=900):
    """Say something and let it land, without finishing the utterance."""
    say(words)
    page.wait_for_timeout(settle)


def finish(page, words):
    """Say something and let the utterance end.

    The trailing stop is what the stub treats as a final result. What is
    in the script file is also what the transcriber reads, so it stays
    there until the next thing is said - clearing it here would hand the
    transcriber an empty utterance, which is a test that fails for a
    reason that has nothing to do with the application.
    """
    say(words + ".")


with sync_playwright() as p:
    b = p.chromium.launch(args=FAKE)
    page = b.new_page(viewport={"width": 1180, "height": 760})
    spoken_aloud = []
    page.on("request", lambda r: spoken_aloud.append(r.url)
            if "/api/tts/speak" in r.url else None)
    setup(page)

    print("\n-- turning it on --")
    page.click(".voice-btn")
    page.wait_for_timeout(3500)
    cls = page.get_attribute(".voice-btn", "class") or ""
    check("she starts out asleep, listening for her name", "listening" in cls, cls)
    placeholder = page.get_attribute(".chat-input", "placeholder") or ""
    check("and says what to say to wake her",
          ASSISTANT.lower() in placeholder.lower(), placeholder)

    print("\n-- the wake word --")
    say(f"hello {ASSISTANT.lower()}")
    check("she wakes", wait_for_state(page, "awake"),
          page.get_attribute(".voice-btn", "class"))
    page.wait_for_timeout(600)
    check("and says so in the chat",
          any("listening" in t.lower() for t in bubbles(page)))
    page.screenshot(path=str(OUT / "awake.png"))

    print("\n-- one sentence after another --")
    # Three in a row, on one wake. Each has to arrive on its own.
    said = ["what time is it", "show features", "help"]
    before = len(bubbles(page))
    for i, sentence in enumerate(said, 1):
        mark = len(bubbles(page))
        finish(page, sentence)
        check(f"sentence {i} became a message ({sentence!r})",
              wait_for(page, sentence, mark, 40))
        # And her answer to it, before the next one is said - otherwise
        # the test is measuring how fast it can type rather than whether
        # she keeps up.
        for _ in range(80):
            if len(bubbles(page)) > mark + 1:
                break
            page.wait_for_timeout(250)
        check(f"she finished speaking answer {i}", quiet(page, 90))
        page.wait_for_timeout(1200)

    sent = [t for t in bubbles(page)[before:]]
    check("she is still awake at the end of it",
          "awake" in (page.get_attribute(".voice-btn", "class") or ""),
          page.get_attribute(".voice-btn", "class"))
    check("and she never went back to sleep in between",
          not any("asleep" in t.lower() for t in sent), sent)
    check("each sentence arrived as its own message, in order",
          [t.lower() for t in sent if t.lower() in said] == said,
          [t for t in sent])
    page.screenshot(path=str(OUT / "three-sentences.png"))

    print("\n-- she speaks her answers --")
    check("every answer was spoken, not just the first",
          len(spoken_aloud) >= 3, f"{len(spoken_aloud)} spoken")

    print("\n-- the voice check, without switching the microphone off --")
    # The reason this is a panel and not a page. Every other screen is a
    # route, and leaving the console unmounts it, which disposes the
    # voice session - so the screen built to show you why she cannot
    # hear you was the screen that stopped her listening.
    # From the button, not by typing: the message box is locked while
    # voice chat is on, so the command cannot be typed - and the people
    # who need this page are the ones she is not hearing, who cannot say
    # it either.
    check("there is a button for it while voice chat is on",
          page.query_selector(".voice-check-btn") is not None)
    page.click(".voice-check-btn")
    check("it opens", wait_until(page, lambda: page.query_selector(".vc-panel"), 25))
    check("over the console, which is still there",
          page.query_selector(".chat-log") is not None)
    check("and voice chat is still on",
          "voice-btn" in (page.get_attribute(".voice-btn", "class") or "")
          and "awake" in (page.get_attribute(".voice-btn", "class") or ""),
          page.get_attribute(".voice-btn", "class"))
    check("the meter says audio is arriving",
          "Sending audio" in page.inner_text(".vc-stages"),
          page.inner_text(".vc-stages")[:200])
    check("and that there is sound in it",
          "Loudest so far" in page.inner_text(".vc-stages"),
          page.inner_text(".vc-stages")[:300])
    check("it lists the microphones to choose from",
          page.query_selector(".vc-select") is not None)
    page.screenshot(path=str(OUT / "voice-check.png"))
    page.click(".vc-close")
    check("and closing it leaves voice chat alone",
          not page.query_selector(".vc-panel")
          and "awake" in (page.get_attribute(".voice-btn", "class") or ""))
    page.wait_for_timeout(800)

    print("\n-- saying her name cuts her off --")
    # A long answer read aloud is half a minute of her talking into a
    # microphone she has switched off. Her name is the way back in.
    finish(page, "show features")
    check("she starts reading the answer out",
          wait_until(page, lambda: speaking(page, "everything I run"), 40),
          page.inner_text(".tts-playback-bar")
          if page.query_selector(".tts-playback-bar") else "nothing playing")
    check("and stops listening while she does",
          wait_until(page, lambda: engine_mode(page) == "suppressed", 10),
          engine_mode(page))
    page.wait_for_timeout(1200)
    say(f"hello {ASSISTANT.lower()}")
    check("saying her name stops her mid-answer",
          wait_until(page, lambda: not speaking(page), 20))
    check("and she is listening again",
          wait_until(page, lambda: engine_mode(page) == "awake", 10),
          engine_mode(page))
    page.screenshot(path=str(OUT / "interrupted.png"))
    check("and she finishes the rest of it", quiet(page, 90))
    page.wait_for_timeout(1500)

    print("\n-- except when the answer is about her name --")
    # "help" ends with a line telling you to say "Hello Ada" to wake
    # her. She says it out loud, the microphone hears it, and without
    # the guard she wakes herself up every time she explains how to
    # wake her up. So her name is only an interrupt when the phrase
    # being spoken does not contain it.
    finish(page, "help")
    check("she starts reading it out",
          wait_until(page, lambda: speaking(page, "what I can do so far"), 40),
          page.inner_text(".tts-playback-bar")
          if page.query_selector(".tts-playback-bar") else "nothing playing")
    page.wait_for_timeout(1500)
    say(f"hello {ASSISTANT.lower()}")
    page.wait_for_timeout(4000)
    check("her own name in the answer does not interrupt her",
          speaking(page))
    # "stop talking" still works, and is how this gets back on track.
    say("stop talking")
    check("but stop talking does", wait_until(page, lambda: not speaking(page), 25))
    say("")
    quiet(page, 90)
    page.wait_for_timeout(2000)

    print("\n-- a long sentence is not interrupted --")
    # The reported fault. The check-in fires after twelve seconds of what
    # the interface thinks is silence, and sleep eight seconds after
    # that. Here somebody talks for twenty-five seconds straight without
    # the gate ever finding its three seconds of quiet - which is what a
    # fan, or a mic with its gain up, does to it. She must not interrupt,
    # and she must not sleep on it.
    quiet(page, 90)
    before = len(bubbles(page))
    trace = []
    for i in range(25):
        speak(page, f"this is a long sentence part {i}", 1000)
        if i % 4 == 0:
            trace.append(page.evaluate("""async () => {
                const r = await fetch('/api/speech/status')
                const d = await r.json()
                return `${d.mode} buffered=${d.utterance_ms} speech=${d.utterance_speech_ms} silence=${d.utterance_silence_ms} t=${d.transcript_seq}`
            }"""))
    during = bubbles(page)[before:]
    check("she did not ask whether you wanted to say something",
          not any("want to say something" in t.lower() for t in during), during)
    check("and did not go back to sleep over the top of it",
          "awake" in (page.get_attribute(".voice-btn", "class") or ""),
          page.get_attribute(".voice-btn", "class"))
    # Fifteen seconds is the ceiling, so a sentence this long comes back
    # in pieces rather than not at all.
    heard = any("long sentence" in t.lower() for t in bubbles(page)[before:])
    if not heard:
        # What the engine was doing while nothing came back. Printed
        # only when it did not, because this is the one failure where
        # the chat log tells you nothing at all.
        for line in trace:
            print(f"        {line}")
    check("and something of it was sent rather than nothing", heard,
          bubbles(page)[before:])
    page.screenshot(path=str(OUT / "long-sentence.png"))
    say("")
    quiet(page, 90)
    page.wait_for_timeout(6000)

    print("\n-- nothing is lost when she goes to sleep --")
    # Switching voice chat off, or the session lapsing, used to throw
    # away whatever was buffered. Said and then immediately turned off:
    # the words still arrive.
    before = len(bubbles(page))
    finish(page, "what time is it")
    check("the last thing said still arrives",
          wait_for(page, "what time is it", before, 35))

    b.close()

print()
print("=" * 52)
print(f"  {PASSED} passed, {FAILED} failed")
print("=" * 52)
sys.exit(1 if FAILED else 0)
