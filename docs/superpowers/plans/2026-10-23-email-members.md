# Rename the sending feature to Email Members (#920)

Source: Felix, 2026-10-23: "Announcements is the other feature that sends emails to members. I think that feature should be renamed somehow to be more clear to Guild Leads that it's the Email Send/Manage Mailing List feature. And announcement should mean the rotating messages on the Spotlight feature." Felix chose: "Email Members" plus a separate "Manage Mailing Lists", the card carrying a "Formerly called Announcements" badge.

### User story

As a guild lead, I want the sending tool named for what it does, so I find "Email Members" when I want to email my guild, and "Announcements" means only the rotating Spotlight messages.

### Summary

Rename the member facing "Announcements" sending feature to "Email Members", add a "Manage Mailing Lists" card, and keep "Announcements" for the Spotlight's rotating messages.

### Goal

One meaning per word: Announcements are the Spotlight and Home banner messages; Email Members sends.

### Current behavior

`/announcements/` (`hub_announcements`, Drafts and Sent tabs) and its compose wizard are labelled "Announcements" on the Admin Tools card ("Write, save, and look back on announcements to a class, a guild, or the whole makerspace"), in the sidebar, on guild pages and in copy. It emails members, can post to Discord and sends an app notification. A guild's list lives in Guild Settings as "Your Mailing List". Since #902/#909, "Announcements" also names the Spotlight's rotating messages (Admin Tools > Spotlight).

### Expected behavior

Every member and staff facing label for the sending feature says "Email Members" (page title, sidebar, Admin Tools card, guild page buttons, compose wizard, Drafts/Sent, emails and notifications that name the feature, Help Center guides, tours). The card carries a small "Formerly called Announcements" badge. A new "Manage Mailing Lists" card opens the mailing list of the guild(s) the viewer can edit (one guild: straight to that guild's Your Mailing List; several: a short picker; admins: every guild). The Spotlight's "Announcements" keep their name.

### Acceptance criteria

1. No member or staff facing text calls the sending feature "Announcement(s)": grep templates, help content, tours, email and notification copy, Discord copy that names the portal feature. The Spotlight's announcements are untouched.
2. The Email Members card says what it does in one line, naming email, Discord and the app.
3. "Formerly called Announcements" badge on the Email Members card, removable later by deleting one line.
4. Manage Mailing Lists card, gated like Guild Settings' mailing list, routes as described; no new list model.
5. URLs keep working: `/announcements/` either stays or redirects permanently to the new path, and old links in sent emails and notifications still land.
6. Model and code names may stay (`GuildAnnouncement`, `AnnouncementDraft`); only what people read changes. CONTEXT.md glossary: Announcement = Spotlight message; Email Members = the sending feature.
7. Specs and e2e that assert the old copy updated; screenshots of the Admin Tools cards, the Email Members page and the guild page buttons in the PR and on this issue.

### Area

hub, membership, help

### Constraints

- No dashes in copy. No model renames or migrations.

### Out of scope

- Guild Tools (its own issue). Changing who can send.

