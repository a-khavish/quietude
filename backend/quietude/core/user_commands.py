# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
user_commands.py
Commands people write themselves.

The built-in commands are a fixed list in assistant.py. This is the
other half: a name you type or say, a description of what it does, a
shell command it runs, and three lines she says.

The three lines, and when each one lands
----------------------------------------
    starting   the moment the command is launched
    running    with it, while the command is still going
    finished   after the process has exited, under its output

That ordering is the whole reason this does not block. The first version
waited for the command, then said all three lines at once - so "this
takes a moment" arrived after the moment, and "finished" arrived before
the output it was meant to follow. A command is started, the reply says
so, and the finishing line is delivered when there is something to
finish: see start() and take_result() below.

Each command is a JSON file in its own directory, one file per command.
A directory rather than a table because these are the user's, not ours:
they can be read, edited, copied to another machine and backed up with
everything else under their data directory, without going through the
application at all. A single file would mean one corrupt write loses
every command; a database would mean they are only reachable from here.

    ~/.local/share/quietude/commands/<slug>.json

What runs, and what does not
----------------------------
These run a real shell command on the real machine, as the user. That is
the point of them and there is no version of this feature that does not.
So the question is not whether to allow it but what must be true around
it, and three things are:

  it is theirs       the command text is typed by the person into their
                     own application, behind their own login. Nothing
                     else writes these files, and the API that creates
                     them answers only the app's own window - see
                     server.py.
  one at a time      a lock, not a queue. Two shell commands running at
                     once from a voice interface is a good way to find
                     out your microphone misheard something twice.
  it ends            a timeout, so a command that waits for input does
                     not leave her unable to run anything else for the
                     rest of the session.

