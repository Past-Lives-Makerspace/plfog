# Equipment & Space Reservations — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build. Phased as **three sequential PRs**, each shipping green to production.
**Date:** 2026-09-03
**Surface:** FOG hub — new sidebar **Equipment** page, per-equipment detail pages, booking flow, manage panel, guild-page seam, home-page card
**Related:**
- `2026-09-03-equipment-reservations-brainstorm.md` — the decision doc this spec implements. Owner said "proceed"; the doc's own recommendations are adopted as the locked decisions below.
- `2026-07-04-space-reservations.md` — superseded by this spec (PR 1 stamps it so).
- `2026-07-04-interactive-space-map.md` — shipped; its `"reserve"` CTA stub is wired up in PR 3.
- `2026-06-21-guild-orientations.md` + `2026-08-26-orienter-availability.md` — shipped; the orientation stack is the single biggest reuse target.

---

## 1. Summary

Members can find every shared tool and room on a new sidebar **Equipment** page, see what stands between them and each one ("Orientation needed," "Woodshop members only"), book the missing orientation in one flow from the tool's own page, and then reserve time on it instantly on a half-hour grid — conflict-checked, no admin in the loop. A designated manager per equipment (or the owning guild's leadership, or an Equipment Administrator) sets the weekly hours, requirements, and limits.

### Locked decisions (from brainstorm §12, owner said "proceed" = adopt the doc's recommendations)

| # | Question (brainstorm §12) | Locked call |
|---|---|---|
| 1 | Instant everywhere, or approval for some? | **Instant, self-serve, everywhere.** Every reservation self-confirms. Rooms may ship as `kind=ROOM` with instant booking; any room that wants a human approver is **out of scope** (§11) until the approval policy returns as a later `booking_policy` field. |
| 2 | Paid reservations? | **No price field, no payment.** Punted until council sets a pricing policy (§11). |
| 3 | Who creates equipment? | **Creation is admin-gated:** full admins + `AdminCapability.EQUIPMENT` holders only. Guild leads and per-equipment managers *edit and run* equipment they manage but do not create it. |
| 4 | Standalone-tool orientations | **A house "Makerspace" guild hosts them** — a real, active guild the council operates (data, not code: no house-guild flag or seed command). A standalone tool's manager is granted the `orienter` staff role there, which hands them the entire existing orientation dashboard. No §5-B pipeline generalization. |
| 5 | Reservation privacy | **Reserver names visible to all logged-in members** on the schedule (community norm, easy coordination). |
| 6 | Naming | **"Equipment"** — sidebar label, model name, URL prefix — even though rooms live there too. Card copy softens it ("tools and rooms"). |
| 7 | Default limits | **Shipped defaults locked:** max 2 upcoming reservations per member per equipment, 4 h max duration, 30 m min duration, 30-day booking horizon. All per-equipment editable. |
| — | Domain model (brainstorm §3) | **Option A + nullable `space` link:** one Django-owned `Equipment` model; optional `guild` FK; optional **read-only** FK to the Airtable-synced `Space` — Django never writes across it. |
| — | Time semantics (§4) | **Open calendar with rules:** weekly `EquipmentHours` + instant conflict-checked `EquipmentReservation`. **Steal the `valid_starts_for` algorithm, not the orientation tables.** No `ExclusionConstraint` (CI runs SQLite). |
| — | Permissions (§6) | **Not django-guardian** (never installed, never used here — adopting it would add a second invisible permission system). Three-tier `can_manage_equipment()`: EQUIPMENT capability / owning-guild leadership / `EquipmentStaffMembership`. |
| — | Orientation gating (§5) | **One FK** — `Equipment.required_orientation` → existing `OrientationType`; gate is the existing `Member.is_oriented_for_type()`. Zero changes to the orientation pipeline. |
| — | Notification category (§8) | Reuse the **"Spaces"** category, relabeled **"Spaces & Equipment"** (code-string relabel, no migration). |

---

## 2. What already exists (reuse, don't reinvent)

All locations re-verified in the current tree, 2026-09-03. **The brainstorm's citations are accurate — no symbol drift found.** One correction to a *recent spec's* claim: `membership/spec/` and `hub/spec/` do **not** exist; membership and hub specs live in the root `tests/` tree (`tests/membership/`, `tests/hub/`) — see §10.

