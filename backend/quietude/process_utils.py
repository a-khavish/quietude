# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
process_utils.py
Bounded-wait-then-escalate shutdown for the worker processes, and that's
the whole story.

The Windows build needed four independent mechanisms to reliably die: an
in-process failsafe timer, a separately-spawned PowerShell kill-watcher
armed at startup, an external cmd.exe kill-switch, and a Win32 call to
close the console window. None of that was paranoia - Ctrl+C delivery
and clean process death really are unreliable in some Windows terminal
configurations, and `taskkill /F /T` was the only thing guaranteed to
work.

On Linux every one of those layers is replaced by two primitives that
are already guaranteed to work:

    SIGTERM   asks the process to stop, and our handler runs
    SIGKILL   is delivered by the kernel and cannot be caught or ignored

So the only thing worth keeping from the Windows design is the
*principle* - wait a bounded amount of time for a cooperative exit, then
stop waiting - and that is all this module is. There is no platform
branch anywhere in it: no taskkill fallback, no retry ladder, no
watchdog process watching the watchdog.
"""

import os
import signal


def stop_process(proc, *, sentinel_queue=None, sentinel=None, timeout=5.0, label="worker"):
    """Shut one worker process down in the known order: hand it a
    sentinel so it can finish what it's doing and exit on its own, wait
    at most `timeout` seconds, then SIGKILL it unconditionally.

    SIGKILL is used rather than Process.terminate() (SIGTERM) for the
    escalation step on purpose. By the time we get here the process has
    already declined a cooperative exit, so sending it another catchable
    signal just invites a second wait. Nothing in a worker holds state
    that needs flushing - synthesized clips live in the cache dir and
    models are read-only - so there is no reason to be gentle twice."""
    if proc is None:
        return
    if not proc.is_alive():
        proc.join(timeout=0.1)
        return

    if sentinel_queue is not None:
        try:
            sentinel_queue.put(sentinel, timeout=0.5)
        except Exception:
            pass  # a full or broken queue just means we go straight to waiting

    proc.join(timeout=timeout)

    if proc.is_alive():
        print(f"[quietude] {label} didn't exit within {timeout:g}s - sending SIGKILL")
        try:
            os.kill(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass  # already gone, or no longer ours - either way, done
        proc.join(timeout=1.0)


def stop_manager(manager, *, timeout=3.0, label="manager"):
    """Shut a multiprocessing.Manager down the same bounded way.

    Worth doing deliberately rather than just calling .shutdown() and
    hoping: a Manager's cooperative shutdown talks to its own server
    process, and that call can block indefinitely if another thread
    still has an in-flight proxy call against one of its dicts - which
    is exactly the situation during shutdown, when a worker may be
    mid-write to the shared status dict. So: ask nicely, wait a bounded
    time, then kill the server process directly."""
    if manager is None:
        return

    server = getattr(manager, "_process", None)

    try:
        manager.shutdown()
    except Exception:
        pass

    if server is None:
        return
    try:
        server.join(timeout=timeout)
    except Exception:
        return
    if server.is_alive():
        print(f"[quietude] {label} didn't exit within {timeout:g}s - sending SIGKILL")
        try:
            os.kill(server.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, AttributeError):
            pass
