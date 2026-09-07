# Equipment Orientations Adopt the Guild Pattern — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build. One corrective PR, ahead of everything else in the round.
**Date:** 2026-09-05
**Surface:** FOG hub — equipment manage panel Orientation tab, equipment detail page orientation section, guild edit Orientations tab (two optional fields only), orientation emails and routing for equipment-owned types
**Related:**
- `2026-09-05-equipment-orientation-hours.md` — shipped as v1.35.0 (PR 1) and v1.36.0 (PR 2). PR 1 gave equipment a "set the hours, tick the days" window editor with no per-manager hours, and a day-chip member picker. The owner rejected both: the brief asked for the guild pattern, and a manager cannot post their own Friday 6 to 8 and Saturday 12 to 4. This spec replaces the manager editor and the member list with the guild's, keeping the slot engine, retirement, closure, and the PR 2 reservation conflict logic untouched.
- `2026-08-26-orienter-availability.md` — shipped; the guild pattern being adopted: Orientation Schedule card grouped by person, Edit Hours modal per person, shared legacy hours card, one time slots with a Runs with picker, "with Bob" everywhere.
- `2026-09-05-guild-orientation-picker-parity.md` — abandoned unbuilt (wrong direction: it made guilds look like equipment). Not in the tree.

---

## 1. Summary

An equipment manager posts **their own** weekly orientation hours on the tool's manage panel exactly the way a guild orienter does on a guild: an Orientation Schedule card lists every manager with an Edit Hours button, the Edit Hours modal takes one row per window (orientation, day, start, end, seats, active), and Dana's Friday 6 to 8 plus Saturday 12 to 4 is two rows in her modal. Members see the same plain list of times on a tool page that they see on a guild page (date, time, who, Request, five at a time), and requests go to the manager whose slot it is. Slot length and break become **optional** fields in the Edit Hours modal for both owners, blank by default, so nothing is carved unless someone asks for it.

### Locked decisions (owner, 2026-09-05)

