# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
window.py
A real window, not a browser pretending to be one.

Run as its own process, by the *system* Python rather than Quietude's
virtualenv - see pick_python() in the module that launches it. That
process boundary is the whole reason this is possible: PyGObject's C
extension is built against the distribution's Python, and importing it
into the venv would mean the venv had to be built on that exact
interpreter. Spawning it instead costs nothing and couples nothing.

Why this replaces the Chromium --app window
-------------------------------------------
The browser window worked, and three things about it could not be
fixed from outside, because a browser does not expose them:

    the dock icon      Chromium derives the first WM_CLASS field from
                       the URL's host and offers no way to set it. Two
                       rounds of work got both fields saying the right
                       thing by renaming the host - and still depended
                       on the desktop matching a window to a .desktop
                       file by one of those fields.
    a minimum size     there is no flag for it. A CSS floor stops the
                       *layout* collapsing, but the window still shrinks
                       to nothing.
    where it opens     --window-position takes absolute coordinates, so
                       "centred" means knowing the screen size, and on
                       Wayland a client cannot position itself at all.

A GTK window has all three as ordinary properties. set_size_request is
a real constraint handed to the window manager: the user cannot drag
the window smaller, on X11 or Wayland. set_position(CENTER) centres it.
And the WM_CLASS and the Wayland app_id are ours to set, which is what
puts the right icon in the dock.

The engine
----------
WebKitGTK, not Chromium. This was the part worth checking rather than
assuming, because the two features that define this app both live in
the browser's media stack: an AudioWorklet resampling microphone audio
for the wake word, and canvas frame capture for face login. Measured
here, over http, before any of this was written:

    secure context   yes          getUserMedia      opens a track
    AudioWorklet     registers    canvas capture    yes

If any of that had come back no, this file would not exist.

