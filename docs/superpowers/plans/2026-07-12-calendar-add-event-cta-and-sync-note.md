# Community Calendar — Header "Add Your Event" CTA + Google-Sync Note — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-07-12
**Surface:** FOG hub `pastlives.test` — Community Calendar page (`/calendar/`, `hub_community_calendar`).
**Related (sibling specs, same batch):**
- `2026-07-12-event-pages-and-qr-codes.md`
- `2026-07-12-event-announcement-scheduling-and-reminders.md`
- `2026-07-12-announcement-compose-wizard-drafts-mentions.md`

---

## 1. Summary

On the Community Calendar page, the "Add your event" call-to-action (admins: **+ Add event**; members: **+ Propose an event**) exists today but only appears on the **Events** tab — the page opens on the **Calendar** tab, so most members never see it. This change lifts that one CTA into the page header (top-right of the H1), so it shows on both tabs without hunting. It also adds a short, honest line telling members that events on this calendar are synced to the shared Past Lives Google Calendar — shown **only** when sync is actually turned on.

Template-and-copy only: no model, no migration, and (because every context flag it needs is already computed) **no view change**.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Where the CTA lives | Header action area, top-right of the H1 row — so it renders on the default Calendar tab too, not just Events. |
| New button vs. existing | Reuse the **existing role gating** exactly (`events_can_manage` → Add; else `member_can_propose` → Propose). No new gating rules. |
| One button or two | **One** CTA, in the header. The Events-tab copy of it is removed so there are never two divergent buttons. |
| Sync note visibility | Gated on the existing `google_sync_enabled` context flag. Both underlying gates default **false**, so the note is correctly hidden today and never claims sync that isn't live. |
| Sync note copy | Honest, present-tense: "Events added to this calendar are also synced to the shared Past Lives Google Calendar." |
| Scope | Small feature — template + reuse of existing context vars only. No model change, no migration, no new view. |

## 2. What already exists (reuse, don't reinvent)

Everything this feature needs is already wired. Confirmed in the codebase:

| Need | Existing thing | Location |
|---|---|---|
| The page view + URL | `community_calendar` view; url name `hub_community_calendar` | `hub/views.py:3026-3072`; `hub/urls.py:216` |
| "Admin can add" gate | `events_can_manage` (= `view_as.is_admin`) in context | `hub/views.py:3052` |
| "Member can propose" gate | `member_can_propose` (= policy ≠ `DISABLED`) in context | `hub/views.py:3055` |
| Sync-live flag | `google_sync_enabled` in context, from `_google_sync_enabled()` (env `GOOGLE_CALENDAR_SYNC_ENABLED` **and** `SiteConfiguration.google_calendar_sync_enabled`, both default false) | `hub/views.py:3056`; helper `hub/views.py:629-636` |
| Reviewer-queue flag | `can_review` + `review_pending_count` in context | `hub/views.py:3060-3062` |
| Admin add / member propose targets | `hub_event_add`, `hub_propose_event` | `hub/urls.py:220`, `hub/urls.py:224` |
| Reviewer queue target | `hub_event_review_queue` | `hub/urls.py:228` |
| The CTA markup to lift | Events-tab button row (Add/Propose + Review queue) | `templates/hub/community_calendar.html:233-242` |
| Header container + right-aligned sibling button | `.hub-page-header` (inline flex, `flex-wrap:wrap`) with the "View Publicly" `hub-btn hub-btn--sm hub-btn--ghost` pushed right via `margin-left:auto` | `templates/hub/community_calendar.html:25-46` |
| Primary button style (theme-safe) | `.hub-btn` / `.hub-btn--primary` — tuscan-yellow bg, navy text, identical in both themes | `static/css/hub.css:952`, `static/css/hub.css:967` |
| Muted info-line style (theme-safe) | `.hub-text-muted` (token `--hub-text-muted`, overridden per theme); already used by the intro paragraph | `templates/hub/community_calendar.html:48` |

**Gaps to close:** none in the backend. The only work is editing `templates/hub/community_calendar.html`.

