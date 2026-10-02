# The class page gallery drops the hover magnifier

Round: 2026-10-02 instructor feedback round, PR 2 of 7.

**User story.** As a member reading a class page on a laptop, when I move the mouse over a gallery photo, I would like to see the photo, not a magnified patch of it. Currently the gallery zooms a 250 percent lens under the cursor the moment the pointer enters it.

**Summary.** Remove the hover magnifier from the class page gallery; clicking a photo still opens the lightbox.

## Current behavior

`templates/classes/_components/gallery.html` renders an "Amazon style" inner zoom: `@mousemove="onZoomMove"`, `zoomActive`, a `.cls-gallery__zoom-lens` div per slide with `background-size: 250%`, and a "Hover to zoom · click to expand" hint with a magnifier icon. Touch devices already skip it via `@media (hover: none)`.

## Expected behavior

- No lens, no `onZoomMove`, no `zoomActive`, no `is-zooming` class, no zoom CSS, no hint badge. The main image shows plainly; `cursor: pointer`.
- Click (or tap) on the main image still opens the lightbox; thumbnails, arrows, keyboard and the counter are unchanged.
- The Alpine component `clsGallery` keeps `activeIndex`, lightbox state and navigation only.
- Delete the now dead CSS and the `@media (hover: none)` rules that only served the lens. Keep the `max-height` media rule for `.cls-gallery__main`.

## Acceptance criteria

- [ ] The rendered gallery contains no element with class `cls-gallery__zoom-lens` or `cls-gallery__zoom-hint` and no `onZoomMove` in the page source.
- [ ] `classes/spec/views/class_preview_spec.py` still passes (it anchors on `cls-gallery` and `cls-gallery__thumbs`).
- [ ] A spec asserts the lens and hint markers are absent from a public class page that has gallery images (anchor on class names, never on copy).
- [ ] Screenshot of the gallery after the change under `mockups/screenshots/` (desktop width, the class page rail).

## Out of scope

The hub guild gallery (`templates/hub/_guild_gallery.html`) and the org floor plan, which have their own lightbox and no magnifier. The lightbox itself.

## Files

`templates/classes/_components/gallery.html`, a spec under `classes/spec/views/`.
