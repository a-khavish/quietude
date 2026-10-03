# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared driving for the GUI tests: get a fresh profile to the console."""
import os, pathlib, sys
from playwright.sync_api import sync_playwright

PORT = int(os.environ.get("QUIETUDE_TEST_PORT", "5190"))
# What the window sends on every request, and what the server refuses
# everything without. See server.py.
TOKEN = os.environ.get("QUIETUDE_TEST_TOKEN", "")
TOKEN_HEADER = "X-Quietude-Window"
TOKEN_COOKIE = "quietude_window"
BASE = f"http://127.0.0.1:{PORT}"
HERE = pathlib.Path(__file__).resolve().parent
WAV = str(HERE / "run" / "speech.wav")
FAKE = ["--no-sandbox", "--use-fake-device-for-media-stream",
        "--use-fake-ui-for-media-stream",
        # Speech-shaped audio rather than the built-in tone, so the
        # voice-activity gate has a start and an end to find.
        f"--use-file-for-fake-audio-capture={WAV}"]
ASSISTANT = "Ada"
SPOKEN = HERE / "run" / "spoken.txt"


def say(text):
    """What the recogniser is currently hearing. A trailing '.' finalises."""
    SPOKEN.write_text(text)


def wait_for_state(page, wanted, seconds=25):
    """Waits for the voice button to reach 'listening' or 'awake'.

    Fixed waits used to be enough because the scripted recogniser
    produced words whether or not there was any sound. It does not any
    more - it only hears when the audio actually has something in it,
    like the real one - and the generated microphone loop is speech for
    1.6 seconds in every 7. So "say the wake phrase and wait two
    seconds" now lands in the silence between words about as often as
    not, which is a test that fails for a reason that has nothing to do
    with the application.
    """
    for _ in range(seconds * 4):
        if wanted in (page.get_attribute(".voice-btn", "class") or ""):
            return True
        page.wait_for_timeout(250)
    return False


def as_the_window(page):
    """Make this page speak the way the app window does.

    Both the header and the cookie, because the real window sets both
    and for the same reason: the header covers what the page requests,
    and the cookie covers what the *engine* requests on its behalf. An
    AudioWorklet module is fetched by the audio engine and carries none
    of the page's headers, so a header-only test would pass while voice
    chat was broken - which is exactly what happened.
    """
    if not TOKEN:
        return page
    page.set_extra_http_headers({TOKEN_HEADER: TOKEN})
    page.context.add_cookies([{
        "name": TOKEN_COOKIE,
        "value": TOKEN,
        "url": BASE,
    }])
    return page


def setup(page):
    """First run through to the main console."""
    as_the_window(page)
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_selector(".agreement-list", timeout=30000)
    page.click(".btn-primary")
    page.wait_for_selector(".setup-panel", timeout=20000)
    page.wait_for_timeout(1200)

    def answer(text, wait=1100):
        page.fill(".setup-panel input", text)
        page.keyboard.press("Enter")
        page.wait_for_timeout(wait)

    answer("Khavish Auckaloo")
    answer("Khavish")
    answer("29")
    answer(ASSISTANT)
    answer("Str0ng!Passw0rd")
    answer("no", 1600)
    page.wait_for_selector(".main-body", timeout=30000)
    page.wait_for_timeout(2500)
    # The console opens with "register your face now? (yes/no)" still
    # outstanding. Until it is answered every other input is read as an
    # answer to it, so the tests clear it before doing anything else.
    page.fill(".chat-input", "no")
    page.press(".chat-input", "Enter")
    page.wait_for_timeout(1800)


