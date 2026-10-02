# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Quietude's entry point.

    python -m quietude

Starts both worker processes, installs the signal handlers, serves the
app, and opens Quietude in a window of her own.

How she opens
-------------
By default she gets her own window - no tabs, no address bar, her own
icon in the dock - and the backend owns it, so "shutdown" closes the
window and closing the window shuts her down. The mechanics are in
desktop.py, including why this is a browser in app mode rather than a
native toolkit window.

  --window app     a window of her own                     (default)
  --window tab     a tab in whatever browser is already open
  --window none    nothing opens; for a service or a remote session

The Windows build hunted Program Files for chrome.exe, launched it with
--app, held the handle, and fell back to the default browser. The
hunting is gone - PATH already answers that question on Linux - but the
app-window idea was right and is kept.

What could not have been kept is any approach that finds, positions or
manipulates a window after the fact: the usual xdotool and wmctrl
toolbox does not work under a Wayland compositor, which deliberately
does not let clients manage each other's windows. Everything here is
asked for at launch - --app, --class, --window-size - so the browser
creates the window it was asked for and nobody has to move it
afterwards. That works identically on Wayland and X11.
"""


import argparse
import os
import sys
import threading
import time
import urllib.error
import urllib.request

from werkzeug.serving import make_server

from quietude import config, lifecycle
from quietude.desktop import AppWindow, NativeWindow
from quietude.core import speech_engine, tts_engine
from quietude.server import create_app

def _wait_until_serving(url, timeout=15.0):
    """Poll the health endpoint instead of sleeping a guessed interval -
    on a cold start the first request can take a moment, and on a warm
    one it's instant. The window must not open before there is something
    for it to show."""
    deadline = time.time() + timeout
    health = f"{url}/api/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(health, timeout=1) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.15)
    return False


def _open_window(url, mode):
    """Open Quietude however the user asked for her.

    Runs on its own thread, after the server answers. Three rungs, each
    a fallback for the one above, because a window is worth having but
    never worth failing to start over:

        her own window   a GTK window with a WebKit view. Sets its own
                         dock identity, opens centred at a fixed size,
                         and cannot be dragged smaller than the
                         interface needs. Needs PyGObject and
                         WebKit2GTK, which install.sh installs.
        a browser window a Chromium --app window. Looks almost the
                         same; none of those three things are possible
                         in it.
        a tab            whatever opens, wherever it opens.
    """
    if not _wait_until_serving(url):
        print(f"[quietude] the server didn't come up in time - open {url} yourself")
        return

    if mode == "app":
        import webbrowser as _wb

        def fall_back_to_browser():
            """Used when the native shell fails *after* reporting that
            it opened. Opening a browser window here rather than a tab
            keeps the user in roughly the app they expected."""
            browser = AppWindow(
                url, config.APP_PROFILE_DIR,
                on_closed=lambda: lifecycle.request_shutdown("the window was closed"),
                on_failed=lambda: _wb.open(url),
            )
            if browser.open():
                lifecycle.register_app_window(browser)
                print(f"[quietude] opened a browser window ({browser.browser})")
            else:
                _wb.open(url)

        window = NativeWindow(
            url,
            # Closing the window is quitting. A real signal, not an
            # inference - see desktop.py.
            on_closed=lambda: lifecycle.request_shutdown("the window was closed"),
            # It never opened, so this is not a quit. Fall back rather
            # than shutting down and leaving the user with nothing.
            on_failed=fall_back_to_browser,
            icon_file=config.icon_file(),
        )
        if window.open():
            lifecycle.register_app_window(window)
            print("[quietude] opened her own window")
            return

        print("[quietude] no GTK/WebKit shell available - using a browser window")
        print("[quietude] (install python3-gi and gir1.2-webkit2-4.1, or re-run "
              "./install.sh, for a window that keeps its size and its icon)")
        fall_back_to_browser()
        return

    import webbrowser
    webbrowser.open(url)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="quietude", description="Quietude - local AI assistant")
    default_host, default_port = config.host_port()
    parser.add_argument("--host", default=default_host)
    parser.add_argument("--port", type=int, default=default_port)
    parser.add_argument("--window", choices=config.WINDOW_MODES, default=None,
                        help="app: a window of her own (default); tab: a browser "
                             "tab; none: open nothing")
    parser.add_argument("--no-browser", action="store_true",
                        help="shorthand for --window none")
    args = parser.parse_args(argv)

    # --no-browser is kept as an alias because it's what the systemd unit
    # and every script written against the old flag already pass.
    window_mode = "none" if args.no_browser else (args.window or config.window_mode())

    config.ensure_dirs()

    # Both workers start before the server does, so model loading happens
    # behind the agreement screen, the setup wizard and login rather than
    # in front of someone waiting at a progress bar. The speech models
    # are the slow ones and the hard readiness gate depends on them, so
    # every second of head start here is a second the user doesn't wait.
    # The wake phrase is built from the assistant's name, so the engine
    # needs it at launch. Read here in the parent, because the worker has
    # no business opening the database.
    try:
        from quietude.core import database as _db
        _assistant = _db.get_assistant()
    except Exception:
        _assistant = {}
    speech_engine.start(wake_name=_assistant.get("name") or "")
    tts_engine.start()

    lifecycle.install_signal_handlers()

    app = create_app()
    server = make_server(args.host, args.port, app, threaded=True)
    lifecycle.register_server(server)

    url = f"http://{args.host}:{args.port}"
    print(f"[quietude] listening on {url}")
    print("[quietude] Ctrl+C to stop")

    if window_mode != "none":
        threading.Thread(target=_open_window, args=(url, window_mode),
                         daemon=True, name="quietude-window-opener").start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        # The signal handler has already started the teardown; this just
        # stops serve_forever from raising its way out of main().
        pass

    # serve_forever returns once lifecycle._teardown calls shutdown().
    # Wait for the rest of that teardown (the workers) to finish before
    # the interpreter exits, so a SIGKILL escalation isn't cut short.
    lifecycle.request_shutdown("serving loop ended")
    lifecycle.wait_for_shutdown(timeout=15)
    return 0


if __name__ == "__main__":
    sys.exit(main())
