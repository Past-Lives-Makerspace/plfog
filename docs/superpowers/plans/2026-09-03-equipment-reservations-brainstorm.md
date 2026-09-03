# Equipment & Space Reservations — Design Brainstorm

**Status:** Brainstorm for council decision — NOT a build spec. Options + tradeoffs + one recommendation. No code, no migrations.
**Date:** 2026-09-03
**Surface:** FOG hub — a new sidebar **Equipment** page, per-equipment detail pages ("mini guild pages"), a booking flow, a manage panel, guild-page and spaces-map seams.
**Related:**
- `2026-07-04-space-reservations.md` — the never-built Spec 2 (meeting rooms + event space). This brainstorm **absorbs and supersedes** it; §11 reconciles the two.
- `2026-07-04-interactive-space-map.md` — shipped. `MapHotspot` already carries `MEETING_ROOM` / `EVENT_SPACE` kinds and a `"reserve"` CTA stub waiting for this feature.
- `2026-06-21-guild-orientations.md` (+ `2026-08-26-orienter-availability.md`, `2026-08-26-paid-orientations.md`) — shipped. The orientation stack is the single biggest reuse target here.

---

## 1. Problem Statement

Members share tools and rooms, but the app has no way to say "the CNC router is mine from 2 to 4 on Saturday." Today that coordination happens in Discord threads, on whiteboards, or not at all — two people show up for the same machine, and a member who was never oriented on a dangerous tool can walk up and use it with nobody the wiser.

What Jo asked for, in plain language:

