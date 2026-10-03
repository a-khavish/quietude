# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Does the camera actually run in the real window?

Everything else about the interface can be tested in a plain browser,
because the interface is a web page. This cannot: the fault it exists
for was the *engine's* media policy, and a page tested in Chromium will
never show you what WebKit does with it.

So this drives the real Electron window over its debugging port, walks
to face registration, and asks the video element whether it is playing -
not whether it exists, which it did throughout the bug, but whether
frames are advancing.
"""
import pathlib
import sys
import time

from playwright.sync_api import sync_playwright

PORT = sys.argv[1] if len(sys.argv) > 1 else "9333"
SHOTS = pathlib.Path(__file__).resolve().parent / "run" / "shots" / "electron"
SHOTS.mkdir(parents=True, exist_ok=True)

failures = []


def verdict(ok, message):
    print(f"  {'pass' if ok else 'FAIL'}  {message}")
    if not ok:
        failures.append(message)


def ready(page, timeout_ms=30000):
    for _ in range(timeout_ms // 100):
        if page.evaluate("() => { const e = document.querySelector('.chat-input');"
                         "        return !!e && !e.disabled; }"):
            return True
        page.wait_for_timeout(100)
    return False


def typed(page, text):
    ready(page)
    page.eval_on_selector(".chat-input", """(e, v) => {
        e.value = v; e.dispatchEvent(new Event('input', {bubbles: true}));
    }""", text)
    page.press(".chat-input", "Enter")


with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
    contexts = browser.contexts
    page = None
    for context in contexts:
        for candidate in context.pages:
            if "quietude" in candidate.url or "127.0.0.1" in candidate.url:
                page = candidate
    if page is None and contexts and contexts[0].pages:
        page = contexts[0].pages[0]
    if page is None:
        print("  FAIL  could not attach to the window")
        sys.exit(1)

    print(f"  attached to {page.url}")
    verdict(True, "the interface is loaded in the real window")

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
    page.wait_for_timeout(2500)

    # ---- to the camera ----
    typed(page, "yes")
    for _ in range(50):
        if page.query_selector(".camera-frame"):
            break
        page.wait_for_timeout(500)

    if not page.query_selector(".camera-frame"):
        verdict(False, "face registration never opened")
    else:
        page.wait_for_timeout(2500)

        # The question is whether frames advance. A frozen preview still
        # has a video element, still reports a stream, and still looks
        # exactly like this to every check except this one.
        first = page.evaluate("""() => {
          const v = document.querySelector('.camera-frame video');
          if (!v) return null;
          return {paused: v.paused, readyState: v.readyState,
                  w: v.videoWidth, h: v.videoHeight,
                  t: v.currentTime};
        }""")
        time.sleep(2.0)
        second = page.evaluate("""() => {
          const v = document.querySelector('.camera-frame video');
          return v ? {t: v.currentTime, paused: v.paused} : null;
        }""")

        print(f"        video: {first}")
        print(f"        two seconds later: {second}")

        verdict(bool(first) and not first["paused"], "the preview is not paused")
        verdict(bool(first) and first["w"] > 0, "the camera gave a picture "
                f"({first['w'] if first else 0}x{first['h'] if first else 0})")
        verdict(bool(second) and second["t"] > (first["t"] if first else 0),
                "frames are advancing - the preview is live, not frozen")

        samples = page.evaluate("""() => {
          const el = document.querySelector('.face-progress-label, .face-status');
          return el ? el.innerText : '';
        }""")
        print(f"        on screen: {samples!r}")
        page.screenshot(path=str(SHOTS / "face-register.png"))

    # Nothing is left running for the next test to inherit.
    browser.close()

print()
print("the camera works in the real window" if not failures
      else f"{len(failures)} failed")
sys.exit(1 if failures else 0)
