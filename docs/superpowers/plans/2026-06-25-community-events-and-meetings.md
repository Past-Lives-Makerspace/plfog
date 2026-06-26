# Community events & meetings (FOG-native events + Discord + calendar) — Spec & Implementation Plan

**Status:** 📋 IMPLEMENTATION PLAN — **NOT YET IMPLEMENTED.** This is a planning document only; none of it is built. Do not treat any model, field, view, or behavior described here as existing in the codebase.
**Date:** 2026-06-25
**Surface:** FOG hub (`pastlives.test:8000`) — guild management pages (reached from `templates/hub/guild_edit.html`) for lead-authored guild events, and the existing **Community Calendar** (`templates/hub/community_calendar.html`), which gains an **"Events" tab** that doubles as the member-facing list and the admin authoring surface.
**Related:**
- **Sibling (depends on — now written):** `docs/superpowers/plans/2026-06-25-discord-notification-routing.md` — the multi-webhook fan-out so a *guild* event posts to the **central** Discord channel **and** the guild's **own** webhook when enabled. This spec passes `guild` in the emit context so that fan-out has what it needs; until the sibling's fan-out ships, a guild event posts to the **central** webhook only (graceful — see §7 / §10). **Build the sibling first or alongside (§10).**
- Mirrors the dedicated-editor + list-page architecture of **Guild Meeting Notes** (`2026-06-24-guild-meeting-notes.md`) and **Guild Orientations** (`2026-06-21-guild-orientations.md`).
- Reuses the event/notification spine (`2026-06-24-notification-architecture-redesign.md`, `2026-06-25-branded-notification-emails.md`).

---

## 1. Summary

