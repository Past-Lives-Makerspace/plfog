# Review Notes Contrast Fix (class-review screen) — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-06-24
**Surface:** FOG hub `pastlives.test` — the admin/guild-lead class-review page (`templates/classes/admin/class_review.html`, which extends `hub/base.html`). Same template also serves the tokenized reviewer URL (`/classes/review/<token>/`).
**Related:** none.

---

## 1. Summary

When a reviewer approves, requests changes on, or declines a submitted class, they type feedback for the instructor into a "Notes for the instructor" box. On the dark "Obsidian" theme that box currently renders as a browser-default **white rectangle with near-invisible pale text** — a reviewer literally can't read what they're typing. This fix wraps that one control so it inherits the hub's theme input colors, making the notes box legible on **both** the dark and light themes. No behavior changes; it's purely a contrast/legibility fix.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Which fix approach — `form_field.html` vs a manual `.hub-form-group` wrapper | **Render the `notes` field through `components/form_field.html`.** FRONTEND.md Rule 1 mandates the component over raw `{{ field }}` + hand-rolled label/error markup, and this page currently hand-rolls all three (label at `:58-60`, the bare control at `:62`, and a separate top-of-form error loop at `:40-46`). The component gives a theme-correct wrapper (`.pl-form-group`), the label, and the per-field error list in one include — and lets us delete the hand-rolled markup. |
| What `form_field.html` does **not** add here | It does **not** produce a persistent visible hint line. `form_field.html` renders `field.help_text` into `.pl-field-hint` (`form_field.html:39-43`) — but `ClassReviewDecisionForm.notes` has **no `help_text`** (`classes/forms.py:399-405` sets only `label` and a widget `placeholder`). So the only on-screen guidance is the in-textarea placeholder ("Optional on approve; required on request-changes and decline."), which **vanishes the instant the reviewer types**. The current hand-rolled block has the same limitation, so this is **not a regression** — but the rationale must not claim the component "shows the placeholder/hint." |
| Should the "optional on approve / required on decline" guidance be persistent visible text? | **Recommended: yes.** Add it as `help_text` on the `notes` field — a one-line change in `classes/forms.py` — so `form_field.html` renders it as a persistent `.pl-field-hint` below the box (visible even while typing). This is the proper home for guidance that the form's own `clean()` enforces, and it costs no template work. See §6 (treated as an optional sub-step, not the core fix). |
| Is any new CSS needed? | **No.** `.pl-form-group` (used by `form_field.html`) is already fully styled with theme tokens in `components.css`, which is loaded on hub pages. The fix is template-only (plus the optional one-line `help_text` addition above). |
| Are any controls *other* than `notes` at risk? | **No.** The decision RadioSelect (`:50-55`) uses native radio inputs whose surrounding label text/borders already use theme color vars; there is no other `<input>`/`<select>`/`<textarea>` on the page. `notes` is the sole offender. |
| How the per-field error displays (avoid the duplicate-error trap) | `form_field.html` renders the `notes` error inline (its `.pl-field-errors`). The existing top-of-form loop (`:40-46`) **must be scoped to `form.decision` only** — not left covering all fields (would duplicate the `notes` error) and not deleted (would orphan the `decision` error, since the radios have no error markup of their own). Directive, not a "decide at build" — see §6/§8. |

## 2. What already exists (reuse, don't reinvent)

This is a reuse fix, not new CSS. Everything needed already ships and is loaded on this page.

| Need | Existing thing | Location |
|---|---|---|
| Theme-correct field wrapper + label + error list | `components/form_field.html` (renders into `.pl-form-group`) | `templates/components/form_field.html:26-45` |
| `.pl-form-group` control styling (dark) — `background:var(--hub-input-bg)`, `color:var(--hub-text)`, themed border/focus | `static/css/components.css:182-201` | shared CSS, loaded on hub pages |
| `.pl-form-group` control styling (light override) | `static/css/components.css:654-660` | shared CSS |
| Alternative manual wrapper (`.hub-form-group`) — same tokens | `static/css/hub.css:719-734` (textarea sizing `:736-739`, focus `:741-747`) | hub CSS |
| Input tokens the wrappers consume | `--hub-input-bg` (`#14161f` dark / `#f4f6f8` light), `--hub-input-border`, `--hub-text` | `static/css/hub.css:49-50` (dark), `:108-109` (light) |
| The form being rendered | `ClassReviewDecisionForm` — `notes` is a `forms.CharField(widget=forms.Textarea(...))`, `required=False`; `clean()` adds a `notes` error ("Please leave a note…") when the decision is `changes_requested`/`denied` | `classes/forms.py:383-413` |

