# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Commands people write themselves.

These run real shell commands on the real machine, so the things worth
testing are the boundaries: that one name cannot become two commands,
that two cannot run at once, and that a command which never ends does
not hold the lock for ever.
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gui" / "stubs"))

HOME = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = HOME

PASSED = FAILED = 0


def check(label, ok, detail=""):
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  pass  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label}  {detail}")


from quietude.core import user_commands as uc        # noqa: E402

print("\n-- storing them --")
cmd, errors = uc.save({"name": "say hello", "command": "echo hello",
                       "say_finished": "Done."})
check("a command saves", cmd and not errors, errors)
check("one file per command, named for it",
      (uc.commands_dir() / "say-hello.json").is_file())
check("it reads back", uc.get("say-hello")["command"] == "echo hello")

_, errors = uc.save({"name": "", "command": "echo x"})
check("a nameless command is refused", "name" in errors)
_, errors = uc.save({"name": "fine", "command": ""})
check("a command with nothing to run is refused", "command" in errors)
_, errors = uc.save({"name": "say hello", "command": "echo again"})
check("a duplicate name is refused", "name" in errors)
_, errors = uc.save({"name": "broken", "command": 'echo "unclosed'})
check("unbalanced quotes are caught before it is stored", "command" in errors)

# A name that would escape the directory if it were used as a filename.
cmd, errors = uc.save({"name": "up and out", "command": "echo x"})
stored = uc.get(cmd["slug"])
check("a slug stays inside the commands directory",
      (uc.commands_dir() / f"{cmd['slug']}.json").resolve().parent
      == uc.commands_dir().resolve())

_, errors = uc.save({"name": "wordy", "command": "echo x",
                     "description": "x" * 400})
check("a description longer than the field allows is refused",
      "description" in errors, errors)
cmd, errors = uc.save({"name": "described", "command": "echo x",
                       "description": "Prints an x."})
check("a description is stored and reads back",
      uc.get("described")["description"] == "Prints an x.", errors)
check("and a command written before descriptions existed still reads",
      uc.get("say-hello")["description"] == "")

print("\n-- running them --")
result = uc.run("say-hello")
check("it runs and reports the output",
      result["ok"] and result["output"] == "hello", result)

uc.save({"name": "nonsense", "command": "definitely-not-a-real-binary"})
result = uc.run("nonsense")
check("a missing program is reported rather than raised",
      not result["ok"] and "couldn't find" in result["error"].lower(), result)

uc.save({"name": "fails", "command": "sh -c 'exit 3'"})
result = uc.run("fails")
check("a non-zero exit is not success", not result["ok"], result)

print("\n-- a semicolon is not a second command --")
marker = Path(HOME) / "SHOULD-NOT-EXIST"
uc.save({"name": "sneaky", "command": f"echo one; touch {marker}"})
uc.run("sneaky")
check("without a shell, the rest of the line is an argument",
      not marker.exists())
# ...and with one, it is a shell, which is what asking for one means.
uc.save({"name": "deliberate", "command": f"echo one; touch {marker}",
         "use_shell": True}, existing_slug=None)
uc.run("deliberate")
check("with a shell, it behaves like a shell", marker.exists())

print("\n-- the three lines land when they say they do --")
# The reply that starts a command carries the starting and running
# lines; the finishing line is recorded when the process exits, for
# whoever is watching to pick up. Said all at once - which is what the
# first version did - "this takes a moment" describes a moment that has
# already passed.
uc.save({"name": "narrated", "command": "sh -c 'sleep 1; echo output'",
         "say_starting": "Starting now.", "say_running": "This takes a moment.",
         "say_finished": "All done."})

before = uc.activity()["result_seq"]
launched = time.monotonic()
started = uc.start("narrated")
took = time.monotonic() - launched
check("start returns while the command is still running", took < 0.5, f"{took:.2f}s")
check("and hands back the two lines to say now",
      started["say_starting"] == "Starting now."
      and started["say_running"] == "This takes a moment.", started)
check("nothing has finished yet", uc.take_result(before) is None)
check("but something is running",
      (uc.running() or {}).get("name") == "narrated")

deadline = time.monotonic() + 12
fresh = None
while fresh is None and time.monotonic() < deadline:
    fresh = uc.take_result(before)
    if fresh is None:
        time.sleep(0.1)
check("the ending arrives on its own", fresh is not None)
text, status = uc.finished_message(fresh["result"])
check("the finishing line is said, and not the other two",
      text.startswith("All done.")
      and "takes a moment" not in text, text)
check("with the output under it, not over it",
      text.index("All done.") < text.index("output"), text)
check("and it reads as having gone well", status == "success", status)
check("a result already seen is not delivered twice",
      uc.take_result(fresh["result_seq"]) is None)

uc.save({"name": "narrated quiet", "command": "sh -c 'exit 4'"})
mark = uc.activity()["result_seq"]
uc.start("narrated-quiet")
deadline = time.monotonic() + 12
fresh = None
while fresh is None and time.monotonic() < deadline:
    fresh = uc.take_result(mark)
    if fresh is None:
        time.sleep(0.1)
text, status = uc.finished_message(fresh["result"])
check("a command with no finishing line still reports its exit code",
      status == "error" and "4" in text, text)

print("\n-- one at a time --")
uc.save({"name": "slow", "command": "sleep 3"})
outcome = []
worker = threading.Thread(target=lambda: outcome.append(uc.run("slow")))
worker.start()
time.sleep(0.7)
second = uc.run("say-hello")
check("a second command is refused while one runs", second.get("busy"), second)
check("and starting one is refused the same way",
      uc.start("say-hello").get("busy"))
check("and it says which one is running",
      "slow" in (second.get("error") or ""), second)
check("the page can see what is running", (uc.running() or {}).get("name") == "slow")
worker.join()
check("nothing is running once it finishes", uc.running() is None)
check("and the lock is free again", uc.run("say-hello")["ok"])

print("\n-- it has to end --")
uc.save({"name": "forever", "command": "sleep 30", "timeout_seconds": 2})
started = time.monotonic()
result = uc.run("forever")
took = time.monotonic() - started
check("a command that never ends is stopped", result["timed_out"], result)
check("at roughly its timeout, not its own pace", took < 8, f"{took:.1f}s")
check("and the lock is released", uc.running() is None)

print("\n-- renaming --")
uc.save({"name": "old name", "command": "echo x"})
uc.save({"name": "new name", "command": "echo x"}, existing_slug="old-name")
check("the new name works", uc.get("new-name") is not None)
check("the old name is gone", uc.get("old-name") is None)

print("\n-- deleting --")
before = len(uc.all_commands())
removed, errors = uc.delete(["new-name", "nonsense"])
check("the chosen ones go", len(removed) == 2 and not errors, (removed, errors))
check("and the rest stay", len(uc.all_commands()) == before - 2)
removed, _ = uc.delete(["never-existed"])
check("deleting something absent is not an error", removed == [])

print()
print("=" * 52)
print(f"  {PASSED} passed, {FAILED} failed")
print("=" * 52)
sys.exit(1 if FAILED else 0)
