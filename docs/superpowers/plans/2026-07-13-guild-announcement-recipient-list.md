# Guild Announcement Recipient Count & List — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-13
**Surface:** FOG hub `pastlives.test` — guild-edit **Announcements** tab (`/guilds/<id>/edit/?tab=announcements`)
**Related:** the leadership comms / announcement-wizard work already on the unreleased `release-0.21.0` line (v0.21.9–v0.21.12); `AnnouncementDraft.recipient_count()`.

---

## 1. Summary

Before a guild lead writes an announcement, they can't tell how many people it will reach — the Announcements tab just links them to the compose wizard. This adds a plain reach line at the top of the "Post an Announcement" card — **"42 members will receive an emailed announcement"** — with the full list of recipient email addresses tucked behind a collapsible, scrollable "Show recipients" control. The lead now knows their audience before composing, and the number is guaranteed to match who the send actually emails because it comes from the same resolver the send uses.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| What produces the count + list | The **real send resolver** (`Recipients.GUILD_MEMBERS`), never a re-written query, and **not** the privacy-filtered public roster — so the number equals delivery. |
| How the emails are shown | A clear count line always visible; the addresses behind a collapsible **"Show recipients"** control that scrolls inside its own box (a guild can have many members). |
| Is exposing member emails to the lead OK | Yes — a lead can already email their own guild's members, so surfacing the addresses to them adds nothing they don't already have. Noted on-screen. |
| Members with no email | Excluded from both count and list, matching delivery — with a one-line note so the lead understands the discrepancy. |

## 2. What already exists (reuse, don't reinvent)

| Need | Existing thing | Location |
|---|---|---|
| The recipient audience (active guild members → `(User, reason)`, no `last_login` gate, **no** directory-privacy filter, dedup + drops no-email/unlinked) | `guild_members` resolver | `core/events/resolvers.py:161-182` (via `_members_to_recipients` / `_member_user` / `_dedupe`, `:44-76`) |
| The canonical way to invoke it | `resolvers.resolve(Recipients.GUILD_MEMBERS, {"guild": guild})` | `core/events/resolvers.py:495`; `Recipients.GUILD_MEMBERS = "guild_members"` `core/events/registry.py:77` |
| Proof this is the delivery audience | `AnnouncementDraft.recipient_count()` uses the exact same call for `GUILD` sends | `membership/models.py:2402-2413` |
| Where the tab's data is assembled | `_guild_edit_context()` | `hub/views.py:641-689` |
| The tab template block (currently: compose CTA + recent posts) | `x-show="section === 'announcements'"` block | `templates/hub/guild_edit.html:481-506` |
| Card / muted-text / small-button styling | `.hub-card`, `.hub-text-muted`, `.hub-btn .hub-btn--sm` | `static/css/hub.css` |

**Gap to close (small):** a testable seam that hands the view the resolved recipients, plus the reach line + collapsible list markup, plus one CSS class for the scroll box. No new model, no migration, no change to how announcements actually send.

## 3. Where the code lives

```
membership/models.py        # NEW Guild.announcement_recipients() — thin delegate to the resolver
hub/views.py                # _guild_edit_context(): derive count + emails from that method
templates/hub/guild_edit.html   # reach line + "Show recipients" collapsible in the announcements card
static/css/hub.css          # NEW .pl-recipient-list scroll box
membership/spec/models/guild_spec.py   # count / exclusion / empty-state / delivery-parity tests
hub/spec/…/guild_edit_spec.py          # render test: reach line + empty-state string
```

## 4. Data model

None. No new model, field, or migration. This is display-only over an existing resolver.

## 5. Business logic (fat model, thin view)

Keep the resolver call out of the view. Add a thin delegate on `Guild`, mirroring `AnnouncementDraft.recipient_count()` (same import-inside-method pattern to dodge the resolver↔models cycle):

