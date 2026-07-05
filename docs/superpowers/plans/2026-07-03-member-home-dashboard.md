# Member Home / Dashboard — a real post-login landing page

**Spec ID:** Spec A of the 0.20.x UAT batch (see `2026-07-03-qa-uat-response.md`, rows #1 + #2).
**Status: Spec only — not yet approved to build.**
**Recommended release:** its **own** release/PR off `main` (proposed `0.21.0`), *not* PR #118 — see "Release sizing" below.
**Source:** Josh's FOG UAT feedback (2026-07-03): *"When logging in, the landing page should be the app's home page. Is there a home page? It would be great to have one."*

---

## 1. Problem & current state (confirmed in code)

There is **no** member home. Today two different destinations both claim to be "home," and they disagree:

| Entry point | Where it sends an authenticated member | Code |
|---|---|---|
| Post-login redirect (members surface) | **Community Calendar** | `plfog/adapters.py:153-162` — `get_login_redirect_url` returns `reverse("hub_community_calendar")` (line 162) |
| Hitting `/` while logged in | **Guild Voting** | `core/views.py:139-143` — `home()` does `redirect("hub_guild_voting")` (line 142) |
| Anonymous `/` | Marketing hero page | `core/views.py:143` renders `templates/home.html` |
| `LOGIN_REDIRECT_URL` | `/` (dead setting) | `plfog/settings.py:341` — set to `"/"` but **overridden** by the adapter above |

So a member who logs in lands on the Calendar, but the brand logo and any `/` link drop them on Voting. Neither is a "home." The sidebar brand link also points at the Calendar (`templates/hub/base.html:52`).

**This spec builds the missing home** and makes all three entry points agree on it.

---

## 2. What the Home/Dashboard shows (v1 — focused, high-value, not a kitchen sink)

A single scannable hub page, top to bottom. Every block reuses an existing data source; nothing here needs a new integration.

1. **Welcome header** — "Welcome back, {preferred_name}." A one-line, warm subhead. Uses `member.display_name` (`membership/models.py:411-413`). If the account has no linked `Member` (unlinked User — a real case the hub already handles), show a gentle "your account isn't linked to a membership yet" line and skip the personalized blocks. Optionally a compact page intro using the **`page_header` component from build item C4** (`2026-07-03-qa-uat-response.md` #9) if C4 has landed; otherwise a plain `hub-page-title` + muted lead paragraph (matches `templates/hub/guild_voting.html:6-11`).

2. **Quick links** — a small row of `pl-btn`/card links to the member's most-used destinations: Community Calendar, Class Catalog, Guild Voting (members only), My Tab (only when `tab_payments_enabled`), Member Directory, and Settings. These are the same URLs the sidebar already exposes; the point is a big, obvious landing target, not a new nav.

3. **Your upcoming** — the next few things on the member's horizon (soonest-first, cap ~5), each linking to its detail/calendar. Sourced from the calendar layer that already exists:
   - The member's **orientation bookings** (confirmed/requested, upcoming) — clearly personal.
   - **Guild meetings / events** for the guilds they've joined, plus **site-wide community events** — `CommunityEvent.objects.upcoming()`.
   - **Classes at the space** in the window — `CalendarEvent.objects.upcoming()` where `source="classes"` (these are already materialized onto the calendar by `sync_local_class_events`).
   - Empty state: "Nothing on your calendar yet — browse the Community Calendar." with a link.

4. **Latest announcements** — recent, still-active guild announcements from the guilds the member has joined (`GuildAnnouncement.objects.active()`, newest first, cap ~4), each linking to its guild page. See the **genuine gap** on makerspace-wide announcements in §4 — v1 scopes this to *guild* announcements and labels the block accordingly ("Latest from your guilds").

5. **Your guilds** — shortcut chips/cards to the guilds the member has **joined** (`GuildMembership`), with the ones they **lead or staff** flagged (`member.staffed_guilds`). This is distinct from the sidebar, which lists *all* guilds; here it's *your* guilds. Empty state links to the guild list / "find a guild."

6. **Finish your profile (nudge)** — a dismissible card shown only when the profile is incomplete (`Member.profile_completeness`, new — see §4). It lists what's missing (photo, bio, pronouns, Discord link, directory visibility) with a single CTA to **Settings** (`hub_user_settings`) and a secondary link to the **Org-info / "new here?" page** (Spec B, `2026-07-03-org-info-page.md`, forthcoming) so a new member knows where the map, who's-who, and code of conduct live. This card is the **persistent** counterpart to the **first-login welcome modal (build item C2, UAT #2)**; they share the same `profile_completeness` check so they never disagree (modal fires once on first login; the card lingers until the profile is filled in). No hard dependency on C2 — the card works standalone if C2 ships later.

**Deliberately NOT in v1** (keep it essential): a notifications list (already in the topbar bell — do not duplicate), a tab-balance widget (already the topbar pill), vote standings, revenue/admin widgets, or any new authoring surface. Home *links* to those; it doesn't re-implement them.

---

## 3. Reuse map (what already exists — with file:line)

Almost the entire dashboard is assembled from data the hub already computes. A hub template that `{% extends "hub/base.html" %}` inherits all the chrome (sidebar, topbar bell, tab pill, theme, version) via **context processors**, so the view stays thin.

**Chrome / context provided for free** (registered in `plfog/settings.py:145-153`):
- Sidebar guilds + initials + avatar — `hub/context_processors.py:12-34` (`hub_sidebar`).
- Notification bell + unread badge — `core/context_processors.py` `notification_badge` (`unread_notification_count`, ~lines 70-81); bell markup `templates/hub/_notification_bell.html`; feed view `core/views.py:290-297`.
- Tab pill — `billing/context_processors.py:30` (`tab_balance`); gated by `tab_payments_enabled` (`core/context_processors.py:~32`).
- App version / changelog — `core/context_processors.py:19-23`.
- Surface/persona flags (`is_public_surface`, `persona`) — `core/context_processors.py`.

**Data sources for the blocks:**
- **Upcoming (calendar):** `CalendarEvent.objects.upcoming()` (`membership/models.py:2575-2578`, `end_dt >= now`) and `CommunityEvent.objects.upcoming()` (`membership/models.py:1621-1628`). The combine pattern to copy: `hub/views.py:2309-2378` (`calendar_export_ics` merges both) and `_get_calendar_context` (`hub/views.py:2082-2232`) with its synthetic-entry helpers in `hub/calendar_entries.py` (`community_event_entries`, `guild_calendar_entries`). Classes are already on the calendar via `sync_local_class_events` (`hub/calendar_service.py:142-201`).
- **Orientations (personal):** `member.orientation_bookings` (used at `membership/models.py:612-621`); `OrientationBooking.objects.upcoming()` (`membership/models.py:~2936`).
- **Announcements:** `GuildAnnouncement.objects.active()` (`membership/models.py:1306-1358`, ordering `-published_at`).
- **Member's guilds:** `GuildMembership` (`membership/models.py:1519-1534`) → `Guild.objects.filter(memberships__member=member)`; lead/staff flag via `member.staffed_guilds` / `member.is_guild_lead` / `member.is_guild_staff` (`membership/models.py:588-605`).
- **Profile fields for the nudge:** `profile_photo` (`:309`), `about_me` (`:308`), `pronouns` (`:301`), `discord_user_id`/`discord_is_linked` (`:283-292`, `:415-418`), `show_in_directory` (`:343`). Settings target: `hub_user_settings` (`hub/urls.py:153`).
- **First-login modal component:** `templates/components/modal.html` (FRONTEND.md §Modal). Note the *book-surface* onboarding flag `UserProfile.onboarding_completed_at` (`core/models.py:567-571`) is **not** the members-surface flag — C2 must introduce its own hub first-login marker; this spec only reads `profile_completeness`, so it's flag-agnostic.
- **Sidebar / active-nav:** `templates/hub/base.html` (brand `:52`, admin nav from `:61`, member/else nav `:170-230`, guilds collapsible `:232-256`); `active_nav` tag in `hub/templatetags/hub_tags.py:15`.

---

## 4. Genuine gaps (net-new, small)

1. **No member-home view / URL / template.** The core build.
2. **No "upcoming for this member" aggregation.** Need a thin, tested aggregator that merges the member's orientation bookings + their guilds' meetings + site-wide events + class calendar entries into one soonest-first list. Per fat-models (CLAUDE.md §2) and the hub app rule ("no models — reads from `membership`/`billing`", `hub/CLAUDE.md`): put the querying on **managers/model methods in `membership`** and keep only orchestration in a small **`hub/home.py` service** (mirrors how `hub/calendar_entries.py` + `hub/calendar_service.py` already split query vs. orchestration). Do **not** grow `_get_calendar_context` — that helper builds a full grid; the home block wants a short flat list.
3. **No `Member.profile_completeness`.** Add a cheap `@property` (fat model) returning the missing-field list and a percent/boolean (fields: photo, bio, pronouns, Discord link, directory listing). Reused by both this card and C2's modal. No migration — pure derived data.
4. **No makerspace-wide announcement model.** Only `GuildAnnouncement` (per-guild) and `CommunityEvent` exist; makerspace-wide broadcasts today live only in the notification spine (the bell) and Discord. **v1 scopes the announcements block to guild announcements** ("Latest from your guilds") and does **not** invent a `SiteAnnouncement` model. Recommend a follow-up spec if a persistent makerspace-wide announcements feed is wanted; the bell already covers the ephemeral case.

---

## 5. New view, URL, template, sidebar entry

**View** — `hub/views.py`, thin, `@login_required`, follows the `_get_member` / graceful-`None` pattern used throughout the file:
```
@login_required
def home(request: HttpRequest) -> HttpResponse:
    """Member Home/Dashboard — the post-login landing page."""
    member = _get_member(request)
    ctx = _get_hub_context(request)   # sidebar data also comes from the context processor; harmless
    if member is None:
        return render(request, "hub/home.html", {**ctx, "member": None})
    from hub.home import build_home_context   # new service (gap #2)
    return render(request, "hub/home.html", {**ctx, "member": member, **build_home_context(member)})
```
`build_home_context(member)` returns `{"upcoming": [...], "announcements": [...], "my_guilds": [...], "profile": {completeness data}}`. All business logic (queries, completeness) lives on `membership` managers/model props; the service only assembles and caps.

**URL** — `hub/urls.py`, name **`hub_home`**, path `home/` (place it near the calendar route, `hub/urls.py:176`):
```
path("home/", views.home, name="hub_home"),
```

**Template** — `templates/hub/home.html`, `{% extends "hub/base.html" %}`. FRONTEND.md compliance: each block is a `<div class="hub-card">`; `hub-page-title` for the H1; `pl-`-prefixed classes for the quick-link grid, the guild chips, and the nudge card; **no inline styles** beyond one-off layout nudges; empty states for every block; test **both dark and light** themes. No new heavy JS — the only interactivity is the nudge's dismiss (Alpine `x-data`/`x-show`, remembering dismissal in `localStorage`; per FRONTEND.md rule 12, keep the `display` in a CSS class, not inline). Page-specific CSS goes in `static/css/hub.css`.

**Sidebar nav entry** — `templates/hub/base.html`:
- Add a **"Home"** link (house icon SVG) as the **first** nav item in **both** branches: the admin branch (before "Activity", ~`:61`) and the member/else branch (before "Class Catalog", ~`:170`). Use `{% active_nav 'hub_home' %}` for the active state.
- Repoint the **brand logo** link (`:52`) from `hub_community_calendar` → `hub_home`, so the logo goes home.

---

## 6. Repointing the landing (both entry points) + interlock with C5

Make all three entry points agree on `hub_home`:

1. **`plfog/adapters.py:153-162`** — in `get_login_redirect_url`, the members-surface branch returns `reverse("hub_home")` instead of `reverse("hub_community_calendar")` (line 162). The public/book branch (`account:overview`) is unchanged.
2. **`core/views.py:139-143`** — `home()` authenticated branch does `redirect("hub_home")` instead of `redirect("hub_guild_voting")` (line 142). Anonymous branch still renders `templates/home.html`.
3. **`plfog/settings.py:341`** — `LOGIN_REDIRECT_URL` stays `"/"`; it's still overridden by the adapter, but now `/` itself lands on `hub_home`, so the fallback is consistent too. (Optional tidy: point it at `hub_home` for clarity; not required.)

Result: login → `hub_home`; `/` (authed) → `hub_home`; brand logo → `hub_home`. One canonical landing. (The `/` → `hub_home` redirect is a single extra hop; acceptable and keeps one canonical URL for active-nav + bookmarks. Rendering the dashboard inline at `/` is an alternative but muddies the anon/authed split in `core.views.home` — prefer the redirect.)

**Interlock with build item C5** (`2026-07-03-qa-uat-response.md`: *"Consistent post-login landing + cross-surface theme persistence"*, #1-consistency + #6a):
- C5 and this spec **touch the same two lines** (`adapters.py:162`, `core/views.py:142`). They must not both land uncoordinated.
- **Recommendation:** C5's *landing-consistency* half (#1) is **superseded by this spec** — instead of making both entry points agree on the Calendar, they agree on `hub_home`. Let **C5 keep only the theme-persistence half (#6a)** and **drop its landing change**, and let **Spec A own the landing repoint**. If Spec A slips and C5 ships first, C5 may set both to the Calendar as an interim; Spec A then repoints them to `hub_home` and the interim is retired. Either way, exactly one of the two owns those lines at merge time. Call this out in the PR description so the reviewer knows C5's #1 was intentionally folded here.

---

## 7. Build sequence

1. **`Member.profile_completeness`** property + `membership` manager/model methods for the upcoming/announcement/guild queries (fat model), with specs. No migration.
2. **`hub/home.py`** service (`build_home_context`) that assembles + caps, with specs (mock nothing but the DB/factories).
3. **`hub/views.home`** + **`hub/urls.py`** `hub_home` route.
4. **`templates/hub/home.html`** + `static/css/hub.css` additions (dark + light).
5. **Sidebar**: `templates/hub/base.html` Home link (both branches) + brand repoint.
6. **Landing repoint**: `plfog/adapters.py` + `core/views.py` (coordinated with C5 per §6).
7. **CHANGELOG**: one member-facing entry ("A home page — see what's coming up, your guilds, and finish your profile from one place") stamped at the release VERSION (see §9). Do not edit `plfog/version.py` if this lands inside a batch that curates centrally; for its own release, bump per CLAUDE.md.

### Files to create
- `templates/hub/home.html`
- `hub/home.py` (service)
- `tests/hub/home_spec.py` (view + service) and additions to `tests/membership/…` specs (property + managers)

### Files to modify
- `hub/views.py` (new `home` view + import)
- `hub/urls.py` (`hub_home` route)
- `templates/hub/base.html` (Home nav link ×2, brand link)
- `plfog/adapters.py` (`get_login_redirect_url` → `hub_home`)
- `core/views.py` (`home` authed branch → `hub_home`)
- `membership/models.py` (`Member.profile_completeness` + any manager methods)
- `static/css/hub.css` (dashboard styles)
- `plfog/version.py` CHANGELOG (per release policy)

---

## 8. BDD test plan (pytest-describe, `*_spec.py`, 100% + mutation per CLAUDE.md §7)

**`describe_hub_home_view`** (`tests/hub/home_spec.py`):
- `it_requires_login` — anonymous → redirect to login.
- `it_renders_dashboard_for_a_linked_member` — 200, uses `hub/home.html`, shows welcome with display_name.
- `it_handles_a_user_with_no_member` — 200, shows the "not linked" state, no crash.
- `describe_upcoming` — `it_lists_soonest_first_capped`, `it_includes_the_members_orientation_bookings`, `it_includes_their_guilds_meetings_and_site_wide_events`, `it_shows_the_empty_state_when_nothing_upcoming`.
- `describe_announcements` — `it_shows_active_guild_announcements_for_joined_guilds`, `it_excludes_expired_ones`, `it_excludes_guilds_the_member_has_not_joined`.
- `describe_my_guilds` — `it_lists_joined_guilds`, `it_flags_led_or_staffed_guilds`, `it_shows_empty_state_with_no_guilds`.
- `describe_profile_nudge` — `it_shows_when_incomplete`, `it_hides_when_complete`, `it_links_to_settings_and_org_info`.

**`describe_build_home_context`** (service, `tests/hub/home_spec.py`):
- `it_caps_each_section`, `it_orders_upcoming_by_start`, `it_scopes_announcements_to_joined_guilds`.

**`describe_Member`** (`tests/membership/…`):
- `describe_profile_completeness` — `it_reports_missing_fields`, `it_is_complete_when_all_set`, `it_computes_percent`.

**Landing repoint** (extend existing suites — `tests/plfog/adapters` + a `core` view spec):
- `describe_get_login_redirect_url` — `it_sends_members_to_hub_home`, `it_still_sends_public_surface_to_account_overview`.
- `describe_home_view` — `it_redirects_authenticated_users_to_hub_home` (replaces the current guild_voting assertion), `it_renders_marketing_home_for_anonymous`.

Use `factory-boy` (`tests/membership/factories.py`, `classes/factories.py`); `respx` only if any external call sneaks in (none expected — the calendar reads DB rows). Autouse fixtures already disable Airtable + fake Stripe (`CODEBASE_INDEX.md` root `conftest.py`).

---

## 9. Release sizing — recommendation

**Ship as its own release/PR off `main` (proposed `0.21.0`), not in PR #118.** Reasons:
- **It's a net-new, high-visibility, member-facing feature** — the literal UAT ask ("is there a home page?"). It deserves its own headline CHANGELOG entry and its own Discord announcement, which the one-entry-per-release-line policy (CLAUDE.md "Versioning") makes cleaner in a dedicated release than buried among PR #118's guild-page batch.
- **It changes the landing page for every member** and wants focused UAT before it goes out — not bundled with unrelated guild-edit tab work.
- **It supersedes C5's landing half** (§6); bundling both into #118 creates avoidable churn on the same two lines. Let #118 carry C5's theme-persistence work; let this feature own the landing.
- **PR #118 is already large** (5 done features + C1–C5 + parallel specs).

If Josh prefers to keep it in #118 anyway, it's technically compatible — but then C5 must drop its #1 landing change entirely and this spec inherits it, and the changelog for #118 must gain the home-page headline. Default recommendation stands: **its own release.**

---

## 10. Standards checklist (must hold at review)
- Fat models / skinny view; validation-free view (read-only page); queries on managers; orchestration in `hub/home.py`. (CLAUDE.md §1–2)
- Full type hints incl. `-> None`; Google-style docstrings on non-obvious methods. (§6)
- `hub-card` wrappers, `pl-` classes, `components/*` where applicable, no raw modal/toggle markup, no inline styles beyond one-offs, dark **and** light verified, empty states everywhere. (FRONTEND.md rules 1–13)
- BDD `*_spec.py`, 100% branch + mutation, no skips/pragmas without approval. (§7)
- `ruff format` + `ruff check` + `mypy` clean before each commit.

---

## Critical files for implementation
- `hub/views.py` (new thin `home` view; reuse `_get_member`/`_get_hub_context`, calendar-combine pattern at 2309-2378)
- `templates/hub/base.html` (sidebar Home nav entry in both branches at ~61 and ~170; brand link at 52)
- `plfog/adapters.py` (`get_login_redirect_url` at 153-162 → `hub_home`)
- `core/views.py` (`home` authed branch at 139-143 → `hub_home`)
- `membership/models.py` (new `Member.profile_completeness`; reuse `CalendarEvent`/`CommunityEvent.upcoming`, `GuildAnnouncement.active`, `GuildMembership`, `staffed_guilds`)
- Supporting (create): `templates/hub/home.html`, `hub/home.py` (service), `hub/urls.py` (`hub_home` route), `tests/hub/home_spec.py`

**Build-time caveat:** whether to personalize "Your upcoming" with the member's *registered* classes (needs the `classes.Registration` ↔ Member/User join confirmed) — v1 as specced avoids that join by using the already-materialized class calendar entries, so it is not a blocker.
