# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Does every view fit, at every size the window can be?"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from playwright.sync_api import sync_playwright
from drive import BASE, FAKE, ASSISTANT, setup, overflow

OUT = pathlib.Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)

# The floor the window enforces, the size it opens at, a laptop, and a
# maximised 1080p screen.
SIZES = [(760, 520), (900, 620), (1180, 760), (1366, 700), (1920, 1040)]

fails = 0
with sync_playwright() as p:
    b = p.chromium.launch(args=FAKE)
    page = b.new_page(viewport={"width": 1180, "height": 760})
    setup(page)

    for w, h in SIZES:
        page.set_viewport_size({"width": w, "height": h})
        page.wait_for_timeout(700)
        o = overflow(page)
        tag = f"{w}x{h}"
        # The message box is the thing that must never be off screen.
        box = page.evaluate("""() => {
          const el = document.querySelector('.chat-input');
          if (!el) return null;
          const r = el.getBoundingClientRect();
          return {bottom: Math.round(r.bottom), right: Math.round(r.right),
                  h: Math.round(r.height), visible: r.bottom <= innerHeight + 1
                                                    && r.right <= innerWidth + 1};
        }""")
        bad = [e for e in o["elements"] if e["over"] > 2]
        scroll = (o["docScrollW"] > o["vw"] + 2) or (o["docScrollH"] > o["vh"] + 2)
        ok = not bad and not scroll and box and box["visible"]
        print(f"  {'pass' if ok else 'FAIL'}  {tag:<10} "
              f"doc {o['docScrollW']}x{o['docScrollH']}  "
              f"message box bottom={box['bottom'] if box else '?'} "
              f"({'on screen' if box and box['visible'] else 'OFF SCREEN'})")
        for e in bad[:4]:
            print(f"           {e['over']}px past {e['side']}: {e['tag']}.{e['cls']} {e['text']!r}")
        if scroll:
            print(f"           page scrolls: {o['docScrollW']}x{o['docScrollH']} in {o['vw']}x{o['vh']}")
        if not ok:
            fails += 1
        page.screenshot(path=str(OUT / f"console-{tag}.png"))

    b.close()
print(f"\n{len(SIZES) - fails}/{len(SIZES)} sizes clean")
sys.exit(1 if fails else 0)
