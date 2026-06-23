# Media Gallery — Enforce a 10-Image Cap on Every Upload Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hard-cap a class at **10 gallery images** across *every* upload path, with a clear user-facing error and a small UI affordance ("10/10" / disabled add zone). Today only the AJAX edit-page add path enforces the cap; the create-time bulk upload path is unbounded, the limit is a magic number, and there is no model-level guard. This plan closes those holes and leaves the (already-correct) layout, gallery component, and hero placement tool untouched.

**Reference for format/convention:** `docs/superpowers/plans/2026-06-18-standardize-light-mode-auth.md` (this plan mirrors its structure).

**Architecture:** Fat models — the count guard belongs in the model, not the view. Introduce one shared constant `MAX_GALLERY_IMAGES = 10`, a model-level guard `ClassImage.clean()` (so *any* save through `full_clean()` is protected), and make `ClassOffering.add_gallery_images()` the single fat-model entry point that rejects an over-cap batch with a domain-appropriate `ValidationError` *before* creating rows. The two views that bulk-add (`teach_class_create`, `admin_class_create`) catch that error and surface a form message — they stay skinny. The existing AJAX endpoint (`admin_class_image_upload`) keeps its early `>= 10` 400 but switches its literal `10` / `"Maximum 10 gallery images."` string to the shared constant so there is one source of truth.

