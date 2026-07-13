# Event Pages & QR Codes — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-12
**Surface:** FOG hub (`pastlives.space` / member host) — a NEW public event detail page + the existing event edit form. No book CMS, no admin.
**Related (same fogstorm batch, cross-referenced by filename):**
- `2026-07-12-calendar-add-event-cta-and-sync-note.md` (calendar "add event" CTA)
- `2026-07-12-event-announcement-scheduling-and-reminders.md` (event scheduling/reminders)
- `2026-07-12-announcement-compose-wizard-drafts-mentions.md` (announcement compose wizard)

This is **Feature B**: give every Community Event a real, shareable public page + a QR code + a share section, exactly mirroring the class-QR feature that shipped this release. **No registration, no payment** — events stay free and open.

---

## 1. Summary

Today a Community Event has no page of its own. Its `absolute_url` points at the whole Community Calendar (`hub_community_calendar`), so a QR on the wall signage, an announcement link, or a calendar click-through all dump the reader onto the generic calendar grid — where they then have to hunt for the event. This feature gives each event a **real public detail page at `/events/<pk>/`** (title, when, where, description, guild, add-to-calendar), makes that page the event's canonical link everywhere, and adds a **QR + copy-link "Share & Print" card** to the event edit form so a lead or admin can print a code for a flyer or the wall. The page is **public (no login)** so a QR works for anyone who scans it. This is the event twin of the class feature — same helpers, same share card, same QR plumbing.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Registration / payment on events | **None.** Explicitly out of scope — "class URLs for events + QR codes," nothing more. Events stay free/open. |
| Login required to view an event page? | **No — public.** So QR codes on flyers/signage resolve for anyone. Mirrors how public class pages work. |
| URL shape | **pk-based `/events/<pk>/`** (`hub_event_detail`). Events have no slug (title only), so a pk URL is inherently stable — no slug-proof permalink redirect needed (simpler than classes). |
| `CommunityEvent.absolute_url` | **Repoint** from `hub_community_calendar` → the new event detail page. Positive change; every consumer audited in §7 and confirmed still correct (or improved). |
| New model fields | **None.** `public_url`/`qr_url`/`qr_svg()`/`qr_png_bytes()` are all properties/methods. **No migration.** |
| Which events get a public page | **PUBLISHED only.** A pending / changes-requested / declined member proposal never has a public page (would leak an unreviewed proposal onto a scannable URL). Editor preview of a non-published event is deferred (§10). |
| Reuse the class share component | **Generalize it.** Extract a reusable `components/qr_share_card.html` parametrized by url + download urls; both classes and events consume it. Internal rename only — no member-visible change to classes. |

---

## 2. What already exists (reuse, don't reinvent)

The class-QR feature shipped **this release** — this is assembly, not invention. Every row below is confirmed in the codebase.

