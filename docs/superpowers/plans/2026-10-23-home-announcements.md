# Announcements: admins post short rotating notices shown as a banner on Home

Issue: #889. Stacked on #888 (PR #896): reuse its rotator, `static/js/pl-rotator.js` (Alpine `plRotator`) and `templates/hub/partials/_rotator_controls.html`, with `data-rotator-interval="8000"`; do not build a second carousel.

Source: Felix, 2026-10-23. "I want the ability for admins to easily have rotating announcements that will show up on the homepage. Maybe make it a max of 160 characters, and allow for rich text. The announcements could be a banner at the top of the homepage." Felix chose on 2026-10-23: the banner slides to the next one every few seconds; announcements have optional start and end dates; members can close the banner; admins set the order by hand.

### User story

As an admin, I want to post a short notice ("Open house Sat Nov 8, bring a friend") that every member sees at the top of Home for as long as it matters, so news reaches people without an email.

### Summary

Admins manage up to a handful of short rich text announcements in Admin Tools; live ones rotate in a banner at the top of Home.

### Goal

One easy, self expiring way for admins to tell every member something.

### Current behavior

There is no site wide notice. The Spotlight (`hub/spotlight.py`, Admin Tools > Spotlight at `/manage/spotlight/`, `hub/spotlight_views.py`) carries a poll and two short text lines, not rich, not scheduled.

### Expected behavior

Admin Tools > Spotlight gains an Announcements section. A live announcement shows in a banner at the very top of Home's content, one at a time, sliding to the next every 8 seconds.

### Acceptance criteria

1. **Model** `Announcement` (in `core`, beside `SiteConfiguration`): `body` (sanitised rich text HTML), `is_active` (default on), `show_from` and `show_until` (both optional datetimes), `position` (manual order), `created_at`, `created_by`, `updated_at`. A queryset method `live(now)`: active, `show_from` empty or not after now, `show_until` empty or after now, ordered by `position` then newest. One migration, numbered after main's latest in `core`.
2. **Rich text, 160 characters.** Bold, italic and links only (plus line breaks), using the repo's existing Quill editor (`static/js/rich-editor-init.js`) with a toolbar limited to those. Sanitise on save with the repo's existing sanitiser (nh3; find the helper the class description uses) to exactly that allow list; links get `rel="noopener noreferrer"`, external ones `target="_blank"`. The 160 limit counts the visible text (tags stripped, entities decoded), enforced in the form's `clean_body` with the message "Keep it to 160 characters (this one is N)." and shown live under the editor as "N / 160", turning to the danger colour past 160. Empty text is refused.
3. **Admin**, on the Spotlight admin page, admins only (same gate as the page): an Announcements section with every announcement as a row showing its text, a status chip (Live, Scheduled, Ended, Off), its dates, an on/off toggle (`components/toggle.html`, saves at once), Edit and Delete. "+ Add announcement" opens the form (editor, Show from, Show until, Active); 4 fields, so a dedicated page or an inline toggle revealed form per `FRONTEND.md`'s interaction table. Delete goes through `components/confirm_modal.html` ("Delete this announcement? Members stop seeing it at once."). Drag to reorder, saved at once, copying the reorder pattern Admin Tools > Slideshow uses (`templates/hub/admin/slideshow.html`), with up and down buttons for keyboard and touch. Show until before Show from is a form error. Empty state: "No announcements yet. Add one and it shows at the top of Home." Success toasts after each save. Follow `.claude/skills/drive/references/ui-checklist.md` in full.
4. **Home banner** (`templates/hub/home.html`, first thing in the content block, above the phone Spotlight and the Welcome back header): shown to everyone who sees Home while at least one announcement is live. One at a time, in admin order. With two or more: advances every 8 seconds with a slide or cross fade; dots to jump to each; previous and next buttons; pauses while hovered or focused; a visible pause/play button (WCAG 2.2.2); swipe left and right on touch; under `prefers-reduced-motion: reduce` it does not auto advance and does not animate. With one: no dots, no buttons, no timer. Accessible as a carousel (`aria-roledescription="carousel"`, each slide labelled "N of M", auto rotation not announced by a live region). Height does not jump between slides (size to the tallest). Reuse `plRotator` from #888 (extend it only if something here needs it, keeping Home's carousel specs green); no new library.
5. **Close.** An X on the banner hides it in that browser until the set of live announcements changes (a signature of their ids and `updated_at` kept in `localStorage`, every read and write in try/catch, and the banner renders normally when storage throws). A new or edited announcement brings the banner back.
6. **Query budget:** the banner costs Home one query. Pin it in a spec.
7. **Tests:** `live()` with every date edge (from in future, until passed, until exactly now, both empty, off); the 160 rule on visible text (tags and entities do not count, 160 passes, 161 fails); sanitiser strips scripts, styles, images and attributes outside the allow list; admin gate (non admin 403 or redirect on every endpoint, crafted POSTs included); add, edit, toggle, reorder and delete flows; Home shows the banner with live ones only, in order, and none when none are live; one versus many renders (no controls with one). e2e for the rotation, pause, close and come back after a new announcement, run locally on Postgres. Screenshots for the PR: the banner on Home at 1440px and 375px, light and dark, and the admin section with a few rows.
8. Copy has no dashes. Docs: `core/AGENTS.md`, `hub/AGENTS.md` (Spotlight admin, Home), `FRONTEND.md` stylesheet table, `CONTEXT.md` glossary entry for Announcement if the glossary lists Spotlight terms.

### Area

core, hub (Home, Admin Tools > Spotlight)

### Constraints

- Admins only; no other role writes announcements.
- No email, push or Discord send: an announcement is shown, never sent.
- The Spotlight sidebar is not changed in this PR (the next PR in the round puts announcements in it and removes the poll).

### Out of scope

- Images in announcements, per guild or per audience targeting, view counts.
- Announcements on pages other than Home (the sidebar comes in the next PR).
