# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bug 2: does the layout hold still while things happen?

The complaint was that playing a reply aloud, or turning voice chat off,
pushed parts of the interface off the screen. So this walks those states
at several window sizes and checks two things each time: nothing outside
the window, and the message box has not moved. The box is the anchor at
the bottom of the layout - whatever pushes it is pushing everything.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from playwright.sync_api import sync_playwright
from drive import FAKE, setup, overflow

OUT = pathlib.Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
SIZES = [(760, 520), (1180, 760), (1920, 1040)]


def ready(page, timeout_ms=25000):
    """Wait for the message box to accept input again.

    Polled from here rather than with a selector or wait_for_function:
    the enabled state is a DOM property and `disabled` is simply absent
    as an attribute, and a plain loop is easier to be sure of than
    either of the clever ways of asking."""
    for _ in range(timeout_ms // 100):
        if page.evaluate("""() => {
              const e = document.querySelector('.chat-input');
              return !!e && !e.disabled;
            }"""):
            return
        page.wait_for_timeout(100)
    raise AssertionError(
        "the message box never became editable; the page is at "
        + page.evaluate("() => location.pathname"))


def typed(page, text):
    ready(page)
    page.eval_on_selector(".chat-input", """(e, v) => {
        e.value = v;
        e.dispatchEvent(new Event('input', {bubbles: true}));
    }""", text)


def anchor(page):
    return page.evaluate("""() => {
      const el = document.querySelector('.chat-input');
      const bar = document.querySelector('.topbar');
      if (!el) return null;
      const r = el.getBoundingClientRect(), b = bar.getBoundingClientRect();
      return {boxBottom: Math.round(r.bottom), barBottom: Math.round(b.bottom),
              inside: r.bottom <= innerHeight + 1 && r.right <= innerWidth + 1
                      && r.left >= -1 && r.top >= -1};
    }""")


def check(page, label, base, shot=None):
    o, a = overflow(page), anchor(page)
    bad = [e for e in o["elements"] if e["over"] > 2]
    scrolls = o["docScrollW"] > o["vw"] + 2 or o["docScrollH"] > o["vh"] + 2
    moved = base is not None and abs(a["boxBottom"] - base["boxBottom"]) > 2
    ok = not bad and not scrolls and a["inside"] and not moved
    print(f"    {'pass' if ok else 'FAIL'}  {label:<36} box bottom {a['boxBottom']}"
          + ("" if base is None else f" (was {base['boxBottom']})"))
    for e in bad[:3]:
        print(f"            {e['over']}px past {e['side']}: {e['tag']}.{e['cls']} {e['text']!r}")
    if scrolls:
        print(f"            page {o['docScrollW']}x{o['docScrollH']} in {o['vw']}x{o['vh']}")
    if not a["inside"]:
        print("            the message box is outside the window")
    if moved:
        print(f"            the message box moved {a['boxBottom'] - base['boxBottom']}px")
    if shot:
        page.screenshot(path=str(OUT / shot))
    return ok


fails = 0
with sync_playwright() as p:
    b = p.chromium.launch(args=FAKE)
    page = b.new_page(viewport={"width": 1180, "height": 760})
    setup(page)

    for w, h in SIZES:
        print(f"\n  {w}x{h}")
        page.set_viewport_size({"width": w, "height": h})
        page.wait_for_timeout(700)
        base = anchor(page)
        fails += not check(page, "at rest", None, f"{w}x{h}-rest.png")

        # Deliberately commands that answer in place. "help" and the
        # like open their own page, which takes the message box off
        # screen - correctly, but it is not what is being measured here.
        # All three answer in place. Several commands - "help", "who are
        # you", "tts settings" - open a page of their own and take the
        # message box off screen, correctly, which is not what is being
        # measured here.
        for q in ("what time is it", "hello",
                  "something long enough that the reply wraps over "
                  "more than one line in a narrow window"):
            typed(page, q)
            page.press(".chat-input", "Enter")
            page.wait_for_timeout(1600)
        fails += not check(page, "with the log full", base, f"{w}x{h}-full.png")

        # A reply played aloud: the playback bar appears between the top
        # bar and the body. This is the state that was reported.
        spk = page.query_selector_all(".speak-btn")
        if spk:
            spk[-1].click()
            page.wait_for_timeout(1300)
            fails += not check(page, "while a reply is playing", base, f"{w}x{h}-tts.png")
            page.wait_for_timeout(2500)
        else:
            print("    ----  no speak button found, skipped the playback state")

        # The message box grown to its ceiling.
        typed(page, "\n".join(f"line {i}" for i in range(1, 9)))
        page.wait_for_timeout(600)
        a = anchor(page)
        print(f"    {'pass' if a['inside'] else 'FAIL'}  "
              f"{'with the box grown to its ceiling':<36} box bottom {a['boxBottom']}")
        fails += not a["inside"]
        page.screenshot(path=str(OUT / f"{w}x{h}-grown.png"))
        typed(page, "")
        page.wait_for_timeout(400)

        page.click(".voice-btn")
        page.wait_for_timeout(3200)
        fails += not check(page, "with voice chat on", base, f"{w}x{h}-voice.png")
        page.click(".voice-btn")
        page.wait_for_timeout(2600)
        fails += not check(page, "after voice chat is turned off", base, f"{w}x{h}-voiceoff.png")

    b.close()
print(f"\n{'all states hold' if not fails else str(fails) + ' failures'}")
sys.exit(1 if fails else 0)