| Need | Existing thing | Location |
|---|---|---|
| Scalable SVG QR of a URL | `qr_svg(url)` (segno, `viewBox` injected so it scales) | `membership/qr.py:21-34` |
| Raster QR for print | `qr_png_bytes(url)` | `membership/qr.py:37-44` |
| The property/method pattern to mirror | `ClassOffering.public_url` / `qr_url` / `qr_svg()` / `qr_png_bytes()` | `classes/models.py:339-368` |
| Editor-gated QR download view (SVG/PNG else 404) | `class_qr_download(request, pk, fmt)` | `classes/views.py:1773-1791` |
| QR download route shape | `class_qr` → `<int:pk>/qr.<str:fmt>/` | `classes/urls.py:136` |
| (Class only) slug-proof permalink | `class_permalink` → `c/<int:pk>/` | `classes/views.py:1762-1770`, `classes/urls.py:138` — **events skip this**; the pk URL is already stable |
| The "Share & Print" card to generalize | `class_qr_share.html` (QR preview + copy field + SVG/PNG buttons + Alpine `classShare()` copy with `execCommand` fallback) | `templates/classes/_components/class_qr_share.html` |
| Its CSS (QR preview box + card) | `.pl-qr-preview` (already generic), `.pl-class-share*` | `static/css/hub.css:3316-3387` |
| Where the class edit page puts the card | right half of a 6/6 `.pl-edit-split` | `templates/classes/admin/class_form.html:9-28` |
| Guild QR precedent (same `qr.py` helper on a model) | guild edit renders `guild.qr_svg()` into `.pl-qr-preview` | `static/css/hub.css:3314` comment |
| The event model | `CommunityEvent` (fields, `when_display`, `is_site_wide`, `occurrences_in`, `ical_rrule`, `absolute_url`) | `membership/models.py:2472-3013`; `absolute_url` at `:2756` (→ `hub_community_calendar`) |
| Published/upcoming filters | `CommunityEventQuerySet.published()` / `upcoming()` / `candidates_for_window()` | `membership/models.py:2449 / 2415 / 2421` |
| Event edit views (both render one template) | `guild_event_edit` (`hub/views.py:2394-2442`), `event_edit` (`:2462-2496`) → `templates/hub/community_event_edit.html` | — |
| The event form | `CommunityEventForm` | `hub/forms.py:1112-1175` |
| Event URL block | `events/add/`, `events/<int:event_pk>/edit/`, `events/propose/…`, `events/review/…`, `calendar/export.ics` | `hub/urls.py:216-229` |
| The combined `.ics` export pattern (VEVENT + RRULE + escaping) to mirror per-event | `calendar_export_ics` — CommunityEvent loop builds `UID:community-{pk}@pastlives`, DTSTART/DTEND, `RRULE:` from `ical_rrule()`, DESCRIPTION, LOCATION via `_ical_escape` | `hub/views.py:3120-3193` (loop `:3168-3187`) |
| **Public-yet-themed page mechanism** | `hub/base.html` auto-degrades for anonymous users: sidebar + member topbar are guarded on `user.is_authenticated and not …` (`:75`, `:363`), and a `{% block public_topbar %}` + `hub-main-wrapper--public` render instead (`:362`, `:442-467`) | `templates/hub/base.html` |
| Public themed page precedent #1 | `classes/base_public.html` extends `hub/base.html` with a public topbar; theme-aware light/dark | `templates/classes/base_public.html` |
| Public themed page precedent #2 | signage standalone themed root (login-exempt, forced dark) | `templates/signage/base.html` |
| A **public-capable hub view** to model the event view on | `guild_detail` — **no `@login_required`**, uses `getattr(request, "surface", "members")`, `_get_hub_context` (anon-safe), 404s private content for anon | `hub/views.py:469-589`; `_get_hub_context` at `:60-80` |
| Edit-authority source of truth | `membership/permissions.py` → `can_edit_guild(request, guild)` / `can_edit_class(request, offering)`; admin gate `_require_admin` used by `event_edit` | `membership/permissions.py`, `hub/views.py` |
| Notification copy that renders `event_url` | `event.guild_published` / `community_published` / `lead_meeting_published` / `approved` | `core/events/copy.py:636 / 666 / 695 / 810` |

### Gaps to close (kept small)

1. Four properties/methods on `CommunityEvent` (`public_url`, `qr_url`, `qr_svg()`, `qr_png_bytes()`) + one repoint (`absolute_url`).
2. One model method `ics_document()` (single-event `.ics`) so the public add-to-calendar reuses the export's VEVENT logic instead of duplicating it.
3. One edit-authority helper `can_edit_event(request, event)` in `membership/permissions.py` (no inline role checks in views).
4. Three thin views + routes: public detail, public per-event `.ics`, editor-gated QR download.
5. One generalized share partial + one CSS rename; the event edit template gets the card. The rename touches **both** class placeholder cards (admin **and** teach portals — see §6), not just one.
6. Copy fix: the four `event_url` blocks' link text + example URLs (they say "the Community Calendar" but now point at the event page).
7. A **themed `templates/404.html`** — there is currently **no** project 404 template or `handler404` (verified: `find templates -iname '*404*'` is empty; no `handler404` anywhere). `/events/<pk>/` is the first public, sequentially-guessable-pk hub URL, so anonymous scanners **will** routinely hit a not-found (an old/withdrawn/deleted pk, or a still-pending proposal). Without a template, prod (`DEBUG=False`) serves Django's bare "Not Found" text — off-brand, though not a 500. This feature ships the friendly themed page (see §6 states).

---

## 3. Where the code lives

