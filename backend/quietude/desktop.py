# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
desktop.py
Quietude's own window.

The interface is a web page served over loopback, and this launches the
window it is drawn in. The relationship runs both ways:

    "shutdown" / SHUT DOWN / Ctrl+C  ->  the window closes
    closing the window               ->  Quietude shuts down

The window is Electron - Chromium, with a small main process in
electron/main.js. That file says what the window does; this one owns the
process that draws it.

What used to be here, and why it is not
---------------------------------------
Three shells have lived in this file. The history is worth a paragraph,
because each one was replaced for a reason that would otherwise be
rediscovered.

A Chromium window launched with --app came first. It worked, but the
window was the browser's: it could not be given a minimum size, and its
WM_CLASS instance came from the address it opened, which took three
attempts and a hostname trick to get a dock icon out of.

A GTK window with a WebKit view came second, and fixed all of that - a
real minimum size, a real WM_CLASS, a real icon. It also brought its own
engine, and the engine turned out to be the problem. WebKitGTK requires
a user gesture before any media may play, and that rule applies to a
<video> fed from getUserMedia: the camera preview was refused, the
rejection was swallowed, and the engine painted its own play button over
a still frame. It looked exactly like a broken camera, and it cost two
releases - the same rule had already silenced the wake tones.

Electron is third, and is the only one now. It is Chromium, which is the
engine the interface is built and tested against, so the thing people
run is the thing people test. It gives the window everything the GTK
shell did and nothing WebKit did.