Still, the browser path is kept as a fallback and nothing here is
required: a machine without PyGObject or WebKit2GTK gets the Chromium
window it got before, and a machine without either gets a tab.
"""

import json
import os
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, GLib, Gtk, WebKit2  # noqa: E402


# Both WM_CLASS fields, and the Wayland app_id. These must match the
# desktop entry's basename and its StartupWMClass, which is what makes
# the desktop show this window under the right name and icon.
#
# Lowercase for the instance because Wayland compositors match app_id
# against the desktop file's name and some do it case-sensitively;
# capitalised for the class because that is the convention, and because
# some X11 docks show it.
WM_INSTANCE = "quietude"
WM_CLASS = "Quietude"

# The size the window opens at, which is also the size below which it
# cannot be dragged. The two are the same on purpose: there is no size
# smaller than the opening size that the interface was designed for, so
# offering one would only be offering a worse version of it. Growing and
# maximising are of course still fine.
PREFERRED_WIDTH = 1180
PREFERRED_HEIGHT = 760

# What the layout genuinely needs, measured rather than chosen: below
# 900px the two columns collapse into one and the command list is
# hidden. Used when the screen is too small for the preferred size -
# a 1366x768 laptop cannot give 760px of window height once panels are
# accounted for, and refusing to open would be a poor answer to that.
FLOOR_WIDTH = 900
FLOOR_HEIGHT = 620

# Leave the screen's own furniture room - panels, docks, title bars.
SCREEN_MARGIN_W = 80
SCREEN_MARGIN_H = 120


def _usable_screen(window):
    """The work area of the monitor the window is on, if the desktop
    will say. Falls back to the full screen, then to something sane."""
    try:
        display = Gdk.Display.get_default()
        monitor = (display.get_primary_monitor()
                   or display.get_monitor(0))
        rect = monitor.get_workarea()
        if rect.width > 0 and rect.height > 0:
            return rect.width, rect.height
    except Exception:
        pass
    return 1920, 1080


def _window_size(window):
    """The opening size, clamped to fit the screen but never below the
    size the layout needs."""
    screen_w, screen_h = _usable_screen(window)
    width = min(PREFERRED_WIDTH, max(FLOOR_WIDTH, screen_w - SCREEN_MARGIN_W))
    height = min(PREFERRED_HEIGHT, max(FLOOR_HEIGHT, screen_h - SCREEN_MARGIN_H))
    return width, height


def _apply_settings(view):
    """Everything the interface needs from the engine, and nothing else.

    Each is set individually and tolerantly: WebKitGTK has renamed and
    retired settings across releases, and one unknown property should
    cost that property, not the window."""
    settings = view.get_settings()
    wanted = {
        # The two that matter. Without media-stream there is no wake
        # word and no face login.
        "enable-media-stream": True,
        "enable-webaudio": True,
        "enable-mediasource": True,
        "enable-webgl": True,
        "enable-smooth-scrolling": True,
        "enable-developer-extras": bool(os.environ.get("QUIETUDE_SHELL_DEVTOOLS")),
        # It is a local application, not a browser: there is nowhere to
        # navigate to and nothing to look up.
        "enable-page-cache": False,
        "enable-html5-database": True,
        "enable-html5-local-storage": True,
        "javascript-can-open-windows-automatically": False,
    }
    for name, value in wanted.items():
        try:
            settings.set_property(name, value)
        except TypeError:
            pass
    view.set_settings(settings)


class Shell:
    def __init__(self, url, icon_name=None, icon_file=None, title="Quietude"):
        self.url = url
        self.closing = False

        # Set before any window exists: this is what becomes the X11
        # WM_CLASS instance field and the Wayland app_id, and it is read
        # when the first window is realised.
        GLib.set_prgname(WM_INSTANCE)
        GLib.set_application_name(title)
        Gdk.set_program_class(WM_CLASS)

        self.window = Gtk.Window(title=title)

        # The icon, by theme name first - that is the path that shares a
        # single installed icon with the dock and the menu. An explicit
        # file is set as well where one was passed, because a theme
        # lookup can fail for reasons outside this process (a stale
        # cache, an unusual prefix) and a window with no icon at all is
        # the one outcome worth ruling out.
        if icon_name:
            self.window.set_icon_name(icon_name)
            Gtk.Window.set_default_icon_name(icon_name)
        if icon_file and os.path.isfile(icon_file):
            try:
                Gtk.Window.set_default_icon_from_file(icon_file)
                self.window.set_icon_from_file(icon_file)
            except GLib.Error:
                pass

        width, height = _window_size(self.window)
        self.window.set_default_size(width, height)
        # The constraint the window manager enforces. This is the part a
        # browser window could not do at all.
        self.window.set_size_request(width, height)
        self.window.set_position(Gtk.WindowPosition.CENTER)

        self.view = WebKit2.WebView()
        _apply_settings(self.view)

        # Microphone and camera, granted for this origin without a
        # prompt. The prompt would be theatre: this is a local
        # application the user installed and launched, talking to a
        # server on their own loopback, and there is no other origin it
        # can load. Anything else is refused rather than ignored.
        self.view.connect("permission-request", self._on_permission)
        # Nothing in the interface opens a window, so anything trying to
        # is not the interface.
        self.view.connect("create", lambda *_: None)
        self.view.connect("load-failed", self._on_load_failed)
        self.view.connect("close", lambda *_: self.window.close())

        self.window.add(self.view)
        self.window.connect("delete-event", self._on_delete)
        self.window.show_all()
        self.view.load_uri(url)

    # -- permissions ---------------------------------------------------

    def _on_permission(self, _view, request):
        allowed = (WebKit2.UserMediaPermissionRequest,)
        notification = getattr(WebKit2, "NotificationPermissionRequest", None)
        if notification is not None:
            allowed = allowed + (notification,)
        if isinstance(request, allowed):
            request.allow()
        else:
            request.deny()
        return True

    # -- failure -------------------------------------------------------

    def _on_load_failed(self, _view, _event, failing_uri, error):
        # Printed rather than rendered: the launcher is watching this
        # process's output, and a WebKit error page inside a frameless
        # window is not something a user can act on.
        print(f"[shell] could not load {failing_uri}: {error.message}",
              file=sys.stderr, flush=True)
        return False

    # -- closing -------------------------------------------------------

    def _on_delete(self, *_args):
        """The user closed the window, which means they are quitting.

        Reported on stdout rather than inferred from the exit code,
        because the launcher needs to tell this apart from the process
        dying on startup - the difference between quitting and failing
        to open."""
        if not self.closing:
            self.closing = True
            print("CLOSED", flush=True)
        Gtk.main_quit()
        return False


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: window.py URL [options-json]", file=sys.stderr)
        return 2

    url = argv[0]
    options = json.loads(argv[1]) if len(argv) > 1 else {}

    shell = Shell(
        url,
        icon_name=options.get("icon_name"),
        icon_file=options.get("icon_file"),
        title=options.get("title", "Quietude"),
    )

    # The launcher closes this window by sending SIGTERM. Handled rather
    # than left to the default so the window comes down through GTK, in
    # the same order it would on any other close.
    def quit_cleanly(*_):
        shell.closing = True
        Gtk.main_quit()
        return GLib.SOURCE_REMOVE

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 15, quit_cleanly)  # SIGTERM
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 2, quit_cleanly)   # SIGINT

    # Announced once the window is up, so the launcher knows the
    # difference between "opened" and "exited before it ever appeared".
    GLib.idle_add(lambda: (print("READY", flush=True), GLib.SOURCE_REMOVE)[1])

    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
