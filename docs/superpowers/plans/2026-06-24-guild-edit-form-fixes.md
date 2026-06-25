# Guild Edit Form Fixes — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-06-24
**Surface:** FOG hub (`pastlives.test:8000`) — the guild **edit** page (`templates/hub/guild_edit.html`) and the public guild **detail** page (`templates/hub/guild_detail.html`).
**Related:** `2026-06-21-guild-pages-expansion.md` (announcements expiry + tabs), `2026-04-16-guild-leads-m2m.md` (`can_edit_guild` authority), `2026-06-21-guild-orientations.md` (separate-form-outside-main-form precedent).

---

## 1. Summary

This is a bundle of four small, well-bounded fixes to the guild pages, all aimed at making editing less fiddly for guild leads and the public page cleaner:

1. **FAQ gets its own Save button.** Today FAQ edits only persist when you scroll to the page-wide "Save Changes" at the bottom of one giant form. We give the FAQ section its own form + its own "Save FAQ" button so adding/editing questions is self-contained.
2. **Links gets its own Save button** — the same fix, for the Links section.
3. **Gallery becomes its own tab** on the public guild page. When a guild has uploaded photos, the Gallery currently sits buried at the bottom of the Overview tab; it should be its own tab in the tab strip.
4. **Announcements become editable.** Today a lead can post and delete an announcement but cannot fix a typo — they have to delete and re-post. We add an Edit affordance.

While refactoring FAQ (fix 1) we also correct two existing bugs in that section that violate FRONTEND.md: its per-row Delete renders as a **toggle switch** (`form_field.html with field=f.DELETE` at `guild_edit.html:98`), and its formset uses **`extra=1`** (`hub/forms.py:420`), which renders a perpetual blank row that can block save. The Links section already does both correctly — we copy it.

### Locked decisions (from the brief)

