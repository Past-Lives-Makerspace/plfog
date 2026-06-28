# Orientations tab: a "Save Hours" button + the margin fix

**Commit 2 of release 0.20.x.** Surface: the Orientations section (inlined into `guild_edit.html` by commit 1; markup
originates from `templates/hub/orientation_settings.html`). Views/forms in `hub/`.

## Two fixes

### A. Recurring hours needs its own "Save Hours" button (match FAQ/Links)

**Now:** the recurring-hours formset lives *inside* the one big orientation-settings `<form>`, so it only saves via the
page's "Save orientation settings" button — there's no dedicated control next to "+ Add recurring hours". The FAQ and
Links editors, by contrast, are each their **own** `<form action="…save">` with their own primary Save button
(`hub/views.py:guild_faq_save` / `guild_links_save`; templates `guild_edit.html:125-191` / `193-252`). Match that.

**Build:**
- New view `guild_orientation_hours_save(request, pk)` in `hub/views.py`, mirroring `guild_faq_save`: editor/orientation-
  manager permission check (`_require_can_manage_orientations`), bind `OrientationAvailabilityFormSet(request.POST,
  instance=guild, prefix="rules")`, `formset.save()`, then `orientations.generate_slots(guild=guild)` (same side effect
  the combined save does today, so saved hours materialize slots immediately), `messages.success("Recurring hours
  saved.")` / friendly error, redirect to `guild_edit?tab=orientations`.
- New URL `hub_guild_orientation_hours_save` in `hub/urls.py`.
- Pull the recurring-hours block out of the settings `<form>` into its **own** `<form method="post"
  action="{% url 'hub_guild_orientation_hours_save' guild.pk %}">`, keeping the existing `extra=0` formset, the
  `__prefix__` clone-the-empty-template "+ Add recurring hours" button, and the per-row Delete pattern
  (hidden `{{ form.DELETE }}` + a real `pl-btn pl-btn--danger pl-btn--sm` Delete button that `requestSubmit()`s; new
  unsaved rows use the client-side Remove). Add the primary **"Save Hours"** button (`pl-btn pl-btn--primary`) for this
  form, placed with the "+ Add recurring hours" control so the two sit together (the user asked for Save *next to* Add) —
  Add button then Save button, matching the FAQ/Links footer rhythm (`margin-top:1rem`).
- The remaining orientation-settings `<form>` no longer contains the hours formset; its own "Save orientation settings"
  button stays. Remove the now-redundant `generate_slots` coupling from the settings save only if hours moved out — keep
  `generate_slots` in BOTH saves is harmless, but cleanest: settings save no longer needs it (it doesn't change hours).
  Decision: keep `generate_slots` only in the hours save and the settings save where seats/duration affect slots — to be
  safe and match current behavior, call `generate_slots` in both. (It's idempotent regeneration.)

### B. "Who runs orientations" must not touch the Save button

**Now:** `orientation_settings.html:108-121` — the "Save orientation settings" button is immediately followed by the
"Who runs orientations" `hub-card`, which has `margin-bottom` but **no `margin-top`**, so it visually butts against the
button.

**Fix:** add `margin-top:1rem;` (8px-grid; 16px) to that card's inline style so it sits clearly above… wait — it sits
*below* the Save button today. The requirement: the "Who runs orientations" section should be **above** the Save button
and not touching it. So **reorder**: move the "Who runs orientations" card to *before* the "Save orientation settings"
button within the settings section, and give it `margin-bottom:1rem` clearance from the button. End state: section content
→ "Who runs orientations" card (with bottom margin) → Save button, with daylight between them. No inline color; reuse the
existing `hub-card`/`hub-text-muted`/`hub-btn` classes.

## UI / UX completeness

- "Save Hours" is a visible primary submit wired to the hours form; "+ Add recurring hours" adds a row; each existing row
  has a real margin-spaced Delete button (auto-submits, no confirm modal — matches the established editable-formset
  rule); new rows have client-side Remove. Empty state: "No recurring hours yet — add your first window."
- Saving hours redirects back to the Orientations tab with a success message; invalid times show the field error
  (`end_time` must be after `start_time`, already enforced in `OrientationAvailabilityForm.clean`).
- "Who runs orientations" sits above the Save button with clear margin; "Manage staff →" link unchanged.
- Dark/light: no inline `background`/`color`; mobile: the hours row already `flex-wrap`s.

## Tests

- `guild_orientation_hours_save`: saves new/edited rows, deletes a row via DELETE, regenerates slots, redirects to
  `?tab=orientations`; permission gate enforced; invalid time range re-renders with the error and saves nothing.
- Settings save still works independently of hours (saving settings doesn't wipe hours and vice versa).
- BDD `*_spec.py`, `describe_`/`it_`, factory-boy.

## Out of scope

- Changing the recurring-hours data model or `generate_slots` logic. Pure UI/save-split + a margin/order fix.
