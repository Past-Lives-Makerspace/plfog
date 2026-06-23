# Brainstorm/Spec: Lively Guild Pages — announcements, per-guild calendar, upcoming classes, next meeting

> **Status: brainstorm/design.** Captures an elegant approach for the four requested features plus
> ideas to make guild pages feel active. Implementation follows once the open decisions are settled.

## What already exists (reuse, don't rebuild)

The research found a lot of scaffolding — the elegant move is to *lean on it*:

- **Announcements:** `GuildAnnouncement` model already exists (`guild`, `author`, `title`, `body`,
  `published_at`) with a create form (`GuildAnnouncementForm`), a delete endpoint, and management UI in
  `guild_edit.html`. The guild page already renders the latest 5 (`hub/views.py:372`,
  `guild_detail.html:135-146`) — just **below** About. Missing: an **expiry** field and the
  **above-About** placement.
- **Calendar:** A full community calendar (`/calendar/`, `hub/views.py:_get_calendar_context`,
  `community_calendar.html`) renders week + rolling-month views server-side with HTMX auto-refresh.
  Crucially, **`CalendarEvent` already has a `guild` FK**, and `sync_local_class_events()` materializes
  every published class session into a `CalendarEvent` with `guild = offering.category.guild`. So a
  per-guild calendar is "the same calendar, filtered to one guild" — the data is already tagged.
- **Class→guild link:** `ClassOffering.category.guild` (`Category.guild` FK). "All classes for a guild"
  = `ClassOffering.objects.filter(category__guild=guild)`.
- **Upcoming classes:** `ClassOffering.objects.bookable()` already returns published, still-bookable
  classes soonest-first. Scope it: `.filter(category__guild=guild).bookable()[:N]`. Register link =
  `{% url 'classes:register' offering.slug %}`.
- **Meeting info:** `Guild.meeting_schedule` is a free-text field today (rendered at
  `guild_detail.html:128-133`). We'll add *structured* recurrence alongside it for the computed
  "next meeting" date.
- **Edit authority:** `membership.permissions.can_edit_guild` (admin/officer OR `guild_lead` FK) is the
  single source of truth, already surfaced as `can_edit_this_guild` on the page. Every new edit
  affordance defers to it.

## Page structure decision (shapes everything)

The guild page is currently **one long linear page** (hero → About → Watch → Gallery → Meetings →
Announcements → FAQ, with a sidebar). Feature #2 asks for a calendar **tab**. Two options:

- **A — Tabs (recommended):** add a slim tab strip under the hero: **Overview · Calendar · Classes**
  (and room for more). Announcements + next-meeting + a few upcoming classes live on Overview; the full
  week/month calendar gets its own tab; a Classes tab lists all the guild's classes. Keeps each view
  focused and the page from becoming an endless scroll. Matches the user's "tab" language.
- **B — Single page, inline sections:** drop the calendar in as another section. Less navigation, but
  the page gets very tall and the calendar is heavy.

This doc assumes **A (tabs)**.

## The four features

### 1. Announcements / bulletin board (above About, auto-expiring)
- Add `GuildAnnouncement.expires_at = DateTimeField(null=True, blank=True)` — null = never expires.
  Property `is_active = expires_at is None or expires_at > now`.
- Manager/query: page shows `guild.announcements.filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))`.
  Keep expired ones in the DB (history); just hide them. The edit page lists all (with an "expired"
  badge) so leads can see/clear them.
- Add `expires_at` to `GuildAnnouncementForm` as an optional date ("Hide after…", blank = keep
  forever) + a couple of quick presets (1 week / 1 month) in the UI.
- Move the announcements block to the **top of Overview**, styled as a bulletin board (pinned cards),
  above About. Empty state stays quiet (nothing renders if none active).

### 2. Per-guild calendar tab
- Refactor `_get_calendar_context(...)` to accept an optional `guild` filter and reuse it verbatim for
  the guild Calendar tab — same week/month grid, scoped via
  `CalendarEvent.objects.filter(guild=guild, ...)`. No second calendar implementation.
- The guild Calendar tab shows that guild's class sessions (already synced) + the guild's own iCal
  events (`source="guild"`, already synced from `Guild.calendar_url`). Reuse the `calendar_color`.
- Lazy-load the tab via HTMX (the calendar is heavy) so Overview stays fast.

### 3. Upcoming classes (click → register)
- A compact "Coming up" strip on Overview: `ClassOffering.objects.filter(category__guild=guild)
  .bookable()[:4]`, each a card with date/time (first upcoming session), title, spots-remaining, linking
  to `classes:register`. "See all →" goes to the Classes tab (or the public catalog filtered to the
  guild).
- Reuses `bookable()` + `spots_remaining`; no new query logic.

### 4. Next Guild Meeting (auto-computed, overridable, TBA)
- Structured fields on `Guild`:
  - `meeting_cadence` (TextChoices: `NONE` (=TBA), `WEEKLY`, `MONTHLY`) — default `NONE`.
  - `meeting_weekday` (0=Mon … 6=Sun, null) and `meeting_week_of_month` (1–4, or 5 = "last"; MONTHLY
    only).
  - `meeting_time` (TimeField, null) + reuse `meeting_schedule`/a `meeting_location` for the where.
  - `meeting_next_override` (DateField, null) — a one-off manual date that wins over the rule.
  - `meeting_is_tba` (bool) — explicit "no meeting scheduled yet" that forces TBA even if a rule exists.
