# Fonts

`install.sh` downloads four `.woff2` files into this directory:

```
orbitron-700.woff2         headings and the wordmark
orbitron-900.woff2
jetbrains-mono-400.woff2   everything else
jetbrains-mono-700.woff2
```

They are not committed to the repo, and nothing breaks if they are
absent — `src/styles/fonts.css` declares fallbacks, so Quietude renders in
the system monospace instead. That is why they live here rather than in
`src/assets/`: files under `public/` are copied verbatim and referenced
by URL, so a missing one is a runtime 404 the browser recovers from,
while a missing `src/assets/` import would fail the build outright.

To fetch them without a full install:

```bash
./install.sh --fonts-only
```
