# Quietude — brand assets

**Motto: _Nothing leaves this room._**

The old promise, in the new name's own words. It is not a slogan
reaching for a feeling; it is the product's defining guarantee stated
plainly, and it is the one thing a person has to believe before they
will hand an assistant their face, their voice and their name. It
belongs on the agreement screen, where that promise is actually made,
and in the desktop entry's description.

"Room" rather than "machine" because the name already made the app a
place. The machine is the hardware; the room is what the user is
standing in, and it is where the assistant they named lives.

### Alternatives

| Motto | Why you'd pick it |
|---|---|
| **Nothing leaves this room.** | **Recommended.** Direct successor to _Nothing leaves this machine._ — same sentence shape, same flat declarative tone, now carrying the name. |
| Heard here, kept here. | Tighter and more rhythmic. Names both halves of the promise — the listening *and* the keeping — which the recommended one only implies. Slightly more of a tagline and slightly less of a guarantee. |
| The room keeps what it hears. | The warmest of the four, and the only one that says it in the positive. Good for a splash screen; weaker as the line under a consent button, where you want "nothing" doing the work. |
| Nothing crosses the threshold. | Matches the mark most literally — the sealed wall is the threshold. Reads a little more ceremonious than the product is. |

Whichever you pick, it keeps its full stop. It is a statement, not a
tagline.

---

## The mark

```
┌─      ─┐
│   ──   │     a sealed room, with a still level at its heart
└─      ─┘
```

Quietude is not the assistant. The user chooses the assistant's name,
its pronouns, its age and where it is from — so the app cannot be a
face, a figure or a mascot without quietly taking that choice back.
What the app *is*, is the place the assistant lives: one room, on one
machine, with the door shut. So the mark is architecture, seen from
above.

**The chamber wall** is an unbroken rounded square. It never opens. Its
four corners are reinforced with a heavier stroke — the corner-bracket
motif from `.hud-frame`, which the interface puts around every panel,
except here the four brackets have been closed into one continuous
boundary. Same vocabulary as the app, opposite statement: the HUD frame
marks an edge, this one seals it.

**The ripples** are three echoes of the wall, radiating from the centre
and damping as they go — brightest at the source, nearly gone by the
time they reach the wall. That is the whole product in one gesture:
sound happens in here, and it settles before it gets anywhere.

Their corner radius tightens as they travel, from nearly circular at
the source to the wall's own squareness at the outside. A disturbance
leaves the centre round and takes the shape of the room on its way out.
(It also stops three concentric squircles reading as a stack of UI
panels, which is exactly what they did when the proportion was held
constant.)

**The still line** is the core. A flat, level bar where a waveform
would be — speech at rest, the room after it has been spoken in. It
carries the only pure brightness in the mark, so the eye lands in the
middle of the room rather than on its edge.

There is no text in the icon. The wordmark lives only in the two
lockups.

## Files

| File | What it's for |
|---|---|
| `icon.svg` | The app icon, full detail. Used at 48px and up. |
| `icon-small.svg` | Ripples dropped, wall and bar thickened. Used at 16–32px. |
| `logo.svg` | Horizontal lockup: mark, wordmark, motto. 805 × 216. |
| `logo-stacked.svg` | Vertical lockup, for square spaces. 680 × 420. |
| `png/quietude-{16,22,24,32,48,64,128,256,512}.png` | Rasterised icons for the hicolor theme. |

The install step should put `icon.svg` in
`~/.local/share/icons/hicolor/scalable/apps/` and each PNG in its
matching `{size}x{size}/apps/` directory, then refresh the icon cache so
the launcher picks it up without a re-login.

### Why two icon SVGs

At 16px the whole 512-unit canvas lands on sixteen device pixels, so
anything thinner than roughly 48 units draws on less than one pixel and
arrives as a grey smear. `icon.svg`'s ripples are 4–7 units and its wall
is 13; all of it dissolves.

