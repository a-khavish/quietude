# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""The chat log follows the newest message, and knows when not to.

Four behaviours, each of which is wrong in a different direction if you
only implement the other three:

  it follows          a reply that arrives while you are at the bottom
                      brings the bottom with it
  it lets go          scrolling up to read something stays where you put
                      it, even when a reply arrives on its own
  it takes hold again scrolling back down resumes following
  it comes back       leaving for another page and returning puts you at
                      the newest message, not at the first one

The last one is what was reported. The messages live in the store rather
than in the component, so coming back rebuilds a log that is already
full: nothing is appended, and a watcher on "a message was added" has
nothing to fire on.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from playwright.sync_api import sync_playwright
from drive import FAKE, setup

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


def metrics(page):
    return page.eval_on_selector(".chat-log", """el => ({
        top: Math.round(el.scrollTop),
        height: Math.round(el.scrollHeight),
        view: Math.round(el.clientHeight),
        bottom: Math.round(el.scrollHeight - el.scrollTop - el.clientHeight),
    })""")


def send(page, text, settle=2600):
    page.eval_on_selector(".chat-input", """(e, v) => {
        e.value = v; e.dispatchEvent(new Event('input', {bubbles:true}));
    }""", text)
    page.press(".chat-input", "Enter")
    page.wait_for_timeout(settle)


def wheel_up(page, amount=400):
    """A real wheel gesture over the log, not an assignment to scrollTop.

    Setting scrollTop is how the code scrolls; using it to simulate the
    user would be the test agreeing with the implementation about what
    scrolling is.
    """
    box = page.query_selector(".chat-log").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.wheel(0, -amount)
    page.wait_for_timeout(500)


with sync_playwright() as p:
    b = p.chromium.launch(args=FAKE)
    page = b.new_page(viewport={"width": 1180, "height": 760})
    setup(page)

    print("\n-- enough to scroll --")
    for _ in range(6):
        send(page, "show features", 2000)
    m = metrics(page)
    check("the log is longer than its window", m["height"] > m["view"] + 200, m)
    check("and it is showing the end of it", m["bottom"] <= 60, m)
    page.screenshot(path=str(OUT / "at-the-end.png"))

    print("\n-- it follows --")
    before = metrics(page)
    send(page, "what time is it")
    after = metrics(page)
    check("a reply makes the log longer", after["height"] > before["height"])
    check("and the view came with it", after["bottom"] <= 60, after)

    print("\n-- it lets go --")
    wheel_up(page, 500)
    parked = metrics(page)
    check("scrolling up goes up", parked["bottom"] > 60, parked)

    # A message that arrives on its own, with nothing sent to summon it:
    # the finishing line of a command started a few seconds earlier. The
    # one case where the log must not move under a reader.
    send(page, "user commands", 2200)
    page.wait_for_selector(".uc-toolbar", timeout=20000)
    page.click(".uc-toolbar .btn-primary")
    page.wait_for_selector(".uc-form", timeout=10000)
    page.fill(".uc-form input[placeholder='back up my notes']", "slow one")
    page.fill(".uc-form textarea", "sh -c 'sleep 4; echo done'")
    page.fill(".uc-form input[placeholder='Your notes are backed up.']",
              "That is finished.")
    page.click(".uc-form-actions .btn-primary")
    page.wait_for_selector(".uc-row", timeout=10000)
    page.click(".back-bar-btn")
    page.wait_for_selector(".chat-input", timeout=20000)
    page.wait_for_timeout(800)

    send(page, "slow one", 1500)
    wheel_up(page, 600)
    parked = metrics(page)
    check("parked part way up while it runs", parked["bottom"] > 60, parked)

    for _ in range(60):
        if "That is finished." in page.inner_text(".chat-log"):
            break
        page.wait_for_timeout(250)
    page.wait_for_timeout(1200)
    held = metrics(page)
    check("a reply arriving on its own does not move the view",
          held["top"] == parked["top"], (parked, held))
    check("and the log did grow underneath it",
          held["height"] > parked["height"], (parked, held))
    page.screenshot(path=str(OUT / "parked.png"))

    print("\n-- your own message always wins --")
    # You wrote it and pressed Enter. Not being shown it would be the
    # application ignoring you.
    send(page, "what time is it")
    m = metrics(page)
    check("sending something brings you back to the end", m["bottom"] <= 60, m)

    print("\n-- it takes hold again --")
    wheel_up(page, 600)
    check("let go", metrics(page)["bottom"] > 60)
    page.eval_on_selector(".chat-log", "el => { el.scrollTop = el.scrollHeight }")
    page.wait_for_timeout(300)
    send(page, "show features")
    m = metrics(page)
    check("scrolling back to the end resumes following", m["bottom"] <= 60, m)

    print("\n-- and back from another page --")
    for name, command in (("commands", "show commands"),
                          ("user-commands", "user commands"),
                          ("app-settings", "app settings")):
        send(page, command, 2400)
        here = page.evaluate("() => location.pathname").strip("/")
        check(f"{name} opened", here == name, here)
        page.click(".back-bar-btn")
        page.wait_for_selector(".chat-log", timeout=20000)
        page.wait_for_timeout(900)
        m = metrics(page)
        check(f"coming back from {name} shows the newest message",
              m["bottom"] <= 60, m)
    page.screenshot(path=str(OUT / "back-from-a-page.png"))

    print("\n-- and at other window sizes --")
    for w, h in ((760, 520), (1920, 1040)):
        page.set_viewport_size({"width": w, "height": h})
        page.wait_for_timeout(600)
        m = metrics(page)
        check(f"resizing to {w}x{h} keeps the end in view", m["bottom"] <= 60, m)

    b.close()

print()
print("=" * 52)
print(f"  {PASSED} passed, {FAILED} failed")
print("=" * 52)
sys.exit(1 if FAILED else 0)