| Need | Existing thing | Location |
|---|---|---|
| Per-thing orientation (duration/price/seats) | `OrientationType` (per-type: "Lathe", "CNC" are already types) | `membership/models.py:8406` |
| "Has this member done orientation X?" | `Member.is_oriented_for_type(orientation_type)` | `membership/models.py:1286` |
| Weekly recurring windows (structural model to clone) | `OrientationAvailability` (weekday/start/end/is_active) | `membership/models.py:8479` |
| Carve valid starts from open time (the algorithm to steal) | `OrientationAvailabilityBlock.free_intervals()` / `valid_starts_for(orientation_type)` / `ensure_start_valid()` — grid-aligned, overlap-aware, `select_for_update` race guard | `membership/models.py:8638` / `:8652` / `:8671` |
| Orientation booking pipeline (fires unchanged when a member books the gating orientation) | `membership/orientations.py` — request/confirm/decline/cancel/auto-complete, signed tokens, `build_ics`, checkout | whole module |
| Per-resource staff rows + leadership fan-out | `GuildStaffMembership`; `Guild.leadership_members()`; `Guild.is_staffed_by()` | `membership/models.py:2296` / `:2193` / `:2105` |
| Scoped site-wide admin duty (routes notifications AND grants the action) | `AdminCapability` — `Capability` TextChoices at `:2389`, currently six entries, no EQUIPMENT | `membership/models.py:2372` |
| One source of truth for "who may edit what" (view_as-aware) | `membership/permissions.py` — `can_edit_guild:53`, `can_manage_orientations:61` as the style to mirror | `membership/permissions.py` |
| Read-only physical-space records (Airtable-pulled; Django never writes) | `Space` | `membership/models.py:7952` |
| Map markers pre-typed for this feature | `MapHotspot` — `Kind.MEETING_ROOM`/`EVENT_SPACE`, `RESERVABLE_KINDS`, `cta_kind == "reserve"` stub | `membership/models.py:9567` |
| Notification spine | `emit()` (context, per-channel `messages`, `attachments`, `period`) | `core/events/emit.py:44` |
| Event registry / channel defaults / copy / resolvers | `EventType` defs, `ChannelDefault.FORCED` for booking updates, compose-style resolvers (`guild_leadership_or_admins:140`, `space_approvers:191`) | `core/events/registry.py`, `resolvers.py`, `copy.py` |
| Settings-page grouping ("Spaces" category; "Staff & leadership" section) | `CATEGORY_ORDER` (contains `"Spaces"`), `STAFF_SECTION` | `core/events/settings_matrix.py:63-84` |
| Half-hour time selects (FRONTEND rule 20) | `half_hour_time_choices(required)` | `hub/forms.py:70` |
| Recurring-hours formset editor (canonical, cited by FRONTEND.md) | orientation hours editor: `extra=0`, hidden `empty_form` `<template>`, "+ Add" clone, real Delete buttons | `templates/hub/guild_edit.html` |
| Mini-page content patterns | Guild page hero (`Guild(HeroCropMixin, …)` at `membership/models.py:1690`; mixin in `core/models.py`), closed-message banner (`GuildOrientationSettings.is_closed`, `:8290`) | `templates/hub/guild_detail.html` |
| Deep link into a guild's orientation section | `guild_detail.html` already reads `?tab=orientations` + `#guild-orientation` and scrolls to the section | `templates/hub/guild_detail.html:122` |
| Sidebar nav (two variants, member + admin) | Spaces entries in `templates/hub/base.html:161` and `:283` | `templates/hub/base.html` |
| Scheduled jobs | `SCHEDULED_JOBS` registry + 15-minute Render cron | `core/scheduled_jobs.py:59` |
| Guild membership check | `GuildMembership` | `membership/models.py:4471` |
| Interval-overlap predicate shape | `starts_at__lt=ends, ends_at__gt=starts` (designed in the 07-04 spec §5; still unbuilt anywhere) | this spec builds it |
| UI components | `modal.html`, `confirm_modal.html`, `form_field.html`, `toggle.html`, `.pl-help`, `page_header.html`, `trigger_toast()` | `templates/components/`, `hub/toast.py` |

**Genuinely net-new:** `Equipment`, `EquipmentStaffMembership`, `EquipmentHours`, `EquipmentReservation`, `EquipmentError`, the overlap predicate, `can_manage_equipment`, one capability enum value, the Equipment index/detail/manage pages, and four notification events. Everything else is assembly.

---

## 3. Where the code lives

Same homes as orientations and the map — inside the existing coverage/mypy source set. The empty `tools/` placeholder app stays empty (wiring it would strand FKs across apps for nothing).

```
membership/models.py            # + Equipment, EquipmentStaffMembership (PR 1); EquipmentHours, EquipmentReservation, EquipmentError (PR 2); AdminCapability.Capability.EQUIPMENT (PR 1)
membership/migrations/          # PR 1: models + capability backfill; PR 2: hours/reservation + config fields; PR 3: MapHotspot.equipment
membership/permissions.py       # + can_manage_equipment(), can_create_equipment() (PR 1)
membership/equipment.py         # PR 2 service: reserve(), cancel flows, .ics builder (mirrors membership/orientations.py)
hub/equipment_views.py          # new module (house precedent: hub/meeting_views.py)
hub/forms.py                    # EquipmentForm, EquipmentHoursFormSet, ReservationForm, manager-cancel form, staff-grant form
hub/urls.py                     # /equipment/ routes (§6)
templates/hub/base.html         # sidebar entry x2 (member + admin variants)
templates/hub/equipment_index.html
templates/hub/equipment_detail.html
templates/hub/equipment_manage.html
templates/hub/partials/equipment_schedule.html   # week strip + day timeline + booking form (HTMX-swapped)
templates/hub/partials/equipment_cards.html      # card grid, shared by index + guild page (PR 3)
templates/membership/emails/equipment_*.{html,txt}  # confirmation / manager-cancel / reminder
core/events/registry.py         # 4 event defs; "Spaces" → "Spaces & Equipment" relabel on the space.* entries
core/events/copy.py             # default copy per channel per event
core/events/resolvers.py        # + equipment_managers resolver
core/events/settings_matrix.py  # CATEGORY_ORDER relabel
core/scheduled_jobs.py          # PR 3: reservation reminder job
tests/membership/ + tests/hub/  # *_spec.py (root tests tree — NOT membership/spec/, which does not exist)
tests/e2e/                      # PR 3: reserve-flow Playwright spec
```

---

## 4. Data model

### 4.1 `Equipment` (PR 1) — `Equipment(HeroCropMixin, models.Model)`

| Field | Type | Notes |
|---|---|---|
| `name` | `CharField(120)` | `help_text="Display name members see, e.g. CNC Router."` |
| `slug` | `SlugField(unique=True)` | Auto-generated from name, stable across renames (the `Guild.slug` pattern at `:1695`). |
| `kind` | `CharField`, `TextChoices` `TOOL`/`ROOM` | `"Tool"` / `"Room"`. |
| `guild` | `FK(Guild, null=True, blank=True, on_delete=PROTECT, related_name="equipment")` | Set: belongs to that guild; its leadership manages it; surfaces on the guild page (PR 3). Null: standalone. PROTECT: deleting a guild with equipment must be a deliberate re-home, not a silent cascade. |
| `space` | `FK(Space, null=True, blank=True, on_delete=SET_NULL, related_name="equipment")` | **Read-only relationship.** Supplies the linked room's size/map identity on the detail page. Django never writes through it; `airtable_pull` never sees `Equipment`. |
| `photo` | `ImageField(blank=True)` | Hero image; `HeroCropMixin` supplies the crop fields, same as `Guild`. |
| `description` | `TextField(blank=True)` | The mini-page About body. |
| `location_note` | `CharField(200, blank=True)` | "Back corner of the wood shop." Cheap, high-value wayfinding. |
| `required_orientation` | `FK(OrientationType, null=True, blank=True, on_delete=PROTECT, related_name="gated_equipment")` | The whole gating mechanism. PROTECT: deleting an orientation type that gates live equipment should fail loudly, not silently un-gate a dangerous tool. |
| `requires_guild_membership` | `BooleanField(default=False)` | Only meaningful when `guild` is set; checked against official `GuildMembership`. |
| `is_active` | `BooleanField(default=True)` | Inactive = hidden from the member index entirely (retired gear). |

