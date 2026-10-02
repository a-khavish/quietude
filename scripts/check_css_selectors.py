#!/usr/bin/env python3
# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Report CSS rules that no longer match any markup.

This exists because of a specific bug. The original build's stylesheet
had exactly one id selector, `#main-screen`, carrying the console's
layout - full-height column rather than the centred panel every other
screen uses. The Vue rewrite renders a class, not an id, so that rule
silently stopped applying and the entire main interface collapsed into a
floating box in the middle of the page. Nothing failed; a stylesheet
just quietly stopped having an opinion.

A dead rule after a markup rewrite is the signature of exactly that, so
it's worth a script rather than an eye.

    python3 scripts/check_css_selectors.py

Exits non-zero if any id selector matches nothing, since that is almost
always a real break. Dead *class* selectors are reported but don't fail:
they're often just styles for a screen not built yet, or state classes
applied dynamically in ways this can't see.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS_DIR = ROOT / "frontend" / "src" / "styles"
SRC_DIR = ROOT / "frontend" / "src"

# Classes toggled from script or bound in ways the crude scan below
# can't follow. Listed explicitly so the signal stays clean.
DYNAMIC = {
    "hidden", "collapsible", "expanded", "success", "warning", "error",
    "speaking", "copied", "checked", "listening", "awake", "unsupported",
    "conversation", "voice-locked", "voice-blurred", "voice-text-red",
    "voice-text-green", "face-unavailable", "shutting-down", "is-paused",
}


def css_selectors():
    ids, classes = set(), set()
    for css in CSS_DIR.glob("*.css"):
        text = re.sub(r"/\*.*?\*/", "", css.read_text(), flags=re.S)
        for block in re.findall(r"([^{}]+)\{", text):
            if block.strip().startswith("@"):
                continue
            for sel in block.split(","):
                ids.update(re.findall(r"#([A-Za-z][\w-]*)", sel))
                classes.update(re.findall(r"\.([A-Za-z][\w-]*)", sel))
    return ids, classes


def markup_tokens():
    ids, classes = set(), set()
    for path in SRC_DIR.rglob("*.vue"):
        text = path.read_text()
        for value in re.findall(r'\bid="([^"]+)"', text):
            ids.update(value.split())
        for value in re.findall(r'(?<!:)\bclass="([^"]*)"', text):
            classes.update(w for w in value.split() if not w.startswith("{"))
        # :class="{ foo: cond }" and :class="['a', b]"
        for value in re.findall(r':class="([^"]*)"', text, re.S):
            classes.update(re.findall(r"['\"]?([A-Za-z][\w-]*)['\"]?\s*:", value))
            classes.update(re.findall(r"'([A-Za-z][\w-]*)'", value))
        # HudPanel composes its classes from a prop.
        classes.update(re.findall(r'variant="([\w-]+)"', text))
    # Classes HudPanel always applies.
    classes.update({"panel", "hud-frame", "corner", "tl", "tr", "bl", "br"})
    return ids, classes


def main():
    css_ids, css_classes = css_selectors()
    markup_ids, markup_classes = markup_tokens()

    orphan_ids = sorted(css_ids - markup_ids)
    orphan_classes = sorted(css_classes - markup_classes - DYNAMIC)

    print(f"stylesheet: {len(css_ids)} ids, {len(css_classes)} classes")
    print(f"markup:     {len(markup_ids)} ids, {len(markup_classes)} classes\n")

    if orphan_ids:
        print("ID selectors matching no markup - almost certainly a break:")
        for name in orphan_ids:
            print(f"  #{name}")
    else:
        print("No orphaned id selectors.")

    if orphan_classes:
        print("\nClass selectors matching no markup (dead, or applied dynamically):")
        for name in orphan_classes:
            print(f"  .{name}")

    return 1 if orphan_ids else 0


if __name__ == "__main__":
    sys.exit(main())
