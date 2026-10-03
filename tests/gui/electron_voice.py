# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Does voice chat actually work in the real window?

The whole of hands-free was tested in a plain Chromium and nowhere else,
on the reasoning that the interface is a web page. That reasoning is
wrong here, and it is wrong in the same way it was wrong about the
camera. Voice chat is not a page feature: it is a microphone permission
the engine grants or refuses, an AudioWorklet module the engine fetches
with its own request rather than the page's, and an audio element the
engine has to be able to play. Each of those is the engine's, not the
page's, and a browser standing in for it will answer for none of them.

So this walks the real window through the whole chain and checks each
link, in order, so that a failure says which one broke:

    the microphone      permission granted, capture running
    the worklet         loaded, and posting audio the engine accepts
    the wake word       heard, and she wakes
    the utterance       buffered, ended, transcribed
    the message         in the chat, with her answer under it
"""
import pathlib
import sys
import time

from playwright.sync_api import sync_playwright

PORT = sys.argv[1] if len(sys.argv) > 1 else "9334"
HERE = pathlib.Path(__file__).resolve().parent
SPOKEN = HERE / "run" / "spoken.txt"
SHOTS = HERE / "run" / "shots" / "electron"
SHOTS.mkdir(parents=True, exist_ok=True)

failures = []


def verdict(ok, message, detail=""):
    print(f"  {'pass' if ok else 'FAIL'}  {message}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(message)


def say(text):
    SPOKEN.write_text(text)


def status(page):
    return page.evaluate("""async () => {
        const r = await fetch('/api/speech/status')
        return await r.json()
    }""")


def until(page, predicate, seconds=30):
    for _ in range(seconds * 4):
        try:
            if predicate():
                return True
        except Exception:
            pass
        page.wait_for_timeout(250)
    return False


def bubbles(page):
    return page.eval_on_selector_all(
        ".bubble", "els => els.map(e => e.innerText.split('\\n')[0])")


with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
    page = None
    for context in browser.contexts:
        for candidate in context.pages:
            if "127.0.0.1" in candidate.url:
                page = candidate
    if page is None and browser.contexts and browser.contexts[0].pages:
        page = browser.contexts[0].pages[0]
    if page is None:
        print("  FAIL  could not attach to the window")
        sys.exit(1)
    print(f"  attached to {page.url}")

    # ---- first run, up to the console ----
    page.wait_for_selector(".agreement-list", timeout=40000)
    page.click(".agreement-panel .btn-primary")
    page.wait_for_selector(".setup-panel", timeout=25000)
    page.wait_for_timeout(1500)

    def answer(text, wait=1300):
        page.fill(".setup-panel input", text)
        page.keyboard.press("Enter")
        page.wait_for_timeout(wait)

    answer("Khavish Auckaloo")
    answer("Khavish")
    answer("29")
    answer("Ada")
    answer("Str0ng!Passw0rd")
    answer("no", 3000)
    page.wait_for_selector(".main-body", timeout=40000)
    page.wait_for_timeout(2000)
    page.fill(".chat-input", "no")
    page.press(".chat-input", "Enter")
    page.wait_for_timeout(2000)

    # ---- the microphone ----
    say("")
    before = status(page)
    page.click(".voice-btn")
    verdict(until(page, lambda: "listening" in
                  (page.get_attribute(".voice-btn", "class") or ""), 25),
            "the microphone opens in the real window",
            page.get_attribute(".voice-btn", "class"))

    # ---- the worklet, and audio actually arriving ----
    #
    # The level is measured by the worker from bytes that came out of
    # the AudioWorklet, through a POST, into another process. A level
    # that moves is the only proof that the whole of that is working -
    # and the worklet module is fetched by the audio engine rather than
    # the page, which is the exact thing that broke once before.
    moved = until(page, lambda: (status(page).get("level") or 0) > 1, 30)
    verdict(moved, "audio from the worklet reaches the engine",
            f"level {status(page).get('level')}")

    # ---- the wake word ----
    say("hello ada")
    verdict(until(page, lambda: "awake" in
                  (page.get_attribute(".voice-btn", "class") or ""), 30),
            "the wake word wakes her in the real window",
            page.get_attribute(".voice-btn", "class"))
    page.screenshot(path=str(SHOTS / "voice-awake.png"))

    # ---- the utterance, and the message ----
    mark = len(bubbles(page))
    say("what time is it.")
    heard = until(page, lambda: status(page).get("transcript_seq", 0)
                  > before.get("transcript_seq", 0), 45)
    verdict(heard, "the utterance is transcribed",
            f"seq {status(page).get('transcript_seq')}")

    landed = until(
        page,
        lambda: any("what time is it" in b.lower() for b in bubbles(page)[mark:]),
        45)
    verdict(landed, "and reaches the chat box as a message",
            str(bubbles(page)[mark:])[:200])

    answered = until(
        page, lambda: len(bubbles(page)) > mark + 1, 45)
    verdict(answered, "and she answers it", str(bubbles(page)[mark:])[:200])
    page.screenshot(path=str(SHOTS / "voice-sent.png"))

    # ---- she reads the answer out ----
    verdict(until(page, lambda: page.query_selector(".tts-playback-bar") is not None, 25)
            or until(page, lambda: status(page).get("mode") == "suppressed", 5),
            "and reads it out loud")

    state = status(page)
    print(f"        engine: mode={state.get('mode')} level={state.get('level')} "
          f"floor={state.get('noise_floor')} gate={state.get('vad_threshold')} "
          f"dropped={state.get('dropped_seq')} {state.get('dropped_reason') or ''} "
          f"error={state.get('error') or 'none'}")

print()
if failures:
    print(f"{len(failures)} failed")
else:
    print("voice chat works in the real window")
sys.exit(1 if failures else 0)