Not run through a shell by default. `shell=False` with the command split
the way a shell would split it means a stray semicolon in a filename is
a filename, not a second command. Someone who wants a pipeline can ask
for one explicitly, and the page says so - but the common case should
not carry the sharp edge of the rare one.
"""

import json
import os
import re
import shlex
import subprocess
import threading
import time

from quietude import config

# How long a command may run before it is stopped. Long enough for a
# build or a backup, short enough that a command waiting on a password
# prompt does not hold the lock until the application is restarted.
DEFAULT_TIMEOUT_SECONDS = 300
MAX_TIMEOUT_SECONDS = 3600

# What comes back from a command. Enough to show, bounded so a command
# that prints a gigabyte does not become a gigabyte in a chat bubble.
MAX_OUTPUT_CHARS = 4000

# Long enough for a sentence about what the command does, short enough
# to sit in a table cell beside the built-in descriptions.
MAX_DESCRIPTION_CHARS = 200

NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9 \-_']{0,58}[a-z0-9]$")


def commands_dir():
    directory = config.DATA_DIR / "commands"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _slug(name):
    """A filename for a command name.

    Lowercased, spaces to hyphens, anything else dropped. Two commands
    whose names differ only in punctuation would collide, which is the
    intent: they would also be indistinguishable to say out loud.
    """
    cleaned = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return cleaned or "command"


def _path(slug):
    """The file for this slug, or None if the slug escapes the directory.

    The slug is built by _slug and cannot contain a separator, but this
    is checked rather than trusted: the cost is one resolve, and the
    thing being prevented is a write anywhere on the filesystem.
    """
    root = commands_dir()
    candidate = root / f"{slug}.json"
    try:
        if candidate.resolve().parent != root.resolve():
            return None
    except OSError:
        return None
    return candidate


def validate(fields, existing_slug=None):
    """Checks a command before it is stored. Returns (cleaned, errors)."""
    errors = {}
    cleaned = {}

    name = str(fields.get("name", "")).strip()
    if not name:
        errors["name"] = "Give it a name - what you'll type or say to run it."
    elif not NAME_PATTERN.match(name.lower()):
        errors["name"] = ("Letters, numbers, spaces, hyphens and apostrophes, "
                          "2 to 60 characters.")
    else:
        slug = _slug(name)
        clash = _path(slug)
        if clash and clash.exists() and slug != existing_slug:
            errors["name"] = "There's already a command with that name."
        cleaned["name"] = name
        cleaned["slug"] = slug

    # What it does, for the person reading the list in six months.
    # Optional: a command called "back up my notes" does not need one,
    # and demanding a description for it would only get "backs up my
    # notes" typed into every box.
    description = str(fields.get("description", "")).strip()
    if len(description) > MAX_DESCRIPTION_CHARS:
        errors["description"] = (
            f"A bit shorter - {MAX_DESCRIPTION_CHARS} characters at most.")
    else:
        cleaned["description"] = description

    command = str(fields.get("command", "")).strip()
    if not command:
        errors["command"] = "Give it something to run."
    else:
        cleaned["command"] = command

    use_shell = bool(fields.get("use_shell"))
    cleaned["use_shell"] = use_shell
    if command and not use_shell:
        # Caught here rather than at run time: being told the quoting is
        # wrong while writing the command is useful, being told it an
        # hour later when you say the command out loud is not.
        try:
            parts = shlex.split(command)
        except ValueError as exc:
            errors["command"] = f"The quoting isn't right: {exc}"
            parts = []
        if command and not parts and "command" not in errors:
            errors["command"] = "That doesn't look like a command."

    for key, default in (("say_starting", ""), ("say_running", ""),
                         ("say_finished", "")):
        cleaned[key] = str(fields.get(key, default)).strip()

    try:
        timeout = int(fields.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_SECONDS
    cleaned["timeout_seconds"] = max(1, min(MAX_TIMEOUT_SECONDS, timeout))

    return cleaned, errors


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("name"):
        return None
    data.setdefault("slug", path.stem)
    data.setdefault("description", "")
    data.setdefault("command", "")
    data.setdefault("use_shell", False)
    data.setdefault("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    for key in ("say_starting", "say_running", "say_finished"):
        data.setdefault(key, "")
    return data


def all_commands():
    """Every stored command, by name."""
    out = []
    for path in sorted(commands_dir().glob("*.json")):
        data = _read(path)
        if data:
            out.append(data)
    out.sort(key=lambda c: c["name"].lower())
    return out


def get(slug):
    path = _path(slug)
    return _read(path) if path and path.exists() else None


def save(fields, existing_slug=None):
    """Creates or updates a command. Returns (command, errors)."""
    cleaned, errors = validate(fields, existing_slug=existing_slug)
    if errors:
        return None, errors

    # A rename is a new file, so the old one goes - otherwise renaming a
    # command leaves the old name still working, which is worse than
    # either outcome on its own.
    if existing_slug and existing_slug != cleaned["slug"]:
        old = _path(existing_slug)
        if old and old.exists():
            try:
                old.unlink()
            except OSError:
                pass

    path = _path(cleaned["slug"])
    if path is None:
        return None, {"name": "That name can't be stored as a file."}

    cleaned["updated_at"] = time.time()
    temporary = path.with_suffix(".json.new")
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(cleaned, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return None, {"command": f"Couldn't save it: {exc}"}

    return cleaned, {}


def delete(slugs):
    """Removes commands. Returns (removed, errors)."""
    removed, errors = [], []
    for slug in slugs:
        path = _path(str(slug))
        if path is None:
            errors.append(f"{slug}: not a command")
            continue
        if not path.exists():
            continue
        try:
            path.unlink()
            removed.append(slug)
        except OSError as exc:
            errors.append(f"{slug}: {exc}")
    return removed, errors


# ============================================================
# Running one
# ============================================================
#
# One at a time, and it is a lock rather than a queue.
#
# A queue would be worse here, not better: these are triggered by typing
# and by speaking, and the failure mode being guarded against is the
# same command arriving twice because the microphone heard it twice. A
# queue runs it twice, politely. A lock refuses the second one and says
# what is already running, which is the answer somebody actually wants.

_lock = threading.Lock()
_running = None          # {"slug", "name", "started_at", "say_running"}
_process = None

# The last finished command, and a counter that changes when it does.
#
# A command no longer blocks the reply, so the finishing line has to
# reach the screen by some other route than the response to the request
# that started it. The screen asks what has happened lately; the counter
# is how it can tell a result it has already shown from a new one,
# without clocks or ids on either side.
_result = None
_result_seq = 0
_result_lock = threading.Lock()


def running():
    """What is running, or None."""
    return dict(_running) if _running else None


def activity():
    """What is running and what finished last. The screen polls this.

    Deliberately cheap: no directory listing, no file reads. It is asked
    for every second or so while something is running.
    """
    with _result_lock:
        return {
            "running": running(),
            "result_seq": _result_seq,
            "result": dict(_result) if _result else None,
        }


def _clip(text):
    text = (text or "").strip()
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n… and {len(text) - MAX_OUTPUT_CHARS} more characters"


def finished_message(result):
    """The text and status for a command that has just ended.

    Here rather than in assistant.py because two callers need the same
    words: the chat, when a command it started finishes, and the user
    commands page, which shows the same thing in its own notice. The
    finishing line the person wrote comes first and the output under it,
    which is the order they described: she says the command is done, and
    then you can see what it did.
    """
    if result.get("timed_out"):
        text = (f"“{result['name']}” was still going after {result['seconds']} "
                "seconds, so I stopped it.")
        status = "warning"
    elif result.get("ok"):
        text = result.get("say_finished") or f"Finished, in {result['seconds']} seconds."
        status = "success"
    elif result.get("error"):
        text, status = result["error"], "error"
    else:
        text = (f"“{result['name']}” stopped with error code "
                f"{result.get('exit_code')}.")
        status = "error"

    output = (result.get("output") or "").strip()
    if output:
        text = f"{text}\n\n{output}"
    return text, status


def _record(result):
    global _result, _result_seq
    with _result_lock:
        _result = result
        _result_seq += 1


def take_result(since):
    """A finished command the caller has not seen yet, or None.

    `since` is the counter the caller last saw. Nothing is consumed or
    removed - two screens open on the same session both get it, and a
    reload does not lose it.
    """
    with _result_lock:
        if _result is None or _result_seq <= (since or 0):
            return None
        return {"result_seq": _result_seq, "result": dict(_result)}


def _execute(command):
    """Runs the process and waits for it. The lock is already held."""
    global _process
    started = time.monotonic()

    if command.get("use_shell"):
        argv = command["command"]
    else:
        try:
            argv = shlex.split(command["command"])
        except ValueError as exc:
            return {"ok": False, "name": command["name"],
                    "error": f"The quoting isn't right: {exc}"}
        if not argv:
            return {"ok": False, "name": command["name"],
                    "error": "There's nothing to run."}

    try:
        _process = subprocess.Popen(
            argv,
            shell=bool(command.get("use_shell")),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            errors="replace",
            cwd=str(config.DATA_DIR),
            # Its own process group, so a command that spawns children
            # can be stopped along with them rather than leaving
            # orphans behind.
            start_new_session=True,
        )
    except FileNotFoundError:
        first = argv if isinstance(argv, str) else argv[0]
        return {"ok": False, "name": command["name"],
                "error": f"I couldn't find “{first}” on this machine."}
    except OSError as exc:
        return {"ok": False, "name": command["name"],
                "error": f"I couldn't run it: {exc}"}

    timeout = command.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS
    try:
        output, _ = _process.communicate(timeout=timeout)
        code = _process.returncode
        timed_out = False
    except subprocess.TimeoutExpired:
        _stop_process()
        output, _ = _process.communicate()
        code, timed_out = None, True

    return {
        "ok": code == 0 and not timed_out,
        "timed_out": timed_out,
        "exit_code": code,
        "output": _clip(output),
        "seconds": round(time.monotonic() - started, 1),
        "slug": command["slug"],
        "name": command["name"],
        "say_finished": command.get("say_finished") or "",
    }


def _claim(slug):
    """Takes the lock for one command. Returns (command, refusal)."""
    command = get(slug)
    if command is None:
        return None, {"ok": False, "error": "There's no command by that name."}

    global _running
    if not _lock.acquire(blocking=False):
        busy = running()
        name = busy["name"] if busy else "something"
        return None, {
            "ok": False,
            "busy": True,
            "error": f"I'm still running “{name}”. One at a time.",
        }
    _running = {"slug": command["slug"], "name": command["name"],
                "say_running": command.get("say_running") or "",
                "started_at": time.time()}
    return command, None


def _release():
    global _running, _process
    _process = None
    _running = None
    _lock.release()


def start(slug):
    """Launches a command and returns straight away.

    This is what both callers use. The reply can then say the starting
    line while the command is actually starting, rather than describing
    something that already happened - and the finishing line is
    recorded when the process exits, for whoever is watching to pick up.

    The thread is a daemon: a command still running when the
    application is told to quit should not be what keeps it open. The
    window closing is an answer.
    """
    command, refusal = _claim(slug)
    if refusal:
        return refusal

    def worker():
        try:
            _record(_execute(command))
        finally:
            _release()

    threading.Thread(target=worker, name=f"command-{slug}", daemon=True).start()
    return {
        "ok": True,
        "started": True,
        "slug": command["slug"],
        "name": command["name"],
        "say_starting": command.get("say_starting") or "",
        "say_running": command.get("say_running") or "",
    }


def run(slug):
    """Runs a command and waits for it. Returns the finished result.

    Nothing in the application calls this - start() is the path a person
    takes. It stays because it is the honest way to test what _execute
    does with a command, without a thread and a poll in the middle of
    the assertion.
    """
    command, refusal = _claim(slug)
    if refusal:
        return refusal
    try:
        result = _execute(command)
        _record(result)
        return result
    finally:
        _release()


def _stop_process():
    """Asks, waits, then insists - the same shape as every other process
    this application owns."""
    if _process is None or _process.poll() is not None:
        return
    import signal
    try:
        os.killpg(os.getpgid(_process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            _process.terminate()
        except OSError:
            return
    deadline = time.time() + 5
    while time.time() < deadline:
        if _process.poll() is not None:
            return
        time.sleep(0.05)
    try:
        os.killpg(os.getpgid(_process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            _process.kill()
        except OSError:
            pass


def stop():
    """Stops whatever is running. Returns what it stopped, or None."""
    was = running()
    if was is None:
        return None
    _stop_process()
    return was