## 3. Where the code lives

```
templates/hub/community_calendar.html   # header action group + sync note; remove tab-local CTA duplicate
plfog/version.py                         # VERSION bump + CHANGELOG (at build time — see §8)
```

No new template, no `hub/views.py` change, no CSS file change (reuses `hub-btn*` and `hub-text-muted`), no new class, token, or prefix.

## 4. Data model

N/A — no model change, no migration.

## 5. Business logic (fat models)

N/A — no new logic. Gating (`events_can_manage`, `member_can_propose`, `google_sync_enabled`) already resolved in the view; the template only reads the flags.

## 6. UI / UX  ← completeness checklist applied

Two edits to one screen. No new form, so most form/list-editor items in the rubric are N/A by design — but the required states are spelled out concretely below.

- **Screen:** `templates/hub/community_calendar.html` (the whole page; no partial).
- **Layout & container:** in-place header edit + one page-level muted line. No modal, no new page, no dedicated form (rubric §2 container choice = "no form" — nothing to place).
- **Components reused:** `hub-btn hub-btn--primary` (CTA), `hub-btn hub-btn--sm hub-btn--ghost` (unchanged View Publicly), `hub-text-muted` (sync note), `components/confirm_modal.html` (unchanged, stays on the Events-tab delete rows). No component is hand-rolled.

### 6.1 The CTA — factored up into the header (not duplicated)

- **Where it sits:** inside the existing `.hub-page-header` row (`templates/hub/community_calendar.html:25-46`), right-aligned. Introduce a single **action group** — a wrapping `<div>` that carries `margin-left:auto` and holds the CTA first, then the existing "View Publicly" button. Move `margin-left:auto` off the View Publicly `<a>` (`:44`) and onto this group so both buttons ride to the right as one unit and wrap together. This matches the header's existing inline-flex idiom (the header is already fully inline-styled), so it introduces **no new class/token/prefix** — nothing to flag.
- **The group's exact inline style:** `style="margin-left:auto;display:flex;align-items:center;gap:0.625rem;flex-wrap:wrap;"` — the `0.625rem` gap matches the header's own gap (`:25`), and `flex-wrap` lets the two buttons stack if the viewport is too narrow for both side-by-side. The group is **not** an `x-show` node (it sits above the tab `x-data` wrapper), so inline `display:flex` is safe — Rule 12 does not apply. When *neither* child renders (a logged-out visitor with policy `DISABLED`), the group is an **empty flex `<div>`** — it has no background/border, so it is invisible and harmless; **do not** wrap the group in its own `{% if %}` guard (that would just duplicate the two child conditions).
- **The control, named:** one `<a class="hub-btn hub-btn--primary">`, gated **exactly** as the current Events-tab block (`:234-238`):
  - `{% if events_can_manage %}` → **`+ Add event`**, `href="{% url 'hub_event_add' %}"`.
  - `{% elif member_can_propose %}` → **`+ Propose an event`**, `href="{% url 'hub_propose_event' %}"`.
  - else → no CTA rendered.
- **Deliberate class harmonization:** the lifted button changes from `pl-btn pl-btn--primary` (its Events-tab styling) to **`hub-btn hub-btn--primary`** to match its new header sibling (`hub-btn ...` View Publicly). Gating and hrefs are byte-for-byte the same as today — only the visual family is harmonized, so this is not a "new button with different rules."
- **Kill the duplicate:** delete the Add/Propose block from the Events-tab row (`:234-238`). The **Review queue** link (`:239-241`) is a reviewer-only tool and stays on the Events tab — but wrap the surrounding `<div>` (`:233`) in `{% if can_review %}` so a non-reviewer no longer renders an empty `margin-bottom` div. Because that outer `<div>` now carries the `{% if can_review %}` guard, **also delete the now-redundant inner `{% if can_review %}…{% endif %}`** around the Review-queue link itself (`:239` / `:241`) — otherwise you leave a pointless nested double-guard. Final Events-tab row: `{% if can_review %}<div …><a …>Review queue …</a></div>{% endif %}`. Net result: **exactly one** Add/Propose CTA in the served DOM (the header one), on both tabs.
- **This is not a list editor:** no formset, so rubric §1 (Add/Delete/Save trio) does not apply — there is nothing to add, delete, or save on this screen. Called out explicitly so the reviewer doesn't read it as a missing control.

