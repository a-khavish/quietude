# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Records a walk through the whole application.

Not a test - nothing here asserts anything. It exists so there is a
moving picture of every screen and every feature, which is both the
thing to look at before a release and the thing to compare against
after one.

  ./tests/run-walkthrough.sh
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from playwright.sync_api import sync_playwright          # noqa: E402
from drive import FAKE, as_the_window, fresh_profile, say   # noqa: E402

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "walkthrough")
OUT.mkdir(parents=True, exist_ok=True)
SIZE = {"width": 1280, "height": 800}
ASSISTANT = "Ada"


def beat(page, ms=1400):
    """A pause long enough to read what just happened."""
    page.wait_for_timeout(ms)


def ready(page, timeout_ms=30000):
    for _ in range(timeout_ms // 100):
        if page.evaluate("() => { const e = document.querySelector('.chat-input');"
                         "        return !!e && !e.disabled; }"):
            return
        page.wait_for_timeout(100)
    raise AssertionError("the message box never became editable")


def command(page, text, settle=2600):
    ready(page)
    page.eval_on_selector(".chat-input", """(e, v) => {
        e.value = v; e.dispatchEvent(new Event('input', {bubbles: true}));
    }""", text)
    beat(page, 700)
    page.press(".chat-input", "Enter")
    beat(page, settle)


def clear_prompt(page, answer="no"):
    """Answer any outstanding yes/no before carrying on.

    Several commands end in a question - "register your face now?" - and
    until it is answered every later command is read as an answer to it.
    A walkthrough that ignores that spends the rest of its run being told
    to choose one of the valid options.
    """
    for _ in range(4):
        last = page.eval_on_selector_all(
            ".bubble", "els => els.length ? els[els.length-1].innerText : ''")
        if "(yes/no)" not in last and "valid options" not in last:
            return
        ready(page)
        page.eval_on_selector(".chat-input", """(e, v) => {
            e.value = v; e.dispatchEvent(new Event('input', {bubbles: true}));
        }""", answer)
        page.press(".chat-input", "Enter")
        beat(page, 2400)


def back(page):
    btn = page.query_selector(".back-bar-btn")
    if btn:
        btn.click()
    else:
        page.go_back()
    beat(page, 1800)


with sync_playwright() as p:
    browser = p.chromium.launch(args=FAKE)
    with fresh_profile(5400) as BASE:
        context = browser.new_context(
            viewport=SIZE, record_video_dir=str(OUT), record_video_size=SIZE)
        page = as_the_window(context.new_page())

        # ---- first launch ----
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_selector(".agreement-list", timeout=30000)
        beat(page, 3200)                      # the four promises
        page.click(".agreement-panel .btn-primary")

        # ---- setup: she is named here ----
        page.wait_for_selector(".setup-panel", timeout=20000)
        beat(page, 1800)

        def answer(text, wait=1900):
            page.fill(".setup-panel input", text)
            beat(page, 600)
            page.keyboard.press("Enter")
            beat(page, wait)

        answer("Khavish Auckaloo")
        answer("Khavish")
        answer("29")
        answer(ASSISTANT, 2600)               # the name is the wake word
        answer("Str0ng!Passw0rd")
        answer("no", 3200)

        page.wait_for_selector(".main-body", timeout=30000)
        beat(page, 2600)
        clear_prompt(page)                    # decline face registration for now

        # ---- talking to her ----
        command(page, "hello")
        command(page, "what time is it")
        command(page, "show features", 3200)
        clear_prompt(page)

        # ---- the pages ----
        clear_prompt(page)
        command(page, "show commands", 3200)
        page.mouse.wheel(0, 600)
        beat(page, 2200)
        page.mouse.wheel(0, 600)
        beat(page, 2000)
        back(page)

        clear_prompt(page)
        command(page, "who are you", 3400)    # identity settings
        beat(page, 2600)
        name_field = page.query_selector(".identity-input")
        if name_field:
            name_field.click()
            beat(page, 500)
        back(page)

        clear_prompt(page)
        command(page, "tts settings", 3200)
        beat(page, 1600)
        preview = page.query_selector(".tts-preview-box")
        if preview:
            preview.click()
            page.keyboard.type("This is how I will sound.", delay=55)
            beat(page, 1200)
        play = page.query_selector(".tts-preview-row .btn-ghost")
        if play:
            play.click()
            beat(page, 2600)
        page.mouse.wheel(0, 500)
        beat(page, 1800)
        back(page)

        clear_prompt(page)
        command(page, "voice library", 3600)
        beat(page, 2400)
        page.mouse.wheel(0, 400)
        beat(page, 1800)
        back(page)

        clear_prompt(page)
        command(page, "speech models", 3600)
        beat(page, 2600)
        page.mouse.wheel(0, 500)
        beat(page, 2000)
        back(page)

        # ---- voice chat ----
        page.click(".voice-btn")
        beat(page, 3600)
        say(f"hello {ASSISTANT.lower()}")
        beat(page, 2200)
        say("what time is it")
        beat(page, 5500)
        page.click(".voice-btn")
        beat(page, 2600)

        # ---- face registration ----
        clear_prompt(page)
        command(page, "register face", 2600)
        for _ in range(40):
            if page.query_selector(".camera-frame"):
                break
            beat(page, 500)
        beat(page, 9000)                      # the samples are captured
        if not page.query_selector(".chat-input"):
            back(page)
        beat(page, 2200)

        clear_prompt(page)
        context.close()

        # ---- the same application in the smallest window it allows ----
        #
        # A second take rather than resizing the first: the video canvas
        # is fixed when recording starts, so shrinking the window mid-run
        # records a small picture in the corner of a large grey one. This
        # also picks up the two login screens, which the first take never
        # sees because its session never ended.
        small = {"width": 760, "height": 520}
        context = browser.new_context(
            viewport=small, record_video_dir=str(OUT), record_video_size=small)
        page = as_the_window(context.new_page())
        page.goto(f"{BASE}/login", wait_until="networkidle")
        beat(page, 3000)
        pw = page.query_selector(".login-choice-panel .btn-primary, .login-option")
        if pw:
            pw.click()
            beat(page, 2000)
        if page.query_selector(".setup-panel input"):
            page.fill(".setup-panel input", "Str0ng!Passw0rd")
            beat(page, 900)
            page.keyboard.press("Enter")
            beat(page, 4000)
        if page.query_selector(".chat-input"):
            clear_prompt(page)
            command(page, "what time is it")
            clear_prompt(page)
            command(page, "show commands", 3400)
            page.mouse.wheel(0, 500)
            beat(page, 2400)
            back(page)
            clear_prompt(page)
            command(page, "shutdown", 5200)
            beat(page, 4200)
        context.close()
    browser.close()

videos = sorted(OUT.glob("*.webm"), key=lambda f: f.stat().st_mtime)
print("\n".join(str(v) for v in videos) if videos else "no video was recorded")