`__str__`: `"CNC Router (Tool)"` via `get_kind_display()`. Every field gets `help_text` (CLAUDE.md §3). Manager: `EquipmentQuerySet` with `.active()`, `.for_guild(guild)`, `.standalone()`.

Model properties/methods (PR 1): `manager_members() -> list[Member]` (staff rows ∪ owning-guild `leadership_members()` ∪ EQUIPMENT capability holders, deduped — the notification audience and manage-panel display), `access_state(member)` returning a small enum/dataclass the banner and index badge both render from (`OK` / `NEEDS_ORIENTATION` / `NEEDS_GUILD` / `INACTIVE_MEMBER`) so the two surfaces can't drift.

### 4.2 `EquipmentStaffMembership` (PR 1) — mirror of `GuildStaffMembership`

| Field | Type | Notes |
|---|---|---|
| `equipment` | `FK(Equipment, on_delete=CASCADE, related_name="staff_memberships")` | |
| `member` | `FK(Member, on_delete=CASCADE, related_name="equipment_staff_memberships")` | |
| `role` | `CharField`, `TextChoices` — single value `MANAGER` | One role today; TextChoices anyway so a future role is a value, not a migration of shape. |
| `granted_by` | `FK(Member, null=True, on_delete=SET_NULL, related_name="+")` | Audit trail, matches the guild-staff pattern. |
| `created_at` | `DateTimeField(auto_now_add=True)` | |

`UniqueConstraint(fields=["equipment", "member"], name="uq_%(class)s_equipment_member")`. `__str__`: `"Dana Reyes: CNC Router manager"`.

### 4.3 `AdminCapability.Capability.EQUIPMENT` (PR 1)

New enum value: `EQUIPMENT = "equipment", "Equipment Administrator"`. Per the capability contract (membership/CLAUDE.md) it **routes** `equipment.reservation_made` to holders **and grants** create/manage on all equipment. Data migration backfills every current `fog_role=admin` member with the capability (the migration-`0118` precedent — nobody loses blanket authority on rollout); reverse deletes exactly the EQUIPMENT rows. The capability appears automatically in the member-edit Details-tab checkboxes (`MemberAdminEditForm.capabilities` builds from the enum).

### 4.4 `EquipmentHours` (PR 2) — structural clone of `OrientationAvailability`, minus orienter/seats

| Field | Type | Notes |
|---|---|---|
| `equipment` | `FK(Equipment, on_delete=CASCADE, related_name="hours_rules")` | |
| `weekday` | same weekday field/choices as `OrientationAvailability` | Monday=0. |
| `start_time` / `end_time` | `TimeField` | Half-hour grid enforced in the form (`half_hour_time_choices`); `CheckConstraint(end_time > start_time)`, name under the 30-char cap (E034 — run `manage.py check`). |
| `is_active` | `BooleanField(default=True)` | Pause a window without deleting it. |

**No hours rows = not bookable yet** (honest empty state, never "always open"). Queryset: `.active()`, `.for_weekday(n)`.

### 4.5 Equipment booking-config fields (PR 2, second migration on `Equipment`)

| Field | Default | Notes |
|---|---|---|
| `min_duration_minutes` | 30 | |
| `max_duration_minutes` | 240 | |
| `max_advance_days` | 30 | Booking horizon. |
| `max_active_reservations_per_member` | 2 | The anti-hog rule; counts CONFIRMED with `ends_at` in the future. |
| `is_closed` | False | Blocks **new** bookings only (mirror of `GuildOrientationSettings.is_closed`); existing reservations stand until a manager cancels each (each cancel notifies). |
| `closed_message` | `CharField(200, blank=True)` | "Down for maintenance. Back Tuesday." |

### 4.6 `EquipmentReservation` (PR 2)

| Field | Type | Notes |
|---|---|---|
| `equipment` | `FK(Equipment, on_delete=CASCADE, related_name="reservations")` | |
| `member` | `FK(Member, on_delete=CASCADE, related_name="equipment_reservations")` | |
| `starts_at` / `ends_at` | `DateTimeField` | Aware UTC; all grid/window math in local (Portland) time — the all-day/local-midnight gotcha applies to the day strip. |
| `purpose` | `CharField(140, blank=True)` | Optional one-liner shown on the schedule. |
| `status` | `TextChoices` `CONFIRMED` / `CANCELLED` | **No PENDING** — instant booking is the locked decision. |
| `cancelled_by` | `FK(Member, null=True, blank=True, on_delete=SET_NULL, related_name="+")` | Distinguishes self-cancel from manager cancel. |
| `cancelled_reason` | `CharField(300, blank=True)` | **Required when a manager cancels** (form-enforced), shown to the member. |
| `created_at` / `cancelled_at` | timestamps | |

Constraints/indexes: `CheckConstraint(ends_at > starts_at)`; partial index `fields=["equipment", "starts_at"], condition=Q(status="confirmed")` (index name ≤ 30 chars).

