# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
lifecycle.py
Quietude's entire shutdown story, which on Linux fits in one file.

The Windows build needed four layered mechanisms to reliably stop:

    1. an in-process failsafe timer
    2. a PowerShell kill-watcher, spawned at startup and armed with a
       flag file it polled
    3. an external cmd.exe kill-switch that ran `taskkill /F /T`
    4. a Win32 call to close the console window

That was not over-engineering. Ctrl+C genuinely isn't delivered reliably
in some Windows console configurations, a Python process can end up
unkillable from inside itself, and `taskkill /T` was the only thing
certain to take the worker processes down with the parent.

Linux removes the need for all four:

    * SIGINT and SIGTERM are delivered reliably, and a handler written
      in Python actually runs - so (1) and (2) have nothing to do.
    * SIGKILL cannot be caught or ignored, so escalation is one syscall
      rather than an external process - (3) collapses into
      process_utils.stop_process.
    * Nothing owns a console window to close - (4) doesn't exist.

What's left is the part that was always the real work, and the one
principle worth porting: close things in a known order, wait a bounded
time for each, then stop waiting.

Order matters, and it's this:

    0. close the app window      first, because it's the visible part:
                                 the window disappearing is how the user
                                 knows the shutdown took
    1. stop the HTTP server      no new requests can arrive, so nothing
                                 can hand the workers new jobs while
                                 we're closing them
    2. speech worker             releases the audio queue; it also holds
                                 the largest allocation (both models)
    3. TTS worker                may be mid-synthesis; its clips live in
                                 the cache dir, so losing one costs
                                 nothing
    4. the Manager processes     last, because both workers write to
                                 their shared dicts right up until they
                                 exit

Nothing here opens an audio device - capture is the browser's, and
playback is the browser's - so there is no device to release. That was a
deliberate architectural choice and this is where it pays off.
"""

import os
import signal
import threading
import time

_server = None
_app_window = None
_shutdown_lock = threading.Lock()
_shutdown_started = False
_shutdown_complete = threading.Event()
_shutdown_timer = None
_shutdown_at = None


def register_app_window(window):
    """Hand in the app window so shutdown can close it.

    It closes first in the teardown, before anything else: when someone
    presses SHUT DOWN or says "shutdown", the window going away is the
    confirmation that it worked. Doing it last would mean staring at a
    live window for several seconds while the workers wind down, which
    reads as nothing having happened."""
    global _app_window
    _app_window = window


def register_server(server):
    """Hand in the werkzeug server so shutdown can stop it directly.

    This is why Quietude runs via make_server rather than app.run(): a
    reference to the server object means step 1 above is a method call,
    instead of the request-scoped shutdown hack that modern Werkzeug
    removed anyway."""
    global _server
    _server = server


def _teardown(reason):
    from quietude.core import speech_engine, tts_engine

    print(f"[quietude] shutting down ({reason})")

    if _app_window is not None:
        try:
            _app_window.close()
        except Exception as e:
            print(f"[quietude] closing the window raised (continuing): {e!r}")

    if _server is not None:
        try:
            _server.shutdown()  # returns once the serving loop has stopped
        except Exception as e:
            print(f"[quietude] http server shutdown raised (continuing): {e!r}")

    # Each of these is individually bounded and individually guarded: one
    # wedged worker must not prevent the other from being closed, and a
    # failure in either must not prevent the process from exiting.
    for label, close in (("speech engine", speech_engine.shutdown),
                         ("tts engine", tts_engine.shutdown)):
        try:
            close()
        except Exception as e:
            print(f"[quietude] {label} shutdown raised (continuing): {e!r}")

    print("[quietude] goodbye")
    _shutdown_complete.set()


def request_shutdown(reason="requested", delay=0.0):
    """Idempotent, and safe from a request handler, a signal handler, or
    both at once.

    The teardown runs on its own thread so an HTTP handler can return
    its response before the server it answered through is stopped.

    A second request is normally ignored - but one that would fire
    *sooner* reschedules instead. That's what lets the backend set a
    generous backstop while the UI plays its four-second power-down
    chime, and still shut down promptly the moment the UI reports the
    chime has actually finished. Without it the first caller's delay
    would win and the backstop would always be waited out in full."""
    global _shutdown_started, _shutdown_timer, _shutdown_at

    with _shutdown_lock:
        now = time.monotonic()
        fire_at = now + max(0.0, delay)

        if _shutdown_started:
            # Already scheduled. Only a strictly earlier time is worth
            # acting on; anything else is a duplicate.
            if _shutdown_at is None or fire_at >= _shutdown_at - 0.05:
                return False
            if _shutdown_timer is not None:
                _shutdown_timer.cancel()
            print(f"[quietude] bringing shutdown forward ({reason})")
        else:
            _shutdown_started = True

        _shutdown_at = fire_at
        _shutdown_timer = threading.Timer(max(0.0, delay), _teardown, args=(reason,))
        _shutdown_timer.daemon = True
        _shutdown_timer.start()
        return True


def wait_for_shutdown(timeout=None):
    return _shutdown_complete.wait(timeout)


def install_signal_handlers():
    """SIGINT (Ctrl+C) and SIGTERM (systemctl stop, a kill, a session
    ending) take the same path. SIGHUP is ignored rather than fatal, so
    closing the terminal Quietude was started from doesn't kill her - which
    is what anyone running her as a background assistant expects, and
    what nohup/systemd would otherwise have to arrange.

    No handler is installed for SIGKILL, because there cannot be one.
    That's the guarantee that makes all of this simple: whatever happens
    here, there is always something stronger."""

    def handler(signum, _frame):
        name = signal.Signals(signum).name
        if not request_shutdown(f"received {name}"):
            # A second signal while already shutting down is someone
            # pressing Ctrl+C again because they want it gone now. Oblige
            # rather than making them wait out the bounded joins.
            print(f"[quietude] second {name} - exiting immediately")
            os._exit(1)

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
