# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Every view, at every size, checked for all four reported faults.

  1. nothing outside the window
  2. the page does not scroll as a whole (each pane scrolls on its own)
  3. no raw {placeholder} reached the screen
  4. nothing calls the assistant by the application's name

Each one was a real bug. Checking all of them on every screen is the
cheap way to stop any of them coming back somewhere else.
"""
import sys, pathlib, re
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from playwright.sync_api import sync_playwright
from drive import FAKE, ASSISTANT, setup, overflow

OUT = pathlib.Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
SIZES = [(760, 520), (1180, 760), (1920, 1040)]

# Each view, and how to get to it from the console.
VIEWS = [
    ("console",      None),
    ("commands",     "show commands"),
    ("identity",     "who are you"),
    ("tts-settings", "tts settings"),
    ("voices",       "voice library"),
    ("models",       "speech models"),
    ("app-settings", "app settings"),
    ("user-commands", "user commands"),
]

PLACEHOLDER = re.compile(r"\{(name|wake|they_are|they|them|their|theirs|themself)\}")

# The only two places the program may be named: the agreement, which is
# about the program, and the identity page, which exists to teach the
# difference between the program and the assistant inside it. Anywhere
# else, "Quietude" on screen means a string that should have been her
# name and wasn't.
ABOUT_THE_PROGRAM = (
    "Quietude runs entirely on",
    "Quietude is the application",
)

fails = 0
with sync_playwright() as p:
    b = p.chromium.launch(args=FAKE)
    page = b.new_page(viewport={"width": 1180, "height": 760})
    setup(page)

    for name, command in VIEWS:
        for w, h in SIZES:
            page.set_viewport_size({"width": w, "height": h})
            page.wait_for_timeout(400)
            if command:
                for _ in range(200):
                    if page.evaluate("() => { const e = document.querySelector('.chat-input');"
                                     "        return !!e && !e.disabled; }"):
                        break
                    page.wait_for_timeout(100)
                page.eval_on_selector(".chat-input", """(e, v) => {
                    e.value = v; e.dispatchEvent(new Event('input', {bubbles:true}));
                }""", command)
                page.press(".chat-input", "Enter")
                page.wait_for_timeout(2600)
            page.wait_for_timeout(500)

            # Did the page actually open?
            #
            # Without this, a command that silently does nothing leaves
            # the console on screen, the console has no overflow and no
            # stray names, and the check passes for a view nobody
            # looked at. Which is exactly what happened.
            where = page.evaluate("() => location.pathname")
            opened = where.strip("/") == name or (name == "console" and
                                                  where.strip("/") in ("", "console"))

            o = overflow(page)
            text = page.evaluate("() => document.body.innerText")
            bad = [e for e in o["elements"] if e["over"] > 2]
            scrolls = o["docScrollW"] > o["vw"] + 2 or o["docScrollH"] > o["vh"] + 2
            leaked = sorted(set(PLACEHOLDER.findall(text)))
            stray = []
            for line in text.splitlines():
                if "Quietude" not in line:
                    continue
                if any(ok in line for ok in ABOUT_THE_PROGRAM):
                    continue
                stray.append(line.strip()[:70])

            ok = opened and not bad and not scrolls and not leaked and not stray
            if not opened:
                print(f"  FAIL  {name:<14} {w}x{h}")
                print(f"          never opened - still at {where!r}")
                fails += 1
                page.screenshot(path=str(OUT / f"{name}-{w}x{h}.png"))
                continue
            print(f"  {'pass' if ok else 'FAIL'}  {name:<13} {w}x{h}")
            for e in bad[:3]:
                print(f"          {e['over']}px past {e['side']}: {e['tag']}.{e['cls']} {e['text']!r}")
            if scrolls:
                print(f"          page {o['docScrollW']}x{o['docScrollH']} in {o['vw']}x{o['vh']}")
            for pl in leaked:
                print(f"          raw placeholder on screen: {{{pl}}}")
            for sline in stray[:3]:
                print(f"          calls her by the app's name: {sline!r}")
            if not ok:
                fails += 1
            page.screenshot(path=str(OUT / f"{name}-{w}x{h}.png"))

            if command:
                # Back to the console by its own route.
                #
                # This used to press Back, which walks the history stack
                # - and after a few views that is not the console, it is
                # wherever the stack happens to be. It ended up on a
                # first-run screen with no session, which is how a sweep
                # that claimed every view was clean managed never to
                # open half of them.
                back = page.query_selector(".back-bar-btn")
                if back:
                    back.click()
                else:
                    page.goto(f"{BASE}/console", wait_until="networkidle")
                page.wait_for_selector(".chat-input", timeout=20000)
                page.wait_for_timeout(900)

    b.close()
total = len(VIEWS) * len(SIZES)
print(f"\n{total - fails}/{total} view/size combinations clean")
sys.exit(1 if fails else 0)
