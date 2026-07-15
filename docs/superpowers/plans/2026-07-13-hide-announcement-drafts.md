# Hide Announcement Drafts (UI only) — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-13
**Surface:** FOG hub `pastlives.test` — `/announcements/compose/` (announcement composer wizard)
**Related:** the composer/wizard work (`templates/hub/announcement_compose.html`); `AnnouncementDraft` model in `membership/models.py`
**Size:** XS / urgent

---

## 1. Summary

The announcement composer at `/announcements/compose/` shows a "Your drafts" panel below the wizard. On the live page it renders as a run-together wall of audience/guild labels ("Everyone (site-wide)Art Framing GuildCeramics Guild…") with no spacing or structure, and the un-contained section spills out of the wizard card and shoves the page/navbar layout around. The fix members need now is simply: **make the drafts UI disappear.** We hide the drafts list and the "Save draft" button so members can neither see nor save drafts, while leaving the entire drafts backend intact and dormant so it can be switched back on later with a one-line change. The composer keeps working end-to-end without drafts: audience → message → email → Discord → send.

### Locked decisions (from brainstorm)

| Decision | Choice |
|---|---|
| Repair the broken panel vs. hide it | **Hide it.** Root cause of the live breakage is unconfirmed (see §3), and drafts are non-essential. Hiding is the fast, safe, fully-reversible move. |
| Delete drafts entirely vs. dormant | **Keep the backend.** Leave the `AnnouncementDraft` model, save/resume/delete/list views, and their URL routes in place — untouched. This is a pure UI hide, not a feature deletion. |
| What members lose | Drafts only. They can still compose and send an announcement completely; they just can't stash a half-finished one for later. |
| Where to hide | Two removals in one template: the drafts-list `{% include %}` and the "Save draft" button. Nothing else. |

## 2. What already exists (stays intact and dormant)

Nothing new is built. The only edits are two deletions in one template. Everything below is **left exactly as-is** — no route removed, no view touched, no migration:

| Kept dormant | Thing | Location |
|---|---|---|
| Model + manager | `AnnouncementDraft`, `AnnouncementDraftManager.for_user()`, `save_from_form()`, `send()` | `membership/models.py:2263–2413` |
| Migration | `0079_announcementdraft` | `membership/migrations/0079_announcementdraft.py` (stays applied) |
| Save-draft view | `hub_compose_save_draft` | `hub/views.py:2015` |
| Delete-draft view | `hub_compose_delete_draft` | `hub/views.py:2069` |
| Resume (draft-scoped compose) | `hub_compose` via `draft_pk` | `hub/views.py:1920` |
| Drafts list injected into compose context | `"drafts": AnnouncementDraft.objects.for_user(...)` | `hub/views.py:1904` (`_render_compose`) |
| Routes (all left registered) | `hub_compose_save_draft`, `hub_compose_resume`, `hub_compose_delete_draft` | `hub/urls.py:109, 111, 112–116` |
| Drafts partial | `_compose_drafts_list.html` | `templates/hub/partials/_compose_drafts_list.html` (kept on disk, just not included) |
| Drafts CSS | `.pl-drafts*` / `.pl-draft-row*` | `static/css/hub.css:3936–3965` (kept — harmless, used again on re-enable) |

Because the routes stay live, a member who has (or guesses) a `?draft_pk` / save-draft URL would still hit a working endpoint. That's acceptable and intentional for a reversible hide — there is no drafts entry point in the UI, so this is not reachable in normal use, and no data is exposed to anyone but the draft's own author (the views already scope to `author=request.user`).

## 3. Why the panel looks broken (brief diagnosis — not the thing we fix)

