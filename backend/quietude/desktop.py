# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
desktop.py
Quietude's own window.

Opening in a browser tab works, but it doesn't feel like an application:
she sits among forty other tabs, shares a window with them, carries an
address bar she has no use for, and has no way to close herself when you
tell her to shut down.

This gives her a window of her own. The backend launches a browser in
app mode, owns that process, and the relationship runs both ways:

    "shutdown" / SHUT DOWN / Ctrl+C  ->  the window closes
    closing the window               ->  Quietude shuts down

Why a browser and not a native toolkit
--------------------------------------
A real GTK window via PyGObject and WebKitGTK was the obvious
alternative, and it was tested rather than dismissed: WebKitGTK does
support getUserMedia, AudioWorklet and canvas, so Quietude's face login and
wake word could run in it. It was still the wrong choice, for two
reasons.

The first is risk. Quietude's two defining features both depend on the
browser's media stack - an AudioWorklet resampling microphone audio for
the wake word, and canvas frame capture for face recognition. Both were
built and tested against Chromium. Swapping the engine to get a nicer
window frame would put the two things that make Quietude *Quietude* on an
engine they have never run on, in exchange for cosmetics.

The second is the dependency. PyGObject's C extension is built against
the distribution's own Python, so depending on it forces the virtualenv
to either be built on that exact interpreter or to inherit system
site-packages. That coupling - "your Python must be the distro's Python"
- is precisely the class of fragility that already broke an install here
once. A window is not worth it.

So: a browser, but owned, isolated and dressed as an application.

    --app=URL          no tabs, no address bar, no bookmarks bar
    --user-data-dir    its own profile. This is not a nicety: it is what
                       makes the launched process *ours*. Without it, a
                       browser you already have open adopts the new
                       window, our subprocess exits immediately, and the
                       exit watcher below would read that as "the user
                       closed the window" and shut Quietude down a second
                       after she started. It also keeps Quietude out of your
                       browsing session, extensions and cookies.
    --class            gives the window its own WM class, so the desktop
                       groups it under Quietude's icon rather than the
                       browser's. Paired with StartupWMClass in the
                       .desktop file that install.sh writes. On its own
                       this turned out to be half a fix - see
                       WINDOW_HOST below for the other half, and for why
                       the dock kept showing a generic icon anyway.
