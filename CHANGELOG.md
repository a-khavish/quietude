# Changelog

## 1.0.0 — 2 October 2026

First public release.

A personal assistant for Linux that runs entirely on your own machine.
You name it; it listens, answers and recognises your face, and nothing
you say goes anywhere.

### What it does

- **Talks and listens.** Wake word, live captions as you speak, and a
  more accurate transcription once you stop. Both models run locally.
- **Speaks back.** Neural speech synthesis on the CPU, with a library of
  around a thousand voices across sixty-odd languages to download from
  inside the app.
- **Knows your face.** Optional face login, with a password as the
  backup. The camera feed never leaves the machine.
- **Is yours to define.** Name, pronouns, age and nationality, all set
  by you and applied as you type. The name you pick becomes the wake
  word, and the app checks it against the wake-word model's vocabulary
  before you commit to it.
- **Opens in its own window.** A real GTK window with its own icon, a
  fixed starting size, centred on screen, that can't be dragged smaller
  than the interface needs.

### Privacy

Everything runs locally. Two screens use the internet — the voice library
and the speech models page — and both say so on the page. Conversation
mode, which sends what you type to Google, is off by default, needs an
API key you supply yourself, and announces itself every time it's turned
on.

### Licence

GNU General Public License v3.0 or later. Copyright © 2026 Khavish
Auckaloo.

### Installing

One script. No Docker, no compose file, no service to configure first.

```bash
git clone https://github.com/a-khavish/quietude.git
cd quietude
./install.sh
```