Both halves look correct in the repo read in isolation: the partial has proper structure (`.pl-drafts` → `.pl-draft-row` → `.pl-draft-row__meta`/`__sub`, each audience label in its own element), and the matching CSS (`.pl-drafts`, `.pl-draft-row`, flex layout, borders, spacing) is present in `hub.css:3936`. So the live symptom — bare audience labels butting together with no separators and the section escaping the card into the navbar — is most consistent with **the drafts section rendering effectively unstyled** (the `.pl-drafts*` rules not reaching the rendered page, so the inline label spans collapse against each other and the un-carded `<section>` sits loose in `{% block content %}` after the wizard's `.hub-card`, with nothing constraining its width). The exact root cause is unconfirmed and **we are deliberately not chasing it** — hiding the panel removes the symptom entirely and is reversible. If drafts are re-enabled later, re-verify the panel renders styled and, if needed, wrap it in a `.hub-card` before shipping.

## 4. The exact change (two removals, one file)

**File:** `templates/hub/announcement_compose.html`

1. **Remove the "Save draft" button** (currently ~line 102–103, inside the Step-3 `.pl-wizard-actions`):
   ```html
   <button type="button" class="hub-btn hub-btn--ghost"
           hx-post="{% url 'hub_compose_save_draft' %}" hx-include="closest form" hx-swap="none">Save draft</button>
   ```
   Step-3 actions then read: **← Back** · **Send announcement**.

2. **Remove the drafts-list include** (currently ~line 113, just before `{% endblock %}`):
   ```html
   {% include "hub/partials/_compose_drafts_list.html" %}
   ```

**How to hide reversibly:** wrap both in a `{% comment %}`/`{% endcomment %}` block (multi-line — a single-line `{# … #}` won't span the include's context), each with a one-line note: `{# Drafts UI hidden 2026-07-13 — backend intact; to re-enable, un-comment. See docs/superpowers/plans/2026-07-13-hide-announcement-drafts.md #}`. Commenting-out (rather than deleting) makes the re-enable a literal un-comment and keeps the anchor visible to the next reader. Leave everything else in the template untouched — including the hidden `draft_pk` input on line 30 (it submits an empty value and is harmless; keeping it means the send/resume plumbing is unchanged for re-enable).

The `"drafts"` context var (`_render_compose`, `hub/views.py:1904`) can stay — it's now simply unused by the template. Leaving it avoids touching views for a template-only hide (YAGNI; also keeps the resume/save responses, which re-render the same partial, coherent if ever reached).

## 5. The composer still works end-to-end without drafts

Nothing on the send path touches drafts. Walking the wizard after the removals:

- **Step 1 — Audience & message:** `form.audience` select, `form.title`, `form.body`, guild-only `form.expires_at` (`x-show`) — unchanged. **Next: email →** advances.
- **Step 2 — Email:** "Also send as email" toggle, live preview (`hub_compose_preview`), "Send a test to me" (`hub_compose_test`) — unchanged. **← Back** / **Next: Discord →**.
- **Step 3 — Discord:** channel picker + opt-in `@mention` radios, "Reaches N member(s)". Actions now: **← Back** and **Send announcement** (`type="submit"` → `hub_compose_send`, with the existing confirm on click). The removed Save-draft button was a sibling `type="button"` — deleting it does not affect the submit button or the form.

The `<form action="{% url 'hub_compose_send' %}">` and its single Send submit are the whole path; drafts were an orthogonal side branch. Removing the branch leaves the trunk intact.

## 6. UI / UX (completeness checklist applied)

- **Screen:** `templates/hub/announcement_compose.html` — the only screen touched.
- **Layout & container:** the wizard stays inside its `.hub-card.pl-wizard`. Removing the un-carded `.pl-drafts` `<section>` that followed it is what restores clean page/navbar layout — the loose spilling section is gone, so `{% block content %}` ends cleanly at the wizard card.
- **Components used:** unchanged — `form_field.html`, `toggle.html`, the channel-picker partial. No new components; nothing hand-rolled.
- **Controls, named:**
  - **Send:** Step-3 `type="submit"` **Send announcement** → `hub_compose_send`, existing "Send to N member(s)?" confirm on `@click`. Untouched.
  - **Navigation:** Next/Back buttons on each step. Untouched.
  - **Removed:** the **Save draft** button (Step 3) and the **Your drafts** panel (Resume / per-row Delete + confirm modal). No orphaned control is left pointing at a removed target.
  - No list editor is introduced or removed in a way that needs Add/Delete controls — the drafts panel (which had its own Resume/Delete) is hidden wholesale.
- **States:**
  - *Empty:* the composer no longer shows an empty-drafts message at all — the whole panel is gone, so there's no empty region to explain. Cleaner than before.
  - *Loading / error / success:* the send, preview, and test flows keep their existing HTMX indicators, field-error re-render, and toast/redirect behavior — none of that ran through drafts.
  - *No dead ends:* every remaining button has a live target (send, preview, test, step nav). No button now points at a hidden feature.
- **Dark + light:** no CSS added or changed. Removing template markup can't introduce a theme bug; the `.pl-drafts*` rules stay in `hub.css` unused. **Verify both themes** on the composer after the change purely to confirm the layout reflows cleanly (see below).
- **Mobile:** the driving reason for the change. Removing the spilling, un-contained drafts section is what fixes the wrecked mobile page/navbar layout. **Verify at ≤480px** that the composer card and navbar sit correctly and there's no horizontal scroll, in both dark and light.

## 7. How a future re-enable works

Single, obvious, reversible:

1. Un-comment the two blocks in `announcement_compose.html` (the include + the Save-draft button).
2. **Before shipping the re-enable,** first diagnose/fix the original visual (see §3) — confirm the `.pl-drafts*` styles actually apply on the rendered page, and if the loose section is the culprit, wrap the `_compose_drafts_list.html` `<section>` in a `.hub-card` so it can't spill. That repair is out of scope for *this* change (we're hiding, not fixing).
3. No migration, no model change, no route change — everything the un-commented markup calls is already live.