The cost is honest: Electron is a 230 MB download. Node was already
required to build the interface, so it adds no system package, but it is
not free and the README says so.
"""

import json
import os
import shlex
import stat
import signal
import subprocess
import threading
import time

# Both WM_CLASS fields the window reports. These MUST match
# StartupWMClass in the .desktop file install.sh writes, or the desktop
# cannot connect the window to the application and draws neither its
# name nor its icon.
#
# The instance field comes from the name of the binary that launched the
# window, which is "electron" as installed - see _named_launcher. The
# class field comes from productName in electron/package.json.
WM_INSTANCE = "quietude"
WM_CLASS = "Quietude"


class _ShellProcess:
    """A child process that draws a window.

    It says READY when the window is up and CLOSED when the user closed
    it, and is asked to quit with a signal. Waiting for it, telling a
    failure to launch apart from a quit, and escalating a close that is
    ignored are all handled here.

    A base class with one subclass is usually a smell. It stays because
    this is the second time the engine underneath has changed, and the
    part that did not need to change either time is exactly this.
    """

    # A shell that dies this fast never opened. The distinction matters:
    # shutting Quietude down because the window failed to launch would
    # make a broken install look like the user quitting.
    MIN_LIFETIME_SECONDS = 3.0

    def __init__(self, url, on_closed=None, on_failed=None,
                 icon_name="quietude", icon_file=None, title="Quietude",
                 token=None):
        self.url = url
        # Passed to the window, which sends it as a header on every
        # request. The server refuses anything without it, which is what
        # keeps the interface out of a browser - see server.py.
        self.token = token
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
            print(f"[quietude] the window closed after {alive_for:.1f}s "
                  f"without opening"
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


ELECTRON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "electron")

_electron_cache = ...


def find_electron():
    """The Electron binary this install has, or None.

    Installed into electron/node_modules by install.sh rather than
    expected on PATH: a system-wide `electron` is somebody else's
    version, and the shell is written against this one.
    """
    global _electron_cache
    if _electron_cache is not ...:
        return _electron_cache

    _electron_cache = None
    override = os.environ.get("QUIETUDE_ELECTRON")
    candidates = []
    if override:
        candidates.append(override)
    candidates.append(os.path.join(
        ELECTRON_DIR, "node_modules", "electron", "dist", "electron"))

    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            _electron_cache = path
            break
    return _electron_cache


# ------------------------------------------------------------
# Chromium's sandbox
# ------------------------------------------------------------
#
# Chromium sandboxes its renderers two ways on Linux, and will refuse to
# start rather than run without one.
#
#   the SUID helper      a small root-owned setuid binary, chrome-sandbox,
#                        shipped beside the engine
#   user namespaces      the kernel's own, needing no privileges at all
#
# npm cannot set a setuid bit - it unpacks as whoever ran it - so the
# helper arrives owned by the user and without its bit. Chromium finds
# it, sees it is not configured, and aborts with a message about mode
# 4755. That is the whole of this failure, and it is the first thing
# anybody hits installing Electron from npm:
#
#   The SUID sandbox helper binary was found, but is not configured
#   correctly. Rather than run without sandboxing I'm aborting now.
#
# install.sh fixes the helper with sudo, which is the proper repair.
# This is what happens when that did not work - no sudo, a read-only
# install, a shared machine. Chromium is told to ignore the helper and
# use namespaces instead, which is a real sandbox, not a weaker one.
#
# --no-sandbox is the last resort and is used only where neither is
# possible: running as root, or a kernel with namespaces turned off.
# It says so when it does, because a browser engine with no sandbox is
# worth a line in the log.


def _sandbox_helper_ok(binary):
    """Whether chrome-sandbox is root-owned and setuid, as Chromium wants."""
    helper = os.path.join(os.path.dirname(binary), "chrome-sandbox")
    try:
        info = os.stat(helper)
    except OSError:
        return False
    return info.st_uid == 0 and bool(info.st_mode & stat.S_ISUID)


def _user_namespaces_available():
    """Whether the kernel will give an unprivileged process a namespace.

    Three different knobs, because three distributions restrict this
    three different ways: the count is the upstream one, Debian added
    unprivileged_userns_clone, and Ubuntu 24.04 restricts it through
    AppArmor instead. Any of them saying no means no.
    """
    checks = (
        ("/proc/sys/user/max_user_namespaces", lambda v: int(v) > 0),
        ("/proc/sys/kernel/unprivileged_userns_clone", lambda v: v != "0"),
        ("/proc/sys/kernel/apparmor_restrict_unprivileged_userns", lambda v: v != "1"),
    )
    for path, ok in checks:
        try:
            with open(path) as handle:
                value = handle.read().strip()
        except OSError:
            continue        # the knob does not exist here, which is not a no
        try:
            if not ok(value):
                return False
        except ValueError:
            continue
    return True


def _sandbox_flags(binary):
    if os.environ.get("QUIETUDE_ELECTRON_NO_SANDBOX"):
        return ["--no-sandbox"]

    if os.geteuid() == 0:
        # Chromium refuses the SUID helper as root regardless, and there
        # is nothing for a namespace to protect from a process that is
        # already root.
        return ["--no-sandbox"]

    if _sandbox_helper_ok(binary):
        return []

    if _user_namespaces_available():
        return ["--disable-setuid-sandbox"]

    print("[quietude] this kernel has user namespaces turned off and "
          "chrome-sandbox isn't set up, so the window is starting without "
          "a sandbox.")
    print("[quietude] to fix it properly:")
    print(f"[quietude]   sudo chown root:root {os.path.dirname(binary)}/chrome-sandbox")
    print(f"[quietude]   sudo chmod 4755 {os.path.dirname(binary)}/chrome-sandbox")
    return ["--no-sandbox"]


class ElectronWindow(_ShellProcess):
    """Quietude's window.

    Everything about how the window looks and behaves is in
    electron/main.js - its size, its floor, what it will and will not
    navigate to, what it grants the camera. This owns the process it
    runs in and nothing else.
    """

    @staticmethod
    def is_available():
        return (find_electron() is not None
                and os.path.isfile(os.path.join(ELECTRON_DIR, "main.js")))

    def open(self):
        binary = find_electron()
        if not binary or not os.path.isfile(os.path.join(ELECTRON_DIR, "main.js")):
            return False

        # WM_CLASS's instance field is the launching binary's own name,
        # which is "electron" as installed - and a dock that identifies
        # windows by it would file Quietude under every other Electron
        # application at once. A link named for the app fixes it, and is
        # made here rather than at install time so that it is right even
        # if node_modules was replaced underneath us.
        launcher = self._named_launcher(binary)

        argv = [launcher, ELECTRON_DIR]
        argv.extend(_sandbox_flags(binary))

        # A debugging port, for the tests that drive the real window
        # rather than a browser pretending to be it. Off unless asked
        # for by name: it is a way into the running interface, and it
        # has no business being open because somebody is debugging
        # something else.
        debug_port = os.environ.get("QUIETUDE_ELECTRON_DEBUG_PORT")
        if debug_port and debug_port.isdigit():
            argv.append(f"--remote-debugging-port={debug_port}")

        # Extra switches, for the machines that need one.
        #
        # Chromium has a long tail of hardware and compositor problems
        # whose answer is a command-line flag - --disable-gpu on a
        # driver it cannot use, --ozone-platform=wayland to stop it
        # going through XWayland. Rather than guess at those, or collect
        # them one bug report at a time, they can be passed in. The
        # tests use it to hand the window a fake camera.
        extra = os.environ.get("QUIETUDE_ELECTRON_FLAGS", "").strip()
        if extra:
            try:
                argv.extend(shlex.split(extra))
            except ValueError:
                print("[quietude] QUIETUDE_ELECTRON_FLAGS is not quoted properly "
                      "- ignoring it")

        env = dict(os.environ)
        env["QUIETUDE_URL"] = self.url
        if self.token:
            env["QUIETUDE_TOKEN"] = self.token
        env["QUIETUDE_OPTIONS"] = json.dumps({
            "icon_name": self.icon_name,
            "icon_file": self.icon_file,
            "title": self.title,
        })
        # The shell is Node, not Python; the venv's variables would only
        # confuse anything it shells out to.
        for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
            env.pop(name, None)

        try:
            self.process = subprocess.Popen(
                argv, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, start_new_session=True,
            )
        except OSError:
            return False

        self.started_at = time.monotonic()
        threading.Thread(target=self._watch, daemon=True,
                         name="quietude-electron-watcher").start()

        if self.ready.wait(timeout=20):
            return True
        if self.process.poll() is not None:
            return False
        return True

    @staticmethod
    def _named_launcher(binary):
        """A path to the binary whose basename is the application's name.

        Returns the binary itself if the link cannot be made, which
        costs the dock icon rather than the window.
        """
        link = os.path.join(os.path.dirname(binary), "quietude")
        try:
            if os.path.islink(link) and os.readlink(link) == binary:
                return link
            if os.path.lexists(link):
                os.unlink(link)
            os.symlink(binary, link)
            return link
        except OSError:
            return binary