def overflow(page):
    """Anything sticking out of the window, measured rather than eyeballed.

    Returns the elements whose box extends past the viewport, plus
    whether the document itself is wider or taller than the window.
    """
    return page.evaluate("""() => {
      const vw = window.innerWidth, vh = window.innerHeight;
      const out = [];
      for (const el of document.querySelectorAll('body *')) {
        const s = getComputedStyle(el);
        if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') continue;
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        // Content scrolled out of a scroller is not content falling off
        // the window - it is content you reach by scrolling, which is
        // the whole point of a scroller. Clip to the nearest scrolling
        // or hidden ancestor and judge the element against that.
        let clipped = false;
        for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
          const as = getComputedStyle(a);
          const ox = as.overflowX, oy = as.overflowY;
          const scrollsX = ox === 'auto' || ox === 'scroll' || ox === 'hidden';
          const scrollsY = oy === 'auto' || oy === 'scroll' || oy === 'hidden';
          if (!scrollsX && !scrollsY) continue;
          const ar = a.getBoundingClientRect();
          // Inside the scroller's own box on the axis it controls, so
          // whatever sticks out is clipped before it reaches the window.
          if ((scrollsX && r.left >= ar.left - 1 && r.left <= ar.right + 1) ||
              (scrollsY && r.top >= ar.top - 1 && r.top <= ar.bottom + 1) ||
              (scrollsX && scrollsY)) {
            clipped = true;
            break;
          }
        }
        if (clipped) continue;
        // Only report the element itself, not every ancestor that
        // inherits the overflow from it.
        const over = {
          right: Math.round(r.right - vw),
          bottom: Math.round(r.bottom - vh),
          left: Math.round(-r.left),
          top: Math.round(-r.top),
        };
        const worst = Math.max(over.right, over.bottom, over.left, over.top);
        if (worst > 2) {
          out.push({
            tag: el.tagName.toLowerCase(),
            cls: (el.className || '').toString().slice(0, 60),
            text: (el.textContent || '').trim().slice(0, 40),
            over: worst,
            side: Object.keys(over).find(k => over[k] === worst),
          });
        }
      }
      // Deepest offenders first; an ancestor overflowing usually just
      // means a child inside it does.
      out.sort((a, b) => b.over - a.over);
      return {
        docScrollW: document.documentElement.scrollWidth,
        docScrollH: document.documentElement.scrollHeight,
        vw, vh,
        elements: out.slice(0, 12),
      };
    }""")


# ---------------------------------------------------------------
# A server of one's own
# ---------------------------------------------------------------
# Most suites share the server the runner starts. The onboarding screens
# cannot: each one has to be met on a profile that has never been set
# up, and setting one up is what the test does. So they get a helper
# that throws the profile away and starts again.

import contextlib          # noqa: E402
import shutil              # noqa: E402
import subprocess          # noqa: E402
import time                # noqa: E402
import urllib.request      # noqa: E402

RUN = HERE / "run"
ROOT = HERE.parent.parent


@contextlib.contextmanager
def fresh_profile(port):
    """Run the app on an empty profile for the duration of the block."""
    sandbox = RUN / f"sandbox-{port}"
    shutil.rmtree(sandbox, ignore_errors=True)
    for part in ("data", "config", "cache"):
        (sandbox / part).mkdir(parents=True, exist_ok=True)
    models = sandbox / "data" / "quietude" / "models"
    (models / "vosk" / "am").mkdir(parents=True, exist_ok=True)
    (models / "whisper").mkdir(parents=True, exist_ok=True)
    (models / "whisper" / "model.bin").touch()

    env = dict(os.environ)
    env.update({
        "XDG_DATA_HOME": str(sandbox / "data"),
        "XDG_CONFIG_HOME": str(sandbox / "config"),
        "XDG_CACHE_HOME": str(sandbox / "cache"),
        "QUIETUDE_NO_BROWSER": "1",
        "QUIETUDE_WINDOW_TOKEN": TOKEN or "fresh-profile",
        "QUIETUDE_VOSK_SCRIPT": str(RUN / "spoken.txt"),
        "PYTHONPATH": f"{HERE / 'stubs'}:{ROOT / 'backend'}",
    })
    proc = subprocess.Popen(
        [sys.executable, "-m", "quietude", "--port", str(port)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        # The liveness probe, not the page: the page is gated behind the
        # window's secret now and answers 403 to a plain request, which
        # urlopen raises on - so polling it would wait for ever on a
        # server that came up perfectly.
        for _ in range(60):
            try:
                urllib.request.urlopen(f"{base}/api/health", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise AssertionError(f"the server on {port} never came up")
        time.sleep(1.5)
        yield base
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=10)
        shutil.rmtree(sandbox, ignore_errors=True)
