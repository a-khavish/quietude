# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
server.py
The Flask app: a pure JSON API, plus static serving for the built SPA.

Flask stayed. The brief allowed swapping it, but there was nothing to
gain: the API is ~25 small synchronous endpoints, none of them
long-lived, none of them streaming. The one thing that might have argued
for an async framework - a websocket for the speech pipeline - isn't
there by design (see api/speech.py). So Flask keeps the port cheap and
the dependency list short.

In production there is no second container and no nginx, because there's
no Docker at all now: Flask serves the built SPA from frontend/dist
directly. For a single-user assistant on loopback, a reverse proxy would
be a process to supervise and a config to get wrong in exchange for
throughput nobody needs. In development the SPA is served by Vite on its
own port, which proxies /api here - so the dev and production paths
differ only in who serves the static files.
"""

import mimetypes
import os
from pathlib import Path

from flask import Flask, jsonify, send_from_directory

from quietude import api, config

# Serve .mjs and .wasm correctly even on systems whose mime database
# doesn't know them - a wrong Content-Type on a module script is a
# silent, confusing failure in the browser.
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("application/wasm", ".wasm")

# backend/quietude/server.py -> backend/quietude -> backend -> repo root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DIST_DIR = Path(os.environ.get("QUIETUDE_DIST_DIR") or (REPO_ROOT / "frontend" / "dist"))


def create_app():
    config.ensure_dirs()

    app = Flask(__name__, static_folder=None)
    # The overall request cap, covering a multi-file upload.
    app.config["MAX_CONTENT_LENGTH"] = api.chat.MAX_UPLOAD_BYTES * 20

    api.register(app)

    @app.get("/api/health")
    def health():
        """Cheap liveness check with no side effects - used by install.sh
        and by the launcher to know when the server is actually up,
        rather than sleeping an arbitrary number of seconds."""
        return jsonify({"ok": True})

    _register_spa_routes(app)
    return app


def _register_spa_routes(app):
    """Serve the built SPA, with a history fallback.

    The fallback is what makes /commands and /tts-settings work as real
    URLs you can reload: any path that isn't an API call and isn't a file
    on disk returns index.html, and the Vue router takes it from there.
    The Windows build didn't need this because those two screens were
    separate server-rendered pages opened in their own browser windows -
    they're routes in one app now."""

    index = DIST_DIR / "index.html"

    @app.get("/")
    def spa_root():
        if not index.is_file():
            return _not_built(), 503
        return send_from_directory(DIST_DIR, "index.html")

    @app.get("/<path:requested>")
    def spa_catch_all(requested):
        # An unmatched /api/... path is a missing endpoint, not a route
        # for the SPA to try to render. Say so in JSON, since that's what
        # the caller was expecting.
        if requested.startswith("api/"):
            return jsonify({"ok": False, "error": "No such endpoint."}), 404

        if not index.is_file():
            return _not_built(), 503

        candidate = (DIST_DIR / requested).resolve()
        try:
            candidate.relative_to(DIST_DIR.resolve())
        except ValueError:
            # The path escaped dist/ via .. - resolve() has already
            # collapsed the segments, so this catches the encoded and
            # doubled-up spellings too, not just a literal "../".
            #
            # Answered with a 404 rather than the app shell: no legitimate
            # client ever requests this, and returning 200 would make a
            # probe look to the prober like it had reached something.
            return jsonify({"ok": False, "error": "Not found."}), 404

        if candidate.is_file():
            return send_from_directory(DIST_DIR, requested)

        # A missing file with an extension is a missing asset, not an app
        # route, so it gets a 404 rather than the app shell. Returning
        # index.html for an absent font would hand the browser HTML with
        # a text/html content type where it expected a font, which it
        # reports as a parse error rather than a missing file. The CSS
        # fallback stack rescues it either way, but a 404 is the truthful
        # answer and makes the real problem obvious in devtools.
        if "." in candidate.name:
            return jsonify({"ok": False, "error": "Not found."}), 404

        return send_from_directory(DIST_DIR, "index.html")


def _not_built():
    return (
        "<pre style=\"font-family:monospace;padding:40px;color:#ff5470;"
        "background:#05080d\">"
        f"Quietude's interface hasn't been built yet.\n\n"
        f"Expected it at: {DIST_DIR}\n\n"
        "Run ./install.sh, or for development:\n"
        "  cd frontend && npm install && npm run dev\n"
        "</pre>"
    )
