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
import shutil
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
# "tab" is gone. It opened the interface in whatever browser happened to
# be running, which was a second way to run the application with its own
# behaviour - and having more than one of those is the thing that cost
# two releases to find. "none" stays because it opens nothing at all,
# which is what a service or a remote session wants.
WINDOW_MODES = ("app", "none")

# The app window's browser profile. Under cache rather than data: it
# holds nothing of yours - no history worth keeping, no account - and
# deleting it just means the next launch starts a fresh window.


def ensure_dirs():
    """Created lazily rather than at import, so merely importing this
    module (e.g. from a test) doesn't scatter directories around."""
    for d in (DATA_DIR, CONFIG_DIR, CACHE_DIR, FACES_DIR, UPLOADS_DIR, TTS_CLIP_DIR):
        d.mkdir(parents=True, exist_ok=True)


# How closing the window behaves:
#   "quit"  closing the window shuts her down          (default)
#   "tray"  closing the window hides it; she keeps running in the tray
DEFAULT_CLOSE_ACTION = "quit"
CLOSE_ACTIONS = ("quit", "tray")


def load_settings() -> dict:
    """Non-private, non-essential preferences. Missing or corrupt file
    just means defaults - this is never worth failing a boot over."""
    defaults = {"host": DEFAULT_HOST, "port": DEFAULT_PORT,
                "window": DEFAULT_WINDOW,
                "close_action": DEFAULT_CLOSE_ACTION,
                "start_at_login": False}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            defaults.update({k: v for k, v in loaded.items() if k in defaults})
    except (OSError, json.JSONDecodeError):
        pass
    return defaults


def save_settings(settings: dict):
    """Write the preferences back. Returns (ok, error).

    Written to a neighbouring file and renamed over the real one, so an
    interrupted write leaves the old settings rather than half of the
    new ones - load_settings would fall back to defaults on a truncated
    file, which would silently undo everything someone had configured.
    """
    keep = {"host", "port", "window", "close_action", "start_at_login"}
    data = {k: v for k, v in settings.items() if k in keep}
    temporary = SETTINGS_FILE.with_suffix(".json.new")
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, SETTINGS_FILE)
        return True, None
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False, f"Couldn't save settings: {exc}"


def window_mode() -> str:
    """Which way Quietude should present herself. QUIETUDE_WINDOW overrides the
    stored setting, and anything unrecognised falls back to the default
    rather than failing a launch over a typo in a config file - which
    also quietly migrates anyone whose stored setting is the "tab" that
    no longer exists.

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


# ============================================================
# Starting at login
# ============================================================
#
# The desktop's own mechanism, which is a file: anything in
# ~/.config/autostart that looks like a .desktop entry is launched when
# you log in. Every desktop that matters reads it - GNOME, KDE, XFCE,
# Cinnamon, LXQt - and it needs no daemon, no permissions and no systemd.
#
# Deliberately not the systemd user service that install.sh can set up.
# That one runs Quietude headless in the background as a service, which
# is a different thing someone might also want; this is "open the window
# when I log in".

AUTOSTART_DIR = pathlib.Path(
    os.environ.get("XDG_CONFIG_HOME") or (pathlib.Path.home() / ".config")) / "autostart"
AUTOSTART_FILE = AUTOSTART_DIR / "quietude.desktop"


def autostart_enabled() -> bool:
    return AUTOSTART_FILE.is_file()


def set_autostart(enabled: bool, launcher=None):
    """Turn starting at login on or off. Returns (ok, error)."""
    if not enabled:
        try:
            AUTOSTART_FILE.unlink(missing_ok=True)
            return True, None
        except OSError as exc:
            return False, f"Couldn't remove {AUTOSTART_FILE}: {exc}"

    # PATH first, then where install.sh puts it. A desktop session's
    # PATH is not a login shell's, and ~/.local/bin is routinely missing
    # from it - which would make this fail on a perfectly good install.
    command = launcher or shutil.which("quietude")
    if not command:
        for candidate in (pathlib.Path.home() / ".local" / "bin" / "quietude",
                          pathlib.Path("/usr/local/bin/quietude")):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                command = str(candidate)
                break
    if not command:
        return False, ("Couldn't find the quietude command. Re-run "
                       "./install.sh, or make sure ~/.local/bin is on your PATH.")

    icon = icon_file() or "quietude"
    try:
        AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
        AUTOSTART_FILE.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Quietude\n"
            "Comment=Nothing leaves this room.\n"
            f"Exec={command}\n"
            f"Icon={icon}\n"
            "Terminal=false\n"
            "X-GNOME-Autostart-enabled=true\n"
            # A few seconds in. At login the desktop is starting
            # everything at once, and the models load on startup - going
            # last costs nothing and leaves the session responsive.
            "X-GNOME-Autostart-Delay=5\n",
            encoding="utf-8")
        return True, None
    except OSError as exc:
        return False, f"Couldn't write {AUTOSTART_FILE}: {exc}"
