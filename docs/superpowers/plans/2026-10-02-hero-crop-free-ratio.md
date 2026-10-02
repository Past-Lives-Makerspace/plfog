# The crop box can be wide, square or free

Round: 2026-10-02 instructor feedback round, PR 7 of 7. Builds on PR 6 (the class page shows the whole crop).

**User story.** As an instructor with a square photo of a reindeer, when I crop it for the class banner, I would like to keep the whole reindeer. Currently the box is locked to 16:9 and I cannot zoom out far enough to fit it ("The square adjuster could let me go larger on the image").

**Summary.** The Photos step offers three box shapes, Wide (16:9), Square and Free; the class page shows whatever shape was chosen, whole, because PR 6 made the banner follow the crop.

## Expected behavior

**Shape picker** in `hero_image_field.html`, directly above the crop preview, shown only when the cropper is active (a photo is present and it is not an imported one): a segmented control of three radio buttons named `hero_crop_shape` (not a model field; it never posts anything the server reads) with labels **Wide** (`16:9`), **Square** (`1:1`) and **Free**. Styled with the existing `pl-` tokens like the scheduling type radios. Default on load: the shape that matches the saved box (ratio within 2 percent of 16/9 → Wide, within 2 percent of 1 → Square, anything else → Free; no saved box → Wide).

**Cropper** (`static/js/hero_cropper.js`): the picker calls `instance.setAspectRatio(16/9 | 1 | NaN)`. Cropper.js re-fits the box to the new ratio; after the change the box is written to the hidden input and announced exactly as a drag is (`writeCrop` + `announceCrop`), so the card previews and the saved value follow the choice. A rebuild on a step reveal restores the shape from the input's box (the same detection as the default above). The hint under the picker: "Wide fits the banner on a laptop. Square and Free show the whole shape on the class page, with soft bars at the sides on a wide screen."

**Server.** `render_hero_crop` already cuts any rectangle; `hero_aspect_ratio` (PR 6) already reports the copy's shape. No model change. The 3MB and long edge caps are unchanged.

**Catalog card.** The card strip stays a cover fit with its own focus sliders; a square crop shows its middle band there, and the composer's card previews already show that truth. Say so in the hint only if the builder finds the previews do not update on a shape change (they listen to `hero-crop` on window; the announce above should cover it).

## Acceptance criteria

- [ ] e2e (extend `tests/e2e/class_composer_cropper_spec.py`): pick Square → the Cropper box is square (`getData` width == height within 1px); save → `hero_crop_w == hero_crop_h` on the row and `hero_cropped` is square; open the class page → the hero `aspect-ratio` is `1 / 1` and the painted image is whole. Pick Free, drag to an odd rectangle, save → the row holds that rectangle. Reload the composer → the picker shows the matching shape.
- [ ] The default (no saved box) is Wide and an untouched cropper still writes nothing (the existing "untouched keeps hero_crop empty" test keeps passing).
- [ ] The card previews move when the shape changes (assert the `hero-crop` event fires once per shape change).
- [ ] A Python spec for a square and an odd rectangle through `render_hero_crop` and `hero_aspect_ratio`.
- [ ] Screenshots: the picker with Square chosen and the resulting class page at 1366 and 390.
- [ ] Both themes.

## Out of scope

Zooming the photo inside the box (the box moves over the photo; that is enough once the shape is free), rotating, the catalog card's shape, category and guild heroes.

## Files

`templates/classes/_components/hero_image_field.html`, `static/js/hero_cropper.js`, `static/css/hub.css` (the picker), `tests/e2e/class_composer_cropper_spec.py`, specs under `classes/spec/`.