**Gap to close:** exactly one — the `notes` textarea at `templates/classes/admin/class_review.html:58-63` is rendered as a bare `{{ form.notes }}` with a hand-rolled `<label>` and **no** field wrapper, so it falls through to the browser default (white box) per FRONTEND.md Rule 13.

## 3. Where the code lives

Template-only change. No CSS, no Python, no migration.

```
templates/classes/admin/class_review.html   # replace the hand-rolled notes block (~:58-63) with
                                             # {% include "components/form_field.html" with field=form.notes %}
```

Home app: `classes`. No `static/css/hub.css` or `components.css` edits are required — `.pl-form-group` already styles inputs/textareas/selects with the theme tokens in both themes (`components.css:182-201` / `:654-660`). There is **no** `<select>` and **no** `<input type="date">`/`type="time"` on this screen, so the Rule 13 `select option { … }` and Rule 14 date-picker handling do not apply here.

## 6. UI / UX  ← completeness checklist, concretely

Walk of the review screen (`templates/classes/admin/class_review.html`), top to bottom of the decision form, after the fix:

- **Screen / partial:** `templates/classes/admin/class_review.html` (one inline `<form method="post">` at `:38-68`; whole page wrapped in `<div class="hub-card">`).
- **Layout & container:** dedicated full page (extends `hub/base.html`), inline form — correct per the FRONTEND.md interaction table (a multi-field decision form, not a 1–3-field modal).
- **Components used:** `components/form_field.html` for the `notes` field (the change). The decision radios stay as the existing hand-rolled `<fieldset>`/`RadioSelect` loop — that is a deliberate styled card-row layout, is theme-legible already, and is out of scope for this contrast fix.

- **The controls, named explicitly:**
  - **Notes textarea** (`form.notes`): becomes `{% include "components/form_field.html" with field=form.notes %}`. After the fix it sits in `.pl-form-group`, so it picks up `background:var(--hub-input-bg)`, `color:var(--hub-text)`, the themed border, and the gold focus ring — legible on dark and light. The component renders the field's label ("Notes for the instructor") and, when present, the `notes` error inline. Delete the hand-rolled `<label>` + bare `{{ form.notes }}` (`:58-63`) it replaces.
    - **Hint guidance (correction):** `form_field.html` does **not** surface the placeholder as a persistent hint. It renders `field.help_text` into `.pl-field-hint` (`form_field.html:39-43`), and `notes` has **no `help_text`** (`classes/forms.py:399-405`) — only a widget `placeholder`, which disappears as soon as the reviewer types. So after the swap there is no always-visible "optional on approve / required on decline" line; the in-box placeholder is the only hint and it's transient. (The current hand-rolled block has this same gap — not a regression.)
    - **Decision (optional sub-step):** to keep that guidance visible while typing, add `help_text="Optional when you approve. Required when you request changes or decline, so the instructor knows what to fix."` to the `notes` field in `classes/forms.py` (one line). `form_field.html` then renders it as a `.pl-field-hint` beneath the box, themed in both modes. This is optional polish layered on the contrast fix, not part of the white-box repair itself.
  - **Decision radios** (`form.decision`, RadioSelect): unchanged. Confirmed legible — native radio inputs inside `<label>` rows whose border/text use `--hub-card-border` / inherited `--hub-text`. Not a white-box risk.
  - **Submit:** existing `<button class="hub-btn hub-btn--primary" type="submit">Submit decision</button>` at `:65`, paired with a `hub-btn--ghost` Cancel link at `:66`. Confirmed wired: the form POSTs to the same review URL; the view records the decision via the model, fires `send_class_review_decision`, and **redirects** back to the review page (`classes/views.py:2108-2112`). This is a full-page POST, so a redirect (not an HTMX toast) is the correct feedback per the interaction table — on return the page shows the "You already decided this …" summary block (`:24-36`), which is the success confirmation. No toast needed; this is not an HTMX request.
  - This screen edits no list/formset, so no "+ Add"/Delete controls and no toggles apply.

- **States:**
  - **Default / empty:** notes box empty, showing its in-box placeholder (which clears on typing), legible on both themes (the whole point). If the optional `help_text` sub-step is taken, a persistent `.pl-field-hint` also shows below.
  - **Error (directive — the half-built risk on this screen):** when the decision is *Request changes* or *Decline* with empty notes, `ClassReviewDecisionForm.clean()` adds a `notes` error ("Please leave a note so the instructor knows what to change."). After the fix, `form_field.html` renders that error inline in `.pl-field-errors` beneath the now-legible, wrapped field. The existing top-of-form error loop (`{% for field in form %}` at `:40-46`) **must be scoped to `form.decision` only** — do **not** ship either of the two wrong outcomes:
    - **Don't leave the loop unscoped** (iterating all fields): the `notes` error would then render **twice** — once at the top of the form and again inline from `form_field.html`. This is the most likely regression and is asserted against in §9.
    - **Don't delete the top loop entirely:** the `decision` radios are a hand-rolled `<fieldset>` (`:47-57`) with **no error markup of their own**, so a scoped top loop is the **only** outlet for the `decision` error. Deleting it would orphan that error (e.g. a POST with no decision selected would fail validation with no visible message). Keep the loop, narrowed to `decision`.
    - Net: exactly one error outlet per field — `decision` via the scoped top loop, `notes` via `form_field.html` inline.
  - **Already-decided:** if `approval.decision` is set, the form isn't rendered at all (`:24` branch) — unaffected.
  - **Loading / success:** standard synchronous POST → redirect; no HTMX in-flight state on this form.