| Decision | Choice |
|---|---|
| Where FAQ/Links Save lives | Each section becomes its own `<form>` outside the main edit form, with its own primary Save button — mirroring how **Announcements** and **Staff** already sit outside the main form (`guild_edit.html:192`, `:230`). You can't nest `<form>`s, so this is the only correct structure. |
| FAQ/Links save feedback | Full-page POST → redirect back to `?tab=content` with a Django success message ("FAQ saved." / "Links saved."), matching the FRONTEND interaction table ("Success feedback (full page) → Django messages"). HTMX+toast noted as optional polish but **not** the spec's default — keep the path simple and consistent with the rest of this page. |
| FAQ Delete control | Fix it to the hidden-`DELETE`-behind-a-real-button pattern (copy Links). Never a toggle. |
| FAQ/Links `extra` | Change both formsets to `extra=0`. Rows are added on demand via the existing clone-template "+ Add" buttons. |
| Page-wide Save scope after the split | The main form's "Save Changes" button is hidden on the `content` tab too (it already hides on `announcements`/`staff`) — each section on that tab now saves itself. |
| Gallery tab visibility | Tab appears **only when `gallery_images`** exist (mirrors the existing FAQ tab's `{% if faq_items %}` guard at `guild_detail.html:105`). No empty state needed. |
| Announcement edit container | 3-field form (title, body, expires_at) → per FRONTEND "Quick action (1–3 fields) → Modal + Toast." Use `components/modal.html` with an HTMX-loaded prefilled form; on submit return a toast via `trigger_toast()` and OOB-swap the updated row. |

## 2. What already exists (reuse, don't reinvent)

Everything here is assembly — the patterns and plumbing are all present.

| Need | Existing thing | Location |
|---|---|---|
| Separate form outside the main edit form, with its own action + Save | Announcement create `<form>` (inside the `:193` panel); Staff add `<form>` (inside the `:231` panel) | `guild_edit.html:196` (announcement form), `:263` (staff form) |
| Correct editable-list pattern (hidden `DELETE` + real Delete button, clone-template "+ Add") | The **Links** editor | `guild_edit.html:133–183` |
| FAQ editor to refactor (wrong DELETE toggle; `extra=1`) | The **FAQ** editor | `guild_edit.html:88–131` (DELETE toggle at `:98`) |
| Permission gate for every mutating guild view | `_require_can_edit_guild(request, guild)` → 403 or None | `hub/views.py:489` |
| FAQ/Links formsets to flip to `extra=0` | `GuildFAQItemFormSet` / `GuildLinkFormSet` | `hub/forms.py:420`, `:432` |
| FAQ/Links forms (question/answer, label/url, hidden `sort_order`) | `GuildFAQItemForm` / `GuildLinkForm` | `hub/forms.py:408–417`, `:423–429` |
| Where FAQ/Links currently save (to be split out) | `guild_edit` GET/POST — saves form + both formsets + gallery | `hub/views.py:509–551` |
| Announcement create/delete views (mirror for edit) | `guild_announcement_create` / `guild_announcement_delete` | `hub/views.py:1472–1493`, `:1494–1507` |
| Announcement form (title, body, `expires_at` date input, "Hide after" label) | `GuildAnnouncementForm` | `hub/forms.py:614–625` |
| Gallery render partial (consumes `gallery_images`) | `hub/_guild_gallery.html` | template + `gallery_images = guild.gallery_images.all()` at `hub/views.py:404` |
| Public-page tab strip + conditional FAQ tab to mirror | `guild_detail.html:100–106` (Alpine `x-data="{ section: 'overview' }"`) |
| Gallery section to move out of Overview | `guild_detail.html:176–181` (inside `x-show="section === 'overview'"`) |
| Modal component (HTMX-loaded body) | `components/modal.html` (`$dispatch('open-modal', id)`) | per FRONTEND component library |
| Server toast helper | `trigger_toast(response, msg, type)` | `hub.toast` |
| Confirm modal already on each announcement row (Delete) | `guild_edit.html:223–228` |

**Gaps to close (kept minimal):**
- Two new thin views + URLs: `guild_faq_save`, `guild_links_save`.
- One new thin view + URL + partial template: `guild_announcement_edit` (HTMX modal form + OOB row).
- Two one-line form-definition edits (`extra=1` → `extra=0`).
- Template restructure of `guild_edit.html` (pull FAQ/Links out of the main form) and `guild_detail.html` (gallery tab).
- No model changes, no migration. `GuildAnnouncement.expires_at` already exists (shipped in the expansion plan) and the announcement form already includes it.

## 3. Where the code lives

```
hub/
  forms.py                    # extra=1 → extra=0 on GuildFAQItemFormSet (:420) and GuildLinkFormSet (:432). No other form changes.
  views.py                    # + guild_faq_save, guild_links_save (thin, gated, redirect+message)
                              # + guild_announcement_edit (thin, gated, HTMX modal form → toast + OOB row)
                              # guild_edit: stop handling faq/link POST (still instantiates formsets for GET render)
  urls.py                     # + hub_guild_faq_save, hub_guild_links_save, hub_guild_announcement_edit
templates/hub/
  guild_edit.html             # FAQ + Links pulled OUT of the main form into their own <form action=…> blocks;
                              # main-form Save x-show also excludes 'content'; FAQ Delete fixed to button pattern;
                              # each "Recent Announcements" row gains an Edit button
  guild_detail.html           # + Gallery tab button (conditional on gallery_images); gallery <section> moved to its own x-show panel
  partials/
    guild_announcement_edit_form.html   # NEW — prefilled GuildAnnouncementForm for the edit modal (HTMX target)
    _guild_announcement_row.html        # NEW (optional) — one row, reused for OOB swap after edit; see §6
tests/hub/
  guild_announcements_spec.py # extend: announcement edit (prefill, update, gating, modal partial)
  guild_edit_spec.py          # extend: faq/links save views; FAQ DELETE renders hidden+button not toggle; extra=0
  guild_tabs_spec.py          # extend: gallery tab present iff images exist
plfog/version.py              # VERSION bump + member-friendly CHANGELOG entry (last phase)
```

Home app: **`hub`** (views/urls/templates/forms), tests in **`tests/hub/`**, factories in **`tests/membership/factories.py`** (`GuildFactory`, `GuildFAQItemFactory`, `GuildLinkFactory`, `GuildAnnouncementFactory` all exist). This keeps everything inside the existing coverage/mypy scope.

## 4. Data model

**No model or migration changes.** `GuildAnnouncement` already has `title`, `body`, `expires_at`, `published_at`, `author`, and an `.active()` manager method (used at `hub/views.py:361`, `:407`). `GuildFAQItem` and `GuildLink` already have their `inlineformset_factory` formsets. The only "model-layer" touch is the two `extra=1 → extra=0` edits in `hub/forms.py`, which are form configuration, not schema.

## 5. Business logic (thin views, no new fat-model logic)

All three new views are thin glue — validation stays in the existing Django forms, persistence stays in the formset/form `.save()`. Each is gated by `_require_can_edit_guild` first.

- **`guild_faq_save(request, pk)`** — `@login_required`, `@require_POST`.
  Gate → `GuildFAQItemFormSet(request.POST, instance=guild, prefix="faq")` → if valid, `.save()` and `messages.success(request, "FAQ saved.")`; else `messages.error(request, "Couldn't save the FAQ — check the highlighted fields.")`. Redirect to `hub_guild_edit?tab=content` either way (errors re-render via the edit page; see §6 error note). Side effects: none beyond the save (no notification — FAQ is page content).
- **`guild_links_save(request, pk)`** — identical shape with `GuildLinkFormSet(prefix="links")` and "Links saved." Redirect to `hub_guild_edit?tab=content`.
- **`guild_announcement_edit(request, pk, announcement_pk)`** — `@login_required`. Gate → fetch `get_object_or_404(GuildAnnouncement, pk=announcement_pk, guild=guild)`.
  - **GET** (HTMX): render `partials/guild_announcement_edit_form.html` with `GuildAnnouncementForm(instance=announcement)` into the modal body.
  - **POST** — on **valid** `.save()`, build the success response from `partials/_guild_announcement_row.html` (the updated row, with `hx-swap-oob="true"` on the row's `id="announcement-row-<pk>"`) and attach **three things to that one response**: (a) `trigger_toast(response, "Announcement updated.", "success")` for the toast; (b) an **`HX-Trigger` header that dispatches `close-modal` with detail `edit-ann-<pk>`** — this is what actually closes the modal, because the shared `components/modal.html` only listens for `@close-modal.window`/Escape/outside-click and has **no** `@htmx:after-request` auto-close of its own. Both must ride the same response. (`trigger_toast` already sets an `HX-Trigger`/`HX-Trigger-After-Settle` payload; the `close-modal` event is added to the same trigger payload so one header carries both — verify the helper's merge behavior when building, or use `HX-Trigger-After-Settle` for `close-modal` to avoid clobbering the toast trigger.) On **invalid**, re-render the modal form partial with errors (status 200, modal stays open, no close trigger). `published_at`/`author` are untouched on edit.

`guild_edit` (`hub/views.py:509`) keeps instantiating both formsets for the **GET render only** (the template still needs `faq_formset`/`link_formset` to draw the rows). Its **POST branch** drops `faq_formset`/`link_formset` handling — the main form now covers only Basic/Meetings/Images (`form` + gallery files). This is a deletion of the two `.is_valid()`/`.save()` lines for the formsets, leaving `form` + `guild.add_gallery_images(...)`.

## 6. UI / UX

### Screen A — Guild edit page, **FAQ & Links** tab (`templates/hub/guild_edit.html`, `section === 'content'`)

- **Layout & container:** Inline editors on the page (data entry, many rows) — correct per the interaction table. The key change: the FAQ block and the Links block move **out** of the main `<form>` (which opens at `:18`, closes at `:190`) into two **independent sibling `<form>`s**, placed structurally like the Announcements/Staff forms (which already live outside the main form). Each is still inside the `x-show="section === 'content'"` panel and each `<div class="hub-card">` keeps its current padding.
  - FAQ form: `<form method="post" action="{% url 'hub_guild_faq_save' guild.pk %}" class="hub-form">` + `{% csrf_token %}` + `{{ faq_formset.management_form }}` + rows + clone `<template>` + "+ Add a question" + **"Save FAQ"**.
  - Links form: `<form method="post" action="{% url 'hub_guild_links_save' guild.pk %}" class="hub-form">` + `{% csrf_token %}` + `{{ link_formset.management_form }}` + rows + clone `<template>` + "+ Add a link" + **"Save Links"**.
- **Components used:** `components/form_field.html` (question/answer; label/url), the existing clone-`<template>` + "+ Add" JS pattern. No new components.
- **The controls, named explicitly:**
  - **Save (FAQ):** a `pl-btn pl-btn--primary` button labelled **"Save FAQ"** — the **same class family as the main-form "Save Changes"** (`guild_edit.html:187`), so the per-section Saves read as the same primary action and don't look like a different control. It sits at the bottom of the FAQ card, `style="margin-top:1rem;"`, `type="submit"` inside the FAQ form. Posts to `hub_guild_faq_save`. Feedback: full-page redirect back to `…/edit/?tab=content` with the Django success message "FAQ saved." rendered by the hub messages region.
  - **Save (Links):** a `pl-btn pl-btn--primary` button labelled **"Save Links"** (same class family, same reasoning), same placement/feedback, posts to `hub_guild_links_save`, message "Links saved."
  - **"+ Add a question" / "+ Add a link":** unchanged from today (`hub-btn hub-btn--sm`, `margin-top:1rem`) — clones the hidden `<template>` of `empty_form`, replaces `__prefix__` with the new index, bumps `id_faq-TOTAL_FORMS` / `id_links-TOTAL_FORMS`. With `extra=0`, no perpetual blank row.
  - **Per-row Delete (saved row):** **FAQ must be fixed** to match Links — replace the toggle at `guild_edit.html:98` (`{% include "components/form_field.html" with field=f.DELETE … %}`) with the hidden-field-behind-a-button pattern: `<div style="display:none;">{{ f.DELETE }}</div>` + a real `pl-btn pl-btn--danger pl-btn--sm` button, `style="margin-top:0.75rem;"`, labelled **"Delete this question"**. Its `onclick` flips the hidden DELETE checkbox and `this.form.requestSubmit()` — but **note**: since FAQ is now its own form posting to `hub_guild_faq_save`, the button no longer needs the `this.form.after.value = 'edit'` trick the Links button uses today (`:146`); the FAQ form always redirects back to `?tab=content`. The Links Delete button keeps working but the `after`-field line is dropped from it too (it's no longer inside the main form). So: deleting a saved row saves **only that section** and returns to the same tab — no lost work elsewhere, because other sections have their own forms now.
  - **Per-row Remove (unsaved cloned row):** `pl-btn pl-btn--danger pl-btn--sm` with `style="margin-top:0.75rem;"`, `onclick="this.closest('.hub-card').remove();"`. Links' template Remove already has this (`:166`); **the FAQ template's Remove (`:114`) is mis-styled — it's missing the `margin-top:0.75rem`**, so it sits flush against the answer field above it. Add the margin to the FAQ Remove so it matches Links and clears the field above (FRONTEND *Editable Lists* / Rule 7).
  - **Margin recap (both buttons, both sections):** the saved-row **Delete** and the cloned-row **Remove** in *both* the FAQ and Links templates carry `margin-top:0.75rem` so neither sits flush against the field above it. This is the one styling bug to correct in the FAQ section beyond the toggle→button swap.
  - **Page-wide "Save Changes":** at `:186` change `x-show="section !== 'announcements' && section !== 'staff'"` to also exclude `'content'`: `x-show="section !== 'announcements' && section !== 'staff' && section !== 'content'"`. So on the FAQ & Links tab there is no page-wide Save — each section saves itself. (Cancel link stays where it is, only on the Basic/Meetings/Images tabs.)
- **States:**
  - **Empty (no FAQs):** with `extra=0` the rows loop renders nothing. Implement via an **`{% empty %}` clause inside the existing `{% for f in faq_formset %}` rows loop** — `{% empty %}<p class="hub-text-muted">No questions yet. Add your first.</p>` — which is the cleanest mechanism with `extra=0` (the loop is empty exactly when there are no rows). Same for Links: `{% empty %}<p class="hub-text-muted">No links yet. Add your first.</p>`. **Note it's a server-render-time state:** the message renders when the page loads with zero rows; a DOM-only "+ Add" click appends a row beside it but won't remove the message until the next page load (after the first real Save). This is acceptable — the user is mid-add and the "+ Add" button is right there; we deliberately don't add JS to hide it.
  - **Loading:** none — full-page POST.
  - **Error:** if a row fails validation (e.g. a required field), `guild_faq_save` redirects back with `messages.error(...)` "Couldn't save the FAQ — check the highlighted fields." (Because this is a redirect, inline per-field error highlighting is not preserved across the redirect; the message tells the user to re-check. Acceptable for these two/three-field rows — and `extra=0` removes the most common failure, the blank-row block. If finer error display is wanted later, switch to render-in-place; deferred, see §10.)
  - **Success:** redirect to `?tab=content` + green Django message; the saved rows re-render from the DB.
- **Dark + light:** No new colors. Reuses `hub-card`, `pl-btn--primary`, `pl-btn--danger`, `hub-btn--sm`, and `components/form_field.html` (which wraps controls in `.hub-form-group`, so inputs/textareas inherit theme input tokens — no white-box risk). The FAQ `answer` is a `Textarea` already routed through `form_field.html`, so it's wrapped. Verify both themes.
- **Mobile:** Links rows already use `flex-wrap:wrap` with `min-width:200px` columns; FAQ rows stack vertically. No fixed widths. Buttons are full tap targets. No change needed.

### Screen B — Public guild page, **Gallery tab** (`templates/hub/guild_detail.html`)

- **Layout & container:** Add a new tab to the existing tab strip and move the gallery into its own panel. No modal, no form — read-only display.
- **Tab strip (`:100–106`):** after the FAQ tab button, add — guarded the same way as FAQ:
  ```django
  {% if gallery_images %}<button type="button" class="vote-tab" :class="section === 'gallery' ? 'vote-tab--active' : ''" @click="section = 'gallery'">Gallery</button>{% endif %}
  ```
- **Move the gallery panel:** delete the `{% if gallery_images %}<section>…{% endif %}` block at `:176–181` from inside the Overview `<main>` and re-add it as its own top-level panel sibling (next to the FAQ panel at `:314`):
  ```django
  {% if gallery_images %}
  <div x-show="section === 'gallery'" x-cloak>
      <section class="pl-guild-section">
          <h2 class="pl-guild-section__h2">Gallery</h2>
          {% include "hub/_guild_gallery.html" %}
      </section>
  </div>
  {% endif %}
  ```
  Reuse `hub/_guild_gallery.html` unchanged (it consumes `gallery_images`, supplied by the view at `:404`).
- **Components used:** existing `_guild_gallery.html` (grid + lightbox). No new components.
- **The controls:** none — it's a display tab. The lightbox open/close already lives in the partial.
- **States:** No empty state needed — the tab only exists when `gallery_images` is truthy (matches the FAQ tab's pattern). Loading: n/a (server-rendered, not HTMX-lazy). Error: n/a.
- **Dark + light:** no new CSS — `pl-guild-gallery` / `pl-guild-lightbox` already themed. **FRONTEND Rule 12:** the new `x-show="section === 'gallery'"` panel must **not** carry an inline `display` style (Alpine strips it on reveal). It carries `x-cloak` only, exactly like the FAQ/schedule panels — the gallery grid's `display` lives in the `.pl-guild-gallery` CSS class, so it reflows correctly on first show.
- **Mobile:** the `.pl-guild-gallery` grid already reflows (confirm in `calendar.css`/hub gallery styles at narrow width); no change.

### Screen C — Guild edit page, **Announcements** tab — Edit an announcement (`templates/hub/guild_edit.html`, `section === 'announcements'`)

- **Layout & container:** **Modal** (`components/modal.html`), per the interaction table (1–3 fields = title, body, expires_at). The modal body is HTMX-loaded with the prefilled form.
- **Recent Announcements list (`:205–220`):** each row currently has a single Delete button (`:214`). Add an **Edit** button beside it. Both buttons share a small action cluster on the right of the row:
  ```django
  <button type="button" class="pl-btn pl-btn--secondary pl-btn--sm" style="min-height:unset; padding:0.25rem 0.6rem;"
          @click="$dispatch('open-modal', 'edit-ann-{{ a.pk }}')"
          hx-get="{% url 'hub_guild_announcement_edit' guild.pk a.pk %}"
          hx-target="#edit-ann-{{ a.pk }}-body" hx-swap="innerHTML">Edit</button>
  ```
  **The `hx-target` must be `#edit-ann-{{ a.pk }}-body`** — `components/modal.html` hard-codes its body element id as **`{{ modal_id }}-body`** (`modal.html:31`), so with `modal_id="edit-ann-{{ a.pk }}"` the real body id is `edit-ann-<pk>-body`. (A `#edit-ann-body-<pk>` target — token order swapped — matches nothing and the modal loads empty.) (Delete button at `:214` is unchanged — it still opens the existing per-row `confirm_modal`.) Wrap each row in `id="announcement-row-{{ a.pk }}"` so the OOB swap can replace it after a successful edit.
- **One modal per row:** alongside the per-row confirm modals (`:223–228`), include one edit modal per announcement:
  ```django
  {% include "components/modal.html" with modal_id="edit-ann-"|add:pks modal_title="Edit announcement" modal_size="md" %}
  ```
  The HTMX `hx-get` above targets that modal's component-provided body, `#edit-ann-<pk>-body`. **No wrapper-div fallback is needed — `modal.html` already parameterizes the body id** (`modal.html:31`), so the edit form partial does *not* need to wrap its own `<div id=…>`.
- **Components used:** `components/modal.html`; `components/form_field.html` (title, body Textarea, expires_at date); `trigger_toast()`; HTMX `hx-get`/`hx-post`.
- **New partial `partials/guild_announcement_edit_form.html`:**
  ```django
  <form hx-post="{% url 'hub_guild_announcement_edit' guild.pk announcement.pk %}" hx-swap="none">
    {% csrf_token %}
    {% include "components/form_field.html" with field=form.title %}
    {% include "components/form_field.html" with field=form.body %}
    {% include "components/form_field.html" with field=form.expires_at %}
    <div style="display:flex; gap:0.75rem; margin-top:1rem;">
      <button type="submit" class="pl-btn pl-btn--primary">Save changes</button>
      <button type="button" class="pl-btn pl-btn--secondary" @click="$dispatch('close-modal', 'edit-ann-{{ announcement.pk }}')">Cancel</button>
    </div>
  </form>
  ```
- **The controls, named explicitly:**
  - **Edit (row):** opens the modal and HTMX-loads the prefilled form.
  - **Save changes (modal):** `pl-btn pl-btn--primary`, posts to `guild_announcement_edit`. On success the view returns the updated row partial with `hx-swap-oob="true"` (replacing `#announcement-row-{{ pk }}`) **plus**, on the same response, `trigger_toast(…, "Announcement updated.", "success")` for the toast **and an `HX-Trigger` (or `HX-Trigger-After-Settle`) that fires `close-modal` with detail `edit-ann-<pk>`** to close the modal. **The shared `components/modal.html` does NOT auto-close on `htmx:after-request`** — it only listens for `@close-modal.window`/Escape/outside-click. (The EYOP modal at `guild_detail.html:~599` *is* a hand-rolled modal that wires its own `@htmx:after-request` close handler; the reusable component does not, so we must drive the close from the server via the `close-modal` trigger. This is the single mechanism that closes this modal — without it the user is stranded on a saved-but-still-open dialog.)
  - **Cancel (modal):** `$dispatch('close-modal', …)` — closes, no save.
- **States:**
  - **Empty:** n/a for a single announcement; the list's empty state ("No announcements yet.") already exists at `:217`.
  - **Loading:** while the `hx-get` is in flight, the modal body shows a brief placeholder (HTMX swaps innerHTML; a simple "Loading…" can seed the `#edit-ann-<pk>-body` element before the swap). Keep it lightweight.
  - **Error:** invalid POST re-renders the form partial **with field errors** inside the open modal (status 200) — `form_field.html` shows the per-field error markup. The user fixes and resubmits; no redirect, no 500.
  - **Success:** toast "Announcement updated." + the row's title/date update in place via OOB swap + modal closes.
- **Dark + light:** `expires_at` is `<input type="date">`, rendered through `form_field.html` → `.pl-form-group`. **The date-picker dark-mode handling is already in place via the field scope, not via per-field `filter:invert`/`showPicker`:** `components.css` sets `color-scheme: dark` on `.pl-form-group` controls (`components.css:221`) — which natively themes the `::-webkit-calendar-picker-indicator` and the picker chrome — with the light override under `[data-theme="light"] .pl-form-group input[type="date"]` (`components.css:663`). So because `expires_at` rides `form_field.html`, it is already theme-correct in both modes; **do not add** ad-hoc `filter:invert(1)`/`showPicker()` to this field. No inline `background`/`color` on any control — all routed through `form_field.html`. Build-time check: open the edit modal and **verify the date field in both dark and light themes** (this is the one thing to eyeball, not re-engineer).
- **Mobile:** `modal--md` is responsive; the three stacked fields and two buttons are usable one-handed. No table involved.

## 8. Build order (phased; each phase ships green)

Each phase is independently shippable (full suite + `ruff format .` + `ruff check .` + mypy green), run in the `plfog-web` Docker image.

1. **FAQ & Links own forms (the core ask).**
   - `hub/forms.py`: `extra=1 → extra=0` on both formsets.
   - `hub/views.py`: add `guild_faq_save` + `guild_links_save`; drop formset POST handling from `guild_edit` (keep GET instantiation).
   - `hub/urls.py`: `hub_guild_faq_save`, `hub_guild_links_save`.
   - `guild_edit.html`: pull FAQ + Links out of the main form into their own `<form action=…>` blocks; fix FAQ Delete to the hidden+button pattern; add FAQ template's Remove button; add empty-state lines; widen the page-wide Save `x-show` to exclude `content`.
   - Tests: faq/links save views (add/edit/delete via own form, gating, `extra=0` no-blank-row-block), FAQ DELETE renders hidden+button not toggle.
2. **Gallery tab.**
   - `guild_detail.html`: add conditional Gallery tab button; move the gallery `<section>` into its own `x-show="section === 'gallery'"` panel.
   - Tests: gallery tab present iff `gallery_images`.
3. **Announcements editable.**
   - `hub/views.py`: `guild_announcement_edit` (GET form / POST update → toast + OOB row).
   - `hub/urls.py`: `hub_guild_announcement_edit`.
   - New partials: `guild_announcement_edit_form.html`, `_guild_announcement_row.html` (and refactor the existing row markup at `:209–215` to include it so create/edit share one row template).
   - `guild_edit.html`: add Edit button + per-row edit modal; wrap rows in `id`.
   - Tests: edit prefill, update persists, gating (403 for non-editor), modal partial renders, OOB row + toast.
4. **Housekeeping.** Bump `plfog/version.py` `VERSION` and prepend a member-friendly `CHANGELOG` entry (sketch below).

> Spec only — do not build until approved.

**Changelog sketch (member-facing, plain language):**
- "Editing your guild's FAQ and Links is simpler — each now has its own Save button, so you can add or fix a question without scrolling to the bottom of the page."
- "Guild photo galleries now get their own tab on the guild page, so your pictures are easy to find."
- "You can now edit an announcement after posting it — fix a typo or update the details without deleting and starting over."

## 9. Testing

BDD `*_spec.py` under `tests/hub/`, `describe_*`/`it_*` (note: `context_*` is **not** collected — use `describe_*` for nested blocks), factory-boy from `tests/membership/factories.py` (`GuildFactory`, `GuildFAQItemFactory`, `GuildLinkFactory`, `GuildAnnouncementFactory` all present), ≥98% coverage gate, run in `plfog-web` Docker (`--no-cov` for a subset).

**FAQ / Links save views (`guild_edit_spec.py`):**
- `guild_faq_save` adds a new FAQ row (POST with one new form) → persists, redirects to `?tab=content`, success message.
- Editing an existing FAQ row's text persists.
- Deleting a saved FAQ row (DELETE checked) removes it.
- `extra=0`: a POST with `TOTAL_FORMS` covering only real rows saves cleanly — no blank-row validation block (the bug this fixes).
- Same four for `guild_links_save`.
- **Links delete-isolates-its-own-row (regression for the split):** POST to `guild_links_save` with one of several saved link rows flagged `DELETE` → only that link is removed, the others survive, and the FAQ rows are untouched (they're in a *different* form now). This guards the behavior change from leaving the main form: the Links Delete button posts to its own `hub_guild_links_save` action and **no longer carries the `this.form.after.value='edit'` line** (the hidden `after` input lived only in the main form, `:20`, and is gone from these split forms). Assert the rendered Links Delete button's `onclick` does **not** reference `this.form.after`.
- Gating: a non-editor user → 403 from `_require_can_edit_guild` on both views.
- **Template assertion:** the FAQ row renders a hidden `{{ f.DELETE }}` + a `pl-btn--danger pl-btn--sm` "Delete this question" button — **not** a `components/toggle.html` switch (assert the toggle markup is absent and the danger button present).
- **Template assertion:** the page-wide "Save Changes" is hidden on the content tab (assert the `x-show` excludes `'content'`).
- **Template assertion:** the announcement edit modal's `hx-target` is `#edit-ann-<pk>-body` (matches `modal.html`'s `{{ modal_id }}-body`), guarding blocker 1 from regressing.

**Gallery tab (`guild_tabs_spec.py`):**
- With ≥1 gallery image: the response contains a Gallery tab button and the `section === 'gallery'` panel.
- With zero gallery images: neither the tab button nor the panel renders (and the gallery no longer appears inside the Overview panel).

**Announcement edit (`guild_announcements_spec.py`):**
- GET `guild_announcement_edit` (editor) renders the prefilled form partial (title/body/expires_at values present).
- POST updates title/body/expires_at on the existing announcement; `published_at` and `author` unchanged.
- POST returns a toast trigger header, an OOB row fragment, **and an `HX-Trigger`/`HX-Trigger-After-Settle` carrying `close-modal` with detail `edit-ann-<pk>`** (guards blocker 2 — the only thing that closes the modal). Assert both the toast and the `close-modal` event are present on the same response.
- Invalid POST (blank title) re-renders the form partial with the field error, status 200, announcement unchanged, **and no `close-modal` trigger** (the modal must stay open so the user can fix the error).
- Gating: non-editor → 403 on both GET and POST.
- A wrong-guild `announcement_pk` → 404.

**Gotchas:** member-gated views need a `MembershipPlanFactory()` seeded before login (the Member-creation signal skips without a plan). `expires_at` is a date (no tz window math here), but assert the rendered value uses the input's `YYYY-MM-DD` format.

## 10. Open / deferred

- **HTMX inline save for FAQ/Links** (toast instead of redirect, in-place per-field error highlighting): noted as polish, deferred. The redirect-and-message path is the spec default for consistency with the rest of this edit page and matches the FRONTEND "full-page → Django messages" rule. Revisit only if leads report the redirect feels heavy.
- **FAQ/Links Save-error fidelity:** because save errors redirect, per-field inline errors aren't preserved across the redirect — the user sees a "check the highlighted fields" message. With `extra=0` the common blank-row block is gone, so this is acceptable; render-in-place is the upgrade path if needed.
- **Announcement create → modal:** out of scope. The existing create form stays an inline form at the top of the Announcements tab; only **edit** moves to a modal. (Could later unify create+edit into one modal, but YAGNI now.)
- **Gallery lazy-loading / reorder on the public page:** out of scope — the gallery stays server-rendered and read-only on the public page; upload/reorder remains on the edit page's Images tab.
- **No model/migration work**, no notification triggers for FAQ/Links/announcement-edit (these are page-content edits, not member-facing events).
