# Spotlight: announcements take the poll's place in the sidebar

Issue: #890. Builds on #889 (merged as PR #902: `core.Announcement`, `live(now)`, `hub.home.HomeBanner`, `templates/hub/partials/_home_announcements.html`, the shared rotator `static/js/pl-rotator.js` with `_rotator_controls.html` taking `noun` and `plural`).

Source: Felix, 2026-10-23. "I no longer want a poll on the sidebar area at all." and "is it possible the sidebar spotlight to also show the announcement below the Details/Updated X area?" Felix chose on 2026-10-23: announcements take the poll's place in the open Spotlight; when minimized, the first line is the announcement and the yellow dot lights when a new one is posted. Depends on the Announcements PR (model `core.Announcement`, `live(now)`).

### User story

As a member on any portal page, I want the Spotlight in the top left to tell me the current announcements instead of a poll, so news follows me around the portal, not just on Home.

### Summary

The poll leaves the Spotlight everywhere; live announcements show where it was, rotating, and a new one lights the yellow dot.

### Goal

The Spotlight becomes the portal wide "what's new" corner: announcements, the next Feature Request Meeting, the version and its latest update.

### Current behavior

`hub/spotlight.py::Spotlight.load` reads the open poll (scalar subqueries in `_row_annotations`), and `templates/hub/partials/_spotlight.html` shows it in Standard with "Past polls"; `_spotlight_expanded.html` shows the poll card and "See all past polls"; Minimized's first line falls back to the poll question; the seen signature includes the poll pk; the admin page (`hub/spotlight_views.py`, `/manage/spotlight/`) has Open poll, Close now, New poll and the replace confirm, and its preview binds `pollQuestion`. With no poll and no meeting the Spotlight is Quiet (version, Updated X, Details).

### Expected behavior

No poll anywhere in the Spotlight. Standard, top to bottom: eyebrow "Announcement", the live announcement's rich text, and with two or more "2 of 3" with previous and next buttons, rotating like the Home banner; then the Next Feature Request Meeting when there is one; then the version pill and "Updated X"; then Details.

### Acceptance criteria

1. **Poll out.** Remove the poll from `Spotlight` (its fields, `_row_annotations`'s poll columns, `card`, `total_votes`, `can_vote`, the poll fallback of the first line), from Standard, Minimized, Expanded, the phone copy on Home and the admin preview. Remove "Past polls" and "See all past polls" links from the Spotlight. On the admin page, remove Open poll, Close now, New poll and the replace confirm, and their URLs and views if nothing else uses them (grep `tests/`, `*/spec/`, `tests/e2e/` and templates first). `/polls/` and the polls app stay (history), untouched.
2. **Announcements in.** `Spotlight.load` reads the live announcements (`Announcement.objects.live(now)`) and the whole Spotlight stays within the hub's existing one extra query budget, or at most one more; pin it in the spec. Standard shows them as described above, sharing the Home banner's rotation component (8 seconds, pause on hover and focus, reduced motion stops it, no controls with one). Long rich text wraps; links work.
3. **Minimized.** First line: the admin's first line text, else the first live announcement's visible text (tags stripped, one line, ellipsis), else the latest update's title (as today). Second line unchanged.
4. **Yellow dot.** `seen_signature` swaps the poll pk for the live announcements' ids and `updated_at` stamps, so a new or edited announcement lights the dot; a release alone still never lights it.
5. **Expanded panel.** An "Announcements" section lists every live announcement in full, in order, in the poll's place; with none, the latest update shows as today.
6. **Quiet.** With no live announcement and no meeting the Spotlight is Quiet as today (`is_empty`, `show_when_empty` keep working).
7. **Home, no duplicate.** On Home the banner already shows announcements, so the phone Spotlight copy at the top of Home leaves announcements out (meeting, version and Details stay).
8. **Admin preview** on `/manage/spotlight/` shows the new Standard and Minimized with the real live announcements.
9. **Tests:** update `tests/hub/spotlight_spec.py`, `spotlight_member_spec.py`, `spotlight_admin_spec.py`, `tests/e2e/spotlight_spec.py`, `tests/core/tours_spec.py` and any guided tour step that targets a poll element (`core/tours.py`, `static/js/pl_tour.js`); grep every spec for the removed selectors (`data-spotlight-poll`, `data-spotlight-past`, `data-spotlight-all-polls`, `data-spotlight-choice`, poll URLs). New specs for: announcement shown in Standard and Expanded, rotation controls with many and none with one, minimized fallback order, the dot signature changes on add and edit, the Home phone copy leaves them out, the query budget, removed admin endpoints 404. Run the e2e specs touched locally on Postgres. Screenshots for the PR: the sidebar Spotlight Standard and Minimized (with the dot) at 1440px, and the Expanded panel, light and dark.
10. Copy has no dashes. Docs: `hub/AGENTS.md` "The Spotlight" section and the Spotlight admin row, `FRONTEND.md` `spotlight.css` row, `polls/AGENTS.md` (the poll is no longer in the Spotlight).

### Area

hub (Spotlight, Admin Tools > Spotlight), polls

### Constraints

- Do not delete the polls app, its models, migrations or `/polls/` pages.
- No migration unless one is needed to drop nothing; no data is deleted.
- Keep the Spotlight's state handling (`static/js/spotlight.js`, `<html data-spotlight-min>`) as is apart from the signature.

### Out of scope

- Retiring the polls app or its data.
- Announcement targeting, history or archive pages.

### Also in this PR

- `hub/spotlight_views.py` answers an invalid boosted POST of the Spotlight text form with 400, so `hub_boot.js` reloads the page and drops what the admin typed (found in #889's review; #889 answers 200 for the same reason). Re-render the invalid form at 200 with its errors, as the announcement form does, and spec it. The New poll form is removed by this PR, so only the text form remains.
