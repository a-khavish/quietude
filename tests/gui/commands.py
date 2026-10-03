# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Both command pages: the reference, and the user's own.

This suite exists because of one character. Renaming the user-commands
route to stop it colliding with the reference also repointed the call
the *reference* makes - so "show commands" listed the user's own
commands, of which there were none, and the shortcut column in the side
panel quietly fell back to a hard-coded list from an older release.

Every other check passed. The page opened, nothing overflowed, no
placeholder leaked, and the table was empty. So this one reads the
table: a reference with no built-in commands in it is a broken page, not
a clean one.

The second half is about timing rather than content. A command says
three lines - starting, running, finished - and the only way to tell
whether they land where they claim to is to watch the chat while one
runs, which is what the last section does.
"""
import re
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from playwright.sync_api import sync_playwright
from drive import BASE, FAKE, setup

OUT = pathlib.Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)

# A handful that have existed for as long as the app has. If these are
# not on the reference page, it is not showing the reference.
EXPECTED = ["show features", "clear terminal", "shutdown", "voice library",
            "voice check"]

PASSED = FAILED = 0


def check(label, ok, detail=""):
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  pass  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label}  {detail}")


def rows(page):
    return page.eval_on_selector_all(
        ".commands-table tbody tr",
        "rows => rows.map(r => r.innerText.replace(/\\s+/g, ' ').trim())")


with sync_playwright() as p:
    b = p.chromium.launch(args=FAKE)
    page = b.new_page(viewport={"width": 1180, "height": 760})
    setup(page)

    print("\n-- the shortcut column --")
    # Built from the reference, with a stale fallback if the call fails.
    # The fallback is why the empty reference was survivable and
    # therefore why it went unnoticed, so it is checked on its own.
    shortcuts = page.eval_on_selector_all(
        ".side-commands button", "b => b.map(e => e.innerText.trim())")
    check("the side panel has shortcuts", len(shortcuts) >= 8, shortcuts)
    check("including ones only the server knows about",
          any("user commands" in s for s in shortcuts)
          and any("app settings" in s for s in shortcuts),
          shortcuts)

    print("\n-- the reference --")
    page.eval_on_selector(".chat-input", """e => {
        e.value = 'show commands';
        e.dispatchEvent(new Event('input', {bubbles:true}));
    }""")
    page.press(".chat-input", "Enter")
    page.wait_for_selector(".commands-table tbody tr", timeout=20000)
    page.wait_for_timeout(600)

    listed = rows(page)
    check("the table has rows", len(listed) > 10, len(listed))
    missing = [c for c in EXPECTED if not any(c in r for r in listed)]
    check("the built-in commands are in it", not missing, f"missing {missing}")

    numbers = page.eval_on_selector_all(
        ".commands-table .cmd-number", "n => n.map(e => e.innerText.trim())")
    check("the rows are numbered from one",
          numbers[:3] == ["1.", "2.", "3."], numbers[:3])
    check("and numbered all the way down", len(numbers) == len(listed))

    page.screenshot(path=str(OUT / "reference-all.png"))

    print("\n-- search --")
    page.fill(".cmd-search-input", "voice")
    page.wait_for_timeout(350)
    found = rows(page)
    check("searching narrows the list", 0 < len(found) < len(listed),
          f"{len(found)} of {len(listed)}")
    check("and every row matches what was typed",
          all("voice" in r.lower() for r in found), found[:3])
    renumbered = page.eval_on_selector_all(
        ".commands-table .cmd-number", "n => n.map(e => e.innerText.trim())")
    check("the numbers run down what is showing, not the whole list",
          renumbered[0] == "1." and len(renumbered) == len(found), renumbered)

    page.fill(".cmd-search-input", "zzzznothing")
    page.wait_for_timeout(350)
    check("a search with no matches says so",
          not rows(page) and page.query_selector(".cmd-none") is not None)
    page.screenshot(path=str(OUT / "reference-no-match.png"))

    page.click(".cmd-search-clear")
    page.wait_for_timeout(350)
    check("clearing it brings everything back", len(rows(page)) == len(listed))

    print("\n-- built in, and mine --")
    counts = page.eval_on_selector_all(
        ".cmd-filter", "f => f.map(e => e.innerText.replace(/\\s+/g,' ').trim())")
    check("there are three filters", len(counts) == 3, counts)

    page.eval_on_selector_all(
        ".cmd-filter",
        "f => f.find(e => e.innerText.includes('Mine')).click()")
    page.wait_for_timeout(350)
    check("with none of your own, Mine is empty and says why",
          not rows(page)
          and "haven't written any" in page.inner_text(".cmd-none"),
          page.inner_text(".cmd-none") if page.query_selector(".cmd-none") else "")

    page.eval_on_selector_all(
        ".cmd-filter",
        "f => f.find(e => e.innerText.includes('Built in')).click()")
    page.wait_for_timeout(350)
    built = rows(page)
    check("Built in shows the reference", len(built) == len(listed), len(built))
    check("and none of them are badged as yours",
          not page.query_selector(".commands-table .cmd-badge"))

    print("\n-- one of your own, in both places --")
    # Written through the page it belongs to, then looked for here: the
    # two lists are separate calls and this is the one thing that proves
    # they end up in the same table.
    page.click(".cmd-footer .btn-ghost")
    page.wait_for_selector(".uc-toolbar", timeout=20000)
    page.click(".uc-toolbar .btn-primary")
    page.wait_for_selector(".uc-form", timeout=10000)
    page.wait_for_timeout(350)
    page.screenshot(path=str(OUT / "own-form.png"), full_page=True)
    page.fill(".uc-form input[placeholder='back up my notes']", "greet me")
    page.fill(".uc-form input[placeholder^='Copies this week']",
              "Prints a greeting.")
    page.fill(".uc-form textarea", "echo hello")
    page.click(".uc-form-actions .btn-primary")
    page.wait_for_selector(".uc-row", timeout=10000)
    check("it saves", "greet me" in page.inner_text(".uc-row"))
    check("numbered there too",
          page.inner_text(".uc-row-number").strip() == "1.")
    check("with its description shown",
          "Prints a greeting." in page.inner_text(".uc-row"))
    page.screenshot(path=str(OUT / "reference-own-command.png"))

    page.click(".back-bar-btn")
    page.wait_for_selector(".chat-input", timeout=20000)
    page.wait_for_timeout(600)
    page.eval_on_selector(".chat-input", """e => {
        e.value = 'show commands';
        e.dispatchEvent(new Event('input', {bubbles:true}));
    }""")
    page.press(".chat-input", "Enter")
    page.wait_for_selector(".commands-table tbody tr", timeout=20000)
    page.wait_for_timeout(800)

    both = rows(page)
    check("the reference now lists it alongside the built-ins",
          any("greet me" in r for r in both) and len(both) == len(listed) + 1,
          f"{len(both)} rows")
    check("and marks which one is yours",
          page.query_selector(".commands-table .cmd-badge") is not None)
    check("with the description it was given, not its shell command",
          any("Prints a greeting." in r for r in both)
          and not any("echo hello" in r for r in both))

    page.eval_on_selector_all(
        ".cmd-filter",
        "f => f.find(e => e.innerText.includes('Mine')).click()")
    page.wait_for_timeout(350)
    mine = rows(page)
    check("Mine shows just that one", len(mine) == 1 and "greet me" in mine[0], mine)
    page.screenshot(path=str(OUT / "reference-mine.png"))

    # Narrow, where the table becomes a column of cards. The number and
    # the badge are part of the name cell rather than columns of their
    # own, which is the whole reason they survive this.
    print("\n-- narrow --")
    for w, h in ((760, 520), (420, 700)):
        page.set_viewport_size({"width": w, "height": h})
        page.wait_for_timeout(450)
        over = page.evaluate("""() => {
            const r = document.documentElement;
            return [r.scrollWidth - r.clientWidth, [...document.querySelectorAll(
                '.cmd-tools *, .commands-table *')].filter(e => {
                const b = e.getBoundingClientRect();
                return b.width && (b.right > innerWidth + 2 || b.left < -2);
            }).length];
        }""")
        check(f"nothing spills at {w}x{h}", over == [0, 0], over)
        page.screenshot(path=str(OUT / f"reference-{w}x{h}.png"))

    print("\n-- the three lines, from the chat --")
    # The one thing no unit test can show: that she says the first two
    # while the command is going, and the last one after it has ended.
    page.set_viewport_size({"width": 1180, "height": 760})
    page.wait_for_timeout(400)
    # Said rather than navigated to. A reload here goes back through the
    # route guard with no session in hand and lands on the first-run
    # screen, which is a different page passing a different test.
    page.click(".back-bar-btn")
    page.wait_for_selector(".chat-input", timeout=20000)
    page.wait_for_timeout(700)
    page.eval_on_selector(".chat-input", """e => {
        e.value = 'user commands';
        e.dispatchEvent(new Event('input', {bubbles:true}));
    }""")
    page.press(".chat-input", "Enter")
    page.wait_for_selector(".uc-toolbar", timeout=20000)
    page.click(".uc-toolbar .btn-primary")
    page.wait_for_selector(".uc-form", timeout=10000)
    page.fill(".uc-form input[placeholder='back up my notes']", "slow greet")
    page.fill(".uc-form textarea", "sh -c 'sleep 3; echo the-output'")
    page.fill(".uc-form input[placeholder='Backing up your notes.']", "Starting now.")
    page.fill(".uc-form input[placeholder='This takes a moment.']", "One moment.")
    page.fill(".uc-form input[placeholder='Your notes are backed up.']", "All finished.")
    page.click(".uc-form-actions .btn-primary")
    page.wait_for_selector(".uc-row", timeout=10000)

    page.click(".back-bar-btn")
    page.wait_for_selector(".chat-input", timeout=20000)
    page.wait_for_timeout(700)
    page.eval_on_selector(".chat-input", """e => {
        e.value = 'slow greet';
        e.dispatchEvent(new Event('input', {bubbles:true}));
    }""")
    page.press(".chat-input", "Enter")

    # While it is still running.
    page.wait_for_timeout(1800)
    during = page.inner_text(".chat-log")
    check("she says the starting line as it starts", "Starting now." in during)
    check("and the running line with it", "One moment." in during, during[-200:])
    check("the finishing line has not been said yet",
          "All finished." not in during)
    check("and the output is not on screen before the command has produced it",
          "the-output" not in during)
    page.screenshot(path=str(OUT / "lines-during.png"))

    # And after. Waiting on the output rather than on the finishing
    # line, because they are two bubbles with a deliberate pause
    # between them - arriving at the moment the first one appears means
    # catching the second one still in the air.
    for _ in range(60):
        if "the-output" in page.inner_text(".chat-log"):
            break
        page.wait_for_timeout(250)
    after = page.inner_text(".chat-log")
    check("the finishing line arrives on its own, with no second command sent",
          "All finished." in after, after[-200:])
    check("and the output arrives with it", "the-output" in after, after[-200:])
    check("in that order - she says it is done, then shows what it did",
          after.index("All finished.") < after.index("the-output"))
    check("the starting line is not repeated",
          after.count("Starting now.") == 1, after.count("Starting now."))
    page.screenshot(path=str(OUT / "lines-after.png"))

    b.close()

print()
print("=" * 52)
print(f"  {PASSED} passed, {FAILED} failed")
print("=" * 52)
sys.exit(1 if FAILED else 0)