- **Dark + light:** theme tokens only, via `.pl-form-group` — no hardcoded colors, no inline `background`/`color` on the control (Rule 13 satisfied). **Verify both themes** manually after the build: on the hub, toggle Obsidian↔Slate and confirm the notes text is readable in each (dark: cream text on `#14161f`; light: dark text on `#f4f6f8`). No `select`/date control on this page, so Rule 13 option-styling and Rule 14 picker handling don't apply.

- **Mobile:** `.pl-form-group input/select/textarea` is `width:100%`, so the notes box is full-width and tappable on narrow screens; the surrounding `.hub-card` (`max-width:900px;margin:0 auto`) already reflows. No fixed widths introduced.

## 8. Build order (phased; each phase ships green)

One small phase:

1. In `templates/classes/admin/class_review.html`:
   - Replace the hand-rolled notes block (`:58-63` — the `<div>`, the `<label>` with `{{ form.notes.label }}`, and bare `{{ form.notes }}`) with `{% include "components/form_field.html" with field=form.notes %}`.
   - **Scope the top-of-form error loop (`:40-46`) to `form.decision` only** — narrow it, do not delete it (the radios have no error markup of their own; deleting orphans the `decision` error). This prevents the duplicate-`notes`-error bug. (See the §6 error directive.)
   - *(Optional polish)* Add `help_text` to the `notes` field in `classes/forms.py` so the optional/required guidance renders as a persistent `.pl-field-hint`. Skip if guidance-on-placeholder-only is acceptable.
   - Add the render + single-error tests (§9). Bump `plfog/version.py` (VERSION + member-friendly CHANGELOG entry) — see closing note.

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*`, run in the `plfog-web` Docker image (`--no-cov` for the subset). The existing review specs live at `classes/spec/views/class_review_spec.py` (`describe_class_review`) and `classes/spec/views/admin_review_spec.py` — add to the appropriate one.

- **Wrapper-present render test** (the guard for this bug): GET the review page for an undecided approval and assert the rendered HTML wraps the notes control in the theme-correct field wrapper — `assertContains(response, "pl-form-group")` and that the `id_notes` textarea is present (e.g. `assertContains(response, 'name="notes"')`). This catches a regression back to a bare, un-wrapped textarea. Extend the existing 200/valid-token case (`it_returns_200_with_valid_token`) or add a sibling `it_wraps_the_notes_field_in_a_themed_wrapper`.
- **Error renders exactly once** (the guard for the duplicate-error trap, the most likely regression): POST a *Decline* (or *Request changes*) with empty `notes` to the review URL and assert the notes error string appears **exactly once** in the rendered HTML — `assert response.content.decode().count("Please leave a note so the instructor knows what to change.") == 1`. A count of 2 means the top-of-form loop wasn't scoped to `decision`; a count of 0 means it was deleted/over-scoped and the error is orphaned. Add as e.g. `it_shows_the_notes_error_once_when_declining_without_notes` in `class_review_spec.py` (alongside the existing `it_records_denial_on_post`).
- **No behavior regression:** the existing `it_records_approved_decision_on_post` / `it_records_denial_on_post` / required-notes-on-decline cases must still pass — the field name (`notes`) and form are unchanged, so POST handling is unaffected.
- **CSS is not unit-tested.** The test only guards the wrapper's *presence* in the markup; the actual dark/light legibility is a **manual** check on the running hub (`pastlives.test:8000`) — toggle Obsidian/Slate on the review page and confirm the notes text is readable in each.

## 10. Open / deferred

- Restyling the decision RadioSelect rows is **out of scope** — they're already theme-legible; this fix is contrast-only on `notes`.

---

**Closing note — version bump & changelog (at BUILD time, not now):** the branch is `release-0.19.x`, where each feature bumps the **patch** and Discord aggregates all 0.19.x changes at merge. Bump `plfog/version.py` `VERSION` and add a top-of-list, member-friendly `CHANGELOG` entry (e.g. "Class reviewers can now read their feedback notes clearly in dark mode."). **Verify the exact next patch number at build time** against `plfog/version.py` — don't assign it in this spec.
