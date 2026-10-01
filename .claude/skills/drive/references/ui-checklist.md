# UI completeness checklist

The bar the **ticket** must clear before the engineer sees it: a real member could finish the task
without hitting a dead end. The PO designs against this and names the answers in the ticket's UX
decisions; it is not a thing the engineer is left to work out. Read it only when the ticket touches a
screen. Everything here is downstream of `FRONTEND.md`; when in doubt, that file wins.

Apply it **per screen the feature touches**, not once globally. A ticket that says "add an FAQ editor"
has not passed until it names the Add button, the Delete control, the Save action, and how each state
looks.


## 1. The famous failure — list editors

This is the failure this checklist exists to prevent: a form to manage a list of things (FAQs, links, hours, members,
prices) that you can't actually use because it's missing the obvious controls.

For **any** "add/edit a list of items" feature, the spec must explicitly name all three:

- [ ] **A "+ Add ___" button** that adds a new blank row on demand. Built the plfog way: `extra=0` on the
      formset (so no perpetual blank row blocks Save), plus a button that clones a hidden `<template>` of
      `formset.empty_form`, swaps `__prefix__` for the new index, and bumps `TOTAL_FORMS`.
- [ ] **A per-row Delete/Remove control** — a real `pl-btn pl-btn--danger pl-btn--sm` *button*, never a toggle
      switch. Saved rows: flip the hidden `DELETE` field and submit the form (preserves other edits). Unsaved
      cloned rows: just remove the DOM node. Give it `margin-top:0.75rem` so it clears the field above.
- [ ] **A Save action wired to the form** — visible, obviously the primary action, and the spec says what it
      submits and what feedback follows (toast or redirect).

Canonical implementations to copy: the FAQ and Links editors, and the orientation recurring-hours editor,
all in `templates/hub/guild_edit.html`. The spec should point at these.

## 2. Forms

- [ ] Every form has a **submit/Save button**, and the spec states where it sits and what happens on submit.
- [ ] Fields use **`components/form_field.html`** — never raw `{{ field }}` with hand-rolled label/error HTML.
- [ ] **Boolean fields are toggle switches** (`components/toggle.html`, or `form_field.html` which auto-detects
      checkboxes). Never a raw checkbox, never custom toggle HTML. The one exception is a formset's `DELETE`
      field, which is hidden behind a real Delete button (see §1).
- [ ] Validation lives in a **Django form** (`clean_*`), not the view — and the spec says what's validated and
      the error message a user sees.
- [ ] Form length picks the right container (FRONTEND.md interaction table): **1–3 fields → modal + toast**;
      **4+ fields → inline form or dedicated page**; **secondary/optional form → toggle-revealed (`x-show`),
      closed by default.**
- [ ] Required vs optional fields are clear, and hints (`field_hint`) explain anything non-obvious.

## 3. Destructive actions

- [ ] Delete / void / deactivate / cancel goes through **`components/confirm_modal.html`** with a real message
      naming the consequence — never an unguarded button.
- [ ] The destructive button uses `pl-btn pl-btn--danger pl-btn--sm` styling, not a raw full-size button.
- [ ] The spec states what happens to related data (cascade? soft delete? frees a seat?).

## 4. Feedback & states — never just the happy path

For every screen, the spec must describe:

- [ ] **Empty state** — what the user sees with zero items ("No FAQs yet. Add your first question."), not a bare
      blank region.
- [ ] **Loading state** — for HTMX swaps, what shows while in flight.
- [ ] **Error state** — validation errors, failed actions, expired/invalid tokens land on a friendly message,
      not a 500.
- [ ] **Success feedback** — HTMX mutations return a **toast** via `trigger_toast()` (don't redirect with Django
      messages for HTMX); full-page form posts use Django messages.
- [ ] No dead ends — every screen has a way back / cancel, and every action tells the user it worked.

## 5. Dark / light mode

Both themes must work. The recurring failures (FRONTEND.md rules 7, 13, 14):

- [ ] Use **theme tokens** only — never hardcode colors. `--surface` is **not** a token; `var(--surface,#fff)`
      silently falls back to a white box with invisible text on dark.
