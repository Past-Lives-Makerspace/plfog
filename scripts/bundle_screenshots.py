#!/usr/bin/env python3
"""Bundle a screenshots/ folder into ONE self-contained HTML file for sharing.

Reads ``<dir>/index.html`` and inlines every referenced PNG as a base64 data
URI, so the result is a single file with no external image dependencies —
double-click to view, or attach to an email. Stdlib only.

    python scripts/bundle_screenshots.py [screenshots] [-o out.html]
"""

from __future__ import annotations

import base64
import re
import sys
from pathlib import Path


def bundle(src_dir: str, out: str | None) -> Path:
    directory = Path(src_dir)
    html = (directory / "index.html").read_text(encoding="utf-8")
    cache: dict[str, str] = {}

    def data_uri(name: str) -> str:
        if name not in cache:
            raw = (directory / name).read_bytes()
            cache[name] = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
        return cache[name]

    def replace(match: re.Match[str]) -> str:
        attr, name = match.group(1), match.group(2)
        if not (directory / name).exists():
            return match.group(0)
        # Embed each image once (on the <img src>). The click-to-zoom <a href>
        # would double the file size, so neutralise it — the inline image is
        # already full resolution; browser zoom covers the rest.
        if attr == "href":
            return 'href="#"'
        return f'{attr}="{data_uri(name)}"'

    html = re.sub(r'(src|href)="([^"]+\.png)"', replace, html)
    out_path = Path(out) if out else directory / "cms-copy-review.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


if __name__ == "__main__":
    argv = sys.argv[1:]
    out_arg: str | None = None
    if "-o" in argv:
        i = argv.index("-o")
        out_arg = argv[i + 1]
        del argv[i : i + 2]
    source = argv[0] if argv else "screenshots"
    result = bundle(source, out_arg)
    size_mb = result.stat().st_size / 1_000_000
    print(f"Wrote {result} ({size_mb:.1f} MB) — one self-contained file, ready to email.")
