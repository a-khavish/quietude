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
She gets her own window - no tabs, no address bar, her own icon in the
dock - and the backend owns it, so "shutdown" closes the window and
closing the window shuts her down. desktop.py owns that process;
electron/main.js is the window itself.

  --window app     her own window                          (default)
  --window none    nothing opens; for a service or a remote session

There is no third option, and that is deliberate. There were three at
one point, each a fallback for the one above, and the arrangement was
worse than it sounds: a problem in the first was not a failure but a
silent demotion to the second, which drew the same interface with
different bugs. Nobody could tell you which one they were looking at,
including the people reporting the bugs.

Everything about the window is asked for at launch - its size, its
floor, its class, its icon. Nothing finds or moves a window after the
fact, because the usual xdotool and wmctrl toolbox does not work under a
Wayland compositor, which deliberately does not let clients manage each
other's windows. Asking up front works identically on Wayland and X11.
"""


import argparse
import os
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

from werkzeug.serving import make_server

from quietude import config, lifecycle
from quietude.desktop import ElectronWindow
from quietude.core import speech_engine, tts_engine
from quietude import server as server_module
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


def _open_window(url, token):
    """Open the window.

    Runs on its own thread, once the server answers.

    There is one window and it is Electron. There used to be three, each
    a fallback for the one above, and the arrangement was worse than it
    sounds: a problem in the first one was not a failure but a silent
    demotion to the second, which drew the same interface with different
    bugs. Two of the four faults in the last release were engine
    differences that nobody could see because nobody knew which engine
    they had. One window means a bug report describes one thing.

    If it cannot open, that is an error and is reported as one. The
    server keeps running, because the interface is a web page and
    opening the address by hand is a perfectly good way to carry on
    while the install is repaired.
    """
    if not _wait_until_serving(url):
        print(f"[quietude] the server didn't come up in time - open {url} yourself")
        return

    if not ElectronWindow.is_available():
        print("[quietude] the window isn't installed, so there is nothing to "
              "open it in.")
        print("[quietude] run ./install.sh to fetch it.")
        lifecycle.request_shutdown("there is no window to open")
        return

    window = ElectronWindow(
        url,
        token=token,
        # Closing the window is quitting. A real signal, not an
        # inference - see desktop.py.
        on_closed=lambda: lifecycle.request_shutdown("the window was closed"),
        # A window that never opened means there is nothing to use, and
        # a server with nobody to serve has no business staying up: it
        # holds the port, and a browser tab left on that address would
        # go on talking to it. So this shuts down too.
        on_failed=lambda: lifecycle.request_shutdown("the window failed to open"),
        icon_file=config.icon_file(),
    )
    if window.open():
        lifecycle.register_app_window(window)
        print("[quietude] opened her own window")
        return

    print("[quietude] the window wouldn't open, so there is nothing to show.")
    print("[quietude] the output above says why - please send it with a bug "
          "report. ./install.sh --doctor is a good first look.")
    lifecycle.request_shutdown("the window wouldn't open")


# How many ports past the usual one to try before giving up. Enough to
# get past a handful of other things and past a copy of Quietude that is
# already running, and few enough that something genuinely wrong does
# not take thirty attempts to report.
PORT_SEARCH_RANGE = 12


def _port_free(host, port):
    """Whether this port can be listened on.

    Asked with a socket rather than by trying and catching, because
    werkzeug's make_server does not raise: it prints "Address already in
    use" and exits the process out from under you. There is nothing to
    catch and nothing to recover from, so the question has to be asked
    before it is handed the port.

    SO_REUSEADDR matches what make_server will do, so this answers the
    question it will actually face rather than a stricter one.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def _serve(host, asked_for, default_port, app):
    """A server, and the port it ended up on.

    Port 5000 is a popular default - it is Flask's, it is AirPlay
    Receiver's on macOS, and it is whatever else somebody started
    earlier - and "Address already in use" followed by a Werkzeug
    traceback is a poor way to meet an application. So when nobody asked
    for a particular port, the next few are tried and the one that
    worked is printed.

    When a port *was* asked for, that is a different situation: the
    caller means that port, probably because something else is pointed
    at it, and quietly moving would be worse than stopping.
    """
    if asked_for is not None:
        if not _port_free(host, asked_for):
            print(f"[quietude] port {asked_for} is already in use.")
            print("[quietude] find what has it with:")
            print(f"[quietude]   ss -ltnp 'sport = :{asked_for}'")
            print("[quietude] or leave --port off and a free one will be picked.")
            return None, asked_for
        return make_server(host, asked_for, app, threaded=True), asked_for

    for port in range(default_port, default_port + PORT_SEARCH_RANGE):
        if not _port_free(host, port):
            continue
        if port != default_port:
            print(f"[quietude] port {default_port} was busy, so this is on {port}")
        return make_server(host, port, app, threaded=True), port

    print(f"[quietude] every port from {default_port} to "
          f"{default_port + PORT_SEARCH_RANGE - 1} is in use.")
    print("[quietude] pick one yourself with --port, or see what's holding them:")
    print(f"[quietude]   ss -ltnp | grep ':{default_port}'")
    return None, default_port


def main(argv=None):
    parser = argparse.ArgumentParser(prog="quietude", description="Quietude - local AI assistant")
    default_host, default_port = config.host_port()
    parser.add_argument("--host", default=default_host)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--window", choices=config.WINDOW_MODES, default=None,
                        help="app: her own window (default); none: open nothing")
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

    # The secret the window gets and nothing else does. Minted per
    # launch, so a token learned from one run is useless against the
    # next, and never written to disk.
    # QUIETUDE_WINDOW_TOKEN is the hook the test suites use: they need
    # to make requests the way the window does, and the alternative was
    # a switch that turns the gate off - which would mean the thing
    # under test was not the thing that ships.
    window_token = os.environ.get("QUIETUDE_WINDOW_TOKEN") or secrets.token_urlsafe(32)
    server_module.set_window_token(window_token)

    app = create_app()
    server, port = _serve(args.host, args.port, default_port, app)
    if server is None:
        return 1
    lifecycle.register_server(server)

    url = f"http://{args.host}:{port}"
    print(f"[quietude] listening on {url}")
    print("[quietude] Ctrl+C to stop")

    if window_mode != "none":
        threading.Thread(target=_open_window, args=(url, window_token),
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
