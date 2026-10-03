# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bugs 3 and 4: the placeholder names her, and spoken words appear."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from playwright.sync_api import sync_playwright
from drive import BASE, FAKE, ASSISTANT, setup, overflow, say, wait_for_state

OUT = pathlib.Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)

failures = []


def verdict(ok, message):
    print(f"  {'pass' if ok else 'FAIL'} - {message}")
    if not ok:
        failures.append(message)

with sync_playwright() as p:
    b = p.chromium.launch(args=FAKE)
    page = b.new_page(viewport={"width": 1180, "height": 760})
    page.on("console", lambda m: print("   [console]", m.type, m.text[:120])
            if m.type in ("error", "warning") else None)
    setup(page)

    avail = page.evaluate("() => !document.querySelector('.voice-btn.unsupported')")
    print(f"  voice offered: {avail}")

    page.click(".voice-btn")
    page.wait_for_timeout(3500)
    state = page.get_attribute(".voice-btn", "class")
    print(f"  voice button class: {state}")
    msgs = page.eval_on_selector_all(
        ".bubble", "els => els.slice(-3).map(e => e.innerText.slice(0,160))")
    for m in msgs:
        print("   [chat]", m.replace("\n", " ")[:150])
    ph = page.get_attribute(".chat-input", "placeholder")
    print(f"  placeholder: {ph!r}")
    verdict(bool(ph) and ASSISTANT in ph and "Quietude" not in ph,
            f"the placeholder names her ({ASSISTANT}), not the application")
    page.screenshot(path=str(OUT / "voice-asleep.png"))

    print("\n  --- speaking ---")
    say(f"hello {ASSISTANT.lower()}")
    verdict(wait_for_state(page, "awake"), "the wake phrase wakes her")
    print(f"  after wake phrase: button={page.get_attribute('.voice-btn','class')}")
    print(f"    box={page.eval_on_selector('.chat-input','e=>e.value')!r}")
    print(f"    placeholder={page.get_attribute('.chat-input','placeholder')!r}")

    say("what time is it")
    page.wait_for_timeout(2000)
    val = page.eval_on_selector(".chat-input", "e=>e.value")
    print(f"  while speaking: box={val!r}")
    page.screenshot(path=str(OUT / "voice-speaking.png"))


    # Poll the box rather than sampling it once: the words are there for
    # a moment and a single check lands wherever it lands.
    print("\n  --- what the box showed while she listened ---")
    seen = set()
    for _ in range(160):
        v = page.eval_on_selector(".chat-input", "e=>e.value")
        if v:
            seen.add(v)
        page.wait_for_timeout(100)
    for v in sorted(seen, key=len):
        print(f"    {v!r}")
    verdict(bool(seen), "the box showed the words while she listened")

    print("\n  --- the utterance ends ---")
    say("what time is it.")          # the trailing stop finalises it
    page.wait_for_timeout(22000)
    msgs = page.eval_on_selector_all(
        ".bubble", "els => els.slice(-4).map(e => e.innerText.split('\\n')[0])")
    for m in msgs:
        print("   [chat]", m[:110])
    sent = any("what time is it" in m.lower() for m in msgs)
    verdict(sent, "the spoken words became a message")
    page.screenshot(path=str(OUT / "voice-sent.png"))

    print("\n  --- no raw template placeholders anywhere on screen ---")
    leaked = page.evaluate("""() => {
      const t = document.body.innerText;
      const m = t.match(/\\{(name|wake|they|them|their|theirs|themself|they_are)\\}/g);
      return m ? [...new Set(m)] : [];
    }""")
    verdict(not leaked,
            "no raw template placeholders on screen"
            + (f" (found {', '.join(leaked)})" if leaked else ""))

    # --- the one that was reported: answering straight away ---
    #
    # She used to say "Yes, I'm listening." aloud, with the recogniser
    # suppressed for the whole phrase. People say the wake word and the
    # command in one breath, so the command landed inside that deaf
    # window, was never heard, and she went back to sleep having
    # answered nothing. No pause here, deliberately.
    print("\n  --- wake word and command in one breath ---")
    before = page.eval_on_selector_all(".bubble", "els => els.length")
    say("hello ada")
    wait_for_state(page, "awake")
    say("what time is it")
    for _ in range(160):
        texts = page.eval_on_selector_all(
            ".bubble", "els => els.map(e => e.innerText.split('\\n')[0])")
        if any("what time is it" in t.lower() for t in texts[before:]):
            break
        page.wait_for_timeout(250)
    texts = page.eval_on_selector_all(
        ".bubble", "els => els.slice(-3).map(e => e.innerText.split('\\n')[0])")
    for t in texts:
        print("   [chat]", t[:90])
    verdict(any("what time is it" in t.lower() for t in texts),
            "a command spoken immediately after the wake word is heard")

    # --- a pause mid-sentence does not cut the sentence in half ---
    #
    # 700ms used to end an utterance, which is a breath rather than a
    # pause: one sentence arrived as three fragments, each sent as its
    # own command, and any fragment too short to clear the voiced
    # minimum was dropped without a trace. The gate is three seconds
    # now, so this pauses for one and a half in the middle of a sentence
    # and expects the whole thing.
    print("\n  --- a pause in the middle of a sentence ---")
    before = page.eval_on_selector_all(".bubble", "els => els.length")
    texts = []
    say("hello ada")
    wait_for_state(page, "awake")
    say("what time")
    page.wait_for_timeout(1500)        # the kind of pause people take
    say("what time is it")
    for _ in range(160):
        texts = page.eval_on_selector_all(
            ".bubble", "els => els.map(e => e.innerText.split('\\n')[0])")
        if any("what time is it" in t.lower() for t in texts[before:]):
            break
        page.wait_for_timeout(250)
    sent = [t for t in texts[before:] if t.strip()
            and not t.lower().startswith(("yes,", "it's", "did you"))]
    for t in sent:
        print("   [chat]", t[:80])
    whole = any("what time is it" in t.lower() for t in sent)
    chopped = any(t.lower().strip() in ("what time", "what") for t in sent)
    verdict(whole and not chopped,
            "the sentence arrived whole, not cut at the pause")

    # --- and she is still listening afterwards ---
    state = page.get_attribute(".voice-btn", "class") or ""
    verdict("awake" in state or "listening" in state,
            f"still listening after answering (button: {state.strip()})")

    b.close()

print()
if failures:
    print(f"{len(failures)} failed")
sys.exit(1 if failures else 0)