Today the Community Calendar can only show events that already live in an external Google/iCal feed — there is **no way for a guild lead or an admin to create an event inside FOG.** This feature makes events **FOG-native**: a guild lead (or any guild staffer) schedules their guild's meetings and events from the guild management area, and an admin schedules **site-wide community events** (One Mic Night, Monthly Potluck) and the cross-guild **Guild Lead Meeting** from the new **"Events" tab on the Community Calendar**. Events can **repeat monthly** (same nth weekday). Every event a member creates immediately appears on the **Community Calendar** (and the guild's own calendar), is exportable in the existing `.ics` download, and fires a **Discord announcement** (central channel for site-wide events; central + the guild's own channel for guild events, via the sibling routing spec) plus an in-app bell. Google Calendar two-way write-back is **explicitly deferred** to a documented Phase 2 — today's one-way iCal pull for Google-made events is untouched.

### Locked decisions

| Decision | Choice |
|---|---|
| Build now vs. defer Google | **Build FOG-native events now.** Google Calendar two-way write-back is a documented **Phase 2** (§10) — not built now. The existing one-way iCal pull into the read-only `CalendarEvent` cache stays as-is. |
| Authoring authority | **Guild leads/staff** create their own guild's meetings & events (gated by `can_edit_guild`). **Admins** create site-wide community events and the Guild Lead Meeting (gated by `view_as.is_admin`). |
| Don't extend `CalendarEvent` | `CalendarEvent` (`membership/models.py:1743`) is a **read-only iCal cache** ("Treat as a read-through cache — do not edit records directly"). A **new `CommunityEvent` model** holds FOG-authored events, in the `Guild*` content cluster. |
| What the Guild Lead Meeting is | A **single cross-guild leadership series** (ALL guild leads), **admin-managed**, **site-level** (`guild` is null). Not per-guild. Surfaced to leads (in-app + Discord) and on the Community Calendar. |
| One Mic Night / Potluck are not types | They are ordinary **`COMMUNITY`** events with a title — **not** separate `event_type` values. The model has exactly three types: `GUILD_MEETING`, `LEAD_MEETING`, `COMMUNITY`. |
| Type ↔ scope invariant | `GUILD_MEETING` ⇒ a guild is required; `LEAD_MEETING` / `COMMUNITY` ⇒ site-wide (`guild` null). Enforced by a `CheckConstraint` + friendly form validation. |
| Publishing | **Automatic on create.** Saving a *new* event posts the Discord/in-app announcement once (idempotent via a unique `period`). **Editing does not re-announce.** The author sees a help line saying so. (No separate "Publish" button — YAGNI.) |
| Event-published email default | **Email channel OFF by default** for the three event events — calendar + Discord + in-app bell is enough; a member who wants emails can opt in. (In-app ON, Discord ON.) |
| Recurrence | **A `recurrence` field: "Does not repeat" / "Repeats monthly."** A monthly event recurs on the **same nth weekday of the month** as its start (e.g. the 2nd Saturday), reusing the existing `_nth_weekday()` helper (`membership/models.py:43`) that already powers guild-meeting cadence. Occurrences are **expanded virtually** inside the calendar's render window (no row-bloat, no cron) and emitted as a single **`RRULE:FREQ=MONTHLY;BYDAY=…`** VEVENT in the `.ics`. **Edit/delete affects the whole series; no per-occurrence exceptions in v1** (§10). The launch announcement fires once for the series. |
| Admin authoring surface | **An "Events" tab on the Community Calendar page** (member-readable upcoming-events **list**; admins additionally get `+ Add` / per-row Edit / Delete) — NOT a `/manage/` admin-nav page. This answers the old "where does the admin nav entry go" open question. Lead authoring stays on the guild page (`/guilds/<pk>/events/`). |
| `google_event_id` | A nullable placeholder field is added now for the future mapping, but **not wired** to anything (§10). |

---

## 2. What already exists (reuse, don't reinvent)

All confirmed in the codebase — the build is assembly. The new model + its CRUD closely mirror **`GuildAnnouncement`** (own form + own save + delete) and the **Guild Meeting Notes** list/edit pages; the datetime fields copy **`OrientationSlotForm`**; the calendar adapter copies **`guild_calendar_entries`**; the Discord/in-app announcement copies the **`guild_announcement`** event.

| Need | Existing thing | Location |
|---|---|---|
| Read-only iCal cache to **not** extend (and the calendar's read source) | `CalendarEvent` ("read-through cache — do not edit records directly") | `membership/models.py:1743` |
| Where the `Guild*` content models live (insertion point) | `Guild` (738), `GuildStaffMembership` (951), `GuildImage` (999), `GuildAnnouncement` (1058), `GuildMeetingNote` (1128), `GuildMembership` (1246) | `membership/models.py` |
| Guild's Discord **display** link (NOT a webhook) | `Guild.discord_url` — "Link to the guild's Discord channel, shown as a button" | `membership/models.py:819` |
| Edit-permission source of truth (lead authoring) | `can_edit_guild(request, guild)` (admin/officer OR lead OR staff membership) | `membership/permissions.py:51` |
| View-level 403 gate (lead routes) | `_require_can_edit_guild(request, guild) -> HttpResponse | None` | `hub/views.py:485` |
| Admin gate pattern (admin routes) | `view_as = getattr(request, "view_as", None); is_admin = view_as is not None and view_as.is_admin` (inline in directory/admin views) | `hub/views.py:~243` |
| The list-page pattern to copy (`+ Add` primary button, per-row `hub-btn--sm` Edit `<a>`, `pl-btn pl-btn--danger pl-btn--sm` Delete → `confirm_modal.html`, empty state) | Guild Meeting Notes list | `templates/hub/guild_meeting_notes.html` (whole file) |
| The list-page views to mirror (list / edit(add) / POST-only delete) | `guild_meeting_notes` (1584), `guild_meeting_note_edit` (1596), `guild_meeting_note_delete` (1634) | `hub/views.py` |
| Their route shape under `/guilds/<pk>/…/` | meeting-notes routes | `hub/urls.py:99-114` |
| ModelForm + "set FK in the view" precedent | `GuildMeetingNoteForm` (no `guild`/`created_by` in the form) | `hub/forms.py:439` |
| Datetime widget to copy verbatim (`datetime-local` + `showPicker` + `input_formats` + end-after-start `clean`) | `OrientationSlotForm` (widget 585-589, `__init__` input_formats 599, `clean` 601) | `hub/forms.py:579` |
| Calendar context builder (merges `CalendarEvent` rows + synthetic entries, sorts by `start_dt`; builds `source_colors` 1814 + per-page `default_filters`) | `_get_calendar_context` (CalendarEvent qs at 1776; guild-entry merge 1785-1788; `source_colors` 1814) | `hub/views.py:1735` |
| **Per-source filter gating** every calendar entry obeys (`x-show="isActive('{{ src }}')"`; `activeFilters` seeded from `default_filters_json`; a source with no default + no button is invisible) | `community_calendar` `default_filters` (1883-1891), `guild_detail` `guild_cal_filters` (~420/423); filter buttons (`community_calendar.html:142-162`, `templates/hub/partials/guild_calendar_app.html:~57-72`); `x-show` (`calendar_content.html:40,91`, `calendar_event_item.html:5`) | `hub/views.py` + templates |
| Community Calendar page + its events partial | `community_calendar` (1878), `calendar_events_partial` (1896) | `hub/views.py`; `templates/hub/community_calendar.html` |
| `.ics` export to extend (note: the loop reads `evt.uid`/`evt.all_day`/`evt.start_dt` — `CommunityEvent` lacks all three, so it needs its **own** VEVENT loop) | `calendar_export_ics` (CalendarEvent qs 1950; VEVENT loop reads `evt.uid`/`evt.all_day` 1967-1985) | `hub/views.py:1943` |
| Synthetic calendar-row duck-type to copy (`pk`, `title`, `start_dt`, `end_dt`, `source`, `url`, `location`, `description`, `all_day`, `guild`, `feed`) + its factory + PK-offset convention | `CalendarEntry` (30), `guild_calendar_entries` (56), `CLASS_PK_OFFSET`/`ORIENTATION_PK_OFFSET` (25/26) | `hub/calendar_entries.py` |
| Single emission point (logs activity, resolves recipients, fans out, dedupes by `period`) | `emit(event_key, *, actor, target, context, …, url, period)` | `core/events/emit.py:43` |
| Event registry + channel specs (`_IN_APP_ON` 143, `_EMAIL_ON`/`_EMAIL_OFF` 144/145, `_DISCORD_ON` 307) + the `EventType` dataclass (~106) + `Recipients` enum (60: `GUILD_MEMBERS` 72, `ALL_ACTIVE_MEMBERS` 82, `GUILD_LEADERSHIP` 70) | `core/events/registry.py` |
| Discord-posting events to mirror | `guild_announcement` (→ `GUILD_MEMBERS`, channels 359-360) / `site_announcement` (→ `ALL_ACTIVE_MEMBERS`, 371-372) | `core/events/registry.py:351-372` |
| Recipient resolvers (read `context["guild"]`) | `guild_members` (149), `all_active_members` (320); admin = `fog_admins` (95); resolver map (397-409) | `core/events/resolvers.py` |
| Curated copy structure (`EventCopy` → `placeholders` + `sample_context` + per-`Channel` copy) | `_CURATED` | `core/events/copy.py:117` |
| Discord delivery today (single webhook resolution) | `webhook_for_event` (54), `post_embed` (126), `global_webhook` (48); `DiscordWebhookRoute` DB override (`core/models.py:1060`) | `core/events/discord.py` |
| Absolute-URL base for notifications | `MEMBER_BASE_URL` (no trailing slash) | `plfog/settings.py:64` |
| Deferred-Google client pattern (`from_site_config()`/`from_settings()`, `enabled` property, blank = disabled, never raises) | `MailchimpClient` (from_site_config 53, enabled 71), `SimplybookClient` (from_settings 57, enabled 66) | `core/integrations/mailchimp.py`, `core/integrations/simplybook.py` |
| Factories to mirror | `GuildFactory` (80), `GuildAnnouncementFactory` (107), `GuildStaffMembershipFactory` (155), `MemberFactory`, `MembershipPlanFactory` | `tests/membership/factories.py` |
| Themed form-control scope (date/select inheriting tokens + `color-scheme` for the picker icon) | `.pl-form-group select` (components.css 340/361), `color-scheme: dark` (378) + `[data-theme="light"]` override (825/835) | `static/css/components.css` |

### Genuine gaps to close (kept small)

1. **No `CommunityEvent` model** — the new model + manager in §4.
2. **No "all guild leads, site-wide" recipient.** Resolvers have `guild_leadership` (per-guild, reads `context["guild"]`) but **no cross-guild "every lead" audience.** The Guild Lead Meeting needs one → add a small `all_guild_leads` resolver + `Recipients.ALL_GUILD_LEADS` (sibling to `fog_admins`). This is a **genuinely new** predicate — there is no existing cross-guild "every lead" query to reuse (the directory `must_show` at `hub/views.py:~244` is `Q(fog_role=ADMIN) | Q(fog_role=GUILD_OFFICER) | Q(led_guilds__isnull=False) | Q(instructor_slug__gt="")`, which keys on `instructor_slug`, not the staff relation — **not** a reuse). The member-side predicate is `Q(led_guilds__isnull=False) | Q(guild_staff_roles__isnull=False) | Q(fog_role=GUILD_OFFICER)`. **Verified accessors (membership/models.py): `led_guilds` (Guild.guild_lead → Member, :749) and `guild_staff_roles` (GuildStaffMembership.member → Member, :976).** NOT `staff_memberships` (that's the *Guild*-side reverse at :970) and NOT `guild_staff_memberships` (does not exist). (§7.1)
3. **No `_require_admin` helper.** The admin gate is inlined (`view_as.is_admin`). Either reuse the inline pattern in the new admin views or add a tiny `_require_admin(request) -> HttpResponse | None` mirroring `_require_can_edit_guild`. (§5.4 — flagged, not assumed.)
4. **Per-guild Discord webhook fan-out** is the **sibling spec's** job, not this one. `Guild.discord_url` is a display link, not a webhook; there is no `Guild.discord_webhook_url` today. This spec only passes `guild` in context so the sibling fan-out can use it. (§7.2 / §10.)

---

## 3. Where the code lives

Mirror the guild-content architecture exactly: **model + business logic in `membership`** (in the `Guild*` content cluster), **CRUD views + templates in `hub`**, **events/copy/resolvers in `core`**, **calendar adapter in `hub/calendar_entries.py`**. No new Django app — everything stays inside the existing coverage/mypy scope.

```
membership/
  models.py
    + CommunityEvent (+ EventType/Recurrence choices, CommunityEventQuerySet, occurrences_in/_occurrence_ordinal)
                                                       # in the Guild* cluster; reuses _nth_weekday@43 for monthly
  migrations/
    + 00xx_communityevent.py                           # CreateModel + 2 CheckConstraints + index
core/
  events/registry.py    + event.guild_published / event.community_published / event.lead_meeting_published
                        + Recipients.ALL_GUILD_LEADS
  events/resolvers.py    + all_guild_leads(context)  (+ map entry)
  events/copy.py         + 3 _CURATED entries (placeholders + sample_context + IN_APP/EMAIL/DISCORD copy)
hub/
  forms.py    + CommunityEventForm  (datetime-local widgets copied from OrientationSlotForm; as_admin flag; recurrence field)
  views.py    + guild_events / guild_event_edit / guild_event_delete        (lead, gated can_edit_guild, guild-scoped fetch)
              + event_edit / event_delete                                    (admin site-wide authoring, gated view_as.is_admin)
              ~ community_calendar       — + upcoming_events + events_can_manage for the new "Events" tab
              ~ _get_calendar_context    — merge community_event_entries(...) into all_events in BOTH branches (~1781 community + ~1788 guild)
              ~ calendar_export_ics      — separate CommunityEvent VEVENT loop, RRULE for monthly (1950)
  urls.py     + 4 lead routes under /guilds/<pk>/events/ ; + 3 admin authoring routes under /events/ (NO list route — the list is the calendar's Events tab)
  calendar_entries.py
              + EVENT_PK_OFFSET = 3_000_000_000 (+ _OCC_STRIDE)
              + community_event_entries(fetch_from, fetch_to, guild=None) -> list[CalendarEntry]   # expands monthly occurrences
templates/hub/
  guild_events.html              # lead list page (mirrors guild_meeting_notes.html)              NEW
  community_event_edit.html      # shared add/edit page (lead + admin, driven by form.as_admin)   NEW
  community_calendar.html        ~ + "Calendar" | "Events" tab bar; the Events tab = member list + admin +Add/Edit/Delete
  guild_edit.html                ~ + "Events" tab-link → hub_guild_events (mirror Orientations link)
tests/
  membership/factories.py        + CommunityEventFactory (traits: guild_meeting / community / lead_meeting)
  membership/community_event_models_spec.py        NEW
  hub/community_events_spec.py                       NEW (lead + admin CRUD, gating, validation, announce)
  hub/community_calendar_spec.py                     ~ + cases: CommunityEvent appears on calendar + export
  core/events/community_event_copy_spec.py           NEW (placeholders == sample_context; resolver audiences)
plfog/version.py                 # version bump + member-friendly changelog (final phase, at build time)
```

---

## 4. Data model (`membership/models.py`)

Placed in the `Guild*` content cluster — after `GuildMeetingNote`/`GuildMembership`, **above** the read-only `CalendarEvent` cache (1743).

### 4.1 `CommunityEvent`

| Field | Type | Notes |
|---|---|---|
| `title` | `CharField(max_length=200)` | `help_text="Event name shown on the calendar — e.g. 'Monthly Potluck'."` |
| `event_type` | `CharField(max_length=20, choices=EventType.choices, default=EventType.GUILD_MEETING)` | The three types below. `help_text="What kind of event this is."` |
| `guild` | `ForeignKey(Guild, null=True, blank=True, on_delete=CASCADE, related_name="events")` | `help_text="The guild this belongs to. Leave blank for a site-wide community or leadership event."` Null = site-wide. |
| `starts_at` | `DateTimeField` | `help_text="When the event starts."` |
| `ends_at` | `DateTimeField` | `help_text="When the event ends."` |
| `location` | `CharField(max_length=200, blank=True, default="")` | `help_text="Where it happens — a room name, address, or a video link. Optional."` |
| `description` | `TextField(blank=True, default="")` | **Plain text** (no Markdown — YAGNI). `help_text="Optional details for members."` |
| `recurrence` | `CharField(max_length=12, choices=Recurrence.choices, default=Recurrence.NONE)` | `help_text="Whether this event repeats. 'Repeats monthly' recurs on the same weekday-of-month as the start (e.g. the 2nd Saturday)."` See §4.1.1. |
| `created_by` | `ForeignKey(AUTH_USER_MODEL, null=True, blank=True, on_delete=SET_NULL, related_name="+")` | Mirrors `GuildAnnouncement.author`. `help_text="Who created this event."` |
| `google_event_id` | `CharField(max_length=1024, blank=True, default="")` | **Placeholder only — not wired (Phase 2, §10).** `help_text="Reserved for a future Google Calendar sync. Not used yet."` |
| `created_at` | `DateTimeField(auto_now_add=True)` | |
| `updated_at` | `DateTimeField(auto_now=True)` | |

```python
class EventType(models.TextChoices):
    GUILD_MEETING = "guild_meeting", "Guild meeting / event"   # a specific guild's — guild required
    LEAD_MEETING  = "lead_meeting",  "Guild Lead Meeting"      # cross-guild leadership — site-wide
    COMMUNITY     = "community",     "Community event"         # site-wide (One Mic Night, Potluck)

class Recurrence(models.TextChoices):
    NONE    = "none",    "Does not repeat"
    MONTHLY = "monthly", "Repeats monthly"

class Meta:
    ordering = ["starts_at"]
    indexes = [models.Index(fields=["starts_at"], name="idx_communityevent_starts")]
    constraints = [
        models.CheckConstraint(
            condition=models.Q(ends_at__gt=models.F("starts_at")),
            name="ck_communityevent_end_after_start",
        ),
        models.CheckConstraint(   # GUILD_MEETING ⇒ has a guild; LEAD/COMMUNITY ⇒ site-wide
            condition=(
                (models.Q(event_type="guild_meeting") & models.Q(guild__isnull=False))
                | (~models.Q(event_type="guild_meeting") & models.Q(guild__isnull=True))
            ),
            name="ck_communityevent_guild_matches_type",
        ),
    ]

def __str__(self) -> str:
    where = self.guild.name if self.guild_id else "Site-wide"
    return f"{self.title} — {where} ({self.starts_at:%Y-%m-%d %H:%M})"
```

> The `end_after_start` constraint mirrors `OrientationAvailability`'s comparison guard (the meeting-notes spec cites the same `CheckConstraint(condition=Q(...))` style at `membership/models.py:~1670`). The friendly per-field message ("End time must be after the start.") lives on the **form** (§5.3); the constraints are DB backstops.

> **`recurrence` is unconstrained vs. `event_type`/`guild`** — any event type may repeat monthly (a monthly potluck, a monthly guild meeting, a monthly Lead Meeting). No new `CheckConstraint`.

### 4.1.1 Monthly recurrence — virtual expansion (reuse the existing helper)

`MONTHLY` events are **not** materialised as rows — one `CommunityEvent` row is the *series*, expanded into occurrences only where they're needed (the calendar render window and the `.ics` horizon). This avoids row-bloat and a materialiser cron, and makes edit/delete naturally affect the whole series.

- **The rule:** a monthly event recurs on the **same nth weekday of the month** as its `starts_at` — e.g. a start of Sat **Jul 12** (the 2nd Saturday) recurs on the 2nd Saturday of each following month. This matches how guild meetings are scheduled and lets us reuse the existing helper rather than write new date math.
- **Reuse `_nth_weekday(month_anchor, weekday, ordinal)`** (`membership/models.py:43`, already `dateutil`-backed). Add a small model method:
  ```python
  def _occurrence_ordinal(self) -> int:
      """Which weekday-of-month the start falls on: 1–4, or -1 for a 5th (treated as 'last')."""
      n = (self.starts_at.day - 1) // 7 + 1
      return -1 if n == 5 else n   # a 5th weekday → 'last' so no month is skipped

  def occurrences_in(self, frm: date, to: date) -> list[datetime]:
      """Start datetimes of every occurrence whose start-date is within [frm, to]."""
      # NONE → [starts_at] if in window; MONTHLY → walk months from starts_at, project the
      # nth weekday via _nth_weekday(...), keep the original time-of-day, stop past `to`.
  ```
  `ends_at` per occurrence = occurrence start + `(self.ends_at - self.starts_at)` (duration preserved).
- **Calendar:** `community_event_entries` (§5.6) calls `occurrences_in(fetch_from, fetch_to)` and emits one `CalendarEntry` per occurrence (synthetic-pk scheme in §5.6).
- **`.ics`:** one VEVENT per series carrying `RRULE:FREQ=MONTHLY;BYDAY=<ord><WD>` (e.g. `BYDAY=2SA`, or `BYDAY=-1FR` for a last-Friday series) — standard, so external calendars expand it themselves (no per-occurrence VEVENTs).
- **Announce once:** the create-time announcement fires for the series, not per occurrence; the copy notes "(repeats monthly)" (§7.1).
- **Edit/delete = whole series**, no per-occurrence exceptions in v1 (§10).

### 4.2 Manager / queryset

```python
class CommunityEventQuerySet(models.QuerySet):
    def upcoming(self):
        # A non-recurring event is upcoming if it hasn't ended; a MONTHLY series is always
        # "upcoming" (it keeps recurring) — its concrete future occurrences are computed by
        # occurrences_in() at render time.
        return self.filter(Q(ends_at__gte=timezone.now()) | ~Q(recurrence="none"))
    def candidates_for_window(self, frm, to):
        # Rows that *might* contribute an occurrence to [frm, to]: a non-recurring event whose
        # start-date is in-window, OR ANY monthly series anchored on/before `to` (its later
        # occurrences are expanded virtually — its anchor may be far in the past).
        return self.filter(
            (Q(recurrence="none") & Q(starts_at__date__gte=frm, starts_at__date__lte=to))
            | (~Q(recurrence="none") & Q(starts_at__date__lte=to))
        )
    def for_guild(self, guild):         return self.filter(guild=guild)
    def site_wide(self):                return self.filter(guild__isnull=True)
```

- **Recurrence changes the window query.** A monthly series anchored in the *past* still has occurrences in a future window, so the calendar adapter (§5.6) selects rows via `candidates_for_window(frm, to)` and then asks each row's `occurrences_in(frm, to)` which concrete dates actually land in the window — a plain `starts_at__date` BETWEEN filter (what `CalendarEvent` uses at 1776) would wrongly drop recurring series. Non-recurring events still filter on the same date boundary as iCal events.
- `objects = CommunityEventQuerySet.as_manager()`.

### 4.3 Properties

```python
@property
def is_site_wide(self) -> bool:
    return self.guild_id is None

@property
def when_display(self) -> str:
    """'Sat, Jul 12 · 6:00–8:00 PM' style — for notification copy and calendar tooltips.
    Appends ' · Repeats monthly' when recurrence == MONTHLY, so the single launch
    announcement makes the cadence clear."""
    ...

@property
def absolute_url(self) -> str:
    """Absolute Community-Calendar URL for notifications (no per-event page in v1)."""
    from django.conf import settings
    from django.urls import reverse
    return f"{settings.MEMBER_BASE_URL}{reverse('hub_community_calendar')}"
```

### 4.4 Migration

One migration: `CreateModel` + the two `CheckConstraint`s + the index. Reverse is the auto-generated `DeleteModel` (no `RunPython`). `ruff format` the generated migration and `git add` it in the same commit (per the migrations-need-ruff-format note).

---

## 5. Business logic (fat model; views stay thin)

### 5.1 `CommunityEvent.announce()` — the one-shot publish

```python
_ANNOUNCE_EVENT = {
    EventType.GUILD_MEETING: "event.guild_published",
    EventType.LEAD_MEETING:  "event.lead_meeting_published",
    EventType.COMMUNITY:     "event.community_published",
}

def announce(self, *, actor=None) -> None:
    """Post the launch announcement (in-app + Discord). Idempotent via `period`."""
    from core.events.emit import emit
    emit(
        self._ANNOUNCE_EVENT[CommunityEvent.EventType(self.event_type)],
        actor=actor,
        target=self,
        context={
            "guild": self.guild,            # → guild_members resolver + the sibling spec's per-guild Discord fan-out
            "guild_name": self.guild.name if self.guild_id else "",
            "event_title": self.title,
            "when": self.when_display,
            "location": self.location,
            "event_url": self.absolute_url,  # ABSOLUTE (MEMBER_BASE_URL) — Discord/email need a full URL
        },
        url=self.absolute_url,
        period=f"event:{self.pk}:published",  # unique per event → announce-once, idempotent backstop
    )
```

- **Called only on create** — `guild_event_edit` / `admin_event_edit` call `event.announce(actor=request.user)` after a *successful first save* (`created` branch), never on edit. The `period` makes a stray double-call a no-op.
- **Side effects:** one in-app bell per resolved recipient, one Discord post (central today; central + guild webhook once the sibling ships), `activity_kind=None` so no `SiteActivity` row (an event posting is a quiet content action). **Precedent note:** this is a *deliberate* choice, not a match to `guild_announcement` — that event actually DOES write a `SiteActivity` (`activity_kind="guild_announcement"`, `registry.py:361`); we follow the quieter meeting-notes pattern instead. Best-effort Discord (`post_embed` no-ops on failure).
- The `guild` object rides in `context` purely for the resolver + Discord routing; the copy layer only substitutes the documented **string** placeholders.

### 5.2 Validation lives on the form (§5.3), not the view. The model trusts the constraints as backstops.

### 5.3 `CommunityEventForm` (`hub/forms.py`)

A single `ModelForm`, parameterized by context so the **same form** serves both the lead and admin pages:

```python
class CommunityEventForm(forms.ModelForm):
    class Meta:
        model = CommunityEvent
        fields = ["event_type", "guild", "title", "starts_at", "ends_at", "location", "description", "recurrence"]
        # `recurrence` is offered to BOTH lead and admin (any event type may repeat monthly);
        # only `event_type`/`guild` are deleted for the lead variant in __init__.
        widgets = {  # copied verbatim from OrientationSlotForm (hub/forms.py:585-589)
            "starts_at": forms.DateTimeInput(
                attrs={"type": "datetime-local", "onclick": "this.showPicker?.()"}, format="%Y-%m-%dT%H:%M"),
            "ends_at": forms.DateTimeInput(
                attrs={"type": "datetime-local", "onclick": "this.showPicker?.()"}, format="%Y-%m-%dT%H:%M"),
        }

    def __init__(self, *args, guild=None, as_admin=False, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("starts_at", "ends_at"):     # mirror OrientationSlotForm.__init__ (599)
            self.fields[name].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"]
        self._as_admin = as_admin
        self._fixed_guild = guild
        if not as_admin:                          # lead: event_type + guild are implied by context
            del self.fields["event_type"]
            del self.fields["guild"]
        else:                                      # admin: guild optional; required iff GUILD_MEETING
            self.fields["guild"].required = False

    def clean(self):
        cleaned = super().clean()
        starts, ends = cleaned.get("starts_at"), cleaned.get("ends_at")
        if starts and ends and ends <= starts:
            self.add_error("ends_at", "End time must be after the start.")
        if self._as_admin:
            etype = cleaned.get("event_type")
            guild = cleaned.get("guild")
            if etype == CommunityEvent.EventType.GUILD_MEETING and guild is None:
                self.add_error("guild", "Pick a guild for a guild event.")
            if etype in {CommunityEvent.EventType.LEAD_MEETING, CommunityEvent.EventType.COMMUNITY} and guild is not None:
                self.add_error("guild", "Leave the guild blank for a site-wide event.")
        return cleaned
```

- **Lead path:** the form has **no** `event_type`/`guild` fields; the view sets `instance.event_type = GUILD_MEETING`, `instance.guild = <context guild>`, `instance.created_by = request.user` (on create).
- **Admin path:** `event_type` renders as a themed `<select>` (via `form_field.html`); `guild` is a `ModelChoiceField` left blank for site-wide. The invariant is validated with friendly per-field errors (the `CheckConstraint` is the backstop).
- No "future-only" guard (unlike `OrientationSlotForm`) — an admin may legitimately log a just-passed or in-progress event; the calendar simply renders it.

### 5.4 Views (`hub/views.py`, all `@login_required`, thin)

| View | Job | Gate |
|---|---|---|
| `guild_events(request, pk)` | List this guild's events (`guild.events.upcoming()`), Edit/Delete each, `+ Add`. | `_require_can_edit_guild` (485) |
| `guild_event_edit(request, pk, event_pk=None)` | Add (no `event_pk`) or edit. **On edit, fetch guild-scoped** (see mandate below). Binds `CommunityEventForm(guild=guild, as_admin=False)`; on **create** sets `guild`/`event_type=GUILD_MEETING`/`created_by`, saves, **calls `event.announce(actor=request.user)`**, `messages.success`, redirect to `hub_guild_events`. On edit: save, no announce. | `_require_can_edit_guild` |
| `guild_event_delete(request, pk, event_pk)` | `@require_POST`; **fetch guild-scoped** (see mandate below); delete; `messages.success`; redirect. | `_require_can_edit_guild` |

> **Authz boundary (BLOCKING — make it explicit, don't leave it implicit in "mirror meeting-notes").** The lead edit/delete routes are `/guilds/<pk>/events/<event_pk>/…`. `_require_can_edit_guild` only checks that the lead may edit **the guild at `<pk>`** — it says nothing about `<event_pk>`. So both `guild_event_edit` and `guild_event_delete` MUST fetch the event scoped to that guild: **`event = get_object_or_404(CommunityEvent, pk=event_pk, guild=guild)`** (exactly the meeting-notes precedent, `get_object_or_404(GuildMeetingNote, pk=note_pk, guild=guild)` at `hub/views.py:1605`). Without the `guild=guild` filter, a lead of guild A passes the gate with A's `pk` but supplies an `event_pk` belonging to guild B and mutates/deletes B's event. A cross-guild **isolation test** is required in §9 (a lead of A gets 404 editing/deleting B's `event_pk`), not just same-guild gating.
| `event_edit(request, event_pk=None)` | The **admin/site-wide** add/edit (reached from the calendar's Events tab). Add/edit with `CommunityEventForm(as_admin=True)`; on create sets `created_by`, saves, **announces**, redirect back to the **Events tab** (`hub_community_calendar` + `?tab=events`). | admin (`view_as.is_admin`) |
| `event_delete(request, event_pk)` | `@require_POST`; delete; redirect to the Events tab. | admin |

> **No separate `admin_events` list view / `/manage/events/` page.** The admin-facing list of all events **is the "Events" tab on the Community Calendar** (§6 Screen B). The `community_calendar` view (below) gains the list data; `event_edit`/`event_delete` are the only new admin endpoints (the authoring pages), reached from that tab.

Plus the **edits to existing views** (inclusion + the filter wiring that actually makes events visible — §5.6): `_get_calendar_context` merges `community_event_entries(...)` into `all_events` **in both the guild branch (~1788) AND the `guild is None` community branch (~1781, which needs a new merge + sort — see §5.6)** **and** adds `source_colors["community"]` (1814); `community_calendar` (1883-1891) and `guild_detail` (`guild_cal_filters`, ~420) add `"community"` to `default_filters`; `calendar_export_ics` gets a **separate** `CommunityEvent` VEVENT loop (§5.6 — it cannot reuse the `CalendarEvent` loop, which reads `evt.uid`/`evt.all_day`).

**`community_calendar` also feeds the new "Events" tab (§6 Screen B/D):** it adds `upcoming_events = CommunityEvent.objects.upcoming().select_related("guild")` and `events_can_manage = bool(view_as and view_as.is_admin)` to the context, so the page can render a member-readable upcoming-events **list** with `+ Add`/Edit/Delete controls shown **only** to admins. (No new list view — it's the same page, a second tab.)

### 5.5 URLs (`hub/urls.py`)

```python
# Lead (mirror the meeting-notes routes at 99-114)
path("guilds/<int:pk>/events/",                       views.guild_events,      name="hub_guild_events"),
path("guilds/<int:pk>/events/add/",                   views.guild_event_edit,  name="hub_guild_event_add"),
path("guilds/<int:pk>/events/<int:event_pk>/edit/",   views.guild_event_edit,  name="hub_guild_event_edit"),
path("guilds/<int:pk>/events/<int:event_pk>/delete/", views.guild_event_delete, name="hub_guild_event_delete"),
# Admin (site-wide authoring) — the LIST is the Events tab on the Community Calendar
# (hub_community_calendar?tab=events), so there is NO separate list route. Only the
# authoring endpoints are new, mounted under the calendar's URL space:
path("events/add/",                   views.event_edit,   name="hub_event_add"),
path("events/<int:event_pk>/edit/",   views.event_edit,   name="hub_event_edit"),
path("events/<int:event_pk>/delete/", views.event_delete, name="hub_event_delete"),
```

### 5.6 Calendar adapter (`hub/calendar_entries.py`)

```python
EVENT_PK_OFFSET = 3_000_000_000   # distinct from CLASS_PK_OFFSET (1e9) and ORIENTATION_PK_OFFSET (2e9)
_OCC_STRIDE = 100                  # max occurrences per event per window (a few months of monthly → << 100)

def community_event_entries(fetch_from, fetch_to, guild=None) -> list[CalendarEntry]:
    qs = CommunityEvent.objects.candidates_for_window(fetch_from, fetch_to)  # incl. past-anchored monthly series
    if guild is not None:
        qs = qs.for_guild(guild)
    entries: list[CalendarEntry] = []
    for ev in qs.select_related("guild"):
        duration = ev.ends_at - ev.starts_at
        for i, occ_start in enumerate(ev.occurrences_in(fetch_from, fetch_to)):  # 1 for NONE, N for MONTHLY
            entries.append(CalendarEntry(
                # Unique synthetic pk per occurrence: base offset + ev.pk*stride + occurrence index.
                pk=EVENT_PK_OFFSET + ev.pk * _OCC_STRIDE + i,
                title=ev.title, start_dt=occ_start, end_dt=occ_start + duration,
                source="community", url=ev.absolute_url,
                location=ev.location, description=ev.description,
                all_day=False, guild=ev.guild,
            ))
    return entries
```

In `_get_calendar_context` (1735) — **two distinct branches, and the community one is NOT a one-line edit:**
- **Guild calendar (`guild is not None`):** merge `community_event_entries(fetch_from, fetch_to, guild=guild)` right beside the existing `guild_calendar_entries(...)` call (the `if guild is not None:` block, ~1785-1788), then the list is already re-sorted by `start_dt`.
- **Community calendar (`guild is None`):** ⚠ the `guild is None` path (~1781) currently builds `all_events` with **no synthetic merge and no re-sort at all** (the synthetic entries are guild-only today). So this branch needs a **NEW** merge of `community_event_entries(fetch_from, fetch_to)` (all in-window events — site-wide + every guild's) **into `all_events` AND a new `sorted(..., key=lambda e: e.start_dt)`** — do **not** just edit line 1788 (that's inside the guild branch; editing only it surfaces community events on guild calendars but **silently not** on the Community Calendar, breaking the headline feature). The §9 test asserts inclusion via `_get_calendar_context(request)` (the community path) precisely to catch this.

The combined list, once merged + sorted in each branch, interleaves FOG events with iCal/class/orientation entries.

**The merge is necessary but NOT sufficient — every calendar entry is filter-gated.** `calendar_content.html:40,91` and `calendar_event_item.html:5` wrap each entry in `x-show="isActive('{{ event.source_key }}')"`, and `isActive(key)` is `activeFilters.includes(key)` (`community_calendar.html:61`). `activeFilters` seeds from `default_filters_json`; a `source="community"` key that is in **neither** the defaults **nor** a filter button is **invisible**. So this feature must wire `"community"` through as a first-class source in **four** places:

1. **`source_colors["community"]`** in `_get_calendar_context` (beside `{"classes": classes_color, "orientation": "#EEB44B"}` at 1814) — without it the chip/dot falls back to `#888888`. Use the existing `--hub-blue` value `#3d8bd4` (an existing brand token, not a new color — **flag for confirmation**, §10) so community events read distinctly from classes/orientation/guild colors. Pass it on as a `community_color` context key for the filter button.
2. **`default_filters`** — append `"community"` in **both** `community_calendar` (1883-1891) and `guild_detail`'s `guild_cal_filters` (~420, json at 423) so the source is **on by default**.
3. **A filter button** in **both** `community_calendar.html` (beside the classes/guild buttons at 142-162) and `templates/hub/partials/guild_calendar_app.html` (filter buttons at ~57-72): `<button class="pl-calendar-filter" :class="{ 'pl-calendar-filter--active': isActive('community') }" @click="toggleFilter('community')" style="--filter-color: {{ community_color }};">Community events</button>`. (Inline `--filter-color` matches the existing legend pattern — a data-driven CSS var, not a hardcoded control color.)
4. **Rollout for returning users (§6 Screen D) — DECIDED: union-on-init.** `activeFilters` falls back to `default_filters_json` **only when localStorage is empty** (`community_calendar.html:51`; `guildCalFilters-{pk}` at `templates/hub/partials/guild_calendar_app.html:11`). Anyone who has opened the calendar before has a stored set with **no** `community` key, so without a rollout they'd see nothing. **Chosen approach:** in the `x-data` initializer, union any `default_filters` source not already tracked into the stored `activeFilters` on load, recording seen sources in a `seenSources` list so a user's explicit *removal* of a pre-existing source is preserved while a newly-shipped source (community) defaults **on**. (Rejected the simpler localStorage key-bump because it discards every member's existing filter prefs.)

**Calendar entry end-time display (`templates/hub/partials/calendar_event_item.html`).** The entry template appends the **end** time only for orientations — `…{% if event.source == "orientation" %}–{{ event.end_dt|… }}{% endif %}` at `:14`. A `source="community"` entry would therefore show **start time only** (so "Monthly Potluck 6:00–8:00 PM" renders as just "6:00 PM"). Extend that condition to include community, e.g. `{% if event.source == "orientation" or event.source == "community" %}`, so community events show the full start–end range. (If we deliberately accept start-only, say so — but the range is expected here.)

**Acknowledged, not-a-bug behaviors (so they aren't later filed as defects).** Because every community-event row is stamped `source="community"`: (a) a guild's **own** FOG meeting renders under the **"Community events"** filter/color (`--hub-blue`), grouped separately from that guild's iCal events (which use the guild's own `source=str(guild.pk)` color) — internally consistent with the single-source design; and (b) a site-wide community event with no guild/feed falls through `calendar_event_item.html`'s `{% else %}` (`:52`) to the **"General"** footer label. Both are harmless and intentional given the single-source model; documented here so a future reader doesn't read them as bugs.

The community-events `.ics` export needs a **separate VEVENT loop** in `calendar_export_ics` (1943) — the existing loop reads `evt.uid` (1969), `evt.all_day` (1974), `evt.start_dt`/`evt.end_dt`, none of which `CommunityEvent` (or the `CalendarEntry` wrapper) has. Add a second loop over `CommunityEvent.objects.upcoming().select_related("guild")` that emits **one VEVENT per series** reading `ev.starts_at`/`ev.ends_at`, synthesizing `UID:community-{ev.pk}@pastlives` and treating them as timed (never all-day), with `SUMMARY`/`DESCRIPTION`/`LOCATION` from the event. **For a `MONTHLY` event, add an `RRULE:FREQ=MONTHLY;BYDAY=<ord><WD>`** line (ord from `_occurrence_ordinal()` → `1`–`4` or `-1`; WD = the 2-letter iCal weekday of `starts_at`, e.g. `2SA`, `-1FR`) so subscribers expand the series themselves — do **not** emit per-occurrence VEVENTs in the `.ics`. Do **not** drop raw `CommunityEvent` rows into the `CalendarEvent` loop.

---

## 6. UI / UX  — completeness checklist applied per screen

Five surfaces. All use `<div class="hub-card">` sections, `pl-`/`hub-` classes, **theme tokens only**, and the component library. **No form control is ever inline-styled with `background`/`color`** (FRONTEND rule 13) — every field renders through `form_field.html`'s `.pl-form-group` wrapper, which carries the input tokens **and** the `color-scheme` rules that fix the native date picker in both themes (components.css 361/378/835).

> **Note on the "list editor" rubric (§1 of the checklist):** this feature's repeated thing is a list of **top-level events, each edited on its own dedicated page** — exactly like Guild Meeting Notes, whose list page is **not** a Django formset. So the famous three controls map as: **"+ Add event"** = the primary button on the list page; **per-row Delete** = a real `pl-btn pl-btn--danger pl-btn--sm` button → `confirm_modal.html` (never a toggle); **Save** = the primary button on the edit page. The `extra=0` + clone-`empty_form` sub-row pattern is **N/A** here (CommunityEvent has no child collection / no inline formset) — called out explicitly so a reviewer doesn't read it as a missing control.

---

### Screen A — Lead events list (`templates/hub/guild_events.html`)

- **Layout:** mirrors `guild_meeting_notes.html` exactly — a "← Back to {{ guild.name }}" ghost button, `<h1 class="hub-page-title">Events · {{ guild.name }}</h1>`, a one-line muted description, a `+ Add event` primary button, then a `hub-card` holding one row per event.
- **Reached from:** an **"Events" tab-link** on `guild_edit.html` rendered as an `<a>` (mirror the Orientations tab-link), plus optionally a staff "Manage events" button on the guild page (nicety, not required).
- **Components:** `confirm_modal.html` per row (delete).
- **Controls, named:**
  - **`+ Add event`** — `<a class="pl-btn pl-btn--primary">` → `hub_guild_event_add`. The page's obvious primary action.
  - **Per-row Edit** — `<a class="hub-btn hub-btn--sm">` → `hub_guild_event_edit`.
  - **Per-row Delete** — `<button class="pl-btn pl-btn--danger pl-btn--sm" @click="$dispatch('open-confirm','{{ confirm_id }}')">Delete</button>` + a sibling `{% include "components/confirm_modal.html" with confirm_id=… confirm_title="Delete this event?" confirm_message="This removes '{{ event.title }}' from the calendar. This can't be undone." confirm_action_url=delete_url confirm_button_text="Delete event" %}`. The modal's form does the full-page POST → `messages.success` → redirect back. (`confirm_id` built per row like the meeting-notes `{% with confirm_id=… %}` block.)
- **Row content:** bold `{{ event.title }}`; a muted sub-line `{{ event.starts_at|date:"D, M j · g:i A" }}–{{ event.ends_at|date:"g:i A" }}{% if event.location %} · {{ event.location }}{% endif %}`. (Event **type** isn't shown here — every event on a lead's page is their guild's. The muted-text approach avoids introducing a new pill class; an optional `.pl-`-prefixed type pill is a later nicety, §10.)
- **States:**
  - **Empty:** `No upcoming events yet — add one.` + the `+ Add event` button (never a bare blank region).
  - **Loading:** none — server-rendered; the confirm modal is client-side Alpine.
  - **Error:** delete of a missing event → 404 (`get_object_or_404`); non-staff → 403 (`_require_can_edit_guild`).
  - **Success:** Django `messages.success` ("Event deleted." / "Event saved.") on redirect — full-page actions use messages, not toasts (FRONTEND interaction table).
- **Dark + light:** all `hub-card` + `pl-btn`/`hub-btn` tokens; no raw inputs on this page. **Verify both themes.**
- **Mobile:** the row is `display:flex; flex-wrap:wrap` (copy the meeting-notes row) so title takes `flex:1; min-width:220px` and the Edit/Delete buttons wrap below on narrow widths, staying full-size tap targets. No horizontal scroll.

---

### Screen B — "Events" tab on the Community Calendar (`templates/hub/community_calendar.html`)

The site-wide events surface is **not** a separate admin page — it's a **second tab on the Community Calendar**, so members get an upcoming-events **list** view (a nice complement to the grid) and admins manage from the same place.

- **Tab bar:** add a `.vote-tab` / `.vote-tab--active` bar at the top of `community_calendar.html` (the same Alpine `section`-toggle pattern as `guild_edit.html`), deep-linkable via `?tab=` (default `calendar`): **"Calendar"** (the existing grid + filters, untouched) and **"Events"** (the list). Toggling is client-side; both tab bodies render in the one page from the context the `community_calendar` view already supplies (`upcoming_events`, `events_can_manage` — §5.4).
- **Events tab body (member-readable list):** mirrors Screen A's row layout — each event a `hub-card` row with bold `{{ event.title }}` and a muted sub-line `{{ event.get_event_type_display }} · {{ event.guild.name|default:"Site-wide" }} · {{ event.starts_at|date:"D, M j · g:i A" }}{% if event.recurrence != "none" %} · Repeats monthly{% endif %}`. Every member sees the list (read-only).
- **Admin controls (gated `{% if events_can_manage %}`):** a `+ Add event` primary button (→ `hub_event_add`) above the list, and per-row **Edit** (`<a class="hub-btn hub-btn--sm">` → `hub_event_edit`) + **Delete** (`<button class="pl-btn pl-btn--danger pl-btn--sm">` → `confirm_modal.html` → `hub_event_delete`). Non-admins simply don't see these (no `+ Add`, no row buttons) — the list is read-only for them.
- **Empty state:** for admins, `No upcoming events yet — add a community event or a Guild Lead Meeting.` + the `+ Add event` button; for members, `No upcoming events yet.` (no button).
- **States / dark+light / mobile:** identical to Screen A (full-page actions → Django `messages` on redirect back to `?tab=events`; rows reflow `flex-wrap`; theme tokens only). **Gate:** the *list* is visible to any logged-in member; the *authoring endpoints* (`event_edit`/`event_delete`) are `view_as.is_admin` → 403 otherwise (so a member who hand-crafts the URL can't add/edit).

---

### Screen C — Add / Edit event (`templates/hub/community_event_edit.html`, shared by lead + admin)

A **dedicated page** (not a modal): 5–7 fields incl. two datetime pickers (FRONTEND interaction table: 4+ fields → dedicated page).

- **Form:** `<form method="post" class="hub-form">` (no file inputs → no `enctype`). One `hub-card` of fields, then a Save/Cancel row. The form posts to the current URL (works for both add and edit); `cancel_url` is passed in context (lead → `hub_guild_events`; admin → the Community Calendar **Events tab**, `hub_community_calendar?tab=events`).
- **Components:** `form_field.html` for **every** field.
- **Fields (admin sees all; lead sees the subset):**
  - *(admin only — guard the render)* `{% if form.event_type %}{% include "components/form_field.html" with field=form.event_type %}{% endif %}` — themed `<select>` (Guild meeting / Guild Lead Meeting / Community event). **The `{% if %}` is required:** the lead form `del`etes `event_type` and `guild` in `__init__`, so an unguarded `{{ form.event_type }}` reference renders empty/odd on the lead page — the shared template MUST guard both admin-only fields.
  - *(admin only — guard the render)* `{% if form.guild %}{% include "components/form_field.html" with field=form.guild field_hint="Pick a guild for a guild event; leave blank for a site-wide event." %}{% endif %}` — a `<select>`; blank allowed.
  - `{% include "components/form_field.html" with field=form.title %}` — required.
  - `{% include "components/form_field.html" with field=form.starts_at %}` and `…with field=form.ends_at` — native `datetime-local` inputs. The picker icon is theme-correct via `.pl-form-group`'s `color-scheme` (no manual `filter: invert` needed — same as the working OrientationSlot editor), and the whole field opens the picker via the widget's `onclick="this.showPicker?.()"` (FRONTEND rule 14, satisfied by the copied widget).
  - `{% include "components/form_field.html" with field=form.location field_hint="A room, address, or video link. Optional." %}`.
  - `{% include "components/form_field.html" with field=form.description field_hint="Optional details for members." %}` — the `<textarea>` renders **inside `.pl-form-group`**, so it inherits the input tokens and is **not** a bare white box (FRONTEND rule 13).
  - `{% include "components/form_field.html" with field=form.recurrence field_hint="A monthly event repeats on the same weekday-of-month as the start (e.g. the 2nd Saturday)." %}` — themed `<select>` (Does not repeat / Repeats monthly). Shown to **both** lead and admin.
- **Publish help line:** directly above Save, a muted line: **"Saving a new event posts an announcement to Discord and adds it to the Community Calendar. Editing an existing event won't re-announce it."** This surfaces the auto-publish decision to the author.
- **Save/submit:** a `pl-btn pl-btn--primary` **"Save event"** + a `pl-btn pl-btn--secondary` **"Cancel"** `<a href="{{ cancel_url }}">`, in a flex row with `gap:1rem`. On valid POST → save, announce-on-create, `messages.success("Event saved.")`, redirect to the list.
- **Validation messages (explicit):**
  - Missing `title`/`starts_at`/`ends_at` → inline field error via `form_field.html`.
  - `ends_at <= starts_at` → "End time must be after the start." on `ends_at`.
  - *(admin)* `GUILD_MEETING` with no guild → "Pick a guild for a guild event."; site-wide type with a guild → "Leave the guild blank for a site-wide event."
- **States:**
  - **Empty (add mode):** blank fields; the page is the form (no confusing blank region).
  - **Loading:** none (synchronous full-page POST). The announce/Discord post is best-effort and inline — the redirect doesn't wait on Discord beyond the (no-op-on-failure) HTTP call.
  - **Error:** re-renders 200 with bound values + inline errors; no redirect, no lost input, no 500.
  - **Success:** redirect + green Django message.
- **Dark + light:** every control through `form_field.html`/`.pl-form-group` → theme input tokens; **no inline `background`/`color`**, **no `var(--surface)`**. The closed `event_type`/`guild` `<select>` controls are themed by `.pl-form-group select` (components.css:361). **But the precedent for the picker (`OrientationSlotForm`) has no `<select>`, and `color-scheme` is applied to `.pl-form-group` date/time inputs, NOT to `select`** — so the native **option popup** is unstyled and risks white-on-white on Obsidian. Validate the option popup against an existing hub `<select>` (the voting-page guild preference selects), and **add `.pl-form-group select option { background: var(--hub-elevated); color: var(--hub-text); }` in `components.css` (treat as required, per FRONTEND rule 13 — not optional).** If an admin-only Alpine `x-show` is added to hide the guild picker for site-wide types, put `display` in a CSS class, never inline (FRONTEND rule 12).
- **Mobile:** single-column fields; the datetime inputs are full-width; Save/Cancel stack on narrow widths; tap targets are real buttons.

---

### Screen D — Community Calendar (`templates/hub/community_calendar.html`, read)

- **New tab bar:** the page now has two tabs — **"Calendar"** (this screen, the grid) and **"Events"** (the list + admin authoring, Screen B). `.vote-tab` pattern, `?tab=` deep-link, default `calendar`. The calendar grid below is otherwise unchanged.
- **Change:** FOG events appear alongside iCal/class/orientation entries once `_get_calendar_context` merges `community_event_entries(...)` (§5.6). **The merge alone is not enough** — every entry is `x-show="isActive(src)"`-gated, so the four-part filter wiring in §5.6 (source_colors + default_filters in both views + a filter button in both templates + the returning-user rollout) is **mandatory**, not optional. Without it, community events render hidden and the feature's headline promise is silently false.
- **Source filter button:** a new **"Community events"** button in the filter row (`community_calendar.html:142-162`, and `templates/hub/partials/guild_calendar_app.html:~57-72` for the guild calendar), styled `style="--filter-color: {{ community_color }};"` exactly like the classes/feed/guild buttons — a real tap target, `pl-calendar-filter--active` when on.
- **Filter color source:** `community_color` comes from `source_colors["community"]` (§5.6) — the `--hub-blue` hex `#3d8bd4` (existing token, **flag before finalizing**, §10), not a new color and not an unsourced literal.
- **Rollout (returning users):** the initializer must union new default sources into the stored `activeFilters` (or bump the localStorage key) so members who opened the calendar before this ships still see community events — see §5.6 item 4 and the §10 decision.
- **States:** empty/loading/error are unchanged (the calendar already handles an empty window + the HTMX week/month paging in `calendar_events_partial`). A FOG event with no `location`/`description` renders those empty — no 500.
- **Dark + light + mobile:** inherited from the existing calendar; the only new control is the filter button (reuses `.pl-calendar-filter`). Verify both themes — the chip/dot color is a data-driven CSS var, theme-neutral.

---

### Screen E — Guild detail calendar (read, optional surfacing)

The guild's own calendar tab already calls `_get_calendar_context(request, guild=guild)` (`hub/views.py:418`), so once §5.6 merges `community_event_entries(..., guild=guild)` there, that guild's events flow into the entry list. **This still needs the guild-calendar filter wiring** (a `"community"` button in `templates/hub/partials/guild_calendar_app.html:~57-72` + `"community"` in `guild_detail`'s `guild_cal_filters` at ~420) — it is **not** automatic, because the guild calendar gates entries the same way (`isActive('community')`). With that wiring, guild events show on the guild calendar. (A separate "Upcoming events" list on the guild Overview is a nicety, **out of scope**, §10.)

---

## 7. Notifications / Discord / activity

### 7.1 Event registry + resolvers + copy

Three new events (in `core/events/registry.py`, beside `guild_announcement`/`site_announcement` 351-372), each `category="Events"`, `activity_kind=None`, channels **`(_IN_APP_ON, _EMAIL_OFF, _DISCORD_ON)`** (in-app on, Discord on, email off-by-default):

| Event key | Fired for | recipient | Discord routing |
|---|---|---|---|
| `event.guild_published` | `GUILD_MEETING` | `Recipients.GUILD_MEMBERS` (existing `guild_members` resolver, reads `context["guild"]`) | central **+ the guild's own webhook** (sibling spec, via `context["guild"]`) |
| `event.community_published` | `COMMUNITY` | `Recipients.ALL_ACTIVE_MEMBERS` (existing `all_active_members`) | central only |
| `event.lead_meeting_published` | `LEAD_MEETING` | **`Recipients.ALL_GUILD_LEADS` (NEW)** | central only |

**New gap to close (§2.2):** `Recipients.ALL_GUILD_LEADS` + an `all_guild_leads(context)` resolver in `core/events/resolvers.py` (sibling to `fog_admins` at 95; registered in the resolver map 397-409). This is a **genuinely new** cross-guild audience — no existing predicate covers it. It returns every active lead/officer/staffer site-wide:
```python
Member.objects.active().filter(
    Q(led_guilds__isnull=False)                       # leads a guild (Guild.guild_lead → Member, related_name=led_guilds @749)
    | Q(guild_staff_roles__isnull=False)              # holds a GuildStaffMembership (member FK related_name=guild_staff_roles @976)
    | Q(fog_role=Member.FogRole.GUILD_OFFICER)        # site-wide guild officer
).distinct()
```
…then `_members_to_recipients(..., "all_guild_leads")` (drops no-account/no-email members, like the other resolvers). It takes **no** `context["guild"]` (it's cross-guild) — that's the whole point of the Guild Lead Meeting. **Accessor note (verified, do NOT re-guess at build):** the member-side reverse of `GuildStaffMembership` is **`guild_staff_roles`** (`:976`); `staff_memberships` (`:970`) is the *Guild* side, and `guild_staff_memberships` does not exist. (The `guild_members` resolver uses `guild_memberships__guild` for the separate *membership* relation.)

Curated copy (`core/events/copy.py` `_CURATED`, structure at 117) — keep `placeholders` and `sample_context` in **lock-step** (a test asserts it, §9):
- `event.guild_published`: placeholders `(guild_name, event_title, when, location, event_url)`. IN_APP subject "New {{ guild_name }} event: {{ event_title }}", body "{{ event_title }} — {{ when }}{% if location %} · {{ location }}{% endif %}."; DISCORD similar; EMAIL copy present (used only if a member opts in).
- `event.community_published`: placeholders `(event_title, when, location, event_url)`.
- `event.lead_meeting_published`: placeholders `(event_title, when, location, event_url)`.

**Deploy step:** run `python manage.py seed_notification_templates` after deploy — `seedable_rows()` iterates all events, so the three new ones get DB copy rows automatically.

### 7.2 Discord routing — what's this spec vs. the sibling

- **Today:** `webhook_for_event(event_key)` (`core/events/discord.py:54`) resolves **one** webhook (DB `DiscordWebhookRoute` override → in-code `EVENT_WEBHOOK_OVERRIDES` → `global_webhook()`), and `post_embed` posts once. So **right now**, all three events post to the **central** webhook only.
- **The sibling spec (`2026-06-25-discord-notification-routing.md`)** adds the per-guild leg: a `Guild.discord_webhook_url` field + a fan-out that, when the emit `context` carries a `guild` with a webhook, posts to **central AND** that guild's webhook. **This spec's only obligation is to pass `guild` in the context** (it does — §5.1), so the leg lights up the moment the sibling ships, with **no change here**. Until then, guild events degrade gracefully to central-only.

### 7.3 Activity log

`announce()` writes **no `SiteActivity`** (`activity_kind=None`) — creating an event is a quiet content action. This is a deliberate choice following the meeting-notes pattern; note `guild_announcement` itself **does** write a `SiteActivity` (`activity_kind="guild_announcement"`, `registry.py:361`), so this differs from it on purpose. The calendar + Discord + in-app bell are the visible trail.

---

## 8. Build order (phased; each phase ships green)

Each phase lands green (full suite + `ruff format`/`ruff check` + `mypy`), run in the `plfog-web` Docker image. **The announce() call is wired in Phase 3** — Phases 1–2 save events without announcing, so the suite stays green before the events are registered.

1. **Model + lead CRUD + admin authoring endpoints (no calendar, no Discord).** `CommunityEvent` (incl. `EventType`/`Recurrence`, `occurrences_in`/`_occurrence_ordinal` reusing `_nth_weekday`) + `CommunityEventQuerySet` + 2 constraints + migration; `CommunityEventFactory`; `CommunityEventForm` (incl. `recurrence`); the lead views (`guild_events`/`guild_event_edit`/`guild_event_delete`, **guild-scoped fetch**) + the admin authoring endpoints (`event_edit`/`event_delete`, `view_as.is_admin`); the 4 lead + 3 admin routes; `guild_events.html`, `community_event_edit.html`; the `guild_edit.html` "Events" tab-link. **Model (incl. monthly `occurrences_in`) + form + view-gating + cross-guild-isolation + validation specs.** *(Leads fully manage their guild's events on the guild page; admins can add/edit via URL; nothing notifies or hits the calendar yet.)*
2. **Community Calendar display + the "Events" tab.** `EVENT_PK_OFFSET`/`_OCC_STRIDE` + `community_event_entries(...)` (with **monthly occurrence expansion**) in `calendar_entries.py`; merge into `_get_calendar_context` in **both** branches (community ~1781 + guild ~1788); the **full filter wiring** (§5.6): `source_colors["community"]` + `community_color`, `"community"` in both `default_filters` sets, a **"Community events"** filter button in `community_calendar.html` **and** `templates/hub/partials/guild_calendar_app.html`, the returning-user `activeFilters` union-rollout, and the `calendar_event_item.html` end-time fix; the **"Calendar" | "Events" tab bar** on `community_calendar.html` (member list + admin `+Add`/Edit/Delete via `upcoming_events`/`events_can_manage`); a **separate `CommunityEvent` VEVENT loop** in `calendar_export_ics` (RRULE for monthly). **Calendar-inclusion (incl. a monthly series' future occurrences) + visibility-gating + Events-tab + export specs.** *(Events show on the calendar grid, the Events tab, and `.ics`; admins manage from the Events tab.)*
3. **Discord + in-app announcement.** `Recipients.ALL_GUILD_LEADS` + `all_guild_leads` resolver; register the three `event.*_published` events; curated copy (placeholders == sample_context); add `CommunityEvent.announce()` + wire the create branch of both edit views to call it; re-seed note. **Emit/audience/copy specs.** *(Creating an event announces it; guild events carry `guild` for the sibling fan-out.)*
4. **Housekeeping.** `ruff format . && ruff check .`; **at build time** bump `plfog/version.py` `VERSION` (patch on `release-0.19.x`) + a member-friendly `CHANGELOG` entry (e.g. *"Guild leads can now post their guild's meetings and events, and admins can post community events and the Guild Lead Meeting — all on the Community Calendar, with a Discord heads-up when they go up."*). Finalize this doc's status.

> Spec only — do not build until approved. Google sync (§10) is a **separate deferred phase**, not part of this build.

---

## 9. Testing (BDD `*_spec.py`, ≥98% gate, run in Docker `plfog-web`)

`describe_*` / `it_*` only (**`context_*` is NOT collected** — use `describe_*` for nested blocks), factory-boy, full type hints, email captured via `mail.outbox` (none expected — events default email OFF), Discord/in-app asserted via `EventDelivery` rows. New `CommunityEventFactory` (mirror `GuildAnnouncementFactory`) with traits: `guild_meeting` (default, `guild` set), `community` (site-wide), `lead_meeting` (site-wide).

- **Model — `tests/membership/community_event_models_spec.py`:**
  - `end_after_start` constraint: `ends_at <= starts_at` raises `IntegrityError`; a valid pair saves.
  - `guild_matches_type` constraint: `GUILD_MEETING` with `guild=None` raises; `COMMUNITY`/`LEAD_MEETING` **with** a guild raises; the three valid combos save.
  - `Meta.ordering` is `starts_at` ascending; `__str__` shows guild name vs "Site-wide".
  - Manager: `upcoming()` excludes a past **non-recurring** event but **includes** a past-anchored **monthly** series; `candidates_for_window()` returns a past-anchored monthly series whose occurrences reach the window (and excludes a non-recurring event outside it); `for_guild()` / `site_wide()` partition correctly.
  - **Recurrence:** `_occurrence_ordinal()` returns 2 for a 2nd-Saturday start and -1 for a 5th-weekday start; `occurrences_in(frm, to)` returns `[starts_at]` for a `NONE` event in window (and `[]` out of window), and for a `MONTHLY` event returns one start per month on the **same nth weekday**, each preserving the time-of-day and the duration (`ends_at - starts_at`), bounded to the window.
  - `absolute_url` is prefixed with `settings.MEMBER_BASE_URL` (override the setting in the test); `when_display` renders the expected range and appends "Repeats monthly" for a monthly event.
  - **`announce()` (Phase-3 spec):** picks `event.guild_published` for a guild event, `event.community_published` for a community event, `event.lead_meeting_published` for a lead meeting; emits with `period == f"event:{pk}:published"` (a second `announce()` is a deduped no-op — assert one `EventDelivery` per recipient); the emit **context carries `guild`** for a guild event (and `None`/site-wide for the others); the emit **url is absolute**.
- **Form — `tests/hub/community_events_spec.py` (form cases):**
  - End-before-start → friendly `ends_at` error, no save.
  - Admin variant: `GUILD_MEETING` without a guild errors; a site-wide type **with** a guild errors; valid combos save.
  - Lead variant: the form has **no** `event_type`/`guild` fields; the view sets them (event saves as `GUILD_MEETING` on the context guild).
- **Views / permissions — `tests/hub/community_events_spec.py`:**
  - **Lead gating:** a non-staff member gets **403** on `guild_events` / `…_add` / `…_edit` / `…_delete`; a lead and a `GuildStaffMembership` member get 200 / can mutate (mirror the meeting-notes gating specs).
  - **Cross-guild isolation (BLOCKING authz test):** a lead of guild A POSTing to `guild_event_edit` / `guild_event_delete` with their own guild A `pk` but an `event_pk` that belongs to guild B gets **404** (the guild-scoped `get_object_or_404(..., guild=guild)`), and B's event is unchanged. Without this, A could edit/delete B's events.
  - **Admin gating:** the Community-Calendar **Events tab list is visible to any logged-in member** (read-only — no `+ Add`/Edit/Delete buttons for non-admins; assert `events_can_manage` is False and the buttons aren't rendered), while the **authoring endpoints** `event_edit` / `event_delete` return **403** for a non-admin and **200** for an admin (`view_as.is_admin`). (No `admin_events` list view exists — assert the tab renders `upcoming_events`.)
  - **Create announces once; edit does not re-announce** (assert `EventDelivery` count unchanged after an edit POST).
  - **Delete** is POST-only and removes the row (GET/other methods 405/no-op); deleting a guild event cascades nothing else.
- **Calendar — `tests/hub/community_calendar_spec.py`:**
  - A `CommunityEvent` inside the window appears in `_get_calendar_context(request)` output (community) and in `_get_calendar_context(request, guild=guild)` (that guild's calendar); a different guild's event does **not** appear on the first guild's calendar.
  - **Monthly expansion:** a `MONTHLY` series anchored **before** the window contributes **multiple** entries inside a multi-month window (one per month, same weekday), each with a **distinct** synthetic pk (no collision), proving `candidates_for_window` + `occurrences_in` are wired (a plain `starts_at` BETWEEN filter would drop it).
  - **Visibility (not just inclusion):** `"community"` is in the rendered `default_filters_json` for both the community page and the guild calendar, and `source_colors["community"]` is set — so a default-filter member actually *sees* the event (guard against the `x-show="isActive('community')"` gating bug — assert the key is present, not merely that the entry is in the queryset).
  - `calendar_export_ics` includes a `VEVENT` for a `CommunityEvent` with a synthesized `UID:community-{pk}@…` and timed `DTSTART`/`DTEND` from `starts_at`/`ends_at` (title/location/description present) — and does **not** raise on the missing `uid`/`all_day` attributes. A `MONTHLY` event emits **one** VEVENT carrying `RRULE:FREQ=MONTHLY;BYDAY=<ord><WD>` (e.g. `2SA`), **not** per-occurrence VEVENTs.
  - The `community` source entry uses `EVENT_PK_OFFSET` (no PK collision with class/orientation entries).
- **Copy/audience — `tests/core/events/community_event_copy_spec.py`:**
  - For each new event, `placeholders == set(sample_context.keys())` (lock-step).
  - `all_guild_leads` returns leads/officers/staff site-wide and **excludes** a plain member and a no-email/no-account lead; `event.lead_meeting_published` resolves to that audience; `event.guild_published` resolves to the guild's members; `event.community_published` to all active members.
- **Gotchas:** freeze `timezone.now()` for `upcoming()`/window tests; set explicit `starts_at`/`ends_at` (don't lean on `auto_now_add`); seed a `MembershipPlan` before any member-gated login (per the e2e-needs-MembershipPlan note); the `all_guild_leads` resolver test must build a lead via `GuildFactory(guild_lead=...)`/`GuildStaffMembershipFactory`, not just a bare member.

---

## 10. Open / deferred / out of scope

### Open questions (need a call)
1. **Sibling spec sequencing (RESOLVED — sibling now written).** `2026-06-25-discord-notification-routing.md` exists and owns the per-guild webhook fan-out (`Guild.discord_webhook_url` + central+guild posting). **Build it first or alongside this one** so guild events reach the guild's own channel; until that fan-out ships, guild events post **central-only** (graceful — this spec just passes `guild` in context, no change needed when it lands).
2. **Lead Meeting calendar visibility — RESOLVED (confirmed).** The in-app/Discord **notification** for a Guild Lead Meeting goes only to leads (`all_guild_leads`), but the event still **shows on the Community Calendar for all members** (v1 has no per-event calendar ACL). Confirmed acceptable; a "leads-only" calendar filter is a deferred future feature, not built.
3. **Email default OFF** for the three event events — confirm members shouldn't be emailed for every new event (calendar + Discord + bell is the intent).
4. **Type↔scope invariant** ties `COMMUNITY`/`LEAD_MEETING` to site-wide. If a guild-scoped community/social event is later wanted, relax `ck_communityevent_guild_matches_type` — flagged.
5. **Admin authoring surface — RESOLVED (confirmed).** Not a `/manage/` admin-nav page — it's the **"Events" tab on the Community Calendar** (member-readable list + admin `+Add`/Edit/Delete). §6 Screen B/D.
6. **Community calendar color** — the spec proposes the `--hub-blue` hex `#3d8bd4` for the `"community"` source dot/chip (an existing token, so not a *new* color). Confirm it reads distinctly from classes (`classes_calendar_color`), orientation (`#EEB44B`), feed, and guild colors; pick another existing legend value if it clashes. **Do not introduce a brand-new color.**
7. **Calendar filter rollout for returning users — DECIDED (no longer open):** union new default sources into the stored `activeFilters` on init, preserving explicit removals via a `seenSources` list (§5.6 item 4). The localStorage key-bump alternative was rejected (it would reset every member's existing filter prefs). Left here only as a record of the call.

### Deferred — Google Calendar two-way write-back (Phase 2, NOT built now)
The clean future shape, mirroring the existing integration clients:
- A `core/integrations/google_calendar.py` `GoogleCalendarClient.from_site_config()` mirroring `MailchimpClient`/`SimplybookClient` — an `enabled` property, **blank config = disabled**, and **never raises** (best-effort, like `post_embed`).
- On `CommunityEvent` save/delete (for events whose guild/site maps to a Google calendar), push create/update/delete to Google and store the returned id in the **already-present** `google_event_id`.
- **Reconciliation (the duplicate problem):** today's one-way iCal pull re-imports Google events into the read-only `CalendarEvent` cache. A FOG-created event pushed to Google would come **back** through that pull as a *second* row. Dedup on the Google event id — the iCal sync skips any cached event whose source id matches a `CommunityEvent.google_event_id` (the FOG row is the canonical one; the cached copy is suppressed). Keep today's one-way pull for **Google-made** events untouched.
- All of this is **out of scope now**; only the `google_event_id` placeholder column is added (unused).

### Out of scope (deliberately)
- **RSVPs / attendance** — events are calendar items; no signup/headcount in v1.
- **Recurrence beyond monthly** — v1 ships **"Does not repeat" / "Repeats monthly"** only (monthly = same nth weekday, virtually expanded + `RRULE` in `.ics`; §4.1.1). **Out of scope:** weekly / every-N-months / custom cadences; an explicit **recurrence end-date** (a monthly series is open-ended in v1 — stop it by editing to "Does not repeat" or deleting; an optional `recurrence_end` is a cheap future add); and **per-occurrence exceptions** (editing or cancelling a single occurrence) — edit/delete always affects the whole series. The `google_event_id` and `Guild.meeting_cadence` fields are unrelated (the latter is descriptive text only — do **not** conflate it with this expansion).
- **A per-event detail page / deep link** — the notification URL points at the Community Calendar (`hub_community_calendar`); a dedicated event page is a future nicety.
- **An "Upcoming events" block on the guild Overview tab** — guild events already surface on the guild calendar (§Screen E); a separate list is a nicety.
- **A new color/token/component** — none introduced if the event-type label is muted text, the `community` calendar source reuses the existing `--hub-blue` hex for its legend dot, and the filter button reuses `.pl-calendar-filter`. The flagged items: the `EVENT_PK_OFFSET` constant (extends the existing PK-offset convention — fine), the `.pl-form-group select option { … }` rule (required, FRONTEND rule 13), the `community_color` legend value (open Q6), an optional `.pl-`-prefixed event-type pill, and the `all_guild_leads` resolver / `Recipients.ALL_GUILD_LEADS` (a small, necessary backend addition). **Flag any genuinely new color/pill before adding it.**

**Housekeeping:** the version bump (`plfog/version.py` `VERSION`) + the member-friendly `CHANGELOG` entry happen **at build time**, one entry per PR — not in this spec.

> Spec only — do not build until approved.
