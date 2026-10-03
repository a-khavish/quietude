# Contributing

Thanks for taking a look.

## Reporting a problem

Run this first and paste the output into the issue:

```bash
./install.sh --doctor
```

It prints what's installed, what your desktop can see, and what versions
of things you have. It changes nothing. It answers most of what I'd
otherwise have to ask you.

Then tell me:

- What you did
- What you expected
- What happened instead
- Which Linux you're on, and whether you're on Wayland or X11 (`echo $XDG_SESSION_TYPE`)

If it's about speech recognition, `speech models` shows the sensitivity
and the live level — mentioning what those read is useful.

## Working on the code

```bash
git clone https://github.com/a-khavish/quietude.git
cd quietude
./install.sh
```

The backend installs in editable mode, so changes under `backend/` take
effect the next time you start it:

```bash
~/.local/share/quietude/venv/bin/python -m quietude --window none
```

For the frontend, in a second terminal:

```bash
cd frontend && npm run dev
```

That gives you hot reload against the real backend.

## A few conventions

**Comments explain why, not what.** The code says what it does. A comment
earns its place by recording the reason — what was tried, what was
measured, what would break if someone changed it back. There are a lot of
these in the codebase and they are the most useful thing in it.

**Before you open a pull request**, run the interface tests:

```bash
cd frontend && npm run build && cd ..
./tests/run-backend-tests.sh
./tests/run-gui-tests.sh
```

They drive the real interface in a browser against a throwaway profile,
and they check the things that are easy to break without noticing: that
everything fits at every window size, that the layout holds still while
a reply is playing or voice chat is toggling, and that nothing calls the
assistant by the application's name. `tests/README.md` says what each
suite is for and why.

**Adding a command?** There is one list: `COMMANDS_REFERENCE` in
`backend/quietude/core/assistant.py`. Add it there and the reference
page, the sidebar and the help text all pick it up. Mark it
`"primary": True` if it belongs in the sidebar.

**Anything the assistant says about itself** goes through `self._text()`,
which fills in `{name}`, `{wake}`, `{they}`, `{them}` and friends. The
assistant's name is the user's to choose, so no string should hard-code
one.

**Measure rather than assume.** If a change depends on how a browser, a
window manager or a model behaves, check it and put the number in the
comment. Several of the trickier bugs in this project were things that
looked obviously correct.

## Testing

There's a browser-driven suite that walks the real flows:

```bash
# start it headless in one terminal
QUIETUDE_NO_BROWSER=1 quietude

# then, in another
pip install playwright && playwright install chromium
python3 scripts/screenshot_ui.py /tmp/shots
```

It asserts as it goes, so a screen that renders but doesn't work still
fails. `scripts/check_css_selectors.py` catches stylesheet rules that no
longer match any markup, which is how a whole page once ended up looking
wrong without anything erroring.

## Licence

By contributing you agree your work is licensed under the GNU General
Public License v3.0 or later, the same as the rest of the project.

Keep the three-line copyright header on any new source file — there's a
copy at the top of every existing one.