"""

import json
import os
import shutil
import signal
import subprocess
import threading
import time
import urllib.parse

# Chromium-family browsers, in preference order. All of these support
# --app, --class and --user-data-dir with the same spelling.
#
# Firefox is deliberately absent: it dropped its site-specific-browser
# mode, and --kiosk is fullscreen rather than a window, which is not
# what "its own window" means. Where only Firefox exists, Quietude opens a
# normal window instead and says so.
APP_MODE_BROWSERS = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "brave-browser",
    "vivaldi-stable",
    "microsoft-edge",
    "microsoft-edge-stable",
)

# Lowercase, and it must stay in lockstep with three other things: the
# desktop file's basename (quietude.desktop), its StartupWMClass line, and
# its Icon name. That's what makes the desktop show Quietude's icon and name
# rather than the browser's.
#
# Measured rather than assumed. With no --class at all, Chromium sets
# WM_CLASS to ("127.0.0.1", "Chromium-browser") - the URL host and the
# browser - which is why an unflagged window shows up in the dock as a
# stray Chromium with a generic icon. With --class it sets the second
# field, which is what X11 desktops match StartupWMClass against and
# what Chromium uses for the Wayland app_id.
#
# Lowercase because Wayland compositors match app_id against the desktop
# file's basename, and some do it case-sensitively: app_id "quietude" and
# quietude.desktop match everywhere, "Quietude" and quietude.desktop only sometimes.
# The capitalised name people see comes from Name= in the desktop file,
# not from this.
WM_CLASS = "quietude"

# The host the window's URL is opened on - which is also, unavoidably,
# the first field of its WM_CLASS.
#
# This is the part the first attempt at the dock icon got wrong. WM_CLASS
# has two fields, an instance and a class, and --class sets only the
# second. The first Chromium derives from the host of --app's URL and
# from nothing else. Measured, with xprop against a real window:
#
#     --app=http://127.0.0.1:PORT/                 ("127.0.0.1", "Chromium-browser")
#     --app=http://127.0.0.1:PORT/ --class=quietude    ("127.0.0.1", "quietude")
#     --app=http://quietude.localhost:PORT/ --class=quietude  ("quietude.localhost", "quietude")
#
# Desktops differ in which field they match a window to an application
# by. GNOME tries the instance first and then the class, so the middle
# row works there. KDE's task manager, Plank and xfce4-panel match on the
# instance - they looked for an application called "127.0.0.1", found
# none, and showed a generic gear. Which is exactly the "it's still a
# settings icon" report that led here.
#
# So the window is opened on a name rather than an address, and both
# fields then read as Quietude. install.sh registers both.
#
# Names ending in .localhost are loopback by specification (RFC 6761) and
# Chromium resolves them internally, without the system resolver and
# without /etc/hosts. Verified here rather than assumed: the page loads,
# and the origin stays a secure context, which the wake word and face
# login both need.
#
# An explicit --host-resolver-rules mapping was tried first, as a belt
# and braces against a build that might not honour the implicit rule.
# It was removed: Chromium treats that switch as a testing flag and
# shows a permanent "You are using an unsupported command-line flag"
# bar across the top of the window because of it. Trading a visible,
# certain, permanent banner in the app window against a hypothetical
# resolution failure is the wrong way round. QUIETUDE_WINDOW_HOST=off below
# is the escape hatch if a setup ever does need it.
WINDOW_HOST = "quietude.localhost"

# Only these get rewritten. Anything else means someone bound Quietude to a
# particular address on purpose, and swapping a host we don't own for a
# name, to chase an icon, would be the wrong trade.
REWRITABLE_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0"}



# ============================================================
# The native window
# ============================================================

# Interpreters to try for the shell, in order. It must be a Python with
# PyGObject and WebKit2GTK, which in practice means the distribution's
# own - the virtualenv deliberately does not have them, because
# PyGObject's C extension is built against one specific interpreter and
# depending on it would pin the venv to that interpreter. Spawning a
# process instead couples nothing.
SHELL_PYTHONS = (
    "python3",
    "/usr/bin/python3",
    "/usr/bin/python3.13", "/usr/bin/python3.12", "/usr/bin/python3.11",
    "/usr/bin/python3.10",
)

_SHELL_PROBE = (
    "import gi;"
    "gi.require_version('Gtk','3.0');"
    "gi.require_version('WebKit2','4.1');"
    "from gi.repository import Gtk, WebKit2"
)

_shell_python_cache = ...


def find_shell_python():
    """An interpreter that can actually run the shell, or None.

    Probed by running the imports, not by looking for a file: a
    python3-gi that is installed but broken, or a WebKit2 typelib that
    is missing, both look exactly like a working one from the outside.
    Cached, because this runs a subprocess per candidate."""
    global _shell_python_cache
    if _shell_python_cache is not ...:
        return _shell_python_cache

    override = os.environ.get("QUIETUDE_SHELL_PYTHON")
    candidates = (override,) + SHELL_PYTHONS if override else SHELL_PYTHONS

    for name in candidates:
        path = shutil.which(name) if not os.path.isabs(name) else name
        if not path or not os.path.exists(path):
            continue
        try:
            result = subprocess.run([path, "-c", _SHELL_PROBE],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            _shell_python_cache = path
            return path

    _shell_python_cache = None
    return None


class NativeWindow:
    """Quietude's own window: a GTK window with a WebKit view in it.

    Everything the browser window could not do - set its own dock
    identity, open centred, refuse to be dragged below a usable size -
    is an ordinary property here. See shell/window.py for the detail and
    for why WebKitGTK was measured before being trusted with the
    microphone.

    The relationship with the backend is the same as the browser
    window's, and for the same reason: closing the window is quitting,
    and quitting closes the window.
    """

    # A shell that dies this fast never opened - the same distinction
    # the browser window has to make, for the same reason.
    MIN_LIFETIME_SECONDS = 3.0

    def __init__(self, url, on_closed=None, on_failed=None,
                 icon_name="quietude", icon_file=None, title="Quietude"):
        self.url = url
        self.on_closed = on_closed
        self.on_failed = on_failed
        self.icon_name = icon_name
        self.icon_file = icon_file
        self.title = title
        self.process = None
        self.started_at = None
        self.ready = threading.Event()
        self._closing = threading.Event()
        self._user_closed = False

    @staticmethod
    def is_available():
        return find_shell_python() is not None

    def open(self):
        python = find_shell_python()
        if not python:
            return False

        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "shell", "window.py")
        if not os.path.isfile(script):
            return False

        options = json.dumps({
            "icon_name": self.icon_name,
            "icon_file": self.icon_file,
            "title": self.title,
        })

        env = dict(os.environ)
        # The shell is the distribution's Python; the venv's paths would
        # only confuse it, and PYTHONHOME in particular would break it.
        for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
            env.pop(name, None)

        try:
            self.process = subprocess.Popen(
                [python, script, self.url, options],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError:
            return False

        self.started_at = time.monotonic()
        threading.Thread(target=self._watch, daemon=True,
                         name="quietude-shell-watcher").start()

        # Wait briefly for the window to actually appear. Not for its own
        # sake - it's for the caller, which needs to know whether to fall
        # back to a browser, and cannot know that from Popen succeeding.
        if self.ready.wait(timeout=12):
            return True
        if self.process.poll() is not None:
            return False
        # Still starting. A slow first WebKit launch on a cold cache is
        # ordinary; treat it as open rather than opening a second window
        # beside it.
        return True

    def _watch(self):
        """Reads the shell's output until it exits.

        The shell says READY when the window is up and CLOSED when the
        user closed it, so neither has to be inferred. That matters most
        for the failure case: a shell that exits in the first second
        never opened, and shutting Quietude down because of it would
        make a failure to launch look like a quit."""
        stdout = self.process.stdout
        try:
            for line in iter(stdout.readline, ""):
                line = line.strip()
                if line == "READY":
                    self.ready.set()
                elif line == "CLOSED":
                    self._user_closed = True
        except (OSError, ValueError):
            pass

        self.process.wait()
        if self._closing.is_set():
            return

        alive_for = time.monotonic() - (self.started_at or 0)
        if not self.ready.is_set() or alive_for < self.MIN_LIFETIME_SECONDS:
            detail = ""
            try:
                detail = (self.process.stderr.read() or "").strip().splitlines()
                detail = detail[-1] if detail else ""
            except (OSError, ValueError):
                pass
            print(f"[quietude] the window closed after {alive_for:.1f}s without "
                  f"opening - falling back to a browser window"
                  + (f" ({detail})" if detail else ""))
            if self.on_failed:
                self.on_failed()
            return

        if self.on_closed:
            self.on_closed()

    def close(self, timeout=4.0):
        """Same ask-wait-escalate shape as every other process Quietude
        owns."""
        self._closing.set()
        if self.process is None or self.process.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                self.process.terminate()
            except OSError:
                return

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                return
            time.sleep(0.05)

        print("[quietude] the window didn't close in time - killing it")
        try:
            os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                self.process.kill()
            except OSError:
                pass

    @property
    def is_open(self):
        return self.process is not None and self.process.poll() is None


class AppWindow:
    """A browser process Quietude owns, opened as an application window."""

    # A window that dies within this many seconds of launching never
    # really opened - see _start_exit_watcher for why that distinction
    # matters enough to time.
    MIN_LIFETIME_SECONDS = 4.0

    def __init__(self, url, profile_dir, on_closed=None, on_failed=None):
        self.url = url
        self.profile_dir = profile_dir
        self.on_closed = on_closed
        self.on_failed = on_failed
        self.process = None
        self.browser = None
        self.window_url = url
        self.started_at = None
        self._closing = threading.Event()
        self._watcher = None

    # ---------------------------------------------------------------
    # starting
    # ---------------------------------------------------------------

    @staticmethod
    def find_browser():
        for name in APP_MODE_BROWSERS:
            path = shutil.which(name)
            if path:
                return path
        return None

    def window_target(self):
        """The URL to open the window on.

        A loopback address is rewritten onto WINDOW_HOST, for the
        WM_CLASS reasons above. Anything else - someone who bound Quietude
        to a particular address on purpose - is left exactly as it is.

        QUIETUDE_WINDOW_HOST is the escape hatch: a hostname to use instead,
        or "off" to open the address directly and accept the icon.
        """
        override = os.environ.get("QUIETUDE_WINDOW_HOST", "").strip()
        if override == "off":
            return self.url

        parsed = urllib.parse.urlsplit(self.url)
        if (parsed.hostname or "").lower() not in REWRITABLE_HOSTS:
            return self.url

        name = override or WINDOW_HOST
        netloc = name if parsed.port is None else f"{name}:{parsed.port}"
        return urllib.parse.urlunsplit(
            (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
        )

    def open(self):
        """Launch the window. Returns True if Quietude now owns one."""
        browser = self.find_browser()
        if not browser:
            return False

        self.profile_dir.mkdir(parents=True, exist_ok=True)

        window_url = self.window_target()
        self.window_url = window_url

        cmd = [
            browser,
            f"--app={window_url}",
            f"--user-data-dir={self.profile_dir}",
            f"--class={WM_CLASS}",
            "--window-size=1440,900",
            # Use the session's native display server: Wayland where the
            # session is Wayland, X11 otherwise. Without the hint,
            # several distributions' Chromium builds still default to
            # XWayland on a Wayland session, which costs fractional
            # scaling and crisp text on HiDPI screens. "auto" rather than
            # forcing it, because forcing Wayland on a build that doesn't
            # support it properly means no window at all.
            "--ozone-platform-hint=auto",
            # Quietude is one local app on loopback, and these are all about
            # not behaving like a general-purpose browser.
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate,MediaRouter",
        ]

        # An escape hatch for setups that need a flag we can't predict -
        # forcing the Ozone platform on an unusual compositor, say
        # (QUIETUDE_WINDOW_FLAGS="--ozone-platform=wayland"), or a sandbox
        # flag in a container. Split on whitespace, which is enough for
        # browser flags and avoids pulling in a shell.
        extra = os.environ.get("QUIETUDE_WINDOW_FLAGS", "").split()
        cmd.extend(extra)

        # Some Chromium builds ship without Google API keys compiled in
        # and show a yellow "Google API keys are missing" bar above the
        # page - confusing in an app window, and irrelevant to Quietude,
        # which uses no Google service. Setting them to anything
        # suppresses the bar.
        env = dict(os.environ)
        env.setdefault("GOOGLE_API_KEY", "no")
        env.setdefault("GOOGLE_DEFAULT_CLIENT_ID", "no")
        env.setdefault("GOOGLE_DEFAULT_CLIENT_SECRET", "no")

        try:
            self.process = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # Its own process group, so a Ctrl+C in the terminal goes
                # to Quietude and is handled in order, rather than being
                # delivered to the browser at the same instant and
                # killing the window out from under the shutdown chime.
                start_new_session=True,
            )
        except OSError:
            return False

        self.browser = os.path.basename(browser)
        self.started_at = time.monotonic()
        self._start_exit_watcher()
        return True

    # ---------------------------------------------------------------
    # closing the window when the user closes it
    # ---------------------------------------------------------------

    def _start_exit_watcher(self):
        """Watch for the user closing the window, and tell Quietude.

        This is a real signal, unlike the heartbeat watchdog the Windows
        build used and this rebuild removed. That one inferred "the
        window is gone" from pings stopping, so a refresh, a sleeping
        laptop or a second tab all looked like a quit. Here the window
        *is* a process we started, with its own profile so nothing else
        can adopt it: the process exiting means the window closed, and
        nothing else makes it exit. A refresh doesn't, and there are no
        tabs to be ambiguous about."""

        def watch():
            if self.process is None:
                return
            self.process.wait()
            if self._closing.is_set():
                return  # we closed it ourselves, during shutdown

            # A window that dies almost immediately never opened: the
            # browser refused to start. A missing library, a sandbox it
            # won't run without, a policy, a flag it doesn't recognise -
            # all of them exit in well under a second.
            #
            # Without this check that exit is indistinguishable from a
            # deliberate close, so Quietude would shut herself down a second
            # after launching and the user would see the app flash and
            # vanish with nothing explaining why. Worth timing: the
            # difference between "you closed it" and "it never opened"
            # is the difference between quitting and a failure to
            # launch, and only one of them should stop Quietude.
            alive_for = time.monotonic() - (self.started_at or 0)
            if alive_for < self.MIN_LIFETIME_SECONDS:
                code = self.process.returncode
                print(f"[quietude] the window closed after {alive_for:.1f}s "
                      f"(exit {code}) - treating that as a failure to open, "
                      f"not as you quitting")
                if self.on_failed:
                    self.on_failed()
                return

            if self.on_closed:
                self.on_closed()

        self._watcher = threading.Thread(target=watch, daemon=True,
                                         name="quietude-window-watcher")
        self._watcher.start()

    # ---------------------------------------------------------------
    # closing the window because Quietude is shutting down
    # ---------------------------------------------------------------

    def close(self, timeout=4.0):
        """Close the window. Same bounded-wait-then-escalate shape as
        every other process Quietude owns - ask, wait, then stop waiting."""
        self._closing.set()
        if self.process is None or self.process.poll() is not None:
            return

        try:
            # SIGTERM to the whole process group: a Chromium window is a
            # browser process plus its renderer and GPU children, and
            # signalling only the parent can leave those behind.
            os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                self.process.terminate()
            except OSError:
                return

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                return
            time.sleep(0.05)

        print("[quietude] the app window didn't close in time - killing it")
        try:
            os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                self.process.kill()
            except OSError:
                pass

    @property
    def is_open(self):
        return self.process is not None and self.process.poll() is None