`EquipmentReservationQuerySet`:
- `.confirmed()` — `status=CONFIRMED`
- `.overlapping(equipment, starts_at, ends_at)` — `.confirmed().filter(equipment=…, starts_at__lt=ends_at, ends_at__gt=starts_at)`. **Adjacent bookings (4:00 end, 4:00 start) do not conflict** — strict inequalities give that for free.
- `.upcoming()` — confirmed, `ends_at__gt=now`, ordered by `starts_at`
- `.active_count_for(member, equipment)` — the per-member cap input

### 4.7 `MapHotspot.equipment` (PR 3)

`FK(Equipment, null=True, blank=True, on_delete=SET_NULL, related_name="hotspots")`. When set on a `RESERVABLE_KINDS` hotspot, the `"reserve"` CTA becomes a real "Reserve" link into the detail page. Degrades cleanly: no link, no button, index still reaches everything.

**Migrations:** one per PR, all-additive, no Airtable-synced table touched. Reverses are plain `RemoveField`/`DeleteModel`; the capability backfill's reverse deletes the created rows (no `RunPython.noop`). Run `manage.py check` after each (CI runs it; local pytest skips it).

---

## 5. Business logic (fat models, skinny views)

### Permissions (`membership/permissions.py`, view_as-aware like siblings)

```python
def can_manage_equipment(request, equipment) -> bool:
    """Site tier: full admin or EQUIPMENT capability.
    Guild tier: owning guild's lead or staff (guild.is_staffed_by / guild_lead_id).
    Resource tier: an EquipmentStaffMembership row."""

def can_create_equipment(request) -> bool:
    """Full admin or EQUIPMENT capability only (locked decision #3)."""
```

Role-check twins on `Member` (`member.can_manage_equipment(equipment)`) for commands/model logic/tests, matching the `can_edit_guild` split. **Never** gated on `is_staff` / `fog_role` / `member_type` (membership/CLAUDE.md hard rule).

### Booking gate — `Equipment.booking_blockers(member) -> list[str]` (PR 2)

Ordered, member-readable blockers; empty list = bookable. Checks: member active (`member.status == ACTIVE`, always required, not configurable), `is_oriented_for_type(required_orientation)` when set, `GuildMembership` when `requires_guild_membership`, `is_closed`. The banner (§7.3), the index badge, and `reserve()` all read this one method.

### Reservation engine (`membership/equipment.py` service + model methods, PR 2)

`Equipment.free_starts_for_day(day: date) -> list[datetime]` — the **`valid_starts_for` algorithm re-derived** over active `EquipmentHours` for that weekday minus `.overlapping()` reservations: build the day's open intervals, subtract confirmed reservations (`free_intervals` logic), emit half-hour-aligned starts that fit at least `min_duration_minutes`. (Note: the original `valid_starts_for(orientation_type)` takes a *type* because orientation duration is fixed per type; here duration is member-chosen, so the start list uses the minimum duration and the duration dropdown shrinks per start.)

`Equipment.durations_for(starts_at) -> list[int]` — 30-minute steps from `min_duration_minutes` up to the largest that fits before the next reservation or closing, capped at `max_duration_minutes`. **The option lists make conflicts unrepresentable in the UI; the transaction makes them impossible in the DB.**

`reserve(equipment, member, starts_at, duration_minutes, purpose) -> EquipmentReservation` (service):
1. Re-validate everything **inside** `transaction.atomic()` with `select_for_update()` on the `Equipment` row (the `ensure_start_valid` two-layer guard, and the same lock object every competing booking takes): blockers empty, grid-aligned start, duration within bounds, inside active hours, `starts_at` within `max_advance_days` and in the future, per-member cap not hit, `.overlapping()` empty.
2. Any failure raises **`EquipmentError`** (new domain exception, sibling of `OrientationError`) with the member-facing message; the view maps it to the friendly error + refreshed start list.
3. Create CONFIRMED, emit `equipment.reservation_confirmed` (with `.ics`) + `equipment.reservation_made`.

`EquipmentReservation.cancel(actor, reason="")` — guards: only CONFIRMED, only future (`starts_at > now` for self-cancel; managers may also cancel in-progress), actor is the member (no reason needed) or passes `can_manage_equipment` (reason **required** — `ValueError` if blank, mirroring decline-notes convention). Sets status/`cancelled_by`/`cancelled_reason`/`cancelled_at`; manager cancel emits `equipment.reservation_cancelled_by_manager`. Member self-cancel notifies nobody (no approver exists to care).

Views stay thin: parse → form → model/service call → toast or redirect. All input validation in `hub/forms.py` forms (`clean_*`), not views.

---

## 6. URL map (`hub/urls.py`, all `@login_required`)

| Path | Name | Method / gate | PR |
|---|---|---|---|
| `/equipment/` | `hub_equipment_index` | GET | 1 |
| `/equipment/add/` | `hub_equipment_add` | GET/POST · `can_create_equipment` | 1 |
| `/equipment/<slug>/` | `hub_equipment_detail` | GET | 1 |
| `/equipment/<slug>/manage/` | `hub_equipment_manage` | GET · `can_manage_equipment` | 1 (Details+Staff) / 2 (Hours, Reservations) |
| `/equipment/<slug>/manage/details/` | `hub_equipment_details_save` | POST | 1 |
| `/equipment/<slug>/manage/staff/add/` · `…/staff/<pk>/remove/` | `hub_equipment_staff_add` / `_remove` | POST | 1 |
| `/equipment/<slug>/schedule/` | `hub_equipment_schedule` | GET (HTMX partial, `?week=` offset) | 2 |
| `/equipment/<slug>/reserve/` | `hub_equipment_reserve` | POST | 2 |
| `/equipment/<slug>/reservations/<pk>/cancel/` | `hub_equipment_reservation_cancel` | POST (self or manager+reason) | 2 |
| `/equipment/<slug>/manage/hours/` | `hub_equipment_hours_save` | POST (formset) | 2 |
| `/equipment/<slug>/manage/settings/` | `hub_equipment_settings_save` | POST (closure + limits + requirements) | 2 |

---

## 7. UI / UX — every screen, rubric applied

