# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
config.py
Where Quietude keeps her files, and the handful of knobs that are worth
having in one place.

The Windows build kept everything in a `data/` folder sitting next to
the source tree, which meant the code directory and the user's private
data were the same directory - you couldn't update one without stepping
around the other, and an uninstall had to be careful not to delete a
profile. On Linux there is a standard answer to this, so Quietude uses it:

    ~/.local/share/quietude   profile, encryption key, face model, models/
    ~/.config/quietude        settings.json (host/port, nothing private)
    ~/.cache/quietude         synthesized speech clips - safe to delete anytime

All three honour the XDG_* environment variables if they're set. The
practical payoff is that `install.sh --uninstall` can remove every byte
of installed code without touching the profile, and `--purge-data` can
remove the profile deliberately, as two separate decisions.
"""

import json
import os
import pathlib
from pathlib import Path


def _xdg(var: str, default: str) -> Path:
    raw = os.environ.get(var, "").strip()
    base = Path(raw).expanduser() if raw else Path.home() / default
    return base / "quietude"


DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share")
CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config")
CACHE_DIR = _xdg("XDG_CACHE_HOME", ".cache")

# ---- data ----
USERS_FILE = DATA_DIR / "users.json"
KEY_FILE = DATA_DIR / "secret.key"
FACES_DIR = DATA_DIR / "faces"
FACE_MODEL_PATH = DATA_DIR / "face_model.yml"
UPLOADS_DIR = DATA_DIR / "uploads"

# ---- models (downloaded once by install.sh, never bundled) ----
MODELS_DIR = DATA_DIR / "models"
VOSK_MODEL_DIR = MODELS_DIR / "vosk"
WHISPER_MODEL_DIR = MODELS_DIR / "whisper"
PIPER_VOICE_DIR = MODELS_DIR / "piper"

# ---- cache ----
TTS_CLIP_DIR = CACHE_DIR / "tts"

# ---- bundled assets (part of the install, not user data) ----
PACKAGE_DIR = Path(__file__).resolve().parent
CASCADE_PATH = PACKAGE_DIR / "assets" / "haarcascades" / "haarcascade_frontalface_default.xml"

SETTINGS_FILE = CONFIG_DIR / "settings.json"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5000

# How Quietude presents herself:
#   "app"  a window of her own, which she owns and can close   (default)
#   "tab"  a tab in whatever browser you already have open
#   "none" nothing opens; for a systemd service or a remote session
DEFAULT_WINDOW = "app"
WINDOW_MODES = ("app", "tab", "none")

# The app window's browser profile. Under cache rather than data: it
# holds nothing of yours - no history worth keeping, no account - and
# deleting it just means the next launch starts a fresh window.
APP_PROFILE_DIR = CACHE_DIR / "window-profile"


def ensure_dirs():
    """Created lazily rather than at import, so merely importing this
    module (e.g. from a test) doesn't scatter directories around."""
    for d in (DATA_DIR, CONFIG_DIR, CACHE_DIR, FACES_DIR, UPLOADS_DIR, TTS_CLIP_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_settings() -> dict:
    """Non-private, non-essential preferences. Missing or corrupt file
    just means defaults - this is never worth failing a boot over."""
    defaults = {"host": DEFAULT_HOST, "port": DEFAULT_PORT,
                "window": DEFAULT_WINDOW}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            defaults.update({k: v for k, v in loaded.items() if k in defaults})
    except (OSError, json.JSONDecodeError):
        pass
    return defaults


def window_mode() -> str:
    """Which way Quietude should present herself. QUIETUDE_WINDOW overrides the
    stored setting, and anything unrecognised falls back to the default
    rather than failing a launch over a typo in a config file.

    QUIETUDE_NO_BROWSER is honoured as an alias for QUIETUDE_WINDOW=none, the
    same way --no-browser aliases --window none on the command line. It
    was documented in the README and in two of the scripts before it was
    read anywhere, so every headless run was in fact still trying to
    open a window and quietly falling back - which is exactly the kind of
    thing that only shows up when something downstream of it breaks."""
    if (os.environ.get("QUIETUDE_NO_BROWSER") or "").strip().lower() in ("1", "true", "yes"):
        return "none"
    mode = (os.environ.get("QUIETUDE_WINDOW") or load_settings()["window"] or "").strip().lower()
    return mode if mode in WINDOW_MODES else DEFAULT_WINDOW


def icon_file():
    """The installed app icon, or None.

    The window sets its icon by theme name first, which is the right way
    and shares one installed file with the dock and the menu. This is
    the belt to that's braces: a theme lookup can fail for reasons
    outside the app - a stale cache, an unusual prefix, a desktop that
    only reads the cache at login - and a window with no icon at all is
    the outcome worth ruling out. Checked in the order a desktop would
    search, PNG before SVG because a window icon is rasterised anyway.
    """
    import os
    roots = [DATA_DIR.parent]                      # ~/.local/share
    roots += [pathlib.Path(p) for p in
              (os.environ.get("XDG_DATA_DIRS")
               or "/usr/local/share:/usr/share").split(":") if p]
    for root in roots:
        for size in (256, 128, 512, 64, 48):
            candidate = (root / "icons/hicolor"
                         / f"{size}x{size}/apps/quietude.png")
            if candidate.is_file():
                return str(candidate)
        svg = root / "icons/hicolor/scalable/apps/quietude.svg"
        if svg.is_file():
            return str(svg)
    return None


def host_port() -> tuple:
    settings = load_settings()
    host = os.environ.get("QUIETUDE_HOST") or settings["host"]
    port = os.environ.get("QUIETUDE_PORT") or settings["port"]
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    return host, port