```python
# membership/models.py — on Guild
def announcement_recipients(self) -> list[tuple["User", str]]:
    """The exact (User, reason) list a guild announcement email fans out to.

    Delegates to the real send resolver so the lead-facing count/list can never
    drift from delivery. NOT directory-privacy filtered (guild members hear from
    their own guild regardless of directory visibility) and NOT last_login-gated.
    """
    from core.events import resolvers
    from core.events.registry import Recipients

    return resolvers.resolve(Recipients.GUILD_MEMBERS, {"guild": self})
```

This satisfies the locked decision (delegates to the resolver — does not reimplement the query) and gives the tests a single seam to assert delivery-parity against.

Then in `_guild_edit_context()` (`hub/views.py`), the view stays a pure assembler:

```python
recipients = guild.announcement_recipients()
# … add to the returned dict:
"announcement_recipient_count": len(recipients),
"announcement_recipient_emails": sorted(user.email for user, _reason in recipients),
```

(`sorted` only makes the shown list scannable — it does not affect the count or who is included.)

## 6. UI / UX

Only one screen changes.

- **Screen / partial:** `templates/hub/guild_edit.html`, the `x-show="section === 'announcements'"` block — inside the existing **"Post an Announcement"** `.hub-card` (`:491-495`), rendered **above** the "Compose announcement →" CTA so the lead sees their reach before they click through.
- **Layout & container:** no new card — it lives in the current announcements card. This is read-only display, not a form, so no `form_field.html` / modal is involved.
- **Components used:** existing `.hub-card`, `.hub-text-muted`, `.hub-btn .hub-btn--sm`; Alpine `x-show` for the collapse (this template is already Alpine-driven for tab switching).
- **The controls, named explicitly:**
  - **Reach line (always visible when count > 0):** `<strong>{{ announcement_recipient_count }} member{{ …|pluralize }}</strong> will receive an emailed announcement.`
  - **"Show recipients" toggle:** a `hub-btn hub-btn--sm` button in a small `x-data="{ showRecipients: false }"` scope; label swaps `Show recipients` ⇄ `Hide recipients`. It reveals `<div x-show="showRecipients" x-cloak class="pl-recipient-list">` holding a `<ul>` of `{{ announcement_recipient_emails }}`, one `<li>` per address.
  - **No Save / no submit** — this screen is informational; there is nothing to post. The existing "Compose announcement →" primary CTA still sits directly below, unchanged.
  - **Privacy note (muted, under the toggle):** "You can already email your guild's members, so their addresses are shown here." — the on-screen acknowledgement of the locked decision.
  - **No-email note (muted):** "Members without an email on file aren't counted — they won't receive the announcement." Shown whenever count > 0 so the number is never a mystery.
- **States:**
  - **Populated (count > 0):** reach line + toggle (collapsed by default) + both notes, then the compose CTA.
  - **Empty (count == 0):** replace the reach line/toggle with a single muted line — **"No members in this guild yet — nobody will receive announcements."** The compose CTA still renders (harmless; the lead may add members). No toggle, no empty `<ul>`.
  - **Loading / error / success:** none — synchronous server render, no HTMX, no mutation, so no toast, spinner, or error path is introduced.
- **Dark + light:** theme tokens only. `.pl-recipient-list` uses `background: var(--hub-surface)`, `border: 1px solid var(--hub-input-border)`, `color: var(--hub-text)` — no `--surface` fallback (that silently goes white on dark). No `<input>/<select>/<textarea>` is added, so the form-control white-box class of bug does not apply here; still, **verify both Obsidian and Slate.** The toggle button is the standard `.hub-btn`, already theme-correct.
  - **Alpine `x-show` guard (Rule 12):** do **not** put `display` in an inline `style=` on the `.pl-recipient-list` element — Alpine strips inline `display` on reveal. Let the default block display stand; the CSS class carries only padding/border/max-height/overflow. `x-cloak` on the revealed div and the "Hide" label prevents a flash on load.
- **Mobile:** this page has had layout bugs, so the email list must never widen the page. `.pl-recipient-list` sets `max-height: 14rem; overflow-y: auto;` (scrolls inside its own box) and `overflow-wrap: anywhere; word-break: break-all;` on the `<li>` so long addresses wrap **inside** the box instead of forcing horizontal scroll or pushing the sidebar/navbar. `overflow-x: auto` on the box as a backstop. Spacing on the 8px grid (`0.75rem`/`1rem`); the toggle button clears the reach line above it (`margin-top`).