**Tech Stack:** Django models/forms/views, pytest + pytest-describe (`*_spec.py`), factory-boy. No new dependencies, no DB schema change (the cap is application-level — a count constraint can't be a DB column constraint across rows cheaply; the guard is `clean()` + the model method). Small template + vanilla-JS affordance in `image_formset.html`.

---

## Background / context for the implementer

### What ALREADY exists and is correct — do NOT rebuild

- **Two-column responsive detail layout — DONE.** `templates/classes/public/detail.html:153–154` opens `.cp-detail__grid` with `<main class="cp-detail__main">` (narrative left) and `<aside class="cp-detail__rail">` (`detail.html:353`) holding the registration/pricing card (`.cp-detail__rail-card` `detail.html:354`) and the meta list (`.cp-detail__rail-meta` `detail.html:380`). The grid CSS is `static/css/cms-public.css:658–671` — single column on mobile, `grid-template-columns: minmax(0, 1fr) 380px` at `min-width: 1024px`; the rail card is `position: sticky` (`cms-public.css:910–911`). The spec's "narrative + prerequisites left, registration + pricing + instructor pinned right" requirement is **SATISFIED**. Verify only (Task 4); plan no layout work.
- **Prominent hero above the fold — DONE.** `templates/classes/public/detail.html:14–151` is the full-bleed `header.cp-detail__hero` (image / legacy image / category hero / guild-logo fallback) rendered *before* the body grid. Gallery sits at the top of the main column (`detail.html:185–186`, `{% include "classes/_components/gallery.html" %}`, immediately after the optional description/video). The spec's "gallery/carousel prominent up top" is **SATISFIED**.
- **Gallery component with lightbox / hover-zoom / keyboard nav — DONE.** `templates/classes/_components/gallery.html` (Alpine `clsGallery()` at `:246`): main image with Amazon-style inner zoom, thumbnail strip, full-screen lightbox with arrow-key + on-screen nav and Esc-to-close (`:59–80`). Feeds off `ClassOffering.display_images` (`classes/models.py:524–541`).
- **Manual sorting / reordering — DONE.** `ClassImage.sort_order` (`classes/models.py:701`), model `Meta.ordering = ["sort_order", "created_at"]` (`:704–705`). Edit page has drag-to-reorder persisting through `admin_class_image_reorder` (`classes/views.py:1880–1891`); the create form and Django form expose `sort_order` (`classes/forms.py:334`). Existing spec covers ordering (`classes/spec/models/class_image_spec.py:20–25`) and reorder (`classes/spec/views/admin_images_spec.py`).
- **Hero placement / "manual positional adjustment" tool — DONE.** `HeroCropMixin` with `hero_crop_x/y/w/h` (`core/models.py:14–32`) and the `hero_object_position` property (`core/models.py:40–76`, supports both focal-point and crop-box math). Focal-point slider UI is inline in `detail.html:80–125` (Alpine `heroPlacement()`), persisting through the `hub_hero_adjust` endpoint (`hub/views.py:333`, URL name `hub_hero_adjust` at `hub/urls.py:13`), already specced at `tests/hub/hub_hero_adjust_spec.py`. The original feature text reads "a front-end canvas crop **or** manual positional adjustment tool" — the slider IS the manual-positional-adjustment option, so this requirement is **SATISFIED**. A draw-a-box crop *rectangle* is optional polish — see Follow-up, not this plan.

### The actual gaps (what this plan fixes)

There are **three** code paths that create `ClassImage` rows; only one caps:

1. **AJAX edit-page add (capped already).** `classes/views.py:1859–1875` `admin_class_image_upload` — `if offering.gallery_images.count() >= 10: return JsonResponse({"error": "Maximum 10 gallery images."}, status=400)` (`:1863–1864`). Tested at `classes/spec/views/admin_images_spec.py:80–90` (`it_enforces_10_image_limit`). The `10` and the message are **hardcoded literals** — magic number.
2. **Create-time bulk upload (UNBOUNDED — the real gap).** `ClassOffering.add_gallery_images()` (`classes/models.py:500–503`) loops over the uploaded files and `ClassImage.objects.create(...)` each one with **no count check**. It is called by:
   - `teach_class_create` (`classes/views.py:845`) — `offering.add_gallery_images(request.FILES.getlist("gallery_images"))`
   - `admin_class_create` (`classes/views.py:1432`) — same call.
   A user attaching 30 files on the New Class form gets 30 rows. (Edit-page bulk re-uses the AJAX endpoint, not this method.)
3. **No model-level guard.** `ClassImage` (`classes/models.py:685–715`) has `sort_order`, `created_at`, `Meta.ordering`, `save()` (image normalization) — but **no `clean()`** and no count constraint. So `full_clean()` (called by the AJAX path at `views.py:1873`) does *not* itself enforce the cap; the cap lives only in the view. Anything that constructs a `ClassImage` without going through the AJAX view is unprotected.

The `ClassImageFormSet` in `classes/forms.py:341–347` (`extra=3`, `can_delete=True`) is **NOT wired to any view** (confirmed: the only reference is its own definition). Uploads go through `add_gallery_images` (create) and the AJAX endpoints (edit). **Do not plan a formset `clean()` — it would be dead code.** (If a future refactor wires the formset up, the model-level `clean()` from this plan still protects it.)

### Decisions baked into this plan

- **One shared constant.** Add `MAX_GALLERY_IMAGES = 10` near the top of `classes/models.py` (module level, after the imports / alongside `DEFAULT_LIABILITY_TEXT`). The model `clean()`, `add_gallery_images()`, and the AJAX view all reference it. No more magic `10`.
- **Model is the source of truth (fat models).** The authoritative guard is `ClassImage.clean()` (per-row: "this offering already has N images") plus a batch guard inside `add_gallery_images()` (so the create form rejects the *whole over-cap batch atomically* with a clear message instead of silently creating the first 10). The views stay skinny — they `try/except ValidationError` and add a form-level message.
- **Domain error, member-friendly message.** Raise `django.core.exceptions.ValidationError` with the exact user-facing string `"A class can have at most 10 images."` (build it from the constant: `f"A class can have at most {MAX_GALLERY_IMAGES} images."`). The AJAX endpoint keeps returning JSON `{"error": ...}` with the same string; the create views surface it via `messages.error` / `form.add_error(None, ...)`.
- **Atomic create-time batch.** `add_gallery_images()` must check `current_count + len(files) > MAX_GALLERY_IMAGES` *before* creating any row and raise, rather than creating up to the cap and dropping the rest. Partial silent success would surprise the user ("I added 12, only 10 saved, no error").
- **`sort_order` start offset bug fix (in-scope, tiny).** `add_gallery_images()` currently sets `sort_order=i` starting at 0 (`models.py:503`), which collides with any pre-existing images on a re-save. Since this method only runs at *create* time (offering has no images yet) this is latent, but while we are in here, start from `current_count` so the contract ("append after existing") holds and the count math is honest. This keeps "reorder preserved" true if the method is ever reused on edit.
- **Deleting then adding works.** The cap counts live rows only (`offering.gallery_images.count()`), and `admin_class_image_delete` (`views.py:1894–1900`) hard-deletes — so after deleting one of ten, the next add succeeds. This already holds; Task 3 adds a regression test to lock it.
- **UI affordance is a nice-to-have, not the gate.** The server is the enforcement boundary. The create-mode template gets a lightweight "N / 10 images" counter and disables the add zone at 10 (`image_formset.html:239–313`); the edit-mode block (`:101–127`) gets the same counter wired to the existing JS. If the JS proves fiddly, ship the server cap + counter text and leave hard-disable for follow-up — do not block the cap on the affordance.
- **Out of scope (flagged, not done):** the draw-a-box hero crop rectangle (the focal-point slider already satisfies the spec — see Follow-up); any change to the gallery component, lightbox, layout, or `hero_object_position`. The `ClassImageFormSet` is left as-is (unused, harmless).
- **Testing reality:** the cap is fully unit-testable at the model and view layers (file paths below). The UI affordance (counter/disable) is vanilla JS and not unit-tested here; verify it in the manual pass (Task 4).

---

## File Structure

- Modify: `classes/models.py` — add `MAX_GALLERY_IMAGES` constant; add `ClassImage.clean()` count guard (`~:704`); harden `ClassOffering.add_gallery_images()` to reject over-cap batches atomically and start `sort_order` from the current count (`:500–503`).
- Modify: `classes/views.py` — `admin_class_image_upload` (`:1859–1875`) uses `MAX_GALLERY_IMAGES` / shared message instead of literal `10`; `teach_class_create` (`:836–852`) and `admin_class_create` (`:1422–1434`) wrap `add_gallery_images()` in `try/except ValidationError` and surface a form/message error instead of 500.
- Modify: `templates/classes/_components/image_formset.html` — add a "N / 10 images" counter and disable the add zone at the cap in both create-mode (`:239–313`) and edit-mode (`:101–237`) blocks.
- Test: `classes/spec/models/class_image_spec.py` (extend) — model `clean()` and `add_gallery_images()` cap behavior.
- Test: `classes/spec/views/admin_images_spec.py` (extend) — confirm the AJAX cap still passes against the constant; delete-then-add regression.
- Test: `classes/spec/views/admin_classes_spec.py` (extend) — create-time bulk over-cap is rejected with the form error, not a 500.
- Modify: `plfog/version.py` — version bump + member-friendly changelog entry.

---

## Task 1: Shared constant + model-level cap guard

**Files:** `classes/models.py`

- [ ] **Step 1 (failing test first):** In `classes/spec/models/class_image_spec.py`, add a `describe_clean()` block under `describe_ClassImage()`:
  ```python
  import pytest
  from django.core.exceptions import ValidationError
  from classes.models import MAX_GALLERY_IMAGES

  def describe_clean():
      def it_rejects_the_11th_image(db):
          offering = ClassOfferingFactory()
          for i in range(MAX_GALLERY_IMAGES):
              ClassImageFactory(class_offering=offering, image=_image_file(f"{i}.png"), sort_order=i)
          eleventh = ClassImage(class_offering=offering, image=_image_file("x.png"))
          with pytest.raises(ValidationError):
              eleventh.full_clean()

      def it_allows_the_10th_image(db):
          offering = ClassOfferingFactory()
          for i in range(MAX_GALLERY_IMAGES - 1):
              ClassImageFactory(class_offering=offering, image=_image_file(f"{i}.png"), sort_order=i)
          tenth = ClassImage(class_offering=offering, image=_image_file("ten.png"))
          tenth.full_clean()  # must not raise
  ```
  (Import `ClassImage`/`ClassImageFactory` are already imported at the top of the spec; add `pytest`, `ValidationError`, `MAX_GALLERY_IMAGES`.)
- [ ] **Step 2: Confirm fail** — `pytest classes/spec/models/class_image_spec.py -q`. Expect `ImportError`/`AttributeError` (no `MAX_GALLERY_IMAGES`) then assertion failure once it exists.
- [ ] **Step 3: Implement.** In `classes/models.py`, add the module-level constant near `DEFAULT_LIABILITY_TEXT`:
  ```python
  MAX_GALLERY_IMAGES = 10
  ```
  Add `clean()` to `ClassImage` (after `__str__`, before `save`):
  ```python
  def clean(self) -> None:
      from django.core.exceptions import ValidationError

      super().clean()
      existing = ClassImage.objects.filter(class_offering=self.class_offering)
      if self.pk:
          existing = existing.exclude(pk=self.pk)
      if existing.count() >= MAX_GALLERY_IMAGES:
          raise ValidationError(f"A class can have at most {MAX_GALLERY_IMAGES} images.")
  ```
  > Excluding `self.pk` means re-saving an existing image (e.g. an alt-text edit that round-trips through `full_clean()`) never trips the guard.
- [ ] **Step 4: Confirm pass** — `pytest classes/spec/models/class_image_spec.py -q`. Green.
- [ ] **Step 5:** `ruff format . && ruff check .` then commit.

---

## Task 2: Harden `add_gallery_images()` (the unbounded create-time path)

**Files:** `classes/models.py`

- [ ] **Step 1 (failing test first):** Add to `class_image_spec.py`:
  ```python
  def describe_add_gallery_images():
      def it_creates_rows_for_each_file(db):
          offering = ClassOfferingFactory()
          offering.add_gallery_images([_image_file("a.png"), _image_file("b.png")])
          assert offering.gallery_images.count() == 2

      def it_rejects_a_batch_that_exceeds_the_cap(db):
          offering = ClassOfferingFactory()
          files = [_image_file(f"{i}.png") for i in range(MAX_GALLERY_IMAGES + 1)]
          with pytest.raises(ValidationError):
              offering.add_gallery_images(files)
          assert offering.gallery_images.count() == 0  # atomic: nothing created

      def it_rejects_when_existing_plus_batch_exceeds_cap(db):
          offering = ClassOfferingFactory()
          for i in range(MAX_GALLERY_IMAGES - 1):
              ClassImageFactory(class_offering=offering, image=_image_file(f"e{i}.png"), sort_order=i)
          with pytest.raises(ValidationError):
              offering.add_gallery_images([_image_file("x.png"), _image_file("y.png")])
          assert offering.gallery_images.count() == MAX_GALLERY_IMAGES - 1  # batch rejected whole

      def it_appends_after_existing_images(db):
          offering = ClassOfferingFactory()
          ClassImageFactory(class_offering=offering, image=_image_file("first.png"), sort_order=0)
          offering.add_gallery_images([_image_file("second.png")])
          orders = list(offering.gallery_images.order_by("sort_order").values_list("sort_order", flat=True))
          assert orders == [0, 1]  # appended, not colliding at 0
  ```
- [ ] **Step 2: Confirm fail** — the over-cap and append tests fail (current method has no guard and starts `sort_order` at 0).
- [ ] **Step 3: Implement.** Replace `add_gallery_images` (`classes/models.py:500–503`):
  ```python
  def add_gallery_images(self, files: list[UploadedFile]) -> None:
      """Create ClassImage rows from uploaded files, appended after existing ones.

      Raises:
          ValidationError: If adding ``files`` would push the offering over
              ``MAX_GALLERY_IMAGES``. The batch is rejected whole — no rows are
              created — so the caller can surface one clear message.
      """
      from django.core.exceptions import ValidationError

      current = self.gallery_images.count()
      if current + len(files) > MAX_GALLERY_IMAGES:
          raise ValidationError(f"A class can have at most {MAX_GALLERY_IMAGES} images.")
      for offset, img_file in enumerate(files):
          ClassImage.objects.create(class_offering=self, image=img_file, sort_order=current + offset)
  ```
- [ ] **Step 4: Confirm pass** — `pytest classes/spec/models/class_image_spec.py -q`. Green.
- [ ] **Step 5:** `ruff format . && ruff check .` then commit.

---

## Task 3: Skinny-view wiring — surface the cap, never 500

**Files:** `classes/views.py`, `classes/spec/views/admin_classes_spec.py`, `classes/spec/views/admin_images_spec.py`

- [ ] **Step 1 (failing test first — create path):** In `classes/spec/views/admin_classes_spec.py`, add a test that posts the New Class form with 11 `gallery_images` files and asserts the response is a re-rendered form (status 200) carrying the error message, **and** that no offering with those images was created over-cap. Mirror the existing `admin_class_create` POST setup in that file (find how it builds valid `form` + `sessions-*` management-form data; reuse it). Assert `"at most 10 images"` (case-insensitive) appears in `response.content.decode()`.
  > If `admin_classes_spec.py` has no create-POST helper, add a minimal one; check `teach_dashboard_spec.py` for the teach-side create POST shape and add a parallel test there if a teach helper already exists. Keep one create-path test minimum (admin) — add the teach one only if its fixtures are already in place.
- [ ] **Step 2: Confirm fail** — currently the over-cap POST raises `ValidationError` out of `add_gallery_images()`, surfacing as a 500 (no handler). Test fails.
- [ ] **Step 3: Implement — `admin_class_create` (`classes/views.py:1422–1434`).** Wrap the bulk add and on error re-render the form instead of letting it 500:
  ```python
  from django.core.exceptions import ValidationError
  ...
  if request.method == "POST" and form.is_valid() and session_formset.is_valid():
      offering = form.save(commit=False)
      offering.status = ClassOffering.Status.PUBLISHED
      offering.save()
      session_formset.instance = offering
      session_formset.save()
      try:
          offering.add_gallery_images(request.FILES.getlist("gallery_images"))
      except ValidationError as exc:
          offering.delete()  # roll back the half-created offering
          form.add_error(None, exc.messages[0])
      else:
          messages.success(request, f"{offering.title} is published.")
          return redirect("classes:admin_class_edit", pk=offering.pk)
  ```
  > `offering.delete()` keeps create atomic from the user's view — an over-cap submission leaves no orphan published class. Confirm `ValidationError` is importable at the top of `views.py` (add to imports if absent).
- [ ] **Step 4: Implement — `teach_class_create` (`classes/views.py:836–852`).** Same shape: wrap `offering.add_gallery_images(...)` (`:845`) in `try/except ValidationError`, on error `offering.delete()` + `form.add_error(None, exc.messages[0])` and fall through to the re-render at `:853`; only `submit/redirect` on success.
- [ ] **Step 5: Update the AJAX endpoint to the shared constant.** In `admin_class_image_upload` (`classes/views.py:1863–1864`) change:
  ```python
  if offering.gallery_images.count() >= MAX_GALLERY_IMAGES:
      return JsonResponse({"error": f"A class can have at most {MAX_GALLERY_IMAGES} images."}, status=400)
  ```
  Import `MAX_GALLERY_IMAGES` from `classes.models` at the top of `views.py`. **Note:** this changes the JSON error string from `"Maximum 10 gallery images."` to `"A class can have at most 10 images."` — update the existing assertion at `classes/spec/views/admin_images_spec.py:90` accordingly (`assert "at most 10 images" in response.json()["error"]`).
- [ ] **Step 6 (delete-then-add regression):** In `admin_images_spec.py`, add:
  ```python
  def it_allows_adding_again_after_a_delete(admin_user, client, db):
      client.force_login(admin_user)
      offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
      imgs = [ClassImageFactory(class_offering=offering, sort_order=i) for i in range(10)]
      ClassImage.objects.filter(pk=imgs[0].pk).delete()
      url = reverse("classes:admin_class_image_upload", kwargs={"pk": offering.pk})
      response = client.post(url, {"image": _tiny_gif()})
      assert response.status_code == 200
      assert offering.gallery_images.count() == 10
  ```
- [ ] **Step 7: Confirm pass** — `pytest classes/spec/views/admin_classes_spec.py classes/spec/views/admin_images_spec.py -q`. Green.
- [ ] **Step 8:** `ruff format . && ruff check .` then commit.

---

## Task 4: UI affordance — "N / 10" counter + disabled add zone

**Files:** `templates/classes/_components/image_formset.html`

Server enforcement (Tasks 1–3) is the gate; this is courtesy UX so users aren't surprised at submit.

- [ ] **Step 1: Edit-mode block (`image_formset.html:101–237`).** Add a counter element near the upload zone, e.g. after `:120`:
  ```html
  <div class="cls-image-count" id="gallery-count"></div>
  ```
  In the IIFE, add an `updateCount()` that sets `gallery-count` text to `${grid.children.length} / 10 images` and toggles a `disabled`/hidden state on `#gallery-upload-zone` when `grid.children.length >= 10`. Call it on init, after `addImageCard()`, and after a card `.remove()` in the delete handler.
- [ ] **Step 2: Create-mode block (`image_formset.html:239–313`).** Same counter using `grid.children.length`; call it inside `addFiles()` and `rebuildFileList()`. In `addFiles()`, stop adding once the preview grid hits 10 (and show a brief inline note) so the file input never carries more than 10 — this mirrors the server batch cap and avoids a guaranteed-to-fail submit.
- [ ] **Step 3:** No unit test (vanilla JS). Verified in Task 6 manual pass. `ruff` does not lint templates. Commit.

---

## Task 5: Lint / format / type-check

- [ ] **Step 1:** `ruff format . && ruff check .`
- [ ] **Step 2:** `mypy .` (export `DATABASE_URL` first if running before push: `export $(grep '^DATABASE_URL=' .env | xargs)`). Confirm the new `ValidationError` imports and the `add_gallery_images` signature type-check.

---

## Task 6: Manual verification + layout/hero sign-off (run skill)

Start the dev server (project `run` skill).

- [ ] **Cap — create form:** New Class form, attach 11 images → submit → form re-renders with "A class can have at most 10 images." and no class is published. Attach exactly 10 → succeeds.
- [ ] **Cap — edit AJAX:** On a class already at 10 images, the add zone is disabled and the counter reads "10 / 10". Delete one → counter "9 / 10", add zone re-enabled, adding one succeeds.
- [ ] **Cap — both admin and teach create paths** behave identically.
- [ ] **Reorder preserved:** drag to reorder on the edit page; reload → order persists (existing behavior — confirm untouched).
- [ ] **Layout (verify-only, expect DONE):** open a class detail page ≥1024px wide — narrative + prerequisites/materials in the left column, registration card + pricing + instructor pinned (sticky) in the right column; gallery prominent near the top. Resize below 1024px → single column, rail stacks under main. No change expected.
- [ ] **Hero placement (verify-only, expect DONE):** as an editor, the hero "Adjust" sliders move the focal point and "Save" persists across reload. No change expected.

---

## Task 7: Version bump + changelog

**Files:** `plfog/version.py`

- [ ] **Step 1:** Bump `VERSION`. **At time of writing the in-flight release is `2.5.8` (PR #108) and `plfog/version.py:5` reads `VERSION = "2.5.8"`; latest merged to `main` is `2.5.7`.** Verify the merged baseline before finalizing and use the next patch — `2.5.9` if 2.5.8 has merged. Do not assume.
- [ ] **Step 2:** Prepend a member-friendly `CHANGELOG` entry (plain language — this posts to Discord):
  ```python
  {
      "version": "2.5.9",  # verify
      "date": "2026-06-18",  # set to merge date
      "title": "Class photo galleries now cap at 10 images",
      "changes": [
          "Classes can now have up to 10 photos in their gallery. If you try to add more, you'll get a clear message instead of an error, and the upload area shows how many photos you've used (e.g. 7 / 10).",
      ],
  }
  ```
- [ ] **Step 3:** Commit.

---

## Final verification

- [ ] `pytest` — all pass, 100% coverage (new branches in `clean()`, `add_gallery_images()`, and the two create views are all exercised by Tasks 1–3).
- [ ] `ruff format . && ruff check . && mypy .` — clean.
- [ ] Manual pass (Task 6) signed off: cap enforced on all three upload paths, delete-then-add works, reorder + layout + hero unchanged.

---

## Follow-up (out of scope for this plan)

- **Draw-a-box hero crop rectangle.** The spec offered "a front-end canvas crop **OR** manual positional adjustment"; the existing focal-point slider (`core/models.py` `HeroCropMixin` already carries `hero_crop_w/h` for a true box, and `hero_object_position` already does crop-box center math) satisfies the "manual positional adjustment" branch. A draw-a-box canvas cropper that *sets* `hero_crop_x/y/w/h` from a dragged rectangle would be a nice enhancement reusing the existing fields and `hub_hero_adjust` endpoint — file separately if requested.
- **Wire up or delete `ClassImageFormSet`.** `classes/forms.py:341–347` defines an inline formset that no view uses. Either delete it (dead code) or wire it into the admin/edit flow; the model-level `clean()` from Task 1 already protects it if it is ever used. Out of scope here.
- **Configurable cap.** `MAX_GALLERY_IMAGES` is a module constant. If product later wants a per-category or site-config limit, promote it to `SiteConfiguration`. YAGNI for now.
