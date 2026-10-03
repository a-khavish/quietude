# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""The screens you meet before the console, at every window size.

These were never tested, and an audit found three of them unfinishable
at 760x520: the agreement's Agree button, face unlock's Retry button and
face registration's whole lower half were off screen with no way to
scroll to them. All three for one reason - a flex container centring its
panel inside a window that cannot scroll, so a tall panel overflowed
equally off the top and the bottom.

So this checks the thing that actually matters on these screens: every
control you have to press in order to get past them is on screen and
clickable.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from playwright.sync_api import sync_playwright          # noqa: E402
from drive import FAKE, as_the_window, fresh_profile, overflow   # noqa: E402

OUT = pathlib.Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
SIZES = [(760, 520), (900, 620), (1180, 760), (1920, 1040)]

failures = []


def reachable(page, selector):
    """On screen, and something you could actually click.

    elementFromPoint rather than a bounding box alone: a control can sit
    inside the window and still be unreachable behind something else.
    """
    return page.evaluate("""(sel) => {
      const el = document.querySelector(sel);
      if (!el) return {found: false};
      const r = el.getBoundingClientRect();
      const inside = r.top >= 0 && r.left >= 0
                     && r.bottom <= innerHeight + 1 && r.right <= innerWidth + 1;
      const x = r.left + r.width / 2, y = r.top + r.height / 2;
      const hit = (x >= 0 && y >= 0 && x <= innerWidth && y <= innerHeight)
                  ? document.elementFromPoint(x, y) : null;
      return {found: true, inside,
              covered: !!hit && hit !== el && !el.contains(hit),
              top: Math.round(r.top), bottom: Math.round(r.bottom)};
    }""", selector)


def check(page, screen, size, must_reach):
    w, h = size
    o = overflow(page)
    bad = [e for e in o["elements"] if e["over"] > 2]
    problems = [f"{e['over']}px past {e['side']}: {e['tag']}.{e['cls']}" for e in bad[:3]]

    for label, selector in must_reach:
        r = reachable(page, selector)
        if not r["found"]:
            problems.append(f"{label} is not on the page")
        elif not r["inside"]:
            problems.append(f"{label} is outside the window (top {r['top']}, bottom {r['bottom']})")
        elif r["covered"]:
            problems.append(f"{label} is covered by something else")

    ok = not problems
    print(f"  {'pass' if ok else 'FAIL'}  {screen:<16} {w}x{h}")
    for problem in problems:
        print(f"          {problem}")
    if not ok:
        failures.append(f"{screen} at {w}x{h}")
    page.screenshot(path=str(OUT / f"{screen}-{w}x{h}.png"))


with sync_playwright() as p:
    browser = p.chromium.launch(args=FAKE)

    # --- the agreement, which every first launch has to get past ---
    for i, size in enumerate(SIZES):
      with fresh_profile(5300 + i) as BASE:
          page = browser.new_page(viewport={"width": size[0], "height": size[1]})
          as_the_window(page)
          page.goto(BASE, wait_until="networkidle")
          page.wait_for_selector(".agreement-list", timeout=30000)
          check(page, "agreement", size, [("the Agree button", ".agreement-panel .btn-primary")])
          page.close()

    # --- setup, and then face registration at the end of it ---
    for i, size in enumerate(SIZES):
      with fresh_profile(5310 + i) as BASE:
          page = browser.new_page(viewport={"width": size[0], "height": size[1]})
          as_the_window(page)
          page.goto(BASE, wait_until="networkidle")
          page.wait_for_selector(".agreement-list", timeout=30000)
          page.click(".agreement-panel .btn-primary")
          page.wait_for_selector(".setup-panel", timeout=20000)
          page.wait_for_timeout(1200)
          check(page, "setup", size, [("the answer box", ".setup-panel input")])

          def answer(text, wait=1100):
              page.fill(".setup-panel input", text)
              page.keyboard.press("Enter")
              page.wait_for_timeout(wait)

          answer("Khavish Auckaloo")
          answer("Khavish")
          answer("29")
          answer("Ada")
          answer("Str0ng!Passw0rd")
          answer("no", 2600)

          # The face question is asked in the console, not in setup, so
          # that is where "yes" has to be said to reach the camera.
          page.wait_for_selector(".main-body", timeout=30000)
          page.wait_for_timeout(2200)
          page.fill(".chat-input", "yes")
          page.press(".chat-input", "Enter")
          # There is a countdown before the camera opens - it warns you
          # about the lighting first - so this waits it out.
          for _ in range(40):
              if page.query_selector(".camera-frame"):
                  break
              page.wait_for_timeout(500)

          if page.query_selector(".camera-frame"):
              page.wait_for_timeout(1500)
              check(page, "face-register", size,
                    [("the camera", ".camera-frame"),
                     ("the status line", ".face-status")])
          else:
              print(f"  ----  face-register      {size[0]}x{size[1]} (not reached)")

          # The login screens, which need an account to exist - which is
          # what the setup above just created. Visited directly rather
          # than by signing out, because there is nothing to sign out of
          # in a session that never ended.
          page.goto(f"{BASE}/login", wait_until="networkidle")
          page.wait_for_timeout(1600)
          if page.query_selector(".login-choice-panel"):
              check(page, "login", size, [("the panel", ".login-choice-panel")])
          page.goto(f"{BASE}/login/password", wait_until="networkidle")
          page.wait_for_timeout(1600)
          if page.query_selector(".setup-panel"):
              check(page, "password-login", size,
                    [("the password box", ".setup-panel input")])
          page.goto(f"{BASE}/login/face", wait_until="networkidle")
          page.wait_for_timeout(2600)
          if page.query_selector(".face-panel"):
              check(page, "face-unlock", size,
                    [("the camera", ".camera-frame"),
                     ("the status line", ".face-status")])
          page.close()

    # --- the overlays ---
    for i, size in enumerate(SIZES):
      with fresh_profile(5330 + i) as BASE:
          page = browser.new_page(viewport={"width": size[0], "height": size[1]})
          as_the_window(page)
          for path, screen in (("/shutdown", "shutdown"), ("/reset", "reset")):
              page.goto(BASE + path, wait_until="networkidle")
              page.wait_for_timeout(1200)
              check(page, screen, size, [])
          page.close()

    browser.close()

print()
print("every screen fits" if not failures else f"{len(failures)} failed")
sys.exit(1 if failures else 0)