Leave a short pointer to this plan in the commented-out block so the next engineer finds the re-enable steps.

## 8. Build order

One phase — it all ships together and green.

1. Comment out the Save-draft button and the drafts-list include in `templates/hub/announcement_compose.html`, each with the dated re-enable note.
2. Run the composer locally (`pastlives.test:8000/announcements/compose/`) as an admin: walk Step 1→2→3 and Send; confirm no drafts panel, no Save-draft button, clean navbar/layout in **dark + light** and at **mobile width**.
3. Update tests (§9).
4. `ruff format .` && `ruff check .`.
5. Bump `plfog/version.py` VERSION. **Changelog:** the composer/announcement-wizard feature is unreleased in the current line — this is an intra-cycle UI fix to unshipped work, so **no changelog entry** (the commit is the record). If the composer is in fact already live on production, add one plain-language line instead ("Tidied up the announcement composer — the drafts panel that was breaking the layout is hidden for now."). Confirm which before finalizing.

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py` in `hub/spec/`, run in the `plfog-web` Docker image, `--no-cov` for the subset.

- **Drafts UI is absent from the composer (the point of the change):** GET `/announcements/compose/` as a user who can compose (seed a `MembershipPlan` + logged-in admin/staff member — see the member-gated-test fixture note). Assert the response does **not** contain the Save-draft anchor (`url 'hub_compose_save_draft'` / `>Save draft<`) and does **not** contain the drafts panel markers (`id="compose-drafts"` / `Your drafts`). Assert against markup, not visible copy that a changelog string could collide with.
- **Composer still renders and can send:** the existing compose GET test still passes (page renders all three steps, the `hub_compose_send` form action is present, Send button present). If there's no such test, add one asserting `action="{% url 'hub_compose_send' %}"` and the Send submit button are in the GET response. A full-form POST to `hub_compose_send` with valid data still creates/sends the announcement (existing send-path spec — should be unaffected; run it to confirm).
- **Backend stays intact (guard against accidental deletion):** keep/confirm the existing specs for `hub_compose_save_draft`, `hub_compose_delete_draft`, resume-by-`draft_pk`, and `AnnouncementDraft` model/manager all still pass unchanged. These prove the hide didn't touch the backend. Do **not** delete them.
- **Gotchas:** the composer is `@login_required` and gated to members who can compose (site-wide for admins, guild-scoped for leads/staff) — a test client must be authenticated as an eligible member or the assertions run against a redirect. Use `describe_*` for any nested blocks (`context_*` isn't collected).

## 10. Open / deferred

- **Actually fixing the drafts panel** is explicitly deferred to a future re-enable (§7). Out of scope here.
- **Removing the unused `"drafts"` context var / `draft_pk` hidden input** — left in place on purpose (keeps views untouched, keeps re-enable trivial). Not worth a view edit for a template-only hide.
- **Removing the dormant routes/model** — not doing it; the whole point is reversibility.