Member-facing copy throughout is plain ELI14 language, short sentences, **no dashes in any copy string**. Verify **both themes** on every screen. All new classes `pl-` prefixed, styles in `hub.css`.

### 7.0 Sidebar (PR 1)

New entry in **both** nav variants of `templates/hub/base.html` (`:161` member block and `:283` admin block), directly under **Spaces**. Wrench icon (inline SVG matching the set), label **Equipment**, `{% active_nav 'hub_equipment_index' %}`. Uses `--hub-sidebar-*` tokens only (never `--color-navy`).

### 7.1 Equipment index — `/equipment/` (PR 1; availability line joins in PR 2)

- **Template:** `templates/hub/equipment_index.html`, cards in `partials/equipment_cards.html` (shared with the guild page in PR 3).
- **Layout:** `page_header.html` — title "Equipment", description "Reserve time on shared tools and rooms." Admins/EQUIPMENT holders additionally get a **"+ Add Equipment"** `pl-btn pl-btn--primary` in the header row, linking to `/equipment/add/`.
- **Filter row:** guild chips (All / one per guild-with-equipment / Standalone), kind toggle pills (All / Tools / Rooms), search box — GET-param filters, member-directory furniture. On mobile the row wraps; chips scroll horizontally in a contained region.
- **Card grid:** `repeat(auto-fill, minmax(16rem, 1fr))`, single column on phones. Each card: photo (or kind-icon placeholder on `--hub-surface`), name, guild chip linking to the guild page (or muted "Makerspace" tag for standalone), and:
  - *Access badge* (PR 1, from `access_state`): green "You're all set" / amber "Orientation needed" / amber "Woodshop members only" / muted "Membership inactive".
  - *Availability line* (PR 2): green "Available now" / amber "Reserved until 4:00 PM" / red "Closed: back Tuesday" / muted "Not taking reservations yet" — computed from now vs hours, confirmed reservations, closure. One queryset with prefetches; no per-card queries (N+1 check in review).
- **States:** empty index — "No equipment is bookable yet. Check back soon." Empty filter result — "Nothing matches those filters." with a "Clear filters" link. No loading state (full-page GET).
- **Dark + light / mobile:** card is `hub-card`; badges are `pl-equip-badge--*` classes on theme tokens; grid reflows to one column; tap target is the whole card.

### 7.2 Add / edit equipment form (PR 1)

