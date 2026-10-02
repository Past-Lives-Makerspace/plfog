# The class page shows the whole photo the instructor cropped

Round: 2026-10-02 instructor feedback round, PR 6 of 7. PR 7 (free crop shapes) builds on this.

**User story.** As an instructor, when I drag the crop box on the Photos step, I would like the class page to show exactly what is inside the box. Currently the live banner cuts the top and bottom off it (Glen's knife: space above and below the blade in the box, none on the page).

**Summary.** The class page banner takes the shape of the cropped photo and shows all of it, so the composer's crop box is the truth; the Adjust sliders go away for a class photo because there is nothing left to adjust.

## Root cause (confirmed by reading the code)

- The composer crops a 16:9 box (`static/js/hero_cropper.js`, `ASPECT = 16 / 9`) and the server cuts exactly that rectangle into `hero_cropped` (`ClassOffering.render_hero_crop`). The page shows that copy (`hero_image_url`).
- The banner frame is not 16:9. `.cp-detail__hero` in `static/css/cms-public.css` is full width with `min-height: clamp(280px, 42vw, 520px)`, which is about 2.4:1 on a laptop and about 1.4:1 on a phone, and the image is `object-fit: cover`. Cover fills the frame and clips whichever dimension does not fit: top and bottom on a laptop, the sides on a phone. No single crop shape can be shown whole in a frame whose shape changes with the screen.
- So the fix is on the page, not in the composer: show the whole copy.

## Expected behavior

**Frame.** `.cp-detail__hero` takes the shown photo's shape: the template sets `style="--cp-hero-ratio: {{ offering.hero_aspect_ratio }}"` where the new model property returns `"W / H"` from `hero_cropped` when there is a copy, else from `image`, else `"16 / 9"` (dimensions read the way `hero_object_position` reads them, with the same guards; a photo whose size cannot be read gives `"16 / 9"`). A `::before` sizer in a grid cell reads that custom property as its `aspect-ratio` with `max-height: 700px`, and the content shares the cell, so the frame is the taller of the photo and the content; `aspect-ratio` plus `max-height` on the frame itself would clip tall content again (#563). The 280px floor goes: the floor is now the content, plus a small floor of 200px for the logo fallback with no photo.

**Photo.** `.cp-detail__hero-img` becomes `object-fit: contain; object-position: 50% 50%`. Behind it a new `.cp-detail__hero-backdrop` div (rendered only when there is a photo) carries the same photo as a blurred, darkened cover background (`background-image` inline, `background-size: cover`, `filter: blur(28px) brightness(0.6)`, `transform: scale(1.1)`, `inset: 0`, under the img). When the frame has the photo's shape the img covers the backdrop completely; when the frame is taller than the photo (a long title on a phone, or the 700px cap on a tall crop) the backdrop fills the bars. The overlay gradient and the content sit on top as today.

**Width cap.** The photo's height is capped at 700px. The hero column is 1240px at its widest, so a 16:9 crop fills the column on any screen; only a taller shape (square, coming in PR 7) hits the cap and is pillarboxed over the backdrop, centred. That is the intended look; the PR body shows it.

**Adjust goes away for a class photo.** With contain nothing is clipped, so the Adjust sliders on the class page have nothing to move. Remove the `heroPlacement` wiring for the class's own photo (uploaded or legacy `legacy_image_url`), the Adjust button and the sliders in that branch, and the `x-ref`/`:style` bindings on the img. **Keep** the category fallback branch exactly as it is (no class photo: the category hero, cover fit, Adjust for `can_edit_category`), and keep the `hub_hero_adjust` endpoint and `hero_placement.js` for guilds, categories and org pages. The Edit link stays.

**Legacy photos.** A class with only an imported photo (`has_imported_photo_only`) shows it whole too. Update the note in `hero_image_field.html` (`#hero-legacy-note`): "This photo came over from the old class site, so the crop box is off for it. The class page shows all of it. Upload a new photo to crop it here."

**Composer hint.** In `hero_image_field.html` the tooltip line "After upload, drag the crop box to pick the focal point." becomes "After upload, drag the crop box. The class page shows exactly what is inside it."

**Flyer and catalog card.** Unchanged in this PR: the flyer prints the copy in its own frame and the card is a 150px strip with its own focus sliders and its own live previews.

## Acceptance criteria

- [ ] `hero_aspect_ratio` returns the copy's ratio with a copy, the upload's without, `"16 / 9"` with neither or when the file cannot be read; `hero_object_position` for a class with a copy is unchanged (`"50% 50%"`).
- [ ] Detail page: with a photo the hero carries the inline `--cp-hero-ratio`, the backdrop div, and no Adjust button or `heroPlacement` for the class photo; with only a category hero the Adjust tooling renders as before for an editor.
- [ ] `tests/e2e/class_page_hero_fit_spec.py` is rewritten for the new rule: for the factory's photo the hero height equals `min(width * h / w, 700)` with no subtitle, the painted photo is never clipped (compare the img's natural ratio to the frame's ratio when under the cap; `object-fit` computed as `contain`), the category row and byline stay inside the hero with the long subtitle, no sideways overflow, at 390 and 1366.
- [ ] A new e2e proves the fix end to end: upload a photo in the composer, drag the box to a known rectangle, save, open the class page, and assert the painted image shows the whole copy (the img's rendered box ratio equals the copy's ratio within 1px, or the frame ratio equals the copy's ratio). Reuse `tests/e2e/class_composer_cropper_spec.py`'s helpers.
- [ ] `python manage.py check`; the Python suite for `classes/spec/views/public_spec.py`, `class_preview_spec.py` and the hero specs pass.
- [ ] Screenshots under `mockups/screenshots/`: the composer crop box and the resulting class page at 1366 and 390, same photo, so the match is visible.
- [ ] Both themes.

## Out of scope

Free or square crop shapes (PR 7), the catalog card strip, the flyer, the category and guild heroes.

## Files

`classes/models.py` (`hero_aspect_ratio`), `templates/classes/public/detail.html`, `static/css/cms-public.css`, `templates/classes/_components/hero_image_field.html`, `tests/e2e/class_page_hero_fit_spec.py`, a new e2e, specs under `classes/spec/`.