`icon-small.svg` therefore keeps only the two parts the mark cannot
lose — the sealed wall and the still line — and draws both heavy enough
to survive (wall 13 → 36, bar 13 → 44). The ripples and the corner
reinforcement are gone entirely: at that size a step in stroke weight is
invisible and only makes the corners look dirty. This is ordinary
icon-theme practice, and it is why the PNGs are shipped at all rather
than letting the desktop scale one SVG down.

The 16–32px PNGs come from the small variant and the 48px-and-up ones
from the full variant.

### How the PNGs were made

Rendered through headless Chromium (Playwright), not a dedicated
rasteriser — none of `rsvg-convert`, `inkscape` or `cairosvg` is present
in this environment, and ImageMagick's SVG delegate just shells out to
the missing `rsvg-convert`. Chromium is also the engine the in-app
interface runs on, so the icon and the UI cannot drift apart over a
gradient or a stroke join. The root `width`/`height` are stripped before
rendering so the `viewBox` scales to the target box; with them left on, a
512-wide SVG renders at 512 inside a 16px viewport and all you capture is
its top-left corner.

Every file has been checked for exact pixel dimensions and for actual
content (alpha coverage ~96%, 59 distinct colours at 16px rising to 5075
at 512px), not just for existing.

### The SVGs are well-formed XML, and that is not automatic

A double hyphen is illegal inside an XML comment. That matters here
because the comments in these files want to name CSS custom properties
(`--cyan`) and want rules of dashes as section separators — and either
one quietly makes the file unparseable to anything stricter than a
browser. librsvg, which is what GTK and the icon cache actually use to
draw an SVG icon, is strict and will simply refuse the file.

So the colour tokens are named here without their leading dashes, the
section rules are drawn with `=`, and all four files are checked against
an XML parser rather than only being eyeballed in a renderer.

All four files in this directory pass. Two of them did not when they
were first written: `icon.svg` had a rule of dashes under a heading in
its opening comment, and `logo.svg` named `--cyan` in a note about the
palette. Both rendered perfectly in a browser and both were refused by
librsvg, which is why the icon came out blank in the dock the first time
round and took three goes to track down.

So the check is mechanical now rather than a thing to remember:

```
for f in branding/*.svg; do
  python3 -c "import xml.dom.minidom,sys; xml.dom.minidom.parse('$f')" \
    && echo "ok   $f" || echo "BAD  $f"
done
```

If that prints anything but `ok`, the icon will not draw, whatever it
looks like in a browser.

### Why the wordmark is paths, not text

Every letter in `logo.svg` and `logo-stacked.svg` is a `<path>`, not
`<text>` with a `font-family`. A logo that depends on a font being
installed is a logo that renders as Times on half the machines it lands
on, and this project has already shipped one bug from assuming a webfont
would be there. Paths render identically everywhere with nothing to
download.

The letterforms are monoline geometric capitals on a strict grid:

```
centreline box   x 6..54, y 6..78      stroke 12
visual letter    60 wide x 84 tall
```

Round letters (Q, U, D) are built from half-ellipses of `ry 36` so they
reach full cap height instead of sitting short the way a true circle
would. The forms are deliberately calm — no chamfers, no angled cuts,
no outward flare. The interface's Orbitron headings supply the HUD edge;
the wordmark is the quiet thing the app is named after.

Spacing is kerned, not metric:

```
Q 0 | U 80 | I 164 | E 200 | T 276 | U 352 | D 436 | E 517     (577 x 84)
```

24 units between two stems, 20–21 where a round flank faces one, 16
either side of the T, and a 36-unit advance for the I. The T is the only
letter here that is empty under a full-width crossbar and the eye reads
that emptiness as part of the gap; left on the common 24 the word
visibly fell apart into "QUIE TUDE".

**The one exception:** the motto under each lockup is real `<text>` in
the generic `monospace` stack, which every system resolves to
*something*. It is a caption, not the identity, and outlining
twenty-five characters of it would cost more than it is worth. If you
need a lockup that is guaranteed glyph-for-glyph identical everywhere,
delete the `<text>` element — nothing else depends on it.

## Palette

The interface's own tokens from `hud.css`. No colours were invented for
the brand, so the icon can never drift away from the app it represents.