- **Template:** `templates/hub/equipment_add.html` for create; the same `EquipmentForm` renders on the manage panel's Details tab for edit.
- **Container:** dedicated page (8 fields — well past the 4+ inline threshold; never a modal).
- **Fields via `form_field.html`:** name, kind (select), guild (select, blank = "Standalone"), space (select of Spaces, blank allowed; hint: "Optional. Link the physical room from the space map. We only read from it."), photo (**draggable upload zone per FRONTEND rule 16** — `image_field.html` with a `.pl-help` size tip: "Wide photo, about 1600x900, JPG or PNG"), description (textarea inside `.hub-form-group` so it inherits input tokens), location note, required orientation (select; owning guild set → that guild's active `OrientationType`s; standalone → all guilds' active types grouped by guild name — the house Makerspace guild is an operating convention, not a code concept; plus a "Create a new orientation type" link to that guild's orientation settings), `requires_guild_membership` → **toggle** via `form_field.html` auto-detect (only rendered when a guild is chosen; Alpine `x-show` with display in a CSS class, not inline), `is_active` → toggle.
- **Validation (form `clean`):** `requires_guild_membership` without `guild` → "Pick a guild first, or turn this off." `space` already linked to other equipment → allowed (a room can host several tools) — no error.
- **Save:** single primary button labeled **"Save"**, last element (rule 21), ≥1.5rem clearance. Create → redirect to the new detail page + Django message "Equipment added." Edit → redirect to manage Details tab + "Saved."
- **States:** field errors inline via `form_field.html`; nothing saved on error. Cancel link back to the index (no dead end).
- **Dark + light:** all controls under `.hub-form-group`; `select option` background/color styled; both themes verified.
- **Mobile:** single-column form, full-width controls.

### 7.3 Equipment detail — `/equipment/<slug>/` (PR 1 skeleton; schedule + booking PR 2)

Single column top-to-bottom, same order on mobile:

1. **Hero** (PR 1): cropped photo (HeroCropMixin), name, guild chip, kind badge; **closed banner** (PR 2) when `is_closed` — amber bar with `closed_message` or "Closed for now." Managers also see a "Manage" `pl-btn pl-btn--secondary pl-btn--sm` linking to the manage panel.
2. **Requirements banner** (PR 1) — one `hub-card`, exactly one state, from `booking_blockers`:
   - All met → green: "You're all set." (PR 2 appends: "Pick a time below.")
   - Orientation missing → amber: "You need the CNC Router orientation before you can book time here." + primary button **"Book the Orientation"** → `/guilds/<guild-slug>/?tab=orientations&type=<pk>#guild-orientation` — the guild page already opens the Orientations tab and scrolls on those params (`guild_detail.html:122`); the one addition is the partial reading `?type=` to scroll/highlight that type's slot group. If the member already has a live orientation booking for the type: "Your orientation is booked for Sat, Sep 12 at 2:00 PM." linking to it. This closes the one-flow loop: discover, see gap, book orientation, auto-complete flips the banner green.
   - Guild membership missing → amber: "Only Woodshop members can book this." + **"Join Woodshop"** button into the existing join flow.
   - Membership inactive → muted: "Your membership needs to be active to reserve equipment."
3. **Schedule, this week** (PR 2): `partials/equipment_schedule.html`, HTMX-swapped. A 7-day strip (today first); tapping a day swaps in its timeline: open windows with reserved spans labeled with the reserver's **name** (locked decision #5) and purpose when set ("2:00 to 4:00 PM. Sam R. Longarm quilting"). Days with no hours, past days, and days beyond `max_advance_days` are muted and unclickable. Prev/next week arrows within the horizon; **loading state:** the strip dims with an `htmx-indicator` spinner during swaps.
4. **Book a Time** (PR 2, rendered only when blockers are empty and not closed): day comes from the selected strip day → **Start** `<select>` of `free_starts_for_day` (an impossible start is never offered) → **Duration** `<select>` from `durations_for` (options shrink to what fits) → optional **Purpose** text input (`.hub-form-group`) → **"Reserve"** `pl-btn pl-btn--primary` opening `components/confirm_modal.html` (`confirm_button_style="primary"`): "Reserve the CNC Router for Sat, Sep 12, 2:00 to 4:00 PM?" → POST `hub_equipment_reserve` → toast **"Reserved. See you Saturday."** and the schedule partial re-swaps. **Race loss:** `EquipmentError` → error toast "That time was just taken. Please pick another time." + refreshed start list. **Fully booked day:** the form area shows "No open times this day. Try another day." **No hours at all:** "This equipment isn't taking reservations yet." (managers see: "Add opening hours to start taking reservations." linking to the manage Hours tab).
5. **Your reservations here** (PR 2): the member's upcoming rows, each with a **Cancel** `pl-btn pl-btn--danger pl-btn--sm` → `confirm_modal`: "Cancel your Saturday 2:00 PM reservation? The time opens up for someone else." → toast "Reservation cancelled." A manager-cancelled row shows "Cancelled by the manager: <reason>" until it ages out.
6. **Upcoming reservations** (PR 2): who has it when — date, time, name, purpose. Empty: "No upcoming reservations."
7. **About** (PR 1): description, location note, and when `space` is set the linked room's details (size, link to the space map) — read-only.
- **Dark + light:** timeline spans use `pl-equip-slot--free/busy` classes on theme tokens; selects under `.hub-form-group`; no native date/time inputs anywhere (rule 20 — selects only), so no rule-14 picker fixes needed.
- **Mobile:** day strip scrolls horizontally in a contained region; timeline is a vertical list (no table); booking selects stack full-width.

### 7.4 Manage panel — `/equipment/<slug>/manage/` (PR 1: Details, Staff; PR 2: Hours & Limits, Reservations)

Tabbed page in the `guild_edit.html` idiom (Alpine `section` state). Every tab's batch form ends in a button labeled **"Save"** with nothing beneath it (rule 21).

- **Details tab** (PR 1): the §7.2 `EquipmentForm` inline. `is_active` toggle rendered **first** (immediate-effect control ordering note: it is part of the batch form here, so it simply sits with the other toggles above Save).
- **Staff tab** (PR 1): current managers listed with avatar + name + granted-by line; per-row **"Remove"** `pl-btn pl-btn--danger pl-btn--sm` (margin-top 0.75rem) behind `confirm_modal`: "Remove Dana as a manager of the CNC Router?" Below the list, an **"+ Add Manager"** toggle-revealed form (`x-show`, closed by default): member select (active members) + **"Add"** submit → POST `hub_equipment_staff_add` → Django message "Manager added." Duplicate grant → form error "They already manage this equipment." Empty state: "No per-equipment managers yet. Guild leadership and Equipment Administrators can always manage this." (states plainly that the list being empty does not mean nobody can manage it).
- **Hours & Limits tab** (PR 2):
  - **Hours formset** — the canonical recurring-hours editor pattern verbatim (`guild_edit.html`): `extra=0`; **"+ Add Hours"** button clones the hidden `<template>` of `formset.empty_form`, swaps `__prefix__`, bumps `TOTAL_FORMS`; rows are weekday select + start/end half-hour selects (`half_hour_time_choices`) + `is_active` toggle; saved rows get a real **Delete** `pl-btn pl-btn--danger pl-btn--sm` (margin-top 0.75rem) that flips the hidden `DELETE` field and `requestSubmit()`s (whole page saves, no lost work); cloned rows get **Remove** (DOM removal only). Validation: end after start — "The end time must be after the start time." Empty state: "No opening hours yet. Members cannot book until you add some."
  - **Closure** — `is_closed` **toggle** ("Closed for new reservations") + `closed_message` text field, hint: "Members will see this message. Existing reservations stay until you cancel them."
  - **Limits** — four number fields via `form_field.html` with hints in plain language ("How far ahead members can book, in days.").
  - One **"Save"** at the very bottom for the whole tab → redirect back to the tab + message "Saved."
- **Reservations tab** (PR 2): upcoming reservations table (date, time, member, purpose) via `prepare_table()` + `table_pagination.html`; per-row **"Cancel"** `pl-btn pl-btn--danger pl-btn--sm` opening a **modal** (`components/modal.html`, 2 fields → modal per the interaction table) with a required **Reason** textarea (`.hub-form-group`; validation: "Please tell the member why.") + **"Cancel Reservation"** danger submit → toast "Reservation cancelled. The member has been told." Empty state: "No upcoming reservations." Table degrades on mobile by stacking into cards (the roster pattern).
- **States everywhere:** every form re-renders its tab with field errors on invalid; every success is a message or toast; every screen has a way back (breadcrumb link "← Back to the CNC Router" at the top).

### 7.5 Guild page seam (PR 3)

`guild_detail.html` gains an **Equipment** section (rendered only when the guild has active equipment): heading "Equipment", the shared `partials/equipment_cards.html` grid filtered to the guild — rooms and tools alike, which is how "guild rentable rooms" surface. Cards link into the detail pages. No guild-edit change: equipment is managed on its own panel, and guild leadership already passes `can_manage_equipment`.

### 7.6 Home page card (PR 3)

`templates/hub/home.html` gains an **"Upcoming Reservations"** `hub-card` (rendered only when the member has any): up to three rows — equipment name (linked), day + time — plus "See all equipment" link. No card when empty (the home page stays quiet rather than advertising an empty list). Dark + light via existing card tokens; rows wrap on mobile.

### 7.7 Space map seam (PR 3)

Admin hotspot form gains an **Equipment** select (nullable). A `RESERVABLE_KINDS` hotspot with `equipment` set renders its existing `"reserve"` CTA as a real **"Reserve"** link to the detail page; without it, current stub behavior is unchanged.

---

## 8. Notifications (PR 2; reminder PR 3)

All through `emit()` + registry + copy + resolvers. Category: the existing **"Spaces"** category is relabeled **"Spaces & Equipment"** — the `CATEGORY_ORDER` string in `core/events/settings_matrix.py` and the `category=` on the two existing `space.*` registry entries change together; code strings only, no migration.

| Event key | When | Recipients | Channels (defaults) | PR |
|---|---|---|---|---|
| `equipment.reservation_confirmed` | on booking | member (`SINGLE_USER`) | in-app + email **FORCED** (operational, like orientation updates), push ON. Email carries an `.ics` via `attachments={Channel.EMAIL: […]}` (builder mirrors `orientations.build_ics`). CTA: "See Your Reservation" → the detail page. | 2 |
| `equipment.reservation_cancelled_by_manager` | manager cancel | member (`SINGLE_USER`) | in-app + email FORCED. Body carries the required reason. CTA: "Pick a New Time". | 2 |
| `equipment.reservation_made` | on booking | new `equipment_managers` resolver: staff rows ∪ owning-guild `leadership_members()` ∪ EQUIPMENT capability holders, deduped, tagged (compose style of `guild_leadership_or_admins`) | in-app ON, email OFF (awareness, not action — no approval exists). Grouped under **Staff & leadership** on the settings page. | 2 |
| `equipment.reservation_reminder` | ~24 h before start, via `run_scheduled_tasks` | member (`SINGLE_USER`) | email FORCED, push ON. Deduped with the scheduled-marker pattern **and** a unique `period` per reservation (`f"reservation:{pk}"`). | 3 |
| `equipment.hours_changed` | — | — | **Not emitted.** The schedule page is the truth. | — |

Member self-cancel emits nothing. The orientation request/confirm/thank-you emails are already shipped and fire unchanged when the member books the gating orientation.

**Copy rules (hard requirements for the builder):**
- Every event declares its `placeholders` in `core/events/copy.py`, and **the emit context must carry every placeholder any channel's copy uses** — a placeholder the context doesn't supply renders as a literal hole in a member's email. Test this per event (§10).
- **Broadcast channels never greet a person.** Per-recipient copy (email, in-app, Discord DM) may open "Hi {{ member_name }},". Copy for any *broadcast* channel — the Discord webhook channel above all — must **never** contain "Hi {{ member_name }}" or any per-recipient placeholder, because one rendering fans out to everyone. All four equipment events keep the DISCORD broadcast channel **off/absent**; the rule is recorded so a later channel addition doesn't walk into it.
- Emails follow the FRONTEND email rules: subject noun (the equipment name) links to the detail page, one primary CTA + helpful secondaries, absolute URLs via the spine resolver, branded shell, `.txt` and `.html` in lockstep, subject/body in one timezone (Portland), and spine copy verified cream-on-dark with gold links (`_style_copy_fragment`).
- All quoted member copy: plain ELI14, no dashes.

---

## 9. Build order — three sequential PRs, each ships green

Each PR: full targeted suite + lint + `manage.py check` green, VERSION bump in `plfog/version.py`, **one curated member-facing changelog entry stamped at that VERSION** (the Discord announce fires automatically on merge when VERSION changes — curate before merging). Entries below are drafts in the required plain no-dash voice.

### PR 1 — Foundation: the Equipment directory

1. Models + migration: `Equipment` (with `slug`, `required_orientation`, `requires_guild_membership`), `EquipmentStaffMembership`, `AdminCapability.EQUIPMENT` + admin backfill (reversible).
2. `can_manage_equipment` / `can_create_equipment` + `Member` twins; `access_state` / `booking_blockers` (blockers minus the PR 2 closure check).
3. Sidebar entry (both variants), index page (filters, cards, access badges, empty states), detail page skeleton (hero, requirements banner with the orientation deep-link + `?type=` highlight in the guild orientation partial, About), add/edit form, manage panel (Details + Staff tabs).
4. Housekeeping: mark `2026-07-04-space-reservations.md` **Superseded by this spec** in its header; VERSION bump.
5. Changelog: *"Equipment directory: Meet the new Equipment page in the sidebar. Browse the makerspace's shared tools and rooms, see what each one needs before you can use it, and book the orientation for a tool right from its page."*

### PR 2 — Reservations

1. Migration: `EquipmentHours`, `EquipmentReservation`, booking-config + closure fields on `Equipment`.
2. Engine: overlap queryset, `free_starts_for_day` / `durations_for`, `reserve()` under `select_for_update`, `cancel()` with the manager-reason guard, `EquipmentError`.
3. UI: schedule partial (week strip, day timeline with reserver names), Book a Time (computed selects, confirm modal, race-loss recovery), your/upcoming reservation lists, self-cancel, manage Hours & Limits tab (formset with + Add Hours / real Delete buttons / Save, closure toggle + message, limits), manage Reservations tab (reason-required cancel modal), closed banner, index availability line.
4. Notifications: three PR 2 events + copy + `equipment_managers` resolver + `.ics` builder + the "Spaces & Equipment" relabel.
5. Changelog: *"Reserve time on equipment: Pick a free time on a tool's schedule and it is yours right away. See the week at a glance, book in a couple of taps, get a calendar invite, and cancel if plans change. Tool managers set the hours and the rules."*

### PR 3 — Integration & polish

1. Guild-page Equipment section (shared cards partial) — guild rooms and tools surface where members already look.
2. Home-page Upcoming Reservations card.
3. `MapHotspot.equipment` FK + live "Reserve" CTA.
4. `equipment.reservation_reminder` event + scheduled job in `core/scheduled_jobs.py` (marker + period dedupe).
5. E2e coverage (`tests/e2e/`): the full loop — index → detail → orientation-gated banner → (oriented fixture) → reserve → appears on schedule + home card → cancel. Run against Postgres 5433 like CI (browser-writing e2e flakes on local SQLite).
6. Changelog: *"Your reservations at a glance: Upcoming equipment reservations now show on your home page. Guild pages list each guild's reservable tools and rooms, the space map links straight to booking, and you get a reminder email the day before your time."*

> Spec only — do not build until approved.

---

## 10. Testing

BDD `*_spec.py` with `describe_*` / `it_*` **only** — `context_*` blocks are silently not collected and everything inside them never runs. Homes: `tests/membership/` (models, permissions, service, events) and `tests/hub/` (views, templates) — the root `tests/` tree; `membership/spec/` and `hub/spec/` do **not** exist. factory-boy factories in `tests/membership/factories.py` (`EquipmentFactory`, `EquipmentHoursFactory`, `EquipmentReservationFactory`). 100% branch coverage; mutation gate per the fast-merge policy. Time fixtures at `now + timedelta(days=2)` and window math asserted in local time (the tz day-window gotcha).

**PR 1**
- Model guards: slug generation + stability across rename; `requires_guild_membership` without guild rejected by the form; PROTECT on `guild` and `required_orientation` deletes.
- Permissions: each `can_manage_equipment` tier true/false (capability holder, full admin, owning-guild lead, guild staff, `EquipmentStaffMembership` holder, plain member); `can_create_equipment` admin/capability only; view_as respected; staff add/remove endpoints gated; duplicate staff grant → form error.
- Capability: backfill migration grants EQUIPMENT to existing admins and its reverse removes exactly those rows; capability routes and grants per the contract.
- `access_state` / `booking_blockers`: every branch (inactive member, unoriented, non-guild-member, all-clear); `is_oriented_for_type` integration.
- Views/templates: index badges and empty/filter states; detail banner one-state-at-a-time; orientation deep link lands with the tab open and the type highlighted; add form admin-gated (plain member and guild lead both 403 on `/equipment/add/`).
- Changelog-renders-everywhere check (no UI-copy string collisions with test assertions).

**PR 2**
- Overlap predicate: strict-inequality truth table incl. **adjacent bookings do not conflict**; cancelled rows never conflict.
- `free_starts_for_day` / `durations_for`: window edges, reservations mid-window, min-duration tail exclusion, multi-window days, inactive rules ignored, horizon and past-day exclusion, half-hour alignment.
- `reserve()`: happy path; every `EquipmentError` branch (blocked member, off-grid, out-of-hours, too long/short, beyond horizon, per-member cap, overlap); **race test** — two competing bookings for one window under `select_for_update` yield one CONFIRMED and one `EquipmentError`; closure blocks new bookings but not existing ones.
- `cancel()`: self-cancel future-only; manager cancel requires reason (blank → error); manager cancel emits with the reason in context; self-cancel emits nothing; `cancelled_by` distinguishes the two.
- Views: booking POST returns the friendly race-loss toast + refreshed starts; schedule shows reserver names to a plain logged-in member; manage tabs gated; hours formset saves, per-row Delete flips `DELETE` and preserves other edits, end-before-start error message.
- Events: registry entries exist with the specified channel defaults; `equipment_managers` resolver dedupes across the three tiers; **per-event: emit context supplies every declared placeholder used by every channel's copy** (render each channel's copy against the real emit context and assert no unresolved `{{ … }}` remains); no broadcast-channel copy contains a per-recipient placeholder; `.ics` attached on confirmation; `.txt`/`.html` parity.
- Template states: closed banner, no-hours state (member vs manager copy), fully-booked day, cancelled-by-manager row with reason.

