# Bundled fonts

Drop TTF/OTF files here that the renderer will pick up automatically.

The rate card looks for these names, in order:
1. `HelveticaNeueCyr-Bold.ttf` / `HelveticaNeueCyr-Roman.ttf` — exact match to the Figma mock.
2. `Inter-Bold.ttf` / `Inter-Regular.ttf` — close free fallback (SIL OFL).
3. System fallback (Segoe UI on Windows, DejaVu on Linux/Docker).

To get the exact Figma look, put `HelveticaNeueCyr-Bold.ttf` and `HelveticaNeueCyr-Roman.ttf`
in this directory. These are licensed fonts; obtain them from your licensed source.