- [ ] **Never inline `background`/`color` on a form control** (`<input>`/`<select>`/`<textarea>`). Wrap it in the
      surface's field scope so it inherits theme-correct input tokens: `.hub-form-group` (hub), `.reg-field`
      (public classes), `.bk-field` (book account). A bare un-wrapped `<textarea>` renders as a default white box.
- [ ] Style **`select option { background; color }`** — native option popups don't inherit the select's colors.
- [ ] **`<input type="date">` / `type="time">`** need dark-mode help: invert the picker icon
      (`filter: invert(1)`), reset under `[data-theme="light"]`, and make the whole field open the picker
      (`showPicker()` in a try/catch).
- [ ] Sidebar uses `--hub-sidebar-*` tokens, never `--color-navy` directly (it won't switch with the theme).
- [ ] The spec explicitly says "verify both themes."

## 6. Mobile

- [ ] Layout **reflows** on narrow screens — no horizontal scroll, no fixed widths that overflow.
- [ ] **Tables** degrade gracefully (stack into cards, or scroll within a contained region) rather than blowing
      out the viewport.
- [ ] Tap targets are real (buttons, not tiny icons), and modals/forms are usable one-handed.
- [ ] Spacing uses the **8px grid** (`0.25/0.5/0.75/1/1.25/1.5/2rem`) so rhythm holds at every width.

## 7. Spacing & layout hygiene

- [ ] **No clashing margins** — every control clears the element above it; nothing sits flush against the field
      before it. (The delete-button `margin-top:0.75rem` rule is the canonical example.)
- [ ] **No inline styles** except a genuine one-off layout nudge — add a `pl-` CSS class instead.
- [ ] **Never put `display` in an inline `style` on an `x-show` element** — Alpine strips inline `display` on
      reveal, so `display:flex/grid` reverts to default and collapses the layout. Put display in a CSS class.
- [ ] New component classes use the **`pl-` prefix**; new styles go in the right CSS file (`style.css` public,
      `hub.css` hub, `components.css` shared, `unfold-custom.css` admin).

## 8. Components — reuse, don't reinvent

The spec should name the existing component for each piece (FRONTEND.md component library):

- [ ] Modals → `components/modal.html` (never one-off overlay HTML).
- [ ] Confirmations → `components/confirm_modal.html`.
- [ ] Toggles → `components/toggle.html`.
- [ ] Fields → `components/form_field.html`.
- [ ] Toasts → `trigger_toast()` server-side / `$dispatch('show-toast', …)` client-side (already in base — don't
      re-include).
- [ ] Sortable/filterable/paginated lists → `prepare_table()` + `{% sort_header %}` + `table_pagination.html`.

## 9. Emails & notifications the feature sends

If the feature sends any email or in-app/push notification, each one must help the recipient act, not just inform (FRONTEND.md → *Email Templates*):

- [ ] The **subject noun** (class, guild, event, registration, order) is a **link to its detail/management page**, not dead text.
- [ ] There's **one obvious primary CTA** plus the helpful secondary links (see details, add to calendar, billing history) — no dead ends.
- [ ] Any **human-written content** the email should carry (instructor note, lead's message) is surfaced, guarded so it only shows when set.
- [ ] Links are **absolute URLs** (`_absolute_url()` / the spine resolver), the **branded shell** is used (no stale "BETA"), and **subject + body share one timezone**.
- [ ] Both **`.txt` and `.html`** versions exist and say the same thing.
- [ ] The send is wired through `emit()` with a **unique `period`** so it actually delivers (and dedupes), not just the first time.

## 10. The user-lens sanity pass

Finally, read the whole feature as the person who'll use it:

- [ ] Is the **primary action obvious** on every screen?
- [ ] Can the user **complete the whole task** start to finish without guessing or getting stuck?
- [ ] Is anything **half-built** — a thing you can create but not edit, edit but not delete, see but not act on?
- [ ] Would a **non-technical makerspace member** (or guild lead) understand the labels and flow?
- [ ] Is it **simple** — the fewest screens and clicks that do the job, nothing speculative bolted on?