- Computed property `next_meeting_at`:
  - if `meeting_is_tba` → None (show "TBA").
  - elif `meeting_next_override` (today or future) → that date.
  - elif `meeting_cadence == MONTHLY` with weekday+week → the next Nth-weekday-of-month ≥ today
    (elegantly via `dateutil.relativedelta` `weekday=TH(3)`, or a small helper).
  - elif `WEEKLY` → next given weekday.
  - else None → "TBA".
- Display a "Next meeting" card near the Guild Lead: "Thursday, July 17 · 6:00 PM · Studio B", or
  "TBA". Edit form: cadence picker → reveals weekday/week/time; an override date; a "Mark TBA" toggle.

## Additional ideas to make guild pages feel alive (pick any)

- **Guild pulse / activity feed:** reuse `SiteActivity`/`CmsActivity` — "New class published," "3 new
  members this month," "New announcement." A short, human "what's happening" list signals momentum.
- **At-a-glance stat chips** in the hero: members count, classes offered, next meeting date — concrete
  proof the guild is active.
- **Member spotlight / recent joiners:** a friendly face or two (opt-in, building on `show_members`)
  with a "Join this guild" CTA right beside it.
- **Featured/【pinned】class or project:** let the lead pin one class or a finished-project photo as a
  hero highlight (reuses the gallery + announcements patterns).
- **"Get involved" CTA block:** join the guild, the next meeting, "teach a class here," and the contact
  email — one clear place that converts a browser into a participant.
- **Project gallery with captions** (the gallery exists; add captions + a "share your build" prompt).
- **Discord/links surfacing:** the `links` already exist — give the guild's Discord channel / chat a
  prominent button.
- **Countdown affordance:** "Next meeting in 5 days," "Next class Saturday" — small live touches.
- **Recently completed classes / testimonials:** social proof that things actually happen here.

## Critical files (when we build)
- `membership/models.py` — `GuildAnnouncement.expires_at`; `Guild` meeting-recurrence fields +
  `next_meeting_at` property (+ migration).
- `hub/forms.py` — `GuildAnnouncementForm` (+expiry), `GuildEditForm` (meeting fields).
- `hub/views.py` — `guild_detail` (tab routing, active announcements, upcoming classes, next meeting);
  `_get_calendar_context` (optional guild filter) + a guild-calendar tab/partial view; announcement
  create endpoint (if not already present).
- `templates/hub/guild_detail.html` — tab strip + Overview (bulletin board on top, coming-up strip,
  next-meeting card) + Calendar tab (HTMX) + Classes tab.
- `templates/hub/guild_edit.html` — announcement expiry, meeting recurrence config.
- `plfog/version.py` — changelog.

## Decisions (locked with the user)
1. **Page structure:** **Tabs** — Overview · Calendar · Classes.
2. **Meeting recurrence:** **Structured** cadence + weekday + week-of-month → auto-computed next date,
   with manual override + "Mark TBA".
3. **Liveliness extras (all four in v1):** guild pulse (activity feed), at-a-glance stat chips,
   get-involved CTA block, lead-pinned featured class/project.

### Feature 5: Join Guild + profile guild badges + directory filter
Most of the join mechanic **already exists** — surface and extend it, don't rebuild:
- `GuildMembership` (`guild`, `member`, `joined_at`, unique together) is a join table → a member can
  already belong to **unlimited** guilds. `guild_join` (`hub/views.py`) is **instant + idempotent**
  (`get_or_create`); `guild_leave` exists; the guild page has a Join/Leave button + `is_member_of_guild`.
- **New work:**
  - **Profile guild badges:** show the guilds a member belongs to (`member.guild_memberships`) as
    icons/chips on their profile + directory card.
  - **Public profiles in rosters:** a member with a public profile (`Member.show_in_directory`) who has
    joined a guild shows up in that guild's member roster (today the roster is gated by the guild-level
    `show_members` toggle; tie it to the member's own public flag too).
  - **Directory filter by guild:** add a `?guild=<id>` filter to `member_directory`
    (`member_qs.filter(guild_memberships__guild=...)`) with a dropdown.

## Build order (phased — each phase is a shippable commit)
1. **Announcements + Next Meeting** (no layout restructure needed): `GuildAnnouncement.expires_at` +
   active-only display moved above About; `Guild` meeting-recurrence fields + `next_meeting_at` +
   sidebar "Next meeting" card + edit-form config. Models, migration, forms, templates, tests.
2. **Tabs + Overview build-out:** restructure to Overview · Calendar · Classes; Overview gets the
   bulletin board (top), upcoming-classes strip, stat chips, and get-involved CTA.
3. **Calendar tab:** refactor `_get_calendar_context` to take an optional `guild` filter; HTMX-loaded
   guild calendar; Classes tab lists the guild's classes.
4. **Liveliness:** guild pulse (activity feed) + lead-pinned featured class/project.
5. **Join surfacing:** profile guild badges + public-profile rosters + member-directory guild filter.

## Testing/verification (when we build)
BDD `*_spec.py`, ≥98% gate: announcement expiry filter; `next_meeting_at` recurrence math (3rd-Thursday,
last-weekday, override wins, TBA); guild-scoped calendar context; upcoming-classes scoping + register
links; edit-permission gating on every new affordance. Manual on `pastlives.test:8000` guild pages.