```
membership/
  models.py                CommunityEvent: + public_url, qr_url, qr_svg(), qr_png_bytes(),
                           ics_document(); REPOINT absolute_url → public_url
  permissions.py           + can_edit_event(request, event)  (mirrors can_edit_class)
  ical.py                  NEW (small): ical_escape(text)  — shared by the model + calendar_export_ics
  spec/models/community_event_spec.py   + describe_ blocks for the new members
  spec/permissions_spec.py              + can_edit_event cases

hub/
  views.py                 + event_detail (PUBLIC, no @login_required)
                           + event_ics    (PUBLIC per-event .ics)
                           + event_qr      (editor-gated SVG/PNG download)
                           calendar_export_ics: CommunityEvent loop reuses ical_escape / VEVENT lines
  urls.py                  + events/<int:pk>/            → hub_event_detail
                           + events/<int:pk>/event.ics   → hub_event_ics
                           + events/<int:pk>/qr.<str:fmt>/→ hub_event_qr
  spec/views/event_pages_spec.py         NEW

templates/
  components/qr_share_card.html          NEW — reusable Share & Print card (url + downloads)
  hub/event_detail.html                  NEW — the public page (extends hub/base.html)
  404.html                               NEW — friendly themed not-found (extends hub/base.html; anon-degrades)
  hub/community_event_edit.html          + Share & Print card (below "Event details")
  classes/_components/class_qr_share.html  refactor → include the shared partial
  classes/admin/class_form.html          placeholder card: .pl-class-share* → .pl-qr-share*
  classes/teach/class_form.html          SAME placeholder card (instructor portal) — MUST also be renamed

static/css/hub.css        rename .pl-class-share* → .pl-qr-share* ; + .pl-event-detail*

core/events/copy.py        4 event_url blocks: fix link text + example URLs

plfog/version.py           VERSION bump + CHANGELOG (final phase)
```

Everything sits inside the existing `membership` + `hub` apps (already in the coverage/mypy scope). No new app, no new dependency (segno is already vendored via `membership/qr.py`).

---

## 4. Data model

**No new fields, no migration.** All additions to `CommunityEvent` (`membership/models.py`) are properties/methods mirroring `ClassOffering`:

| Member | Kind | Behaviour |
|---|---|---|
| `public_url` | `@property -> str` | `f"{settings.MEMBER_BASE_URL}{reverse('hub_event_detail', args=[self.pk])}"` — absolute URL of the public page. |
| `qr_url` | `@property -> str` | Returns `self.public_url`. Events have no slug, so the pk URL is already stable — the QR encodes the public page directly (no permalink redirect, unlike classes). Kept for API parity with `ClassOffering`. |
| `absolute_url` | `@property -> str` | **Repointed** — now `return self.public_url` (was `MEMBER_BASE_URL + reverse('hub_community_calendar')`). |
| `qr_svg()` | `-> str` | `render_qr(self.qr_url)` via `membership.qr.qr_svg`. |
| `qr_png_bytes()` | `-> bytes` | `render_png(self.qr_url)` via `membership.qr.qr_png_bytes`. |
| `ics_document()` | `-> str` | A full single-VEVENT `VCALENDAR` string for this event: `UID:community-{pk}@pastlives`, `DTSTART`/`DTEND` (`…Z`), `RRULE:` from `ical_rrule()` when recurring, `DESCRIPTION`/`LOCATION` escaped via `membership.ical.ical_escape`. Mirrors the CommunityEvent loop in `calendar_export_ics` (`hub/views.py:3168-3187`) so the two never drift — that loop is refactored to build its lines the same way. |

`membership/ical.py` (new, ~4 lines): `ical_escape(text: str) -> str` (backslash/comma/semicolon/newline escaping), lifted from `hub/views.py`'s `_ical_escape` so both the model and the combined export share one implementation (hub→membership import is the allowed direction).

No `TextChoices`/`help_text`/index changes — this is behaviour on an existing model.

---

## 5. Business logic (fat models / permissions)

**`CommunityEvent`** — see §4. `ics_document()` is the only method with real logic (iCal serialization); `public_url`/`qr_url`/`absolute_url` are cheap derived properties; `qr_svg()`/`qr_png_bytes()` delegate to `membership.qr`.

