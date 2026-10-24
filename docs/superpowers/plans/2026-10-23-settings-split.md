# Split Site Settings tabs into their own cards (#922)

Stacked on #920 (PR #923), which already moved the Announcements tab to the Email Members page; this PR does only the four other tabs.

Source: Felix, 2026-10-23: "some Site Settings tabs should be split into their own cards. Like Member Agreement, Automations, Features (call it Manage Features), CMS (Manage CMS), and move Announcements tab into the Spotlight." The Announcements tab is the email sending feature's (a link to the composer and "Draft from latest release"), so under #920's naming it moves to the Email Members page, not the Spotlight (orchestrator's correction, Felix to confirm on the PR).

### User story

As an admin, I want Member Agreement, Automations, Features and the CMS as their own Admin Tools cards, so I reach each one directly instead of hunting through Site Settings' tab row.

### Summary

Split four Site Settings tabs into their own Admin Tools cards and pages (Member Agreement, Automations, Manage Features, Manage CMS) and move the Announcements tab's tools onto the Email Members page.

### Goal

Site Settings keeps only site wide settings; each larger tool has its own card.

### Current behavior

`templates/hub/admin/site_settings.html` is one form with ten tabs: Brand, General, Calendar, Legacy CMS, Features, Member Agreement, Automations, Announcements, Emails, Discord (`submitted_tab` picks what saves).

### Expected behavior

Admin Tools gains four cards, each opening its own page with exactly today's controls and behaviour: Member Agreement, Automations, Manage Features (the Features tab), Manage CMS (the Legacy CMS tab). Site Settings keeps Brand, General, Calendar, Emails and Discord. The Announcements tab's "Open Announcements" link and "Draft from latest release" (with release mode) move to the Email Members page (#920). Old `?tab=` links to the moved tabs redirect to the new pages.

### Acceptance criteria

1. Four new admin only pages, each saving only its own fields; nothing else on Site Settings changes or is lost on save (specs prove a save on one page leaves the others' fields alone).
2. Cards placed in the right Admin Tools sections (#912) with one line descriptions; Site Settings card copy updated.
3. `?tab=features|member-agreement|automations|legacy-cms|announcements` on Site Settings redirect to the new homes; every internal link and spec updated (grep templates, tours, help, emails).
4. Release email drafting works from Email Members exactly as before.
5. Screenshots of Admin Tools and each new page in the PR and on this issue.

### Area

hub

### Constraints

- Admins only, as today. No model changes. Depends on #920 for the Email Members page.

### Out of scope

- Redesigning the moved controls.