| Token | Hex | Role in the mark |
|---|---|---|
| `--bg` | `#05080d` | Icon plate, outer stop of its gradient |
| — | `#0d2632` | Icon plate, inner stop (top-lit, matching the app's backdrop) |
| — | `#061017` | Small icon's flat plate |
| `--cyan` | `#2de2e6` | Chamber wall, corner bracing, ripples, rim light |
| `--cyan-bright` | `#8ff9ff` | Still line, wordmark |
| — | `#f2feff` | Centre stop of the still line's gradient only |
| `--cyan-dim` | `#0f8b9e` | Reserved; not currently used in the mark |
| `--text-muted` | `#6c8a92` | Motto |

`#0d2632`, `#061017` and `#f2feff` are the only values not lifted
directly from `hud.css`, and each is a shade of a token that already
exists rather than a new hue.

## Using it

**Clear space.** Keep space on all four sides equal to the chamber
wall's corner radius — 72 units in the icon's 512 grid. That is 14% of
the icon's width, or one fifth of the mark's height in either lockup.
Nothing — no text, no rule, no edge of a container — comes inside it.

The icon already contains its own clear space: the wall sits at 84..428
inside a 512 plate — 73.5..438.5 once the corner bracing's half-stroke
is counted, a 14.4% safe margin on every side. Do not crop into it to make the mark look bigger.

**Backgrounds.** The *icon* brings its own ground and its own rim light
and is built to hold up on a white dock as well as on the near-black the
app runs on — both are checked. The *lockups* are dark-background
assets: they drop the plate, and the wordmark is `#8ff9ff`, which
disappears on white. If you need the name on a light background, use the
icon beside text set in your own dark colour rather than recolouring
these files.

**Don't recolour the mark.** The cyan is the identity. A monochrome
version loses the ripples, which only exist as a gradient of opacity —
flatten them and the mark becomes a box with a dash in it.

**Don't open the wall.** The whole idea is that it is closed. If you
need an "active" or "listening" state, animate the ripples outward or
brighten the still line; do not break the boundary.

**Don't give it a face.** No eyes, no mouth, no figure, no tilt that
suggests one. The assistant is whoever the user named; this is the room
they are in.

## Why the app icon is a bright plate

The first version of this icon used the interface's own palette
literally: a near-black plate with thin cyan strokes. It looked right on
a page and was almost invisible in a dock, because a dock is itself a
dark strip. Measured against a #2b2b2b background, only 11% of its
pixels were meaningfully different from what was behind them.

The weight is inverted now. The plate carries the colour and the mark is
cut out of it in near-black ink. An icon cannot assume what it will be
drawn on, so it has to bring its own contrast. The same measurement on
the current icon:

| background | before | now |
|---|---|---|
| dark dock (#1c1c1e) | 13% | 67% |
| GNOME dark (#2b2b2b) | 12% | 64% |
| KDE (#31363b) | 10% | 62% |
| light (#f0f0f0) | 96% | 79% |

This was changed while chasing a dock that showed an empty square, and
it is worth recording that it did not fix that. The empty square came
from `Icon=` in the `.desktop` file being a theme name rather than a
path, so the desktop resolved it through an icon cache it builds once
at login - see `write_desktop_entry` in `install.sh`. A bright icon and
an invisible one look the same when the desktop never reads either. The
inversion stays because it is the better icon, not because it was the
fix.

The logo lockups keep the line version of the mark on a dark ground,
which is the right form for a wordmark on a page. An app icon and a
logo being different weights of the same mark is normal; they are doing
different jobs.

## A trap worth knowing about

A double hyphen is illegal inside an XML comment. librsvg - which is
what GTK and the icon theme cache actually use to draw an SVG icon -
refuses the whole file for it. Chromium does not, so a broken file can
look perfectly fine in a browser and still never appear in a dock.

Every SVG here is checked against an XML parser before it ships. If you
edit one, check it:

```bash
python3 -c "import xml.dom.minidom,sys; xml.dom.minidom.parse(sys.argv[1])" branding/icon.svg
```