### 6.2 The Google-sync note

- **Where it sits:** a page-level muted line placed **after the intro paragraph** (`:48-51`) and **before** the tab `x-data` wrapper (`:53`) — i.e. above the tab bar, so it appears on both Calendar and Events tabs and reads as page context, not tab content. It sits directly under the header/intro, near (above) the Calendar tab's Export controls.
- **Exact condition:** `{% if google_sync_enabled %} … {% endif %}`. When either underlying gate is off — which is the default today — the block is not rendered at all, so the page never claims sync it isn't doing.
- **Exact copy:** `Events added to this calendar are also synced to the shared Past Lives Google Calendar.`
- **Markup/style:** a `<p class="hub-text-muted" style="margin:-0.75rem 0 1.5rem;max-width:65ch;">`. The intro paragraph directly above already carries `margin:-0.25rem 0 1.5rem` (`:48`), and adjacent block margins here **sum** because the top is negative — so `-0.75rem` pulls the note up to sit ~0.75rem under the intro (reading as an attached sub-line, not a floating second paragraph), while the `1.5rem` bottom clears the tab bar below. `max-width:65ch` matches the intro's measure. Reuses the existing muted-text token — **no `hub-card` wrapper, no new box, no background color** → nothing that could render as a white box on dark. (A muted line is preferred over a card for a single honest sentence.)

### States (no new form — the ones that apply, spelled out)

- **CTA target per role:**

  | Viewer | CTA shown | Label | href |
  |---|---|---|---|
  | Admin (`events_can_manage`) | yes | `+ Add event` | `hub_event_add` |
  | Member, policy ≠ DISABLED, not admin (`member_can_propose`) | yes | `+ Propose an event` | `hub_propose_event` |
  | Logged-out visitor (anon on members host, `?public=1` preview, or public book surface), policy ≠ DISABLED | yes | `+ Propose an event` | `hub_propose_event` |
  | Neither (policy DISABLED, not admin, logged-out or in) | no | — | — |

  When an authenticated viewer has no CTA (policy DISABLED, not admin), the header degrades cleanly to `[title] [? tooltip] [View Publicly]`.
- **Logged-out / public-surface viewer (spelled out):** `community_calendar` is **not** `@login_required` (`hub/views.py:3026` has no decorator; `/calendar/` is not in `MEMBER_ONLY_PATH_PREFIXES`, `plfog/settings.py:75-89`), so anonymous visitors reach it — on the members host, via `?public=1` preview, and on the public book surface. `member_can_propose` does not check authentication, and the **default policy is `APPROVAL`** (`core/models.py:229`, ≠ DISABLED), so by default an anonymous visitor **sees `+ Propose an event`** in the header. "View Publicly" is hidden for them (its `{% if user.is_authenticated … %}` gate, `:43`), so the header reads `[title] [? tooltip] [+ Propose an event]`. Clicking Propose hits `hub_propose_event`, which **is** `@login_required` (`hub/views.py:2541`), so the click lands cleanly on the login flow — no 500, no dead end. This is unchanged gating (see §10); the only change is that the CTA is now visible on both tabs instead of only the Events tab. The sync note, when live, also renders for logged-out viewers — it's honest page context about the calendar, not a member-only action.
- **Sync note:** hidden unless `google_sync_enabled` (both env + SiteConfiguration gates true); shown with the exact copy above when true. No other states.
- **Empty / loading / error / success:** N/A — no data list is added, no HTMX swap is added, no form is submitted on this screen. The CTA is a plain navigation link; feedback lives on its destination page (`hub_event_add` / `hub_propose_event`), unchanged.
- **Dark + light:** both verified. `hub-btn--primary` is tuscan-yellow bg + navy text defined identically for both themes (`static/css/hub.css:967`); `hub-text-muted` swaps via `--hub-text-muted` per theme. No form control is added, so Rules 13/14 (input tokens, date/time picker) do not apply. No `x-show` element gains an inline `display` (Rule 12 not triggered — the header is not an `x-show` node). Spec instruction: **verify both themes** at `pastlives.test:8000` on both the Calendar and Events tabs.
- **Mobile:** the header already has `flex-wrap:wrap` (`:25`). With the CTA + View Publicly grouped in one right-aligned flex container (own `gap`), on narrow screens the group wraps **as a unit under the title** rather than each button stranding on its own line. Both are real, full-size tap targets (`hub-btn`), 8px-grid gaps. No horizontal scroll introduced.