1. **Members can reserve time** on a tool (CNC router, kiln, long-arm) or a space (a guild's classroom, the media room) and **see when it's already taken** before they pick a time.
2. Some things belong to a guild (the lathe is Woodshop's); some are standalone (the CNC router is the makerspace's). Guilds can also have **rentable spaces**.
3. A designated person — not necessarily a site admin — **sets the schedule and the rules** for each thing: when it's bookable, and what a member must have done first (e.g. "you must be oriented on the CNC router before you can book it"). That implies **per-equipment orientations**, which the per-type guild orientation system (v2.7+) already almost is.
4. Discovery lives on a new sidebar **Equipment** page; each item gets its own detail page that works like a small guild page.

The success test: a brand-new member finds the CNC router in the sidebar, sees "Orientation needed," books the orientation *from that page* in one flow, and after completing it comes back and books machine time — with zero admin involvement at any step.

---

## 2. What Already Exists (Reuse Map)

This is the most reuse-rich feature plfog has attempted. Almost every moving part is already on production.

| Need | Existing thing | Location |
|---|---|---|
| Per-thing orientation with duration/price/seats | `OrientationType` (per-type since issue #282: "Lathe", "CNC" are already types) | `membership/models.py:8406` |
| "Has this member done orientation X?" | `Member.is_oriented_for_type(type)` — one call, already the booking guard | `membership/models.py:1286` |
| Recurring weekly availability windows | `OrientationAvailability` (weekday + start + end + active flag) | `membership/models.py:8479` |
| Carving a booking out of an open window | `OrientationAvailabilityBlock.free_intervals()` / `valid_starts_for()` / `ensure_start_valid()` — 15-minute grid, overlap-aware, race-guarded via `select_for_update` | `membership/models.py:8551` |
| Booking lifecycle + seat holds + duplicate guards | `OrientationSlot.book()` / `OrientationBooking` statuses, `OrientationError` | `membership/models.py:8777` / `:9020` |
| Orientation booking pipeline (emails, iCal, tokens, holds, refunds) | `membership/orientations.py` (request/confirm/decline/cancel/auto-complete, `.ics`, checkout) | whole module |
| Per-resource staff role rows + "leadership" fan-out | `GuildStaffMembership` + `Guild.leadership_members()` | `membership/models.py:2296` |
| Scoped site-wide admin duty | `AdminCapability` (routes notifications AND grants the action) | `membership/models.py:2372` |
| One source of truth for "who may edit what" | `membership/permissions.py` (`can_edit_guild`, `can_manage_orientations`, …) | `membership/permissions.py` |
| Read-only physical-space records | `Space` (Airtable-pulled; `sublet_guild`, `photo`, `full_price`) | `membership/models.py:7952` |
| Map markers already typed for this feature | `MapHotspot.Kind.MEETING_ROOM` / `EVENT_SPACE`, `RESERVABLE_KINDS`, `cta_kind == "reserve"` | `membership/models.py:9587` |
| Request → review pattern (if approval is ever wanted) | `SpaceRequest` submit/approve/decline/withdraw | `membership/models.py:9902` |
| Notification spine + per-user channel prefs | `emit()` + registry/copy + settings matrix ("Spaces" category exists; "Staff & leadership" section exists) | `core/events/*` |
| Scheduled jobs (reminders, expiry) | `run_scheduled_tasks` dispatcher, 15-minute Render cron | `core/management/commands/run_scheduled_tasks.py` |
| Overlap-free time-range queries | interval predicate pattern (`starts_at__lt=ends, ends_at__gt=starts`) designed in the 07-04 spec §5 — still the right shape | that spec |
| UI components | `modal.html`, `confirm_modal.html`, `form_field.html`, half-hour time selects (FRONTEND rule 20), recurring-hours formset editor in `guild_edit.html`, `page_header.html`, toasts | `templates/components/`, `templates/hub/guild_edit.html` |
| Mini-page content patterns | Guild page: hero image (`HeroCropMixin`), FAQ/Links formset editors, gallery, closed-message banner | `templates/hub/guild_detail.html` / `guild_edit.html` |

**Genuinely net-new:** one resource model, one reservation model, weekly-hours rows for equipment, the overlap check (still doesn't exist anywhere — `Lease` has none either), the Equipment index/detail pages, and 4–5 notification events. That's it. Everything else is assembly.

---

## 3. Domain Model — What Is "an Equipment"?

Three candid options, weighed against the one hard constraint in this repo: **`Space` is an Airtable read model.** `airtable_pull` owns it; Django never writes it (membership/CLAUDE.md is explicit). Any design that needs Django-authored rows in the `Space` table fights the sync.

### Option A — New `Equipment` model, optional `guild` FK

A Django-owned model: name, photo, description, `kind` (tool / room), optional `guild` FK (blank = standalone, like the CNC router), `is_active`, plus booking config (§4) and requirements (§5).

| For | Against |
|---|---|
| Django owns it outright: leads/admins CRUD it in the hub, no sync choreography | Rooms that are *also* leasable Airtable Spaces need a link (solved below) |
| Tools were never in Airtable; nothing to migrate | A second "physical thing" model next to `Space` — must keep their roles crisply separated |
| `guild` FK mirrors how `OrientationType.guild`, `Product.guild`, `Category.guild` already work | |

### Option B — Extend `Space` to cover tools

Add `TOOL` to `Space.SpaceType`, bolt booking fields onto `Space`.

| For | Against |
|---|---|
| One model for every physical thing | **Breaks the Airtable contract.** Either tools go into Airtable (council now maintains a tool inventory in a second system, and every new tool is a sync round-trip) or Django-native rows live in a synced table and `airtable_pull` has to tip-toe around them forever. Both are standing footguns. |
| | `Space` is leasing-shaped (sqft, rent, deposits). A router has none of that; a dozen null columns per tool. |
| | Guild leads could never self-serve tool creation (Space edit authority is Airtable's). |

Rejected. This is the option the Airtable constraint exists to prevent.

### Option C — Polymorphic `Bookable` wrapper (GenericFK over Space, Equipment, future kinds)

A `Bookable` row with a GenericForeignKey to whatever is being booked, like `Lease.tenant`.

| For | Against |
|---|---|
| Infinitely extensible | plfog has exactly two bookable kinds in sight (tools, rooms), and both fit one table. GenericFKs cost joins, admin ergonomics, and query clarity — `Lease.tenant` is tolerated, not loved. |
| | The 07-04 spec already found the simpler shape: a concrete resource row that *optionally points at* a Space. Polymorphism is YAGNI on top of that. |

### Recommendation: **A, with a nullable `space` link** (this is really A absorbing the good half of C)

One Django-owned model — working name **`Equipment`** (member-facing word Jo already chose; it reads fine for rooms too, but see §12 open question 6 on naming):

- `name`, `photo`, `description` (the mini-page content), `kind` (`TOOL` / `ROOM`), `is_active`
- `guild` — nullable FK. Set: "belongs to Woodshop," surfaces on that guild's page, guild leadership manages it. Null: standalone, managed by its own managers + admins.
- `space` — nullable FK → `Space`, `on_delete=SET_NULL`, **read-only relationship**: when an Equipment fronts a physical room that exists in Airtable, the link supplies photo/size/map identity. Django never writes through it. This is exactly the `ReservableResource.space` seam the 07-04 spec designed, and how `MapHotspot.space` already behaves.
- Booking config and requirements per §4/§5.

**The rentable-guild-space tension, resolved:** a guild's room lives *twice on purpose*, because it is two different products. Its **Airtable `Space` row** is the *monthly-lease* identity (rent, occupants, `SpaceRequest` → human finalizes in Airtable — all shipped). Its **`Equipment` row** is the *hourly-reservation* identity (this feature). The `Equipment.space` FK ties them so the map and the detail page tell one story. Hourly booking never touches Airtable; monthly leasing never touches Equipment. No sync, no write conflict, and each flow keeps its existing owner.

**Where the code lives:** `membership/models.py` + `hub/` views/templates, same as orientations and the map — keeps everything inside the coverage/mypy source set (`plfog`, `core`, `membership`). The empty `tools/` placeholder app is tempting but would need coverage/mypy/app wiring and would strand FKs across apps for no gain; leave it empty.

---

## 4. The Reservation Model — Time Semantics

Three ways to model "book 2 to 4 on the CNC":

| Model | How it works | Verdict |
|---|---|---|
| **(1) Open calendar with rules** | Equipment publishes weekly opening hours + duration limits. Member picks any free start on a half-hour grid within hours; system checks overlap; booking is instant. | **Right fit.** Equipment use is continuous and self-serve. No human in the loop. |
| **(2) Pre-published slots** (like `OrientationSlot`) | Manager posts discrete slots; members claim seats. | Wrong fit for tools: usage lengths vary, a manager would hand-post every bookable hour forever, and seats > 1 makes no sense for a router. Slots exist because orientations are *taught sessions*; reservations aren't. |
| **(3) Generalize `OrientationAvailabilityBlock`** | Reuse `valid_starts_for` / `free_intervals` to carve reservations out of posted windows. | The *algorithm* is exactly right — grid-aligned starts, overlap-aware free intervals, `select_for_update` race guard. But the *model* is one person's one-off posted time with a carved-out `OrientationSlot` + booking pair riding a request/confirm pipeline. Reservations need none of that ceremony. **Steal the algorithm, not the tables.** Lifting the block machinery into a shared base class would churn a shipped, subtle system (payment holds, seat-holding semantics) for two callers — tend, don't churn. |

### Recommended shape

Two small models:

**`EquipmentHours`** — weekly opening windows, structurally a clone of `OrientationAvailability` (weekday, start_time, end_time, is_active) minus orienter/seats. A formset on the manage panel, using the exact recurring-hours editor pattern from `guild_edit.html`. No hours rows = not bookable yet (honest empty state, not "always open").

**`EquipmentReservation`** — `equipment` FK, `member` FK, `starts_at`, `ends_at`, optional short `purpose`, `status` (`CONFIRMED` / `CANCELLED` — note: no PENDING in the MVP, see §12 Q1), `cancelled_by` / `cancelled_reason` for manager cancels, timestamps.

**Semantics and rules:**

- **Grid:** starts on half-hour marks (FRONTEND rule 20 already bans per-minute pickers — the domain rule and the UI rule agree). Duration from a dropdown of sensible lengths (30m … up to the equipment's max).
- **Conflict prevention:** the interval predicate (`starts_at__lt=ends, ends_at__gt=starts`) in a manager method + `clean()`, re-checked inside `transaction.atomic()` with `select_for_update()` on the Equipment row before insert — the same two-layer guard `ensure_start_valid` uses. **No Postgres ExclusionConstraint** (CI runs SQLite; the 07-04 spec settled this). Adjacent bookings (4:00 end, 4:00 start) do not conflict.
- **Per-equipment knobs** (fields on Equipment, all with sane defaults so config is optional): `max_duration_minutes` (default 240), `min_duration_minutes` (default 30), `max_advance_days` (default 30 — how far ahead you can book), `max_active_reservations_per_member` (default 2 — the anti-hog rule; cheap to enforce, huge fairness payoff).
- **Quiet hours:** not a separate concept — that's just what the weekly hours *don't* cover. YAGNI on a second layer.
- **Closures:** `is_closed` + `closed_message` ("Down for maintenance, back Tuesday"), mirroring `GuildOrientationSettings.is_closed`. Closing blocks new bookings only; the manager decides whether to cancel existing ones (each cancel notifies).
- **Cancellation:** the member cancels their own anytime before start (confirm modal, frees the window, no penalty). A manager can cancel any reservation **with a required reason** that reaches the member (mirrors `decline` requiring notes everywhere else in the codebase).
- **Recurring reservations:** punt (§10). Singles first; recurrence is where booking systems go to die, and the standing-meeting case is better served by the never-built approval flow anyway.
- **No-show policy:** punt. Track nothing in MVP; a `no_show` flag a manager can set is a cheap later add once there's evidence of a problem. Social enforcement first.
- **No approval loop.** Booking is instant self-serve. This is the deliberate philosophical split from orientations (a person must agree to teach you) and from the 07-04 spec (rooms wanted stewarding). A machine doesn't need to consent. §11/§12 cover the resources that *do* want approval.

---

## 5. Requirements & Gating

Per-equipment booking requirements, checked at book time and (crucially) *explained* on the detail page before the member hits a wall:

| Requirement | Mechanism | Cost |
|---|---|---|
| Active member | `member.status == ACTIVE` — always required, not configurable (same as space requests) | free |
| Oriented on this equipment | `required_orientation` FK → `OrientationType`, nullable. Gate = `member.is_oriented_for_type(equipment.required_orientation)`. | **one FK + one existing method call** |
| Guild membership | `requires_guild_membership` bool (only meaningful when `guild` is set); checks official `GuildMembership` | one bool |

### How per-equipment orientation reuses the machinery — three options

`OrientationType` already *is* per-equipment in spirit — "Lathe" and "CNC" are types today. The question is only where the type hangs when equipment enters the picture:

**(A) FK to an existing guild's type (recommended).** `Equipment.required_orientation` points at any guild's `OrientationType`. Woodshop's lathe requires Woodshop's "Lathe" type — which already exists, already has slots/blocks/orienters/emails/auto-complete/paid checkout. **Zero changes to the orientation pipeline.** The wrinkle: a *standalone* tool's orientation still needs a hosting guild, because the whole pipeline (settings, dashboard, orienter staff roles, `bookable()` filters) is guild-anchored. Fix: standalone equipment orientations live under a house guild (a real "Makerspace" or "Shop" guild the council already effectively is — the same move as the Cartographers example-guild mechanics, but active), and the tool's schedule manager is granted the `orienter` staff role there, which hands them the entire existing dashboard for free.

**(B) Generalize the FK.** Make `OrientationType.guild` nullable and add `OrientationType.equipment`; teach settings/slots/blocks/dashboard/bookable()/emails to resolve "the offering entity" polymorphically. Honest cost: the guild FK is denormalized onto `OrientationSlot` and `OrientationBooking`, `bookable()` joins through `guild__orientation_settings`, orienters are `GuildStaffMembership` rows, and the dashboard scopes by guild — this touches a shipped, payment-holding, race-guarded system in ~15 places. It is the *right* end state if guild-free orientations become common, but it is a refactor of working plumbing, not a feature.

**(C) Auto-created pseudo-guild per standalone tool.** A hidden guild row per router. Rejected: pollutes voting, directory, guild lists; every guild surface grows an "except pseudo-guilds" filter.

**Recommendation: A now, B only if reality demands it.** The FK is one column; if B ever happens, A's data migrates trivially (the types just move).

**Who manages requirements:** whoever can manage the equipment (§6) edits its requirement fields on the manage panel. Setting `required_orientation` offers the owning guild's active types (or the house guild's, for standalone) in a dropdown, plus "Create a new orientation type" linking to the existing orientation settings editor.

---

## 6. Permissions — the "Schedule Manager for X" Grant

Three candidates:

| Option | Shape | Verdict |
|---|---|---|
| **django-guardian object perms** | `assign_perm("manage_equipment", user, equipment)` | The workspace CLAUDE.md §5 names guardian as the standard — but **guardian is not installed and nothing in plfog uses it** (the only "guardian" hits are parent/guardian copy in `classes/`). Adopting it here would introduce a second, invisible permission system next to the real one, with no hub UI for grants and no `leadership_members()`-style audience fan-out. Following the letter of the doc would violate its spirit (one source of truth). |
| **`EquipmentStaffMembership`** (mirror of `GuildStaffMembership`) | A row: equipment FK, member FK, role (`manager`), granted_by, timestamps | **The house's actual working pattern.** Gives: a visible, hub-manageable grant; a notification audience (`equipment.manager_members()`); the same mental model leads already have. |
| **`AdminCapability.EQUIPMENT`** | Site-wide "Equipment Administrator" | Wrong granularity alone (Jo asked for per-tool managers) — but the right *complement*: capabilities are exactly how plfog scopes site-wide duties. |

**Recommendation — three-tier resolution, one function:**

`can_manage_equipment(request, equipment)` in `membership/permissions.py` (view_as-aware, like its siblings) returns true for:
1. **Site tier:** full admins, plus holders of a new `AdminCapability.Capability.EQUIPMENT` ("Equipment Administrator") — which also routes equipment admin notifications, per the capability contract.
2. **Guild tier:** the owning guild's leadership (`guild.is_staffed_by(member)` or lead) — Woodshop's staff automatically manage Woodshop's tools; no extra grants to babysit.
3. **Resource tier:** `EquipmentStaffMembership` holders — the targeted "Dana runs the CNC schedule" grant Jo described, assignable by tiers 1–2 from the manage panel (Staff-tab pattern from guild edit).

Never gate on `is_staff` / `fog_role` / `member_type` (membership/CLAUDE.md's hard rule). Equipment creation authority is §12 Q3.

---

## 7. UX Walkthrough

### 7.1 Sidebar: **Equipment**

New entry in `templates/hub/base.html` directly under **Spaces** (they're siblings: Spaces = where things are and monthly leasing; Equipment = booking time on things). Wrench icon. Appears in both the member and admin nav variants like Spaces does.

### 7.2 Equipment index — `/equipment/`

`page_header.html` ("Equipment", description: "Reserve time on shared tools and rooms."). Then:

- **Filter row:** guild filter chips (All / per-guild / Standalone), kind toggle (Tools / Rooms), and a search box — same interaction furniture as the member directory.
- **Card grid** (`repeat(auto-fill, minmax(16rem,1fr))`, one column on phones). Each card: photo (or kind-icon placeholder), name, guild chip (linking to the guild page), and **two status lines that answer the member's real questions at a glance:**
  - *Availability now:* "Available now" (green) / "Reserved until 4:00 PM" (amber) / "Closed: back Tuesday" (red) — computed from current reservations + closure.
  - *Your access:* "Ready to book" / "Orientation needed" / "Woodshop members only". This badge is the discovery hook — the index teaches members what stands between them and each tool.
- **Empty state:** "No equipment is bookable yet. Check back soon."
- Managers/admins see a "+ Add equipment" button (per §12 Q3's answer).

### 7.3 Equipment detail — `/equipment/<slug>/` — the mini guild page

Top to bottom (mobile = same order, single column):

1. **Hero:** photo (reuse `HeroCropMixin` conventions), name, guild chip, kind badge, closed banner when `is_closed` (exact `closed_message` pattern from guild orientations).
2. **Requirements banner** — the heart of the gating UX. One card, one state:
   - All met → green: "You're all set. Pick a time below."
   - Orientation missing → amber, with the requirement named and **the fix embedded**: "You need the CNC Router orientation before you can book time here." + primary button **"Book the orientation"** → deep-links into the *existing* guild orientation booking flow with this type preselected (the guild page partial already books per-type; this is a query param, not a new flow). If the member has a pending orientation booking: "Your orientation is booked for Sat, Sep 12 at 2:00 PM." with a link to it. **This closes Jo's one-flow loop:** discover tool → see gap → book orientation → orientation auto-completes after it happens (existing job) → banner flips green.
   - Guild membership missing → amber + "Join Woodshop" button (the existing join flow).
   - Membership inactive → "Your membership needs to be active to reserve equipment."
3. **Schedule (this week):** a 7-day strip; tap a day to see its timeline of open hours with reserved spans marked "Reserved" (busy/free, like the 07-04 busy-times panel but always-on). Days outside opening hours or past `max_advance_days` are muted. Prev/next week arrows within the advance window.
4. **Book a Time** (only rendered when requirements are met): day (from the strip) → **start select** (a `<select>` of the *computed free half-hour starts* for that day — the `valid_starts_for` algorithm re-derived over EquipmentHours minus reservations; an impossible start is simply never offered) → **duration dropdown** (30m increments up to the max that fits before the next reservation/closing — the option list itself shrinks so conflicts are unrepresentable) → optional one-line purpose → **"Reserve"** in a confirm modal ("Reserve the CNC Router for Sat, Sep 12, 2:00 to 4:00 PM?") → toast "Reserved. See you Saturday." The rare race loss returns the friendly error and refreshes the start list ("That time was just taken. Please pick another time." — same voice as `ensure_start_valid`).
5. **Your reservations here** + **Upcoming reservations** list (who has it when — see §12 Q5 on name visibility). Member's own rows get a Cancel button (danger, confirm modal).
6. **About:** description, location, and the linked `Space` details (size, map link) when `space` is set.
7. **Manage panel** (managers only, collapsed or a separate `/manage/` tab): hours formset (recurring-hours editor pattern), closure toggle + message, requirement fields, per-member limits, staff grants, reservation list with manager-cancel (reason required), edit name/photo/description.

### 7.4 Guild page seam

Guilds with equipment get an **Equipment section/tab** on `guild_detail.html`: the same cards, filtered to the guild, linking into the detail pages. The guild edit page's tab strip gains nothing new — equipment is edited on its own manage panel (guild leadership passes `can_manage_equipment` automatically).

### 7.5 Spaces map seam

`MapHotspot` already ships `MEETING_ROOM` / `EVENT_SPACE` kinds whose `cta_kind` is `"reserve"` with a dead-end label. Phase 2: an `Equipment.hotspot` (or hotspot → equipment) link makes that CTA a real "Reserve" button into the detail page — the exact seam the 07-04 spec drew, pointed at the new model. Degrades cleanly: no link, no button, index still reaches everything.

### 7.6 States checklist

- **Empty schedule** (no hours yet): "This equipment isn't taking reservations yet." (managers see "Add opening hours to start taking reservations").
- **Fully booked day:** the start select for that day is replaced by "No open times this day. Try another day."
- **Closed:** banner + booking UI hidden, schedule still visible.
- **Race conflict:** friendly error + refreshed starts (above).
- **Cancelled by manager:** the member's row shows "Cancelled by the manager: <reason>".
- Dark/light themes, `pl-` classes, theme tokens only, half-hour selects — all per FRONTEND.md; nothing here needs a new component primitive.

---

## 8. Notifications

All through `emit()` + registry/copy + the settings matrix. Reuse the **"Spaces"** trigger category, relabel to **"Spaces & Equipment"** (one string; avoids a category migration). Every email follows the FRONTEND email rules (linked subject noun, one CTA, absolute URLs, branded shell).

| Event key | When | Audience | Notes |
|---|---|---|---|
| `equipment.reservation_confirmed` | on booking | member (`SINGLE_USER`) | Instant confirmation + `.ics` attachment (builder pattern from `membership/orientations.py:build_ics`). CTA: "See your reservation". |
| `equipment.reservation_reminder` | ~24h before, via `run_scheduled_tasks` | member | Dedupe with the `ScheduledNotificationMarker` pattern. Phase 2. |
| `equipment.reservation_cancelled_by_manager` | manager cancel | member | Carries the required reason. CTA: "Pick a new time". |
| `equipment.reservation_made` | on booking | managers (resolver: equipment staff ∪ owning-guild leadership ∪ EQUIPMENT capability holders — a compose-style resolver like `guild_leadership_or_admins`) | Shown in the **Staff & leadership** settings section; default in-app on, email off (it's awareness, not action — no approval exists). |
| `equipment.hours_changed` | — | — | Not emitted. YAGNI; the schedule page is the truth. |

Member self-cancel notifies nobody by default (no approver exists to care); the managers' `reservation_made` feed shows the freed slot naturally. Orientation-related notifications (request/confirm/thank-you) are **already shipped** and fire unchanged when the member books the gating orientation.

---

## 9. Phasing

**MVP (phase 1 — the decision-complete cut):**
- `Equipment` (+ optional `guild`, `space`, `required_orientation`, `requires_guild_membership`, limits, closure) + `EquipmentHours` + `EquipmentReservation` + overlap guard + `EquipmentError`.
- `can_manage_equipment` + `EquipmentStaffMembership` + `AdminCapability.EQUIPMENT`.
- Equipment sidebar page, index, detail page with requirements banner + orientation deep-link, Book a Time, cancel flows, manage panel.
- `equipment.reservation_confirmed` / `_cancelled_by_manager` / `_made` events.
- Changelog entry, member-friendly.

**Phase 2 (fast follows):**
- Reminder cron event + `.ics` on confirmation (if not squeezed into MVP).
- Guild-page Equipment section; map hotspot link.
- Reservation history/CSV on the manage panel.

**Deliberately punted (with the reason):**
- **Recurring reservations** — complexity magnet; no evidence of demand yet.
- **Approval-required booking** (`booking_policy=APPROVAL`) and the **event-space → CommunityEvent publish** — this is the 07-04 spec's remaining half; see §11. Punt until the event space actually onboards.
- **Paid reservations** — the plumbing exists (Tab, paid-orientation checkout) but pricing shared tools is a council policy question first (§12 Q2).
- **No-show tracking / penalties, usage analytics, check-in kiosk** — measure the honor system before building enforcement.

---

## 10. Migration & Data Notes (design-level only)

- All-additive schema; no changes to any Airtable-synced table.
- Seeding: council enters the initial inventory by hand (a dozen items); no import needed.
- Existing "Lathe"/"CNC" orientation types link up via the new FK with zero data movement.

## 11. Reconciling the 2026-07-04 Space-Reservations Spec

That spec (never approved, never built) designed `ReservableResource` + approval-gated `SpaceReservation` for meeting rooms and the event space. This design **replaces its resource model** — `Equipment` with `kind=ROOM` and a `space` link *is* `ReservableResource`, generalized — and **defers its approval + CommunityEvent-publish flow** rather than deleting it. If/when the event space needs stewarded bookings, it returns as a per-equipment `booking_policy` (instant vs. approval) on this model, reusing `SpaceRequest`'s review shape and the spec's `publish()` choke-point plan verbatim. The old spec should be marked superseded-by-this-file when this ships, so nobody builds two resource models.

## 12. Open Questions for Jo (product-level either/ors only)

1. **Instant everywhere, or approval for some?** MVP proposes every reservation self-confirms. Is that acceptable for *rooms* too (a guild's classroom), or should rooms ship later with the approval policy? (Tools instant, rooms deferred is the clean cut.)
2. **Paid reservations ever?** e.g. laser time at $X/hour onto the member's Tab. Shapes whether Equipment carries a price field from day one (I'd leave it off until yes).
3. **Who creates equipment?** Admins/EQUIPMENT capability only, or may guild leads self-serve create equipment for their own guild? (Recommendation: leads can, for their guild; standalone creation stays admin.)
4. **Standalone-tool orientations under a house guild** — is an active "Makerspace"/"Shop" guild hosting them (with tool managers granted the orienter role there) acceptable, or is guild-free orientation a hard requirement (that triggers the §5-B pipeline generalization)?
5. **Reservation privacy:** show reserver names on the schedule to all logged-in members (community norm, easy coordination: "oh, I'll ask Sam to swap"), or names to managers only and "Reserved" to everyone else?
6. **Naming:** "Equipment" for the sidebar and model even though rooms live there too, or a broader word ("Reservations", "Book Time")? Copy on cards can soften it either way.
7. **Default limits:** happy with max 2 upcoming reservations per member per equipment, 4h max duration, 30-day booking horizon as the shipped defaults (all per-equipment editable)?

## 13. Recommendation (plain)

Build **one Django-owned `Equipment` model** (optional guild, optional read-only link to an Airtable `Space`), with **weekly opening hours + instant, self-serve, conflict-checked reservations** on a half-hour grid — stealing the `valid_starts_for` algorithm from orientation blocks but not their tables, and adopting the never-built 07-04 spec's overlap engine. Gate booking with **one FK to the existing `OrientationType`** so "must be oriented on the CNC" costs a single column and reuses the entire shipped orientation pipeline, dashboard, and emails; standalone tools host their orientations under a house guild for now. Authorize managers with the **house's real pattern** — `can_manage_equipment()` over admin capability + owning-guild leadership + a per-equipment staff row — not guardian, which the codebase has never actually used. Ship the sidebar **Equipment** index and mini-guild-page details whose requirements banner books the missing orientation in one flow. Punt recurrence, approvals, payment, and penalties until demand shows up. This lands as roughly three new models and one new hub surface on top of plumbing that is already on production.