**`membership/permissions.py` → `can_edit_event(request, event) -> bool`** (new, mirrors `can_edit_class`, `view_as`-aware):
- Site-wide event (`event.guild is None`): admin only — reuse the same admin check `event_edit`/`_require_admin` uses (`request.view_as.is_admin`).
- Guild event (`event.guild is not None`): `can_edit_guild(request, event.guild)` (lead / staff / admin).
- Single source of truth so the QR download view and the "Edit event" affordance never drift, and no inline role checks land in a view (per `membership/CLAUDE.md`).

Views stay thin: they call `get_object_or_404(CommunityEvent.objects.published(), pk=pk)`, `can_edit_event(...)`, `event.ics_document()`, `event.qr_svg()/qr_png_bytes()` and return.

No new signals, no new emails from this feature. (The existing `announce()` / review notifications simply pick up the repointed `absolute_url` — see §7.)

---

## 6. UI / UX

### Screen A — Public event detail page

- **Screen / partial:** `templates/hub/event_detail.html` — `{% extends "hub/base.html" %}`. Sets `{% block title %}{{ event.title }} · Past Lives{% endblock %}` so the browser tab / share preview names the event (don't leave the default, which duplicates the base title — a known a11y finding).
- **Route / view:** `path("events/<int:pk>/", views.event_detail, name="hub_event_detail")`. **No `@login_required`** (this is the whole point) — and that alone is sufficient: there is **no** global `LoginRequiredMiddleware` (login is per-view), and `SurfaceMiddleware._handle_members_surface` doesn't force login for anon on the member host (verified `core/middleware.py`; `plfog/settings.py:178` MIDDLEWARE), so omitting the decorator genuinely makes the page reachable logged-out — exactly as the existing public-capable `guild_detail`/`community_calendar` already are. Register the bare-pk route so it can't shadow siblings: `events/<int:pk>/` won't match the literal `events/add/` (non-int) nor the deeper `events/<int:...>/edit|delete|withdraw|...` (extra segment), so ordering among the `events/` block is safe. View mirrors `guild_detail`: `event = get_object_or_404(CommunityEvent.objects.published(), pk=pk)` (non-published or unknown → 404), `ctx = _get_hub_context(request)` (anon-safe), `can_edit = can_edit_event(request, event) and getattr(request, "surface", "members") == "members"` (never show editor affordances on a non-member surface, matching `guild_detail`).
- **Layout & container:** dedicated page (not a modal). For an **anonymous** visitor `hub/base.html` renders its public topbar + `hub-main-wrapper--public` (expanded, no sidebar); for a **logged-in member** they get the full hub chrome. Both are theme-aware. Content is a centered column of `.hub-card` sections with new `.pl-event-detail*` classes.
- **Components used:** `hub/base.html` shell, `.hub-card`, `.hub-page-title`, `.hub-btn` variants. No form on this page.
- **The content, named explicitly:**
  - **Title** — `<h1 class="hub-page-title">{{ event.title }}</h1>`.
  - **Kind / guild** — if guild-scoped, a pill linking to `hub_guild_detail event.guild.slug` ("{{ event.guild.name }}"); else a muted "Community event" / "Guild Lead Meeting" label from `get_event_type_display`. Recurrence badge from `get_recurrence_display` when not `NONE`.
  - **When** — `event.when_display` (already Portland-local and tz-correct — `membership/models.py:2739`), with a calendar icon row.
  - **Where** — `event.location` with a map-pin icon **only if set** (blank → row omitted, no empty label).
  - **Description** — `event.description|linebreaks` **only if set**.
  - **Primary CTA — "Add to calendar"** — `.hub-btn hub-btn--primary`, links to `hub_event_ics` (downloads the single-event `.ics`). Works for anonymous visitors (the `.ics` view is public), so a flyer scanner can add it to their own calendar. This is the "reuse `calendar_export_ics` pattern" deliverable.
  - **Secondary links (no dead ends):** when `user.is_authenticated`, "View the Community Calendar" → `hub_community_calendar`; when anonymous, "Browse Past Lives classes" → `{% url 'classes:public_list' %}` (a genuinely public, themed page — also the anon public-topbar's own home, `hub/base.html:444`). **Correction to an earlier draft claim:** the calendar is **not** login-gated — `community_calendar` has no `@login_required` and branches on `request.user.is_authenticated` (`hub/views.py:3026,3063`), the member host doesn't force login (no `LoginRequiredMiddleware`; `SurfaceMiddleware._handle_members_surface` only bounces `PUBLIC_ONLY_PATH_PREFIXES` like `/account/`), so linking anon there wouldn't bounce. We still send anon to the classes list because it's the unambiguously-themed, verified-clean next step and matches the topbar, rather than coupling to the calendar page's anon layout. **Do not** hardcode a member-host URL for the anon link (host-fragile; `home()` at `core/views.py:153` redirects authenticated users away anyway).
  - **Editor affordance** — when `can_edit`, an "Edit event" `.hub-btn hub-btn--sm hub-btn--ghost` → `hub_guild_event_edit`(guild event) or `hub_event_edit`(site-wide). Hidden for everyone else and on non-member surfaces.
- **States:**
  - *Not found / not published* → friendly **404** via the **new `templates/404.html`** (there is no existing one — see §2 gap 7). It `{% extends "hub/base.html" %}` so it auto-degrades for the anon scanner (public topbar, no sidebar) and stays themed for members; body is a `.hub-card` with "We couldn't find that event." + a link to `{% url 'classes:public_list' %}` (anon-safe) and, `{% if user.is_authenticated %}`, the Community Calendar. It must render under Django's minimal `handler404` context — safe here because the common case (the anon scanner) only touches context-processor vars (`app_version` via `core.context_processors.app_version`) and `request`, never the per-view sidebar/topbar vars (`guilds`/`user_initials`/`persona`/`MEMBER_HOST`, all inside `hub/base.html`'s `{% if user.is_authenticated %}` branches). An *authenticated* 404 does render those branches, but degrades gracefully — undefined template vars render empty (not raise) and `request.view_as` is middleware-set, so no 500-inside-404. **Build check:** render the 404 with `DEBUG=False` for both an anon and a logged-in request and confirm 404 + themed body, no exception. A published event's page **never** reveals a pending/changes-requested/declined proposal — those 404 identically to an unknown pk (no state leak).
  - *Past event* → still fully viewable; if the event is non-recurring and `event.ends_at < now`, a muted `.pl-event-detail__past` note: "This event has already taken place." (Recurring series never shows it — they're ongoing.)
  - *No location / no description* → those rows/sections are simply omitted (guarded), never rendered as empty labels.
  - *Anonymous* → public topbar, Add-to-calendar still works, no member-only links, no editor buttons.
  - *Loading* → n/a (full-page GET, no HTMX).
- **Dark + light:** theme tokens only (`--hub-text`, `--hub-text-muted`, `--hub-card-bg`, `--color-tuscan-yellow` for accents); icons use `currentColor`. No form controls on this page, so no input-token pitfalls. `.pl-event-detail*` goes in `hub.css`. **Verify both themes.**
- **Mobile:** single centered column; cards stack; CTA + secondary links wrap and are full-width-friendly tap targets; 8px-grid spacing. No tables, no horizontal scroll.

### Screen B — "Share & Print" card on the event edit form

- **Screen / partial:** `templates/hub/community_event_edit.html` (rendered by both `guild_event_edit` and `event_edit`) + new reusable `templates/components/qr_share_card.html`.
- **Layout & container:** the event edit page is a single-column `.hub-form` (not the class page's 6/6 split), so the card is a **full-width `.hub-card`** placed **after the "Event details" card and before the Save row** — i.e. between `community_event_edit.html:29` (details card close) and `:35` (the Save/Cancel row), which puts it **inside** the `<form>` (spans `:16`–`:39`). That's safe **only because** the shared partial's copy input is `readonly` with **no `name`** (never submitted) and its Copy button is `type="button"` with the downloads as `<a>` links (never submit) — exactly as the class card already is (`class_qr_share.html:23-30`). Keep it that way; a stray `name`/submit button here would ride along on Save. It renders the live QR **only when** `event.pk and event.moderation_state == 'published'` (a live public page exists — `ModerationState.PUBLISHED == 'published'`, verified `membership/models.py:2500`); otherwise a placeholder card — "A QR code and share link appear here once this event is published." — mirroring the class draft state (`classes/admin/class_form.html:22-25`).
- **Components used:** the new shared `components/qr_share_card.html`, included with:
  - `qr_svg=event.qr_svg`  (rendered `|safe` inside the partial)
  - `share_url=event.public_url`
  - `svg_url` = `{% url 'hub_event_qr' event.pk 'svg' %}`, `png_url` = `{% url 'hub_event_qr' event.pk 'png' %}`
  - `title="Share & Print"`, `hint="This QR opens the event's public page — print it on signage, add it to a flyer, or hand it out."`
- **The controls, named explicitly:**
  - **QR preview** — `.pl-qr-preview` (the existing white, scannable box — white is correct in both themes so the code reads).
  - **Copy-link field** — a `readonly` `<input>` wrapped in **`.hub-form-group`** (so it inherits the theme input tokens — never a bare, white-box input) + a "Copy" button driven by Alpine `qrShare()` (the class's `classShare()` renamed), with the `execCommand` fallback for non-secure local dev.
  - **Downloads** — "Download QR (SVG)" and "Download QR (PNG)" `.hub-btn hub-btn--sm hub-btn--ghost` links to `hub_event_qr`.
  - Not a list/formset → no Add/Delete controls apply. Not destructive → no confirm modal. The form's own **Save** (`community_event_edit.html:36`) is unchanged.
- **States:** *published* → live QR + copyable link + downloads; *unsaved / not-yet-published* → the placeholder card (no dead QR). *Copy tap* → button flips to "Copied!" for 1.5s (Alpine).
- **Dark + light:** copy input scoped under `.hub-form-group` (theme-correct); QR box intentionally white; buttons are `.hub-btn` (themed). **Verify both themes.**
- **Mobile:** the card stacks below the details card; the copy field + Copy button and the two download buttons wrap (`flex-wrap`).

### Shared-component generalization (internal, no member-visible change)

- Extract `templates/components/qr_share_card.html` from `class_qr_share.html`, parametrized by `qr_svg` / `share_url` / `svg_url` / `png_url` / `title` / `hint`. It emits generalized `.pl-qr-share*` classes and the renamed `qrShare()` Alpine function. **`hint` is a single pre-computed string** passed by the caller — so the class side's existing **two-state** hint (`class_qr_share.html:10-14`: "…print it on signage…" when `offering.status == 'published'` vs "…opens once it's published…" otherwise) must move to the class wrapper (compute the string, then include), not be lost. The event side passes its own one-line hint.
- In `hub.css`, **rename `.pl-class-share*` → `.pl-qr-share*`** (`:3333-3387`). `.pl-qr-preview` and `.pl-edit-split` are already generic and unchanged.
- Refactor **all three** class-side consumers of the old names to the shared partial / new class names — grep confirms these are the complete set:
  1. `classes/_components/class_qr_share.html` — the live card (uses `.pl-class-share*` + `classShare()`).
  2. `classes/admin/class_form.html:22-25` — the "not yet saved" placeholder card (admin portal).
  3. `classes/teach/class_form.html:25-27` — **the same placeholder card in the instructor portal.** The original spec draft missed this one; renaming the CSS without updating it would silently strip the teach-portal placeholder's title/hint styling — a member-visible regression on the class side, breaking the "invisible refactor" promise.
- Rendered output stays visually identical across **both** portals → **no changelog entry for the class side** (invisible refactor). Only events get the new capability.

---

## 7. Repoint audit — every `absolute_url` consumer

Repointing `CommunityEvent.absolute_url` from the calendar to the event's own page touches five consumers. Each is confirmed to stay correct or improve:

| # | Consumer | Location | After repoint |
|---|---|---|---|
| 1 | Signage event slide QR + "learn more" caption | `membership/signage.py:149,152` | **Improves.** The wall QR now opens the real event page instead of the generic calendar (this is exactly the flyer/signage use case the feature targets). Only site-wide PUBLISHED events slide, so the target page always exists. |
| 2 | Launch announcement (in-app + Discord) `event_url` + `url` | `membership/models.py:2784,2786` (`announce()`) | **Improves.** `announce()` runs from `publish()`, so the event is PUBLISHED and the page is live when the link goes out. |
| 3 | Approval notification `url` | `membership/models.py:2900` (`approve()`) | **Improves.** `approve()` calls `publish()` first → page is live; the proposer's "approved" link lands on their now-public event. |
| 4 | `_emit_decision` context `event_url` | `membership/models.py:3007` | **Safe.** Only `event.approved` copy renders `event_url` (page is live post-publish). `event.changes_requested` renders **`edit_url` only** (`copy.py:867`) and `event.declined` renders **`propose_url` only** (`copy.py:923`) — neither surfaces `event_url`, so no dead draft-page link. **Verified in `core/events/copy.py`.** |
| 5 | Calendar grid entry click-through `url` | `hub/calendar_entries.py:136` → rendered at `templates/hub/partials/calendar_event_item.html:20-21` | **Improves.** A community event's title in the calendar list currently links back to the calendar itself (a no-op self-link); now it links to the event's own page. |

**Copy text fix (required, or the link text lies):** the four blocks that render `event_url` — `event.guild_published` (`copy.py:636`), `event.community_published` (`:666`), `event.lead_meeting_published` (`:695`), `event.approved` (`:810`) — currently say "See it on the Community Calendar" and carry example placeholder URLs of `https://pastlives.example/calendar/`. After the repoint the link goes to the **event page**, so update:
- link text → "See the event details" (or "See the event page"), and
- the example `event_url` placeholders → an event-page example (e.g. `https://pastlives.example/events/5/`).
Keep `.txt` and `.html` variants in sync (both exist in each block). No new placeholders, no shell changes — the copy already styles cream-on-dark via `notification_shell.html`.

No template renders `event.absolute_url` directly (confirmed — grep of `templates/` is empty); all consumers are the Python ones above.

---

## 8. Build order (phased; each phase ships green)

1. **Model + logic (no UI).** Add `public_url` / `qr_url` / `qr_svg()` / `qr_png_bytes()` / `ics_document()`; repoint `absolute_url`; add `membership/ical.py::ical_escape`; add `can_edit_event`. (No migration.) Specs for each. `absolute_url` repoint immediately improves signage/announcements/calendar — ship it.
2. **Public detail page + add-to-calendar.** `event_detail` view + `hub_event_detail` route + `templates/hub/event_detail.html`; the new **`templates/404.html`** (friendly, themed, anon-degrading — this is the first public guessable-pk hub URL, so it needs one); `event_ics` view + `hub_event_ics` route (public, published-only); refactor `calendar_export_ics`'s CommunityEvent loop onto the shared `ical_escape`. Specs: gating, states, `.ics` content, 404 render.
3. **QR download.** `event_qr` view (editor-gated via `can_edit_event`, SVG/PNG else 404) + `hub_event_qr` route. Specs: gating + formats.
4. **Share card + component generalize.** Extract `components/qr_share_card.html`; rename `.pl-class-share*` → `.pl-qr-share*`; refactor **all three** class-side files onto the new names/partial — `class_qr_share.html`, `classes/admin/class_form.html`, **and `classes/teach/class_form.html`** (moving the two-state hint to the wrapper); add the card to `community_event_edit.html` (published-only, else placeholder). Template-state specs, incl. a grep/assert that no `.pl-class-share` or `classShare(` reference survives.
5. **Copy fix.** Update link text + example URLs in the four `event_url` copy blocks (`.txt` + `.html`). Copy-render specs.
6. **Version + changelog.** Bump `plfog/version.py` VERSION (currently `0.21.7`). Since events are a new member-facing capability on the live-this-release calendar, add a grouped `CHANGELOG` entry (member-friendly), e.g.:
   > **Events have their own shareable page now** — Every event on the Community Calendar gets its own page with the details and an "Add to calendar" button, plus a QR code and share link (on the event's edit screen) you can print for a flyer or the wall. The wall slideshow and event announcements now link straight to the event instead of the whole calendar.

> Spec only — do not build until approved.

---

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*`, factory-boy, run in the `plfog-web` Docker image, ≥98% coverage gate.

- **Model (`membership/spec/models/community_event_spec.py`):**
  - `public_url` / `qr_url` / `absolute_url` all resolve to `MEMBER_BASE_URL + /events/<pk>/` and are equal (pk-stable).
  - `qr_svg()` returns SVG with a `viewBox` (scalable); `qr_png_bytes()` returns PNG bytes (`\x89PNG` header).
  - `ics_document()`: contains `BEGIN:VCALENDAR`, one `BEGIN:VEVENT`, `UID:community-{pk}@pastlives`, DTSTART/DTEND; a recurring event emits exactly one `RRULE:` (from `ical_rrule()`); location/description are iCal-escaped; a non-recurring event emits no `RRULE`.
- **Permissions (`spec/permissions_spec.py`):** `can_edit_event` — admin can edit any; guild lead/staff can edit their guild's event, not another guild's; a plain member cannot; site-wide requires admin. `view_as` respected.
- **Views (`hub/spec/views/event_pages_spec.py`):**
  - `event_detail`: anonymous **200** for a published event; **404** for pending/changes-requested/declined and for an unknown pk; the "Edit event" affordance shows only for an editor and not on a non-member surface; the past-event note shows for a non-recurring ended event and not for a recurring one; location/description rows omitted when blank. A guild-scoped **and** a site-wide published event both render 200 (guild pill vs "Community event" label).
  - **404 template:** with `DEBUG=False`, an unknown/unpublished pk renders the new `templates/404.html` (status 404, themed body — assert the friendly copy + the anon-safe `classes:public_list` link are present), **not** a 500 and not a state leak of the missing/pending event's title.
  - `event_ics`: anonymous **200**, `Content-Type: text/calendar`, body is `event.ics_document()`; **404** for a non-published event.
  - `event_qr`: editor gets SVG (`image/svg+xml`) and PNG (`image/png`) with a `Content-Disposition` attachment filename; a non-editor gets **403**; an unknown `fmt` → **404**; anonymous → 403 (download is an editor convenience, the page is the public artifact).
- **Copy (`core/events/…_spec.py`):** rendering `event.approved` yields the event-page URL (not `/calendar/`) and the updated link text; `event.changes_requested` / `event.declined` render without `event_url` (no draft-page link).
- **Template states:** `community_event_edit.html` shows the live share card for a published saved event and the placeholder for an unsaved/unpublished one; the copy input sits inside `.hub-form-group` and carries no `name` (won't ride along on Save).
- **Class-side no-regression (invisible refactor):** a repo grep asserts **zero** surviving `.pl-class-share` / `classShare(` references, and that **both** `classes/admin/class_form.html` **and** `classes/teach/class_form.html` still render a themed placeholder card (now `.pl-qr-share*`) for an unsaved class — catching the missed teach-portal file.
- **tz gotcha:** `when_display` is Portland-local (`membership/models.py:2739`); assert the page + `.ics` agree (DTSTART is UTC `…Z`, page copy is local) — no UTC/local subject-vs-body split.

---

## 10. Open / deferred

- **Editor preview of a non-published event page** — deferred. For now the public page is PUBLISHED-only (safest for scannable URLs); an `is_preview` banner for editors (like the class preview at `classes/public/detail.html`) can be added later if leads want to eyeball a proposal's page pre-approval.
- **Per-event Open Graph / share image** — deferred. The page uses the base meta; a rich social card (and a rendered-QR OG image) is a later polish, not needed for the flyer/signage use case.
- **Slug for events** — deferred and probably unneeded. Titles aren't unique and events are ephemeral; the pk URL is stable and sufficient. Revisit only if pretty URLs are ever requested.
- **Explicitly out of scope (do not build):** any registration, RSVP, capacity, waitlist, or payment on events. This feature is pages + QR only. Events stay free and open.
- **`calendar_export_ics` broader refactor** — only the CommunityEvent loop is touched (to share `ical_escape`); the `CalendarEvent` (iCal cache) loop is left as-is.
- **Guild-scoped events are publicly viewable too (confirm intended).** The page is PUBLISHED-only but **not** site-wide-only: any published event — including a guild event — gets a public `/events/<pk>/` with its title, time, and location visible to a logged-out scanner (only *signage* is site-wide-only; the page and QR are not). This is deliberate — the flyer/QR use case is exactly a guild printing a code for its own event — but it does mean a guild event's when/where leaves the members-only calendar. Flagging so Josh can confirm no guild event is considered members-private. (No new privacy flag exists on `CommunityEvent`; adding one would be scope creep.)
- **Past-event "Add to calendar" CTA** — kept even for a non-recurring event that has already ended (importing a past event as a personal record is harmless; the common past case is a recurring series that still has future occurrences). The muted "already taken place" note is the honest signal; suppressing the CTA for ended one-offs is optional polish, not shipped here.