## 7. Notifications / emails / activity

N/A — no email, notification, or activity is sent or logged. (The GCal sync itself already exists and is out of scope; this only *mentions* it in copy.)

## 8. Build order (single phase — ships green)

1. **Edit `templates/hub/community_calendar.html`:**
   - Add the right-aligned header action group (CTA + moved View Publicly) inside `.hub-page-header`; move `margin-left:auto` onto the group.
   - Add the `{% if google_sync_enabled %}` muted sync-note line under the intro paragraph.
   - Remove the Add/Propose block from the Events-tab row; wrap the remaining Review-queue row in `{% if can_review %}`.
2. **Verify both themes** on `pastlives.test:8000` — Calendar and Events tabs, as admin, as a proposing member, and as a member with proposals disabled; confirm exactly one CTA and correct href per role; confirm the sync note stays hidden (default gates off).
3. **Tests** (§9) — run in the `plfog-web` Docker image.
4. **Version + changelog (at build time — do NOT do now):** bump `VERSION` in `plfog/version.py` (currently `0.21.7` → next patch, i.e. `0.21.8`). This is a **refinement of the existing v0.21.1 "Propose events for the Community Calendar" CHANGELOG entry** (`plfog/version.py:73-75`), which is still in the unreleased `0.21.x` line — so per the versioning rules **edit that entry in place** (add one plain-language bullet like "The 'Add your event' button now shows right at the top of the calendar, on every tab" and re-stamp its `version`/`date` to the new VERSION, moving it to the top) — **do not add a second entry**. The sync note gets **no** changelog line: it stays hidden until Google sync is switched on in production, so it is invisible to members in this release.

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*`, run in the `plfog-web` Docker image (`--no-cov` for the subset; full suite holds the ≥98% gate). This change is template-only, so all assertions are on rendered HTML via the Django test `Client` — extend the existing **`tests/hub/community_calendar_spec.py`** (patterns for GET `/calendar/`, `view_as` admin, and `MembershipPlanFactory` login already live there; sync-gate toggling patterns live in `tests/hub/community_calendar_sync_spec.py`; role gating in `tests/hub/propose_event_spec.py`; header assertions in `tests/hub/page_header_spec.py`).

**The existing CTA-presence assertions live in a different file — keep them green.** `tests/hub/community_events_spec.py` → `describe_events_tab_visibility` (lines 216-233) already asserts `+ Add event` **present** for an admin GET of `/calendar/` and **absent** for a plain member. Those assert page-level presence (`in resp.content`), **not** tab location, so lifting the CTA into the header keeps them passing — but the builder must re-run that file to confirm, and should treat it (not `community_calendar_spec.py`) as the home of the presence assertions when deciding where new cases go.

Cases:

- `describe_header_cta`
  - `it_shows_add_event_for_admin` — `view_as.is_admin` → response contains `href="…/events/add/"` and label `+ Add event`.
  - `it_shows_propose_for_member_when_policy_open` — non-admin, `member_event_policy ≠ DISABLED` → contains `href="…/events/propose/"` and `+ Propose an event`; does **not** contain the add-event href.
  - `it_hides_cta_when_policy_disabled_and_not_admin` — neither href present.
  - `it_renders_the_cta_exactly_once` — assert the CTA href count `== 1` in the served HTML (guards against leaving the Events-tab duplicate), and that it appears **before** the tab-bar sentinel (`tab === 'events'`) — i.e. it lives in the header, so it's present on the default Calendar tab.
- `describe_google_sync_note`
  - `it_hides_the_sync_note_by_default` — with both gates off (default), the copy "synced to the shared Past Lives Google Calendar" is **absent**.
  - `it_shows_the_sync_note_when_sync_is_live` — with **both** gates on (set `SiteConfiguration.google_calendar_sync_enabled=True` **and** patch `settings.GOOGLE_CALENDAR_SYNC_ENABLED=True`, or patch `hub.views._google_sync_enabled` to `True`), the copy is **present**.

**Gotchas to honor:**
- `_google_sync_enabled()` is an **AND** of env + SiteConfiguration (`hub/views.py:629-636`) — a test that flips only one gate still gets `False`; the "shown" case must set both.
- Member-gated GETs need a `MembershipPlan` seeded before login (the member-creation signal no-ops without one) — reuse `MembershipPlanFactory` as the existing spec does.
- **Default policy is `APPROVAL`, not `DISABLED`** (`core/models.py:229`), so a plain logged-in member — and an anonymous visitor — sees `+ Propose an event` by **default**. `it_hides_cta_when_policy_disabled_and_not_admin` must explicitly set `SiteConfiguration … member_event_policy = DISABLED`; it cannot rely on the default.
- **Counting the propose CTA (for `it_renders_the_cta_exactly_once`):** `_my_proposed_events.html` links each in-flight proposal via `hub_propose_event_edit` → `/events/propose/<pk>/edit/`, which *contains* the propose-new path `/events/propose/` as a prefix. Count the **exact quoted href** `b'href="/events/propose/"'`, or assert on the admin `/events/add/` URL (which has no such collision) — **never** a bare `/events/propose/` substring, or the count double-counts as soon as the member has any proposal.
- No timezone/date-window logic is touched.

## 10. Open / deferred

- **Public-surface Propose link:** `member_can_propose` does not check authentication, so on the logged-out public surface (`?public=1` / `is_public_surface`) a visitor already saw `+ Propose an event` on the Events tab when the policy is open; after this change they'll see it in the header on both tabs. `hub_propose_event` is login-gated, so a logged-out click lands on the login flow. This is **pre-existing behavior with unchanged gating** — adding an auth guard would be "a new button with different rules," which the locked decisions forbid. Deferred; revisit only if the public surface gets its own event-CTA treatment.
- **Review-queue placement:** left on the Events tab (reviewer-only tool), not lifted to the header — out of scope for this CTA-focused change. Could join the header action cluster later if reviewers want cross-tab access.
- **Sync-note precision for proposals (open — Josh's call):** the locked copy says "Events **added** to this calendar are also synced." Admin-added events publish (and sync) immediately; a member's **proposed** event is not on the calendar — and is not synced — until a reviewer approves it (`CommunityEvent.objects.published()` drives both the list and the per-event sync badge, `hub/views.py:3051`, `community_calendar.html:257`). The sentence is accurate for what actually lands on the calendar, but a member reading it right after tapping "+ Propose" might expect an instant sync. Options: keep the locked copy, or soften "added to" → "on" ("Events **on** this calendar are also synced to the shared Past Lives Google Calendar.") to sidestep the timing implication. **Left as the locked copy — not changed unilaterally, since it's a locked brainstorm decision; flag for Josh at build time.**
- **Sync-note richness:** kept to one honest sentence. A future enhancement could link "Past Lives Google Calendar" to the public calendar or an add-to-calendar action — deferred to `2026-07-12-event-pages-and-qr-codes.md` / the export controls, not built here.
- **Cross-refs:** event detail pages + QR (`2026-07-12-event-pages-and-qr-codes.md`), announcement scheduling/reminders (`2026-07-12-event-announcement-scheduling-and-reminders.md`), and the compose wizard (`2026-07-12-announcement-compose-wizard-drafts-mentions.md`) are sibling specs in this batch; this CTA/note change is independent of all three and can ship first.