CSS to add (`static/css/hub.css`):

```css
.pl-recipient-list {
  margin-top: 0.75rem;
  max-height: 14rem;
  overflow-y: auto;
  overflow-x: auto;
  padding: 0.75rem 1rem;
  border: 1px solid var(--hub-input-border);
  border-radius: 6px;
  background: var(--hub-surface);
  color: var(--hub-text);
}
.pl-recipient-list ul { margin: 0; padding-left: 1.1rem; }
.pl-recipient-list li { overflow-wrap: anywhere; word-break: break-all; line-height: 1.5; }
```

## 7. Notifications / emails / activity

None. No email is sent, no `emit()`, no `SiteActivity` — the feature only *describes* the audience of an announcement the lead may later send through the existing wizard.

## 8. Build order (phased; each phase ships green)

1. **Model seam + view context.** Add `Guild.announcement_recipients()`; wire `announcement_recipient_count` / `announcement_recipient_emails` into `_guild_edit_context()`. Tests for the model method (Phase 3 can extend). Full suite + lint + mypy green.
2. **Template + CSS.** Reach line, "Show recipients" collapsible, both notes, empty state in `guild_edit.html`; `.pl-recipient-list` in `hub.css`. Verify Obsidian **and** Slate, and narrow-viewport (no navbar/layout break). Render test.
3. **Version + changelog.** Bump `plfog/version.py` `VERSION` (current `0.21.15` → `0.21.16`).
   - The guild announcement / leadership-comms experience is on the **current unreleased `0.21.x` line**. If a changelog entry for guild announcements already exists in that line, **fold a bullet into it and re-stamp its `version`/`date` to `0.21.16`** (move to top) — do not add a second entry. If there is no such entry, add one short, friendly entry, e.g.:
     > **Know your reach before you post.** The guild Announcements tab now shows how many members will receive your emailed announcement, with the full recipient list a click away.

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*`, factory-boy, run in the `plfog-web` Docker image (`--no-cov` for the subset), ≥98% gate on the full run.

**`Guild.announcement_recipients()` (membership spec):**
- `it_counts_active_members_with_email` — guild with N active members (linked users, emails) → `len(...) == N`, emails all present.
- `it_excludes_members_with_no_email` — a member whose linked User has a blank email (and/or an unlinked member) is dropped, so the count is lower and the address is absent — matching delivery.
- `it_excludes_inactive_members` — a non-`ACTIVE` guild member isn't counted (guards against drift from the resolver's `status=ACTIVE` filter).
- `it_is_empty_for_a_guild_with_no_members` — returns `[]`.
- `it_matches_the_send_resolver` — the **delivery-parity** assertion: `guild.announcement_recipients() == resolvers.resolve(Recipients.GUILD_MEMBERS, {"guild": guild})` (proves we never diverge from the send).

**View / render (hub spec):**
- `it_shows_the_reach_count_on_the_announcements_tab` — GET `/guilds/<id>/edit/?tab=announcements` for a lead → context `announcement_recipient_count` set and the "will receive an emailed announcement" line rendered. Assert on the specific reach-line markup, **not** on numbers/text that could collide with the "what's new" changelog widget echoed on every hub page.
- `it_shows_the_empty_state_when_the_guild_has_no_members` — count 0 → the "No members in this guild yet — nobody will receive announcements." line renders and no recipient `<ul>` is present.

No tz/date-window gotchas (no dates involved).

## 10. Open / deferred

- **Not** re-fetching the list live/HTMX as membership changes — it reflects the roster at page load, which is correct for a "know your reach before composing" glance. Deferred unless asked.
- **No** copy/export-emails button — out of scope; the lead can already reach these members through the existing channels. Add later only if requested.
- **No** change to the compose wizard's own confirm-count (`AnnouncementDraft.recipient_count()` already covers that step) — this feature is purely the pre-compose glance on the tab.
