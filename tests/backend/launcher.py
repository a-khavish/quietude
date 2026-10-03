# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Starting up: which port, and which sandbox.

Both of these are decisions made before anything is on screen, and both
of them were first reported as "it just doesn't open".
"""
import os
import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gui" / "stubs"))

PASSED = FAILED = 0


def check(label, ok, detail=""):
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  pass  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label}  {detail}")


from quietude import __main__ as launcher        # noqa: E402
from quietude import desktop                     # noqa: E402


# ------------------------------------------------------------
print("\n-- the port --")
# Port 5000 is Flask's default, AirPlay Receiver's on macOS, and
# whatever somebody started an hour ago. Meeting that with a werkzeug
# traceback is a poor introduction.

held = socket.socket()
held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
held.bind(("127.0.0.1", 0))
held.listen(1)
busy = held.getsockname()[1]

check("a port in use reads as in use", not launcher._port_free("127.0.0.1", busy))

free = socket.socket()
free.bind(("127.0.0.1", 0))
spare = free.getsockname()[1]
free.close()
check("a free port reads as free", launcher._port_free("127.0.0.1", spare))


class FakeApp:
    pass


made = []


def fake_make_server(host, port, app, **kw):
    made.append(port)
    return f"server-on-{port}"


launcher.make_server = fake_make_server

made.clear()
server, port = launcher._serve("127.0.0.1", None, busy, FakeApp())
check("with no port asked for, a busy default moves on", port == busy + 1,
      f"landed on {port}")
check("and the server was made on the port it reports", made == [port], made)

made.clear()
server, port = launcher._serve("127.0.0.1", busy, 5000, FakeApp())
check("a port asked for by name is not quietly moved", server is None, port)
check("and nothing was bound", made == [], made)

made.clear()
server, port = launcher._serve("127.0.0.1", spare, 5000, FakeApp())
check("a free port asked for by name is used", port == spare and server is not None)

# Every port in the range taken.
hold_all = []
base = busy
for offset in range(launcher.PORT_SEARCH_RANGE):
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", base + offset))
        s.listen(1)
        hold_all.append(s)
    except OSError:
        s.close()
made.clear()
server, _ = launcher._serve("127.0.0.1", None, base, FakeApp())
check("a full range gives up rather than looping", server is None)
for s in hold_all:
    s.close()
held.close()


# ------------------------------------------------------------
print("\n-- the sandbox --")
# Chromium will not run without a sandbox, and npm cannot set the setuid
# bit its helper needs - so a fresh install hits this before it hits
# anything else. The engine has a second sandbox that needs no
# privileges; the point of this is to reach for that one rather than
# turning sandboxing off.

tmp = tempfile.mkdtemp()
binary = os.path.join(tmp, "electron")
Path(binary).write_text("")
helper = Path(tmp) / "chrome-sandbox"

real_uid = os.geteuid
real_ns = desktop._user_namespaces_available
try:
    os.geteuid = lambda: 1000

    check("no helper at all is not treated as set up",
          not desktop._sandbox_helper_ok(binary))

    helper.write_text("")
    os.chmod(helper, 0o755)
    check("a helper without the setuid bit is not set up",
          not desktop._sandbox_helper_ok(binary))

    desktop._user_namespaces_available = lambda: True
    check("...so namespaces are used instead",
          desktop._sandbox_flags(binary) == ["--disable-setuid-sandbox"],
          desktop._sandbox_flags(binary))

    desktop._user_namespaces_available = lambda: False
    check("with neither, it says so and goes without",
          desktop._sandbox_flags(binary) == ["--no-sandbox"],
          desktop._sandbox_flags(binary))

    desktop._user_namespaces_available = lambda: True
    os.environ["QUIETUDE_ELECTRON_NO_SANDBOX"] = "1"
    check("the override is honoured",
          desktop._sandbox_flags(binary) == ["--no-sandbox"])
    del os.environ["QUIETUDE_ELECTRON_NO_SANDBOX"]

    os.geteuid = lambda: 0
    check("as root, there is nothing to sandbox from",
          desktop._sandbox_flags(binary) == ["--no-sandbox"])
finally:
    os.geteuid = real_uid
    desktop._user_namespaces_available = real_ns

# The real helper, if this checkout has one installed.
real_binary = desktop.find_electron()
if real_binary:
    ok = desktop._sandbox_helper_ok(real_binary)
    print(f"        this checkout's helper is {'set up' if ok else 'not set up'}")

# ------------------------------------------------------------
print("\n-- the window gate --")
# Loopback is not private: every process running as this user can reach
# it, which meant the whole assistant was one address bar away. The
# window is given a secret and sends it as a header; a page cannot set a
# header on the navigation that loads it, so a browser cannot get in.

from quietude import server as server_module                    # noqa: E402

app = server_module.create_app()
server_module.set_window_token("a-secret")
client = app.test_client()
try:
    check("a request with no header is refused",
          client.get("/").status_code == 403)
    check("a request with the wrong secret is refused",
          client.get("/", headers={server_module.TOKEN_HEADER: "no"}).status_code == 403)
    check("the window's own requests go through",
          client.get("/", headers={server_module.TOKEN_HEADER: "a-secret"}).status_code != 403)
    check("the refusal says where to go",
          b"own window" in client.get("/").data)
    # The launcher polls this to know when to open the window, and
    # install.sh polls it to know the install works. Neither has the
    # secret, and it answers {"ok": true} and nothing else.
    check("the liveness probe stays open",
          client.get("/api/health").status_code == 200)
finally:
    server_module.set_window_token(None)

check("with no token set, nothing is gated",
      app.test_client().get("/").status_code != 403)


# ------------------------------------------------------------
print("\n-- starting at login --")
from quietude import config                                      # noqa: E402

home = tempfile.mkdtemp()
real_dir, real_file = config.AUTOSTART_DIR, config.AUTOSTART_FILE
try:
    config.AUTOSTART_DIR = Path(home) / "autostart"
    config.AUTOSTART_FILE = config.AUTOSTART_DIR / "quietude.desktop"

    check("off to begin with", not config.autostart_enabled())
    ok_, err = config.set_autostart(True, launcher="/usr/bin/quietude")
    check("turning it on writes the entry", ok_ and config.autostart_enabled(), err)
    body = config.AUTOSTART_FILE.read_text()
    check("the entry points at the launcher", "Exec=/usr/bin/quietude" in body)
    check("and it is a desktop entry", body.startswith("[Desktop Entry]"))
    ok_, err = config.set_autostart(False)
    check("turning it off removes it", ok_ and not config.autostart_enabled(), err)
    ok_, err = config.set_autostart(False)
    check("turning it off twice is fine", ok_, err)
finally:
    config.AUTOSTART_DIR, config.AUTOSTART_FILE = real_dir, real_file


print()
print("=" * 52)
print(f"  {PASSED} passed, {FAILED} failed")
print("=" * 52)
sys.exit(1 if FAILED else 0)
