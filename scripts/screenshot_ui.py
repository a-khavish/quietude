#!/usr/bin/env python3
# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Screenshot every screen, and assert the interface actually works.

    python3 scripts/screenshot_ui.py OUT_DIR [BASE_URL]

Requires Quietude running (QUIETUDE_NO_BROWSER=1 quietude) and `pip install
playwright && playwright install chromium`.

Two things this does that a status-code test cannot:

  * It drives the real flows - first-run setup, the console, the command
    reference, voice settings, face registration, both login paths -
    asserting at each step, so a screen that renders but doesn't work
    still fails.
  * It measures the console's layout (topbar full width and at the top,
    side panel left, input row near the bottom) instead of trusting it.
    That check is what catches a stylesheet rule silently no longer
    applying, which is how the console once shipped collapsed into a
    floating box mid-page.

Navigation is in-app rather than page.goto: a full page load drops the
session and the router correctly bounces you back to login, which is
right behaviour and makes page.goto useless for reaching the console.

Chromium runs with fake media devices so the camera screens genuinely
execute - getUserMedia succeeds and frames are posted to the backend.
The video is a test pattern, so no face is ever detected; that is
expected, and the backend saying "no face detected" is the pipeline
working.
"""
import sys, traceback
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:5090"
VIEWPORT = {"width": 1440, "height": 900}
FAKE_MEDIA = ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"]

errors, steps = [], []

def shoot(page, name, wait=500):
    page.wait_for_timeout(wait)
    page.screenshot(path=str(OUT / f"{name}.png"))
    print(f"  captured {name}", flush=True)

def step(ok, label):
    steps.append((ok, label))
    print(f"  {'pass' if ok else 'FAIL'}  {label}", flush=True)

class Soft:
    """Never let one broken screen abort the whole capture run."""
    def __init__(self, page, name): self.page, self.name = page, name
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb):
        if exc is not None:
            step(False, f"{self.name} - {type(exc).__name__}: {str(exc)[:90]}")
            try: self.page.screenshot(path=str(OUT / f"{self.name}-FAILED.png"))
            except Exception: pass
        return True

def box_metrics(page, selector, text):
    """Fills a text box and reports what the browser made of it."""
    page.fill(selector, text)
    page.wait_for_timeout(140)
    return page.evaluate("""(s) => {
      const el = document.querySelector(s);
      const cs = getComputedStyle(el);
      return {
        height: el.getBoundingClientRect().height,
        overflow: cs.overflowY,
        resize: cs.resize,
        tag: el.tagName.toLowerCase(),
        scrolls: el.scrollHeight > el.clientHeight + 1,
      };
    }""", selector)


def check_autogrow(page, selector, label, max_rows):
    """Asserts a box grows with its content, stops at a ceiling, and
    scrolls from there - and cannot be dragged.

    Measured rather than asserted from the CSS, because the growth is
    done in JavaScript from the rendered line count: the only way to
    know it works is to put text in and look at the box."""
    one = box_metrics(page, selector, "one line")
    step(one["tag"] == "textarea", f"{label} is a textarea (was a single-line input)")
    step(one["resize"] == "none", f"{label} cannot be dragged (resize: {one['resize']})")

    few = box_metrics(page, selector, "\n".join(f"line {i}" for i in range(3)))
    step(few["height"] > one["height"] + 4,
         f"{label} grows with content ({one['height']:.0f}px -> {few['height']:.0f}px)")

    at_cap = box_metrics(page, selector, "\n".join(f"line {i}" for i in range(max_rows)))
    over = box_metrics(page, selector, "\n".join(f"line {i}" for i in range(max_rows + 8)))
    step(abs(over["height"] - at_cap["height"]) < 3,
         f"{label} stops growing at {max_rows} rows "
         f"({at_cap['height']:.0f}px, still {over['height']:.0f}px with 8 more lines)")
    step(over["scrolls"] and over["overflow"] == "auto",
         f"{label} scrolls inside once capped (overflow-y: {over['overflow']})")

    shrunk = box_metrics(page, selector, "one line")
    step(abs(shrunk["height"] - one["height"]) < 3,
         f"{label} shrinks back down again ({shrunk['height']:.0f}px)")
    page.fill(selector, "")


def chat(page, message, wait=1600):
    page.fill(".chat-input", message)
    page.press(".chat-input", "Enter")
    page.wait_for_timeout(wait)

with sync_playwright() as p:
    browser = p.chromium.launch(args=FAKE_MEDIA)
    ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=2,
                              permissions=["camera", "microphone"])
    page = ctx.new_page()
    page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

    # ---------- first run ----------
    page.goto(BASE, wait_until="domcontentloaded")
    page.wait_for_selector(".agreement-list", timeout=15000)
    step(page.locator(".agreement-list li").count() == 4, "agreement lists 4 points")
    shoot(page, "agreement")

    page.click("button.btn-primary")
    page.wait_for_selector(".setup-panel input", timeout=10000)
    page.wait_for_timeout(700)

    page.fill(".setup-panel input", "Bob99")                 # invalid on purpose
    page.press(".setup-panel input", "Enter")
    page.wait_for_timeout(900)
    step(page.locator(".bubble.error").count() > 0, "invalid name shows an error bubble")

    page.fill(".setup-panel input", "Khavish Auckaloo")
    page.press(".setup-panel input", "Enter")
    page.wait_for_timeout(900)
    step(page.locator(".bubble.success").count() > 0, "valid answer shows 'Saved.'")
    shoot(page, "setup")

    # "Ada" is the assistant's own name - setup asks for it, because she
    # does not have one until someone gives her one and because it is
    # what her wake word is built from.
    for answer in ("Khavish", "27", "Ada", "Str0ng!Passw0rd"):
        page.fill(".setup-panel input", answer)
        page.press(".setup-panel input", "Enter")
        page.wait_for_timeout(1000)

    log = page.inner_text(".setup-log")
    step("what would you like to call me" in log.lower(),
         "setup asks what to call the assistant")
    step("wake word" in log.lower(),
         "and says that the name is also the wake word")
    step("register your face" in log, "wizard reaches the face question")

    # "no" routes straight into the console - all in-app.
    page.fill(".setup-panel input", "no")
    page.press(".setup-panel input", "Enter")
    page.wait_for_selector(".main-body", timeout=45000)
    page.wait_for_timeout(2500)
    step(True, "reached the console through the real flow")

    # Quietude nags about face registration right after login and holds a
    # pending yes/no, so answer it before anything else - exactly as a
    # user would. Same behaviour as the original build.
    step("register it now" in page.inner_text(".chat-log"), "post-login face nag appears")
    chat(page, "no")
    step("won't be available" in page.inner_text(".chat-log"), "declining the nag is acknowledged")

    chat(page, "what time is it")
    step("currently" in page.inner_text(".chat-log"), "chat replies to a command")
    chat(page, "show features", wait=1800)
    step("Leaves this machine" in page.inner_text(".chat-log"),
         "'show features' reports what runs where")
    step("Loopback only" in page.inner_text(".chat-log"),
         "and states the network posture")

    chat(page, "show account information")
    step("isn't registered yet" in page.inner_text(".chat-log"), "face-gated command is blocked")
    chat(page, "no")
    shoot(page, "console")

    # Layout assertions against what the original does.
    box = page.evaluate("""() => {
      const q = s => document.querySelector(s)?.getBoundingClientRect() ?? null;
      return { screen: q('.main-screen'), top: q('.topbar'), body: q('.main-body'),
               side: q('.side-panel'), chat: q('.chat-panel'), input: q('.input-row'),
               vw: innerWidth, vh: innerHeight };
    }""")
    step(box["top"] and box["top"]["width"] >= box["vw"] - 1,
         f"topbar spans the full width ({box['top']['width'] if box['top'] else '?'} / {box['vw']})")
    step(box["top"] and box["top"]["y"] < 2, "topbar sits at the very top")
    step(box["side"] and box["side"]["x"] < 40, "side panel is on the left edge")
    step(box["chat"] and box["chat"]["width"] > box["vw"] * 0.6, "chat panel takes the remaining width")
    step(box["input"] and box["input"]["y"] > box["vh"] * 0.75, "input row is pinned near the bottom")

    # ---------- the message box ----------
    with Soft(page, "chat-input"):
        check_autogrow(page, ".chat-input", "the message box", 6)

        # Enter sends; Shift+Enter is how you get a second line. A
        # textarea does neither by default - Enter just inserts a
        # newline - so both are behaviour this has to carry itself.
        page.fill(".chat-input", "hello")
        page.press(".chat-input", "Enter")
        page.wait_for_timeout(1500)
        step(page.input_value(".chat-input") == "", "Enter sends the message and clears the box")
        step("hello" in page.inner_text(".chat-log"), "...and the message reaches the log")

        page.fill(".chat-input", "first")
        page.press(".chat-input", "Shift+Enter")
        page.type(".chat-input", "second")
        page.wait_for_timeout(200)
        value = page.input_value(".chat-input")
        step("\n" in value, f"Shift+Enter makes a new line instead of sending ({value!r})")
        grew = page.evaluate(
            "() => document.querySelector('.chat-input').getBoundingClientRect().height")
        page.fill(".chat-input", "")
        page.wait_for_timeout(200)
        back = page.evaluate(
            "() => document.querySelector('.chat-input').getBoundingClientRect().height")
        step(grew > back + 4, f"two lines is taller than one ({back:.0f}px -> {grew:.0f}px)")

    # ---------- the sidebar's command list ----------
    with Soft(page, "sidebar"):
        buttons = page.locator(".side-command")
        count = buttons.count()
        step(count >= 10,
             f"the sidebar lists {count} commands (it was 8 hardcoded strings)")
        labels = [buttons.nth(i).inner_text().strip(' "') for i in range(count)]
        step("voice library" in labels, "including the voice library")
        # The column is 240px. A name like "turn on voice chat / turn off
        # voice chat" is both too long for it and only half clickable,
        # which is what the `short` field in the command reference is for.
        longest = max(labels, key=len) if labels else ""
        step(len(longest) < 32, f"all short enough for the column ({longest!r})")

        before = len(page.inner_text(".chat-log"))
        page.locator(".side-command", has_text="what time is it").first.click()
        page.wait_for_timeout(1800)
        step(len(page.inner_text(".chat-log")) > before,
             "clicking a command runs it, rather than only typing it")
        step(page.input_value(".chat-input") == "",
             "and leaves nothing in the box to send again")
        shoot(page, "sidebar")

    # ---------- the voice library ----------
    with Soft(page, "voice-library"):
        chat(page, "voice library", wait=2200)
        page.wait_for_selector(".voice-library", timeout=20000)
        step(True, "'voice library' opens the library")
        rows = page.locator(".voice-row")
        step(rows.count() >= 1, f"it lists {rows.count()} voices")
        step("only two in Quietude that use" in page.inner_text(".voices-online-note"),
             "and says plainly that this screen goes online")
        shoot(page, "voice-library")

        page.fill(".voices-search", "zzzznotavoice")
        page.wait_for_timeout(900)
        step(page.locator(".voice-row").count() == 0, "search filters the list")
        page.fill(".voices-search", "")
        page.wait_for_timeout(900)
        step(page.locator(".voice-row").count() >= 1, "and clearing it brings them back")

        page.click(".back-bar-btn")
        page.wait_for_selector(".main-body", timeout=10000)

    # ---------- the two former pop-up windows, reached as Quietude opens them ----------
    with Soft(page, "commands"):
        chat(page, "show commands", wait=2200)
        page.wait_for_selector(".commands-table", timeout=12000)
        step(True, "'show commands' navigates to the reference")
        page.mouse.wheel(0, 1400)
        page.wait_for_timeout(500)
        bar = page.locator(".back-bar-btn")
        step(bar.is_visible(), "back button still visible after scrolling the table")
        shoot(page, "commands")
        bar.click()
        page.wait_for_selector(".main-body", timeout=10000)

    with Soft(page, "tts-settings"):
        chat(page, "tts settings", wait=2200)
        page.wait_for_selector(".tts-voice-list", timeout=12000)
        step(True, "'tts settings' navigates to settings")

        # Only one voice may be selected at a time. The ids used to come
        # from espeak's Language column, where sixteen English voices
        # share the code "en", so clicking one checked all sixteen.
        options = page.locator(".voice-option")
        count = options.count()
        step(count > 1, f"settings lists {count} voices")
        for index in (0, 2, 1):
            if index >= count:
                continue
            options.nth(index).click()
            page.wait_for_timeout(260)
            checked = page.locator(".voice-option.checked").count()
            step(checked == 1, f"voice {index} selected -> exactly 1 checked (got {checked})")

        # ---------- the preview box ----------
        step(page.locator(".tts-preview-box").count() == 1, "settings has a preview box")
        placeholder = page.get_attribute(".tts-preview-box", "placeholder") or ""
        step(len(placeholder) > 10,
             f"the preview box invites you to type ({placeholder[:40]!r})")
        check_autogrow(page, ".tts-preview-box", "the preview box", 4)

        # The <style> block used to sit inside the <template>, between
        # elements. Vue is forgiving about it; the browser is not, and
        # the rules landed on the page as visible text.
        step("margin-top" not in page.inner_text(".tts-settings-panel"),
             "no stylesheet text leaking into the page")

        preview_btn = page.locator(".tts-preview-row button")
        if preview_btn.is_disabled():
            step(True, "preview is disabled because no voice is installed (expected here)")
        else:
            # The point of the whole change: the preview must speak the
            # text in the box using the controls as they read NOW. It
            # used to speak a hardcoded line with the saved settings,
            # so the one control for "what will this sound like?"
            # couldn't answer until after you had committed.
            page.evaluate("""() => {
              const el = document.querySelector('#tts-rate');
              el.value = 173;
              el.dispatchEvent(new Event('input', { bubbles: true }));
            }""")
            page.fill(".tts-preview-box", "Testing one two three.")
            page.wait_for_timeout(200)
            with page.expect_request(
                    lambda r: "/api/tts/speak" in r.url, timeout=15000) as caught:
                preview_btn.click()
            sent = caught.value.post_data_json or {}
            step(sent.get("text") == "Testing one two three.",
                 f"preview speaks what you typed ({sent.get('text')!r})")
            step(sent.get("rate") == 173,
                 f"preview uses the rate on screen, not the saved one (sent {sent.get('rate')})")
            step("voice_id" in sent and "volume" in sent,
                 "preview carries the voice and volume on screen too")
            page.wait_for_timeout(600)
            step(preview_btn.inner_text().strip().lower() in ("stop", "preview"),
                 f"the button offers to stop while playing ({preview_btn.inner_text().strip()!r})")
            page.fill(".tts-preview-box", "")

        # The back button must be reachable without scrolling back up.
        page.mouse.wheel(0, 900)
        page.wait_for_timeout(500)
        bar = page.locator(".back-bar-btn")
        step(bar.is_visible(), "back button still visible after scrolling down")
        box = bar.bounding_box()
        step(bool(box) and box["y"] < 260,
             f"back button stays near the top (y={box['y']:.0f})" if box else "no back button")
        shoot(page, "tts-settings")
        bar.click()
        page.wait_for_selector(".main-body", timeout=10000)

    with Soft(page, "face-register"):
        chat(page, "register face", wait=900)
        step("well-lit" in page.inner_text(".chat-log"), "register face warns about lighting first")

        # The countdown has to actually tick. It used to render its first
        # value and freeze, because the message handed back to the
        # countdown was the raw object rather than the one inside Vue's
        # reactive array, so mutating it never triggered a render.
        seen = []
        for _ in range(7):
            text = page.inner_text(".chat-log")
            import re as _re
            found = _re.findall(r"registration in (\d+)", text)
            if found:
                seen.append(found[-1])
            page.wait_for_timeout(1000)
        distinct = len(set(seen))
        step(distinct >= 3,
             f"countdown updates live ({distinct} distinct values seen: {' '.join(dict.fromkeys(seen))})")
        page.wait_for_selector(".face-panel", timeout=25000)
        page.wait_for_timeout(3000)
        shoot(page, "face-register")

    # ---------- returning user, fresh browser session ----------
    ctx2 = browser.new_context(viewport=VIEWPORT, device_scale_factor=2,
                               permissions=["camera", "microphone"])
    page2 = ctx2.new_page()
    page2.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page2.goto(BASE, wait_until="domcontentloaded")
    page2.wait_for_selector(".login-choice-buttons", timeout=15000)
    step("Khavish" in page2.inner_text(".login-choice-panel"), "login greets by name")
    shoot(page2, "login-choice")

    page2.click("text=Login via Password")
    page2.wait_for_selector(".setup-panel input", timeout=10000)
    page2.wait_for_timeout(900)
    page2.fill(".setup-panel input", "wrongpassword")
    page2.press(".setup-panel input", "Enter")
    page2.wait_for_timeout(1400)
    step("attempts remaining" in page2.inner_text(".setup-log"), "wrong password warns with a countdown")
    shoot(page2, "password-login")

    page2.fill(".setup-panel input", "Str0ng!Passw0rd")
    page2.press(".setup-panel input", "Enter")
    page2.wait_for_selector(".main-body", timeout=45000)
    step(True, "correct password logs in")

    # Face unlock, reached from the login screen.
    ctx3 = browser.new_context(viewport=VIEWPORT, device_scale_factor=2,
                               permissions=["camera", "microphone"])
    page3 = ctx3.new_page()
    page3.goto(f"{BASE}/login/face", wait_until="domcontentloaded")
    page3.wait_for_selector(".face-panel", timeout=15000)
    page3.wait_for_timeout(3500)
    shoot(page3, "face-unlock")

    browser.close()

print()
failed = [l for ok, l in steps if not ok]
print(f"{len(steps) - len(failed)}/{len(steps)} checks passed")
if errors:
    print("\nbrowser errors:")
    for e in dict.fromkeys(errors):
        print(f"  {e[:170]}")