| # | Decision | Choice |
|---|---|---|
| 1 | Manager editor | **The guild's Orientation Schedule card + Edit Hours modal, on the equipment Orientation tab.** The "Orientation Hours" window card, its form, formset, save view, URL, model reconcile methods (`orientation_hours_windows` / `apply_orientation_hours_windows`), and their tests are **removed**. |
| 2 | Per-manager hours | Equipment rules carry `orienter` = the manager (the FK already exists). Any of the three manager tiers (`can_manage_equipment`) can post hours; slots and emails say "with Dana"; requests route to Dana. |
| 3 | Shared hours | Existing equipment rules with no orienter (the ones created by the window editor since v1.35.0) keep working as **Shared Hours (Any Manager)**, shown and edited the guild's legacy way: keep, edit, retire only; no new shared recurring rows. Shared coverage going forward is an "Any manager" one time slot. |
| 4 | Member list | **Plain list everywhere.** The equipment partial returns to the guild layout (date, time, with-cell, Request; five at a time with arrows). The day chips, their CSS, and the `days` post-processing are removed. No slot cap for either owner (the pager bounds the view). |
| 5 | Slot length and break | Optional fields in the shared Edit Hours modal for **both** owners: blank ("Whole window") keeps one slot per window, the guild behavior and the new default; a set length carves as v1.35.0 does. Rows carved by the old window editor keep their values and show them. |
| 6 | Who may edit whose hours | Mirror `can_edit_orienter_hours`: own hours → anyone who can manage the equipment; someone else's hours or the shared rows → a full admin, an EQUIPMENT capability holder, or the owning guild's lead. |
| 7 | Request routing | Personal slot → the slot's manager plus the EQUIPMENT capability holders and the owning guild's lead if any (deduped), the equipment twin of "the orienter plus the lead". Shared slot → `manager_members()` as today. |
| 8 | Departed manager | A personal slot whose manager no longer passes `can_manage_equipment` stops taking new bookings (the guild's departed-orienter guard, mirrored for the equipment gate); `generate_slots` skips such rules; removing a manager from the Staff tab retires their rules (the `retire_orienter` twin), leaving booked slots for the Upcoming Slots card. |
| 9 | One time slots | `EquipmentOrientationSlotForm` gains the guild's **Runs with** picker (the manager set plus "Any manager"), default the acting manager; plain managers are fixed to themselves like plain guild staff. |
| 10 | Everything else stays | Slot generation, carving, off-grid cleanup, reseat, retirement with seat caps and detachment, closure pause, the booking guards, PR 2's reservation conflict logic, Pending Requests, and Upcoming Slots (flat paged list with the with-chip like the guild card, plus the equipment-only attendee sub-rows) are unchanged. |

## 2. What already exists (reuse, don't reinvent)

All in the current tree at main e9cd9214.

| Need | Existing thing | Location |
|---|---|---|
| Orientation Schedule card (groups by person, Edit Hours button, Former Staff group) | the card in `templates/hub/guild_edit.html` (search "Orientation Schedule"); context built in `_guild_edit_context` (`orienter_overview`, `former_staff_overview`, `show_my_hours_card`, `can_edit_others_hours`, `viewer_member_pk`) | `hub/views.py` |
| Edit Hours modal partial (HTMX, `modal_rules` prefix, confirm-guarded Delete, + Add hours, Save) | `templates/hub/partials/_orienter_hours_modal_form.html`; views `guild_orientation_hours_form` / `guild_orientation_hours_save`; `_personal_hours_prefix`, `_apply_hours_formset`, `_hours_save_message` | `hub/views.py` |
| Rule row form (half-hour selects, type scoped to the owner's active types, end after start) | `OrientationAvailabilityForm` (guild kwarg) + `OrientationAvailabilityFormSet` (inline on Guild) | `hub/forms.py` |
| Edit permission split | `can_edit_orienter_hours(request, guild, orienter)` | `membership/permissions.py` |
| Rule retirement, retire an orienter's rules on staff removal | `retire_rule`, `retire_open_slots`, `retire_orienter`; `guild_staff_remove` calls it | `membership/orientations.py`, `hub/views.py` |
| Departed-orienter guard in the queryset and the property | `bookable()` guild gate (`still_on_staff` Exists + `guild_lead_id`), `is_bookable` | `membership/models.py` |
| Stale-rule skip in generation | `generate_slots` (`_rule_generates`) guild branch via `leadership_members()` | `membership/orientations.py` |
| Manager set | `Equipment.manager_members()`; `EquipmentStaffMembership`; `can_manage_equipment` | `membership/models.py`, `membership/permissions.py` |
| Request routing | `_request_audience` (equipment branch → `manager_members()`), `_emit_lead_request` (`primary_responder`, resolver context), resolver `guild_orienters_or_equipment_managers` and `equipment_managers` | `membership/orientations.py`, `core/events/resolvers.py` |
| Member list markup (date, time, with-cell, Request, five per page pager, confirm copy variants) | `templates/hub/partials/guild_orientation.html` slot rows | template |
| One time slot form with Runs with picker | `OrientationSlotForm` (`orienter` field, `clean_orienter`), the `slot_form_locked` idiom in `guild_edit.html` | `hub/forms.py`, template |
| "with Dana" label, orienter first name, dashboard Orienter column, `.ics` copy | `OrientationSlot.with_label`, `with_display`, `orienter_first_name`; `orientations_dashboard.html`; email templates | shipped |
| Slot length and break fields, off-list duration append, "shorter than one slot" check | `EquipmentOrientationHoursWindowForm` (being removed; lift its field definitions and validation into `OrientationAvailabilityForm`) | `hub/forms.py` |

## 3. Data model

No migration. `OrientationAvailability.orienter` and `OrientationSlot.orienter` exist and are nullable; equipment rows simply start using them. `slot_minutes` / `buffer_minutes` exist. Rows created by the removed window editor stay valid (orienter NULL = shared).

## 4. Business logic

- **Permissions:** `can_edit_equipment_orienter_hours(request, equipment, target)` in `membership/permissions.py`: `target` is the viewer → `can_manage_equipment`; anyone else or `None` (shared rows) → full admin, EQUIPMENT capability, or `equipment.guild.guild_lead_id == member.pk`. View-as aware like its guild twin.
- **Generation:** `generate_slots` stale-rule skip for equipment rules: skip when `rule.orienter_id` is set and that member does not pass `Member.can_manage_equipment(equipment)` (the role twin). Slots carry `orienter=rule.orienter` already.
- **Booking gate:** `bookable()` equipment gate drops `orienter__isnull=True` and gains the manager-still-current condition, expressed as `Q(orienter__isnull=True) | <still a manager>` where "still a manager" mirrors `manager_members()` in SQL: an `EquipmentStaffMembership` row, the owning guild's lead id, a `GuildStaffMembership` row on the owning guild, an `AdminCapability` EQUIPMENT row, or `fog_role="admin"`. `is_bookable` mirrors it in Python via `Member.can_manage_equipment`. Blocks new bookings only; existing bookings untouched.
- **Retirement on manager removal:** `hub_equipment_staff_remove` calls a `retire_equipment_orienter(equipment, member)` twin after the row delete, only when the member no longer passes `can_manage_equipment` (they may still be owning-guild staff or an admin); the flash gains the guild's "They still have N upcoming booked orientation(s)…" sentence when booked slots remain.
- **Routing (decision 7):** `_request_audience` equipment branch: personal slot → `[slot.orienter] + EQUIPMENT capability holders + owning guild lead`, deduped; shared → `manager_members()`. `_emit_lead_request`: `primary_responder = slot.orienter` for equipment personal slots (None for shared, as today); the `equipment_managers` resolver narrows the same way when the context carries a personal slot (mirror `guild_orienters`). `confirm()` / `mark_completed()` already default `oriented_by` to `slot.orienter`.
- **One time slots:** `EquipmentOrientationSlotForm` gains `orienter` (choices: `manager_members()` plus blank "Any manager"; `clean_orienter` requires a current manager); the view stamps it; plain managers get it fixed to themselves.
- **Removed:** `Equipment.orientation_hours_windows`, `apply_orientation_hours_windows`, `EquipmentOrientationHoursWindowForm`, `_BaseEquipmentOrientationHoursWindowFormSet`, `EquipmentOrientationHoursWindowFormSet`, `hub_equipment_orientation_hours_save` and its URL, `_orientation_hours_save_message`, the `days` post-processing in `_equipment_orientation_sections`, and their tests. `_orientation_sections` keeps `slot_cap` but every caller passes `None`.

## 5. UI / UX

Member copy plain, no dashes. Theme tokens only. Verify both themes. Save last and labeled Save.

### 5.1 Equipment manage panel, Orientation tab (`templates/hub/equipment_manage.html`)

Card order: **Pending Requests** (unchanged) → **Orientation Types** (unchanged) → **Orientation Schedule** (new, replaces Orientation Hours) → **Shared Hours (Any Manager)** (only when shared rows exist) → **Upcoming Slots + Add a Time**.

- **Orientation Schedule:** heading "Orientation Schedule"; hint for a lead-level viewer "Everyone who manages this equipment, and when they give orientations." and for a plain manager "Your weekly orientation hours. Members book you by name." One group per `manager_members()` entry (a plain manager sees only their own group): avatar + name + **Edit Hours** (`hub-btn hub-btn--sm`, HTMX GET `hub_equipment_orientation_hours_form?orienter=<pk>` into the shared `edit-hours-modal`), then that person's rules as read-only lines ("Operator Basics · Saturday · 10:00 am to 6:00 pm · 1 seat · 60 min slots" with a muted "paused" tag) or "No hours published". A **Former Managers** group appears for orphan personal rules exactly like the guild's Former Staff group. Include the shared modal shell once on the page (`components/modal.html`, `modal_id="edit-hours-modal"`, `modal_size="lg"`).
- **Edit Hours modal:** the existing `_orienter_hours_modal_form.html`, generalized to an `owner` (guild or equipment): heading "Editing Dana's Hours", hint "Weekly windows when Dana can give orientations. We turn them into bookable slots automatically. Members book them by name.", rows of orientation · day · start · end · seats, then **Slot length** (select: blank "Whole window", the shared duration choices, plus the row's saved off-list value) and **Break between slots** (None / 15 / 30 / 60) with the hint "Leave slot length blank to offer the whole window as one slot.", the Active toggle, confirm-guarded Delete, + Add hours, Cancel, Save. Posts `hub_equipment_orientation_hours_save` with `orienter_scope` and `formset_prefix=modal_rules`; valid → 204 + `HX-Redirect` to `?tab=orientation`; invalid → the bound partial re-renders inside the modal. The guild modal gets the same two fields through the shared form (its only change).
- **Shared Hours (Any Manager):** rendered only when `OrientationAvailability.objects.for_equipment(equipment).guild_level()` is non-empty (orienter NULL). Editable by the decision 6 "others" tier through the same modal with an empty scope; plain managers see it read-only with "Only an administrator or the owning guild's lead can change shared hours." No + Add. Delete confirm names the one-way door like the guild card.
- **Upcoming Slots:** the equipment card becomes the guild card's shape: flat list, five per page with arrows, each row date · time · type · with-chip (avatar + name, or muted "Any manager") · seats booked / held · source tag `recurring` / `one time` · Cancel behind the existing confirm modal, plus the equipment-only attendee sub-rows underneath (kept: pure managers have no dashboard). The day grouping and "Show later days" are removed. **Add a Time** gains the **Runs with** select (or the fixed "Runs with: you" line for plain managers).
- **States:** empty schedule → "No hours yet. Add a window and members can start booking you." inside the modal; card shows "No hours published" per person. Validation errors inside the modal. Success flash from `_hours_save_message` (shared helper, owner-neutral wording).

### 5.2 Equipment page, orientation section (`templates/hub/partials/equipment_orientation.html`)

Identical to the guild partial's request state: the header row (Date · Time · blank · blank), rows of `slot.starts_at|date:"D M j"` · time range · with-cell (`slot.with_display` with avatar, empty for shared slots) · Request or Full, `x-data` pager five at a time with ← n / N → arrows. Confirm copy: personal slot "We'll send your request to Dana to confirm. You can cancel any time."; shared slot "We'll send your request to the equipment managers to confirm. You can cancel any time."; paid copy unchanged. The booking status line gains "· with Dana" and "awaiting confirmation from Dana" like the guild. Empty state unchanged. The day chips, `pl-orient-days*`, `pl-orient-times*` CSS, and the `aria-pressed` buttons are removed.

### 5.3 Staff tab

Removing a manager retires their personal rules (decision 8); the confirm modal message gains "Their upcoming orientation hours are removed too. Slots someone already booked stay until you cancel them."

## 6. Notifications / emails

No new events. Equipment request emails for a personal slot go to the decision 7 audience and read "Sam requested an orientation with you" for the manager; member emails and `.ics` already carry `with_label`. The `equipment_managers` resolver narrows on a personal slot in context (mirror `guild_orienters`).

## 7. Build order (one PR)

1. Permissions helper; `bookable()` / `is_bookable` equipment gate; `generate_slots` stale-manager skip; `retire_equipment_orienter` + staff-remove hook; routing (`_request_audience`, `_emit_lead_request`, resolver narrowing). Tests.
2. Shared `OrientationAvailabilityForm` (owner kwarg, slot length + break, validation) and an equipment formset (`modelformset_factory` on `OrientationAvailability`, queryset scoped by equipment + orienter); the two hours views + URLs for equipment; `_orientation_tab_context` builds the schedule groups (`orienter_overview`, former managers, shared rows, `can_edit_others_hours`, `viewer_member_pk`); modal partial generalized. Remove the window editor (form, formset, view, URL, model methods, message helper) and its tests.
3. Templates: Orientation Schedule + Shared Hours cards, modal shell, Upcoming Slots back to the flat paged shape with the with-chip, Add a Time Runs with; equipment partial back to the plain list; CSS cleanup; staff-remove modal copy.
4. Member-facing copy variants, dashboard untouched (Orienter column already works).
5. VERSION 1.37.0 + one changelog entry: *"Equipment orientations now work like guild orientations: Each tool's managers post their own weekly hours, and when you book a time you see who you're meeting. Tool pages list orientation times the same way guild pages do."*

> Spec only — do not build until approved.

## 8. Testing

BDD `describe_` / `it_` in `tests/hub/` and `tests/membership/`; run the guild orientation specs unchanged as the regression bar (`orienter_hours_editor_spec`, `guild_orientation_tab_spec`, `orientation_booking_spec`, `orienters_spec`, `orienter_routing_spec`, `orienter_availability_spec`) plus the equipment ones. Cases: manager saves own hours (two windows Fri 6 to 8 and Sat 12 to 4 → two rules with `orienter` = manager, slots "with Dana"); a plain manager cannot save another manager's hours (403) nor the shared scope; admin, EQUIPMENT holder, and owning-guild lead can; view_as respected; blank slot length keeps one slot per window, a set length carves; shared legacy rows render and retire; former manager rules listed and retirable; departed manager's slots unbookable and skipped in generation; staff removal retires rules and keeps booked slots with the flash sentence; personal slot request routes to the manager plus capability holders plus guild lead, shared to `manager_members()`; one time slot Runs with defaults to self and rejects a non-manager; equipment partial renders the plain paged list with the with-cell and the confirm copy variants; no day-chip markup anywhere; Upcoming Slots flat list with the with-chip and attendee rows; the guild modal shows the two new fields and a blank save changes nothing; the removed window endpoint returns 404. E2E: `tests/e2e/orientation_booking_spec.py` (guild) must still pass on Postgres 5433; no equipment e2e exists.

## 9. Open / deferred

- Personal availability blocks and custom-time requests for equipment: still deferred (guild-settings features).
- Dashboard access for pure equipment managers: still deferred; the manage tab is their surface.
