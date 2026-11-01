"""Take a guidance shot to the standard frame (#1050, ``docs/GUIDANCE.md``).

Every picture of a What's New walkthrough is taken the same way, so a set reads as one set and
a member sees the portal as it is: the desktop frame is a 1280 by 800 viewport, dark, never a
full page scroll; the phone frame is 390 by 844, dark, for UI that exists on a phone alone; PNG,
at most 500 KB; and the control the step is about is ringed in the New! gold. A Playwright spec
drives the page to the moment (opens the modal, picks the tab) and calls :func:`shoot` once per
step; the files are then uploaded with ``scripts/upload_screenshot.py --guidance <slug>``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: The two frames a shot is taken to, by the fragment's ``frame`` (``plfog.changelog.SHOT_FRAMES``).
FRAMES: dict[str, dict[str, int]] = {"desktop": {"width": 1280, "height": 800}, "phone": {"width": 390, "height": 844}}

#: A shot is at most this many bytes, the same cap the upload script enforces.
MAX_BYTES = 500 * 1024

#: The ring: the New! badge's gold, drawn as an outline with a warm halo, over the page's own
#: focus and hover styles. Fallbacks for a page that does not load guidance.css.
_RING_CSS = (
    '[data-guidance-target="{target}"] {{'
    " outline: 3px solid var(--pl-new-from, #ffd36a) !important; outline-offset: 4px !important;"
    " box-shadow: 0 0 0 7px var(--pl-new-to, #f08a3e), 0 0 28px var(--pl-new-ring, rgba(238, 180, 75, 0.55)) !important;"
    " border-radius: 0.5rem; }}"
)

_DARK_JS = """() => {
    const root = document.documentElement;
    if (window.__plSetTheme) { window.__plSetTheme(false); }
    else { root.removeAttribute('data-theme'); root.classList.add('dark'); }
}"""

_RING_JS = """(css) => {
    document.querySelectorAll('style[data-guidance-ring]').forEach((style) => style.remove());
    if (!css) return;
    const style = document.createElement('style');
    style.setAttribute('data-guidance-ring', '');
    style.textContent = css;
    document.head.appendChild(style);
}"""

#: Wait for the pictures that are drawn to finish loading (or fail), so a shot never shows a half
#: drawn one. A lazy picture that is hidden or clipped never loads, so only pictures with a box
#: are waited for, and the wait is capped: a hung promise here would hang the spec.
_IMAGES_JS = """() => Promise.race([
    Promise.all(Array.from(document.images)
        .filter((image) => !image.complete && image.getClientRects().length > 0)
        .map((image) => new Promise((done) => { image.addEventListener('load', done); image.addEventListener('error', done); }))),
    new Promise((done) => setTimeout(done, 1500)),
])"""


def shoot(page: Any, path: Path | str, target: str | None = None, frame: str = "desktop") -> Path:
    """Screenshot ``page`` at the standard frame into ``path`` and return it.

    Sets the viewport for ``frame``, forces the dark theme, rings ``[data-guidance-target=target]``
    when a target is given (the hook a walkthrough's controls carry), waits for every picture on
    the page, and writes a PNG of the viewport. The ring stays until the next call rings something
    else, so a spec can check it. Raises ``ValueError`` on a frame outside :data:`FRAMES`, a
    target the page does not have, or a file over :data:`MAX_BYTES`.
    """
    if frame not in FRAMES:
        raise ValueError(f"frame must be one of {', '.join(FRAMES)} (got {frame!r})")
    out = Path(path)
    page.set_viewport_size(FRAMES[frame])
    page.evaluate(_DARK_JS)
    if target is not None and page.locator(f'[data-guidance-target="{target}"]').count() == 0:
        raise ValueError(f"No [data-guidance-target={target!r}] on the page to ring")
    page.evaluate(_RING_JS, _RING_CSS.format(target=target) if target is not None else "")
    page.evaluate(_IMAGES_JS)
    page.wait_for_timeout(350)  # the theme's colour transitions and the stepper's slide settle
    out.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out), type="png", full_page=False)
    size = out.stat().st_size
    if size > MAX_BYTES:
        raise ValueError(f"{out.name} is {size / 1024:.0f} KB; a guidance shot is at most {MAX_BYTES // 1024} KB")
    return out
