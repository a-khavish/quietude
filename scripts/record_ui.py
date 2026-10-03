#!/usr/bin/env python3
# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Record a video walkthrough of the whole interface.

    python3 scripts/record_ui.py OUT_DIR

Requires Quietude running (QUIETUDE_NO_BROWSER=1 quietude) and `pip install
playwright && playwright install chromium`.

Writes two .webm files - the first run, then the returning-user login,
which needs a fresh browser context. Join and convert them with:

    ffmpeg -f concat -safe 0 -i list.txt -c copy joined.webm
    ffmpeg -i joined.webm -vf scale=1280:-2 -c:v libx264 -crf 25 \
           -pix_fmt yuv420p -movflags +faststart walkthrough.mp4

Input is typed at a human pace rather than filled instantly, and there
are deliberate pauses on each screen, so the result is watchable instead
of a blur.
"""
import shutil, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:5090"
VIEWPORT = {"width": 1440, "height": 900}

def type_slowly(page, selector, text, delay=55):
    page.click(selector)
    page.type(selector, text, delay=delay)

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--use-fake-device-for-media-stream",
                                      "--use-fake-ui-for-media-stream"])
    ctx = browser.new_context(viewport=VIEWPORT,
                              permissions=["camera", "microphone"],
                              record_video_dir=str(OUT / "raw"),
                              record_video_size=VIEWPORT)
    page = ctx.new_page()

    # --- first launch: the agreement ---
    page.goto(BASE, wait_until="domcontentloaded")
    page.wait_for_selector(".agreement-list", timeout=20000)
    page.wait_for_timeout(2600)
    page.click("button.btn-primary")

    # --- conversational setup ---
    page.wait_for_selector(".setup-panel input", timeout=10000)
    page.wait_for_timeout(1100)
    for answer in ("Khavish Auckaloo", "Khavish", "27", "Str0ng!Passw0rd"):
        type_slowly(page, ".setup-panel input", answer)
        page.wait_for_timeout(400)
        page.press(".setup-panel input", "Enter")
        page.wait_for_timeout(1500)
    page.wait_for_timeout(900)
    type_slowly(page, ".setup-panel input", "no")
    page.press(".setup-panel input", "Enter")

    # --- the boot sequence, then the console ---
    page.wait_for_selector(".main-body", timeout=45000)
    page.wait_for_timeout(3200)

    # Answer the face-registration nag.
    type_slowly(page, ".chat-input", "no")
    page.press(".chat-input", "Enter")
    page.wait_for_timeout(2000)

    for message in ("hello", "what time is it", "help"):
        type_slowly(page, ".chat-input", message)
        page.press(".chat-input", "Enter")
        page.wait_for_timeout(2200)

    # What runs where - the privacy summary, read live.
    type_slowly(page, ".chat-input", "show features")
    page.press(".chat-input", "Enter")
    page.wait_for_timeout(3400)

    # A face-gated command, refused and explained.
    type_slowly(page, ".chat-input", "show account information")
    page.press(".chat-input", "Enter")
    page.wait_for_timeout(2600)
    type_slowly(page, ".chat-input", "no")
    page.press(".chat-input", "Enter")
    page.wait_for_timeout(1800)

    # --- the message box, growing and then capping ---
    type_slowly(page, ".chat-input",
                "The message box grows as I type, so a long command stays "
                "readable instead of scrolling past the caret one character "
                "at a time - and it stops at six lines rather than eating "
                "the conversation above it.", delay=22)
    page.wait_for_timeout(1800)
    page.fill(".chat-input", "")
    page.wait_for_timeout(900)

    # --- the sidebar's commands are buttons now ---
    page.wait_for_timeout(700)
    page.locator(".side-command", has_text="what time is it").first.click()
    page.wait_for_timeout(2200)

    # --- the voice library ---
    type_slowly(page, ".chat-input", "voice library")
    page.press(".chat-input", "Enter")
    page.wait_for_selector(".voice-library", timeout=15000)
    page.wait_for_timeout(2200)
    type_slowly(page, ".voices-search", "english", delay=70)
    page.wait_for_timeout(1800)
    page.fill(".voices-search", "")
    page.wait_for_timeout(1400)
    row = page.locator(".voice-row").filter(has=page.locator("button",
                                                            has_text="Download")).first
    if row.count():
        row.locator("button", has_text="Download").click()
        # Let the progress bar actually be seen.
        for _ in range(40):
            page.wait_for_timeout(250)
            if row.locator("button", has_text="Remove").count():
                break
        page.wait_for_timeout(1600)
    page.click(".back-bar-btn")
    page.wait_for_selector(".main-body", timeout=10000)
    page.wait_for_timeout(1200)

    # --- the command reference ---
    type_slowly(page, ".chat-input", "show commands")
    page.press(".chat-input", "Enter")
    page.wait_for_selector(".commands-table", timeout=12000)
    page.wait_for_timeout(1500)
    page.mouse.wheel(0, 900)
    page.wait_for_timeout(1700)
    page.mouse.wheel(0, 1100)
    page.wait_for_timeout(1700)   # back button still pinned at the top
    page.click(".back-bar-btn")
    page.wait_for_selector(".main-body", timeout=10000)
    page.wait_for_timeout(1200)

    # --- voice settings ---
    type_slowly(page, ".chat-input", "tts settings")
    page.press(".chat-input", "Enter")
    page.wait_for_selector(".tts-settings-panel", timeout=12000)
    page.wait_for_timeout(1600)
    # Drag the rate slider so the live value readout is visible.
    rate = page.locator("#tts-rate")
    box = rate.bounding_box()
    if box:
        page.mouse.move(box["x"] + box["width"] * 0.47, box["y"] + box["height"] / 2)
        page.mouse.down()
        for frac in (0.6, 0.72, 0.3, 0.52):
            page.mouse.move(box["x"] + box["width"] * frac, box["y"] + box["height"] / 2)
            page.wait_for_timeout(320)
        page.mouse.up()
    page.wait_for_timeout(900)
    voices = page.locator(".voice-option")
    if voices.count() > 2:
        voices.nth(2).click()
        page.wait_for_timeout(1200)

    # The preview box: typed into, growing as it wraps, then played with
    # the settings that are on screen rather than the ones on disk.
    page.locator(".tts-preview-box").scroll_into_view_if_needed()
    page.wait_for_timeout(700)
    type_slowly(page, ".tts-preview-box",
                "Nothing leaves this machine. Here is how that sounds "
                "at the rate and voice you just picked.", delay=34)
    page.wait_for_timeout(1100)
    preview = page.locator(".tts-preview-row button")
    if not preview.is_disabled():
        preview.click()
        page.wait_for_timeout(3200)
    page.wait_for_timeout(800)

    page.click(".back-bar-btn")
    page.wait_for_selector(".main-body", timeout=10000)
    page.wait_for_timeout(1400)

    # --- face registration, with its lighting warning and live countdown ---
    type_slowly(page, ".chat-input", "register face")
    page.press(".chat-input", "Enter")
    page.wait_for_timeout(7000)          # the countdown bubble, ticking down live
    page.wait_for_selector(".face-panel", timeout=25000)
    page.wait_for_timeout(4000)          # the camera, scanning
    page.click(".back-link")
    page.wait_for_selector(".main-body", timeout=10000)
    page.wait_for_timeout(1800)

    ctx.close()

    # --- returning user: login ---
    ctx2 = browser.new_context(viewport=VIEWPORT, permissions=["camera", "microphone"],
                               record_video_dir=str(OUT / "raw2"),
                               record_video_size=VIEWPORT)
    page2 = ctx2.new_page()
    page2.goto(BASE, wait_until="domcontentloaded")
    page2.wait_for_selector(".login-choice-buttons", timeout=20000)
    page2.wait_for_timeout(2400)
    page2.click("text=Login via Face Recognition")   # unavailable - explains itself
    page2.wait_for_timeout(2400)
    page2.click("text=Login via Password")
    page2.wait_for_selector(".setup-panel input", timeout=10000)
    page2.wait_for_timeout(1300)
    type_slowly(page2, ".setup-panel input", "wrongpassword")
    page2.press(".setup-panel input", "Enter")
    page2.wait_for_timeout(2600)
    type_slowly(page2, ".setup-panel input", "Str0ng!Passw0rd")
    page2.press(".setup-panel input", "Enter")
    page2.wait_for_selector(".main-body", timeout=45000)
    page2.wait_for_timeout(2600)

    # Shut down properly, so the recording shows the power-down chime
    # playing all the way through before the screen goes.
    page2.click("text=SHUT DOWN")
    page2.wait_for_timeout(6500)
    ctx2.close()
    browser.close()

videos = sorted(OUT.glob("raw*/*.webm"))
for i, v in enumerate(videos, 1):
    shutil.move(str(v), OUT / f"part{i}.webm")
    print(f"  part{i}.webm")
print("recorded")
