# Guild edit: tab conformity (Orientations / Meeting Notes / Events become in-page tabs)

**Commit 1 of release 0.20.x.** Surface: FOG hub, `templates/hub/guild_edit.html`, view `guild_edit` in `hub/views.py`.

## The problem (in the user's words)

Basic Information, Images, and Announcements are real in-page tabs — click and the section swaps instantly, the tab bar
stays. **Events, Meeting Notes, and Orientations are not like that** — they're `<a href>` links to standalone pages
(`guild_events.html`, `guild_meeting_notes.html`, `orientation_settings.html`) that **drop the whole Edit-Guild tab bar**,
dumping you in a separate area. Make all three behave exactly like the other six. Also: **Meeting Notes tab goes
immediately to the right of Meetings.**

## Current state (verified)

`guild_edit.html:4-20` — Alpine `x-data="{ section: …'tab'… || 'basic' }"`; six `<button @click="section='…'">` in-page
tabs (basic, meetings, images, content, announcements, staff) + three `<a href>` links (Meeting Notes →
`hub_guild_meeting_notes`, Orientations → `hub_guild_orientation_edit`, Events → `hub_guild_events`). The three targets
extend `hub/base.html` directly and render no tab bar. `guild_edit` view (`hub/views.py:546-588`) only builds context for
the inline tabs + posts `GuildEditForm`.

The three standalone pieces:
- **Meeting Notes** — `guild_meeting_notes` view (list only, `guild.meeting_notes.prefetch_related("attachments")`),
  template `guild_meeting_notes.html` (46 lines: a list with Edit/Delete + an "Add meeting notes" button). Per-record
  add/edit is a *separate* view.
- **Events** — `guild_events` view (list only, `guild.events.upcoming()`), template `guild_events.html` (45 lines: list
  + "Add event"). Per-record add/edit is a separate view.
- **Orientations** — `guild_orientation_edit` view (settings form + recurring-hours formset + `generate_slots` side
  effect), template `orientation_settings.html` (124 lines). The heavy one.

## Design — full inline, matching the existing pattern

Make `section` drive nine tabs. Tab bar order: **Basic Information · Meetings · Meeting Notes · Events · Orientations ·
Images · FAQ & Links · Announcements · Staff** (Meeting Notes right of Meetings; Events and Orientations follow so the
formerly-separate trio sits together — confirm order with Josh if a different placement is wanted, but Meeting-Notes-
right-of-Meetings is the hard requirement).

1. **Tab bar:** convert the three `<a>` links to `<button type="button" class="vote-tab" :class="…" @click="section='…'">`
   exactly like the others, with new section keys `meeting_notes`, `events`, `orientations`. Reorder per above.
2. **Sections:** add three `<div x-show="section === '…'" x-cloak>` blocks in `guild_edit.html`, moving in the *body*
   of each standalone template (the list markup for Meeting Notes/Events; the settings + hours + email editors for
   Orientations — note commit 2 then splits hours' Save, and commit 3 moves the email editors out to Announcements).
3. **View:** `guild_edit` builds the extra context — `notes = guild.meeting_notes.prefetch_related("attachments")`,
   `events = guild.events.upcoming().select_related("guild")`, the `GuildOrientationSettingsForm` +
   `OrientationAvailabilityFormSet` (get-or-create the settings row). Keep the existing per-section permission posture:
   Orientations editing already allows lead/admin/staff via `_require_can_manage_orientations`; preserve that — if the
   viewer can open the edit page at all they can see the tabs, and each section's own save endpoint re-checks perms.
4. **Saves stay on their own endpoints (the established FAQ/Links idiom).** Don't merge all forms into one POST. Each
   section form keeps `action="{% url '…' %}"` and redirects back to `…/edit?tab=<section>`:
   - Orientation settings → `hub_guild_orientation_edit` (POST handler stays; on success redirect to
     `guild_edit?tab=orientations` instead of the old standalone page; it still calls `generate_slots`).
   - Recurring hours → new endpoint in commit 2.
   - Meeting Notes / Events lists have no form here — their **Add/Edit/Delete** stay as per-record pages (small,
     single-record forms), each redirecting back to `guild_edit?tab=meeting_notes` / `?tab=events` and carrying a
     "← Back to <Guild> · Meeting Notes" link instead of "Back to guild".
5. **Old list URLs:** `hub_guild_meeting_notes`, `hub_guild_events`, and the GET of `hub_guild_orientation_edit` now
   **redirect** to `guild_edit?tab=…` (301/302) so bookmarks and in-app links land on the tab. Delete the now-unused
   list template bodies (or keep the per-record add/edit templates only). Update any in-app links that pointed at the
   standalone pages (search `hub_guild_meeting_notes`, `hub_guild_events`, `hub_guild_orientation_edit`).

## UI / UX completeness

- **Tab bar identical** to existing: same `.vote-tab` / `.vote-tab--active`, same wrap behavior, always visible on every
  section. Active tab reflects `?tab=` deep-link on load (Alpine init already reads the param).
- **Each section** keeps its existing visible Save submit (FAQ/Links/Orientation settings already have one), its
  empty-state line ("No meeting notes yet — add the first."), and its Add button.
- **Deep links / redirects:** `?tab=orientations`, `?tab=meeting_notes`, `?tab=events` all open the right section; old
  URLs redirect cleanly (no 404, no tabless page).
- **Mobile:** tab bar already `flex-wrap`s; new sections reuse the same `hub-card` layout — no horizontal scroll.
- **Dark/light:** pure structural move; no new inline `background`/`color`. Any styles copied from the standalone
  templates that used inline colors should use existing tokens/classes (most already do via `hub-card`/`hub-text-muted`).

## Tests

- View spec: `guild_edit` GET includes the new context (notes/events/orientation form+formset) and renders all nine tab
  buttons; `?tab=orientations|meeting_notes|events` selects the right section (assert the section markers / a
  distinguishing string per section).
- Redirect specs: GET `hub_guild_meeting_notes` / `hub_guild_events` / `hub_guild_orientation_edit` redirect to
  `guild_edit?tab=…`.
- Existing Orientation settings POST spec still passes (now redirecting to the tab); meeting-note/event per-record
  add/edit specs updated for the new back-redirect target.
- BDD `*_spec.py`, `describe_`/`it_`, factory-boy; cover the new branches at the CI gate.

## Out of scope

- Converting per-record meeting-note/event editors into modals or inline forms (they stay as small pages).
- Changing what each section *does* (commits 2 & 3 handle Orientations/Announcements content). This commit is purely the
  structural move to in-page tabs + ordering + redirects.