**PR 3**
- Guild page section renders only for guilds with active equipment; cards filtered correctly.
- Home card renders only with upcoming reservations, caps at three, links correctly.
- Hotspot CTA: with `equipment` → Reserve link; without → unchanged stub.
- Reminder job: fires inside the window once (marker + period dedupe — a second run sends nothing), skips cancelled reservations.
- E2e reserve loop per §9 (Postgres).

---

## 11. Out of scope (explicit punts, with reasons)

- **Recurring reservations** — complexity magnet, no demand evidence; the standing-meeting case belongs to the approval flow below.
- **Paid reservations** — Tab/checkout plumbing exists, but pricing shared tools is a council policy question first. No price field until then.
- **No-show tracking / penalties** — measure the honor system first; a manager-set `no_show` flag is a cheap later add.
- **Approval-required rooms** (`booking_policy=APPROVAL`) and the event-space → CommunityEvent publish — the 07-04 spec's deferred half; returns as a per-equipment policy field reusing `SpaceRequest`'s review shape when the event space onboards.
- **Guild-free orientations** (brainstorm §5-B pipeline generalization) — the house-guild convention covers standalone tools; revisit only if reality demands it (the `required_orientation` data migrates trivially).
- **Reservation history CSV, usage analytics, check-in kiosk** — no evidence of need yet.
- **A "house guild" code concept** — the Makerspace guild is ordinary data the council creates by hand.

## 12. Done criteria

- A brand-new member finds the CNC router in the sidebar, sees "Orientation needed," books the orientation from that page in one flow, and after it auto-completes comes back and books machine time — zero admin involvement at any step (the brainstorm's success test, end to end in e2e).
- Two members can never hold overlapping confirmed reservations on one equipment, even under concurrent booking; adjacent bookings always succeed.
- No write ever crosses `Equipment.space` into the Airtable-synced `Space` table; `airtable_pull` is untouched.
- Every manage surface passes only through `can_manage_equipment` (no `is_staff` / `fog_role` / `member_type` gate anywhere); creation only through `can_create_equipment`.
- Every screen in §7 has its named Save/Add/Delete/Cancel controls, empty/error/success states, both themes, and mobile reflow, as written.
- Three merged PRs, each with a VERSION bump and one curated plain-language changelog entry; `2026-07-04-space-reservations.md` carries the superseded stamp.
