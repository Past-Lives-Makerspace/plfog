# Instructors choose the gallery's cover photo and how each photo is framed

Round: 2026-10-05 round, PR 2 of 2.

**User story.** As an instructor uploading gallery photos, I would like to choose which photo leads my class page gallery and which part of each photo shows in the frame. Currently the first photo I happened to upload leads, and every photo is cropped from its centre, so a photo of a pendant can show only the table it sits on. (Lee, by email: "could we look into setting a thumbnail when uploading gallery images? Currently it seems to choose that for you!")

**Summary.** Each gallery photo in the class composer gets a "Make cover" button (the cover leads the class page gallery and wears a "Cover" badge) and a "Set focus" tool (tap the part that matters; the gallery frame and its thumbnails crop around that point).

## Current behavior

- `ClassImage` (`classes/models.py`) has `image`, `alt_text`, `sort_order`. The class page gallery (`templates/classes/_components/gallery.html`) opens on the lowest `sort_order`, and both the 16:9 main frame and the 64x48 thumbnails use `object-fit: cover` with the default centre position.
- The composer's gallery editor (`templates/classes/_components/image_formset.html`, edit mode) already lets an instructor drag cards to reorder (`_gallery_reorder` in `classes/views.py`), but nothing says the first photo is the one that leads, and drag is awkward on a phone.
- The hero already has focal and crop tools, and the catalog card has `card_focus_x/y` (0 to 100, null follows the banner) rendered as `object-position`. Gallery photos have nothing.

## Expected behavior

**Cover**
- The first card in the edit mode grid wears a "Cover" badge. Every other card has a "Make cover" button (`pl-btn pl-btn--sm`, never a toggle). Pressing it moves that card to the front of the grid and posts the existing reorder route with the new order; on success a toast "Cover photo updated." On failure the grid is put back and an error toast shows.
- A one line hint above the grid: "The cover leads the gallery on your class page. Drag to reorder, or use Make cover." Drag reorder keeps working and the badge follows whichever card is first after any reorder, upload or delete.
- No new field: the cover IS `sort_order` first. Nothing else reads the order differently.

**Focus**
- New nullable `ClassImage.focus_x` and `focus_y` (`PositiveSmallIntegerField`, 0 to 100, help text) plus a property `object_position` returning `"x% y%"`, or `"50% 50%"` when either is null. One migration, `ruff format`ted.
- Each card has a "Set focus" button that opens a panel for that photo: the whole photo (contain), tap or click to place a marker, and a live 16:9 preview showing what the class page frame will show. "Save focus" posts; "Reset" posts nulls; "Cancel" closes without posting. Keyboard reachable; works at 390px.
- New route `teach/images/<int:pk>/focus/` (name `teach_class_image_focus`) plus the `admin/images/<int:pk>/focus/` legacy mirror, built exactly like the alt route: `@login_required`, `@require_POST`, scoped by `_class_image_or_404`. JSON body `{"x": int, "y": int}` or `{"x": null, "y": null}`; anything else (missing key, non int, bool, out of 0..100, only one null) is a 400. Validation lives in a small helper or form, not inline branching in the view.
- The card's own thumbnail in the editor uses the saved `object_position`, so the instructor sees the effect straight away.
- `gallery_display_images` and `display_images` dicts gain a `"position"` key (`gi.object_position`; the hero entry uses `hero_object_position`; the category fallback `"50% 50%"`). `gallery.html` sets `object-position` on the main slide image and its thumbnail. The lightbox still shows the whole photo.
- Copying a class (`_copy_photos_and_faqs_from`) carries `focus_x/focus_y` with each row.
- Update the `teach.class-gallery` entry in `core/help_registry.py` to mention the cover and focus (plain language, no dashes).

## Acceptance criteria

- [ ] Make cover on the third card makes it first in the grid, first in `offering.gallery_images.all()`, and the first slide on the public class page; the "Cover" badge moves with it (Playwright e2e on the composer gallery, Postgres).
- [ ] Set focus, tap near the top left, Save: the row stores roughly that point, the editor thumbnail and the public page's main slide and thumbnail render that `object-position` (e2e plus a view spec on the rendered style).
- [ ] Reset stores nulls and the public page renders `50% 50%`.
- [ ] The focus route returns 400 for each malformed body listed above, 404 for a viewer without Edit on the class and for a cancelled class edited by a non admin (crafted POST specs), and 200 for the instructor and an admin.
- [ ] A duplicated class's gallery rows keep their focus.
- [ ] Screenshot of the composer gallery with the Cover badge and the focus panel open, at laptop and 390px, under `mockups/screenshots/`.

## Out of scope

The guild page gallery (`templates/hub/_guild_gallery.html`); create mode (no pk), where the multi file input has no cards yet and the first picked file is simply the cover; cropping or rotating gallery photos; any change to the hero or catalog card tools.

## Files

`classes/models.py`, a migration, `classes/views.py`, `classes/urls.py`, `templates/classes/_components/image_formset.html`, `templates/classes/_components/gallery.html`, `core/help_registry.py`, specs under `classes/spec/`, an e2e under `tests/e2e/`.
