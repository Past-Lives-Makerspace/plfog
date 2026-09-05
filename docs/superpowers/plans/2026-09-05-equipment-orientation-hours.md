# Equipment Orientation Hours — Recurring Availability & Slot Booking — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build. Phased as **two sequential PRs** (§8).
**Date:** 2026-09-05
**Surface:** FOG hub — equipment manage panel (`/equipment/<slug>/manage/?tab=orientation`), equipment detail page orientation section, equipment schedule/reservation engine (PR 2)
**Related:**
- `2026-09-03-equipment-owned-orientations.md` — **shipped (v1.32.0)**. Its locked decision #4 put recurring availability for equipment out of scope ("slots are added directly"). Reality has now declared itself: managers are hand-creating every fixed-date slot, which is the maintenance burden this spec removes. Everything else in that spec (owner resolution, manage tab, member partial, respond flow, paid checkout) is reused unchanged.
- `2026-08-26-orienter-availability.md` — shipped; the guild recurring-rule engine (`OrientationAvailability` → `generate_slots` → `OrientationSlot`) this spec extends to a second owner.
- `2026-09-03-equipment-reservations.md` — shipped; the multi-day "hours then days" window editor on the same manage panel is the manager pattern this spec mirrors, and PR 2 makes orientations and reservations conflict-aware.

---

## 1. Summary

An equipment manager posts **weekly orientation hours** for a tool ("CNC Basics, every Saturday and Sunday, 10:00 AM to 6:00 PM, 60 minute slots, one person at a time") once, and the app turns those hours into bookable time slots automatically, eight weeks ahead, forever. Members open the tool's page, pick a day, pick a time, and request it. Managers still confirm each request (lifecycle unchanged), and one time slots can still be added for the odd exception. Editing, pausing, or deleting a window cleans up its open future slots honestly and never touches a slot someone already booked.

### Locked decisions

| # | Decision | Choice and why |
|---|---|---|
| 1 | Data model | **Extend the shipped rule model, don't add one.** `OrientationAvailability.guild` becomes nullable (exactly what `OrientationSlot.guild` / `OrientationBooking.guild` did in v1.32.0). A rule is owned through its `orientation_type`, whose owner is already resolved (`OrientationType.owner`, `is_accepting`). One engine, two owners. |
| 2 | Window vs slot | **Windows are carved into slots by a per-window slot length.** The shipped guild engine makes ONE slot spanning the whole window (a 6 to 8 pm rule is one 2 hour slot with N seats); the owner's brief asks for "Sat 10 to 6, 60 minute slots, members book open slots within the window." New nullable `slot_minutes` on the rule: `NULL` = legacy one-slot-per-window (every existing guild row, untouched); set = carve. The equipment editor always sets it. Guilds can opt in later by exposing the same field (§10). |
| 3 | Slot length default | Defaults to the orientation type's `duration_minutes` (the "60 min" already shown on the Orientation Types card) and is editable per window from the shared duration choices (`_SLOT_DURATION_CHOICES`). One place says how long an orientation is; the window can override for a longer hands-on session. When a type's duration is not one of the shared choices (a 75 minute type), the select appends that value as an extra choice (the off-grid idiom in `EquipmentHoursWindowForm.__init__`, `hub/forms.py:3807`), so a type can always be carved at its own length. |
| 4 | Buffer time | New `buffer_minutes` on the rule (0 / 15 / 30 / 60), applied **between consecutive carved slots** (setup, cleanup, a breath). A 10 to 12 window with 60 min slots and a 15 min buffer yields 10:00 only (11:15 + 60 = 12:15 does not fit). Default 0. The owner's brief names buffer times as a required edge case, which is why this ships in PR 1 rather than waiting for demand. |
| 5 | Seats | Per window, defaulting to the type's `default_seats`. Equipment orientations are usually one on one, so the manager sets 1 where that is true; group orientations (a 4 seat laser intro) work the same way. |
| 6 | Who runs it | **No orienter on equipment rules** (parity with the shipped no-per-orienter decision). "Any manager" runs the slot; whoever confirms is credited via `oriented_by`. Personal hours per manager are deferred (§10). |
| 7 | Editor shape | **The "hours then days" multi-day window formset already on this panel's Hours & Limits tab** (`EquipmentHoursWindowForm`), not the guild's per-person modal. Consistency within the same manage panel beats parity with a different surface, and it matches the brief's "Every Sat/Sun, 10 to 6" literally. One editor row expands to per-weekday `OrientationAvailability` rows on save. |
| 8 | Overlaps | Two windows on the same weekday on one equipment **may not overlap, regardless of orientation type** — the machine is the scarce resource. Rejected in the formset with the existing message shape ("Those hours overlap on Saturday."). Windows may touch (5:00 end meets 5:00 start). Overlap is also enforced at the **slot** layer for equipment: the carve loop skips a candidate that would overlap any other uncancelled slot of this equipment's types (a booked slot kept from an old grid, a one time slot), and the one time slot form refuses a time that overlaps an existing slot. |
| 9 | Pause and delete are honest | Turning a window's Active toggle off, or deleting it, **retires its future open generated slots** (the `retire_rule` guard: seat-holding bookings survive, everything else goes), and a paused rule's surviving slots stop taking **new** bookings: `bookable()` / `is_bookable` gain an `availability.is_active` gate (no rule, or an active rule). Today a paused guild rule leaves every open slot bookable (`bookable()` has no such gate, verified at `membership/models.py:8883`), which is a lie to members. The read-layer gate fixes that for both owners at once, and the guild save path (`_apply_hours_formset`, `hub/views.py:1263`, which today only retires on delete) gains the same retire-on-pause call. Existing bookings on a paused rule's slots are untouched. Slots a delete or pause **keeps** are capped to their taken seats (`retire_open_slots` sets `seats = seats_taken` at the seat-holding scope), so a 1 of 4 booked slot from a deleted window cannot take three more bookings through the `availability` SET_NULL door; when a checkout hold on such a slot later expires, the hold-release path re-caps the slot and cancels it if nothing seat-holding remains. No member-visible copy changes for guilds, so the changelog stays silent about them. |
| 10 | Closure pauses orientations | `Equipment.is_closed` ("Closed for new reservations", maintenance) now also pauses **new** orientation bookings: `OrientationType.is_accepting` for an equipment-owned type becomes `equipment.is_active and not equipment.is_closed`. Existing bookings stand until a manager cancels them (same rule as reservations). |
| 11 | Member picker | The equipment partial's flat 5-per-page slot list is replaced by a **day chip row + time rows** picker (Alpine only, no new endpoint) because carving produces dozens of slots. The guild partial is untouched. |
| 12 | Conflicts with reservations | **PR 2, two way:** a seat-holding orientation slot is busy time on the tool's reservation schedule, and a confirmed reservation makes any overlapping orientation slot unbookable (hidden from the picker). A booked orientation on a CNC occupies the CNC. |
| 13 | Generation horizon | 8 weeks (the existing `window_weeks`), regenerated on every hours save, when a closed tool reopens, and by the existing **nightly** `generate_orientation_slots` job (`Cadence.DAILY`, about 6 AM, `core/scheduled_jobs.py:198`). No new job. |
| 14 | Lifecycle | Unchanged: request → manager confirm/decline → auto-complete; paid types go through the same Stripe checkout and automatic refunds. This spec only changes where slots come from. |

---

## 2. What already exists (reuse, don't reinvent)

All locations re-verified in the current tree, 2026-09-05 (`VERSION` 1.34.2).

| Need | Existing thing | Location |
|---|---|---|
| The recurring rule row (weekday, start, end, seats, location, is_active, orienter, orientation_type) | `OrientationAvailability` (guild FK currently `NOT NULL`) | `membership/models.py:8606` |
| Materialize slots from rules, idempotent on `(availability, starts_at)` | `generate_slots(*, guild=None, window_weeks=8, now=None)` | `membership/orientations.py:1011` |
| Slot uniqueness that already permits many slots per rule per day | `uq_orientationslot_rule_start` on `(availability, starts_at)` | `membership/models.py:8975` |
| Honest rule deletion (retire future open generated slots, keep booked) | `retire_rule(rule) -> (removed, kept)` | `membership/orientations.py:1067` |
| Slot cancel with full per-booking fan-out + hold release + refunds | `cancel_slot(slot, *, reason)` | `membership/orientations.py:917` |
| Owner-aware "taking bookings" gate | `OrientationType.is_accepting` (equipment branch: `equipment.is_active`) | `membership/models.py:8569` |
| Member-facing bookable queryset + per-slot twin | `OrientationSlotQuerySet.bookable()` / `OrientationSlot.is_bookable` | `membership/models.py:8883` / `:9035` |
| Booking guards, friendly errors, duplicate/hold checks | `OrientationSlot.ensure_bookable_for`, `book()` | `membership/models.py:9057` / `:9090` |
| Equipment manager gate (three tiers) | `_require_can_manage` / `can_manage_equipment` | `hub/equipment_views.py:73`, `membership/permissions.py` |
| The manage panel, tab whitelist, render helper, Orientation tab context | `hub_equipment_manage:642`, `_render_manage:597`, `_orientation_tab_context:562` | `hub/equipment_views.py` |
| Orientation tab markup (Pending Requests, Types formset, Upcoming Slots + Add a Time) | `templates/hub/equipment_manage.html:178-342` | template |
| One time slot form (type select, date, half-hour start, duration, seats, location) | `EquipmentOrientationSlotForm` | `hub/forms.py:3954` |
| **The manager editor pattern to mirror:** "hours then days" window form, cross-window overlap guard, half-hour full-day choices | `EquipmentHoursWindowForm:3777`, `_BaseEquipmentHoursWindowFormSet:3840`, `equipment_hour_choices`, `EquipmentHoursWindowFormSet:3864` | `hub/forms.py` |
| Window ⇄ per-day row reconcile (the model-side twin of that editor) | `Equipment.hours_windows()` / `apply_hours_windows()` | `membership/models.py:10630` / `:10650` |
| Hours tab markup (rows, day checkbox pills `.pl-equip-days`, template clone, + Add Hours, Delete/Remove, Save last) | `templates/hub/equipment_manage.html:86-177` | template |
| Hours save view shape (one POST, bound re-render on error, redirect to tab) | `hub_equipment_hours_save` | `hub/equipment_views.py:531` |
| Duration and time choice lists (Rule 20 half-hour selects) | `_SLOT_DURATION_CHOICES:1986`, `half_hour_time_choices:74`, `_parse_time_choice`, `_meeting_time_label` | `hub/forms.py` |
| Member orientation section on the equipment page (states, confirm modals, price chips) | `templates/hub/partials/equipment_orientation.html`; context built by `_equipment_orientation_sections` | `hub/equipment_views.py:308` |
| Reservation engine day math (open windows, busy spans, free intervals, starts, row-locked guard) | `Equipment.open_intervals_for_day:10555`, `_busy_spans_for_day:10571` (reservations only), `free_intervals_for_day:10585`, `free_starts_for_day:10601`, `ensure_reservable:10733` (**checks reservations directly, never the busy spans**) | `membership/models.py` |
| The Equipment row lock every competing booking takes | `reserve()` service: `select_for_update()` on the Equipment row | `membership/equipment.py:113` |
| Shared orientation section builder (flat per-type slot list, `[:30]` cap) | `_orientation_sections(types, member, slots_by_type)` | `hub/views.py:472` |
| Reservation overlap predicate (strict inequalities) | `EquipmentReservationQuerySet.overlapping` | `membership/models.py:10910` |
| Guild-side save helpers whose shape the equipment save mirrors | `guild_orientation_hours_save:1154`, `_apply_hours_formset:1263`, `_hours_save_message:1117` | `hub/views.py` |
| Upcoming Slots card with day-bounded pager and source tags (guild) | `templates/hub/guild_edit.html:614-660` | template |
| Scheduled slot generation | `generate_orientation_slots` job (`Cadence.DAILY`, nightly about 6 AM) | `core/scheduled_jobs.py:198` |
| UI components | `form_field.html`, `confirm_modal.html` (with `confirm_js`), `toggle.html` auto-detect, `.pl-help`, `trigger_toast` | `templates/components/` |

**Genuinely net-new:** three columns + one migration, the carve loop inside `generate_slots`, `retire_open_slots`, the equipment window form/formset + save view + card, the two model reconcile methods, the day-chip picker in the equipment partial, and (PR 2) the shared busy-span union and the two conflict guards. Everything else is assembly.

---

## 3. Where the code lives

```
membership/models.py            # OrientationAvailability: guild nullable, + slot_minutes, + buffer_minutes, carve helper;
                                #   Equipment.orientation_hours_windows() / apply_orientation_hours_windows();
                                #   OrientationType.is_accepting closure leg; bookable()/is_bookable closure + (PR 2) reservation legs;
                                #   (PR 2) Equipment.busy_spans_for_day() union
membership/migrations/0165_equipment_orientation_hours.py   # 0164 is the current head; re-check at build time
membership/orientations.py      # generate_slots: equipment= kwarg, owner-aware gate, carve loop, slot-layer overlap skip; retire_open_slots(); (PR 2) Equipment row lock around guard + hold
membership/equipment.py         # (PR 2) unchanged lock object; cited so the orientation path takes the SAME lock reserve() takes
hub/views.py                    # _orientation_sections gains slot_cap; _apply_hours_formset retires open slots on a pause (decision 9)
hub/forms.py                    # EquipmentOrientationHoursWindowForm + _BaseEquipmentOrientationHoursWindowFormSet + factory
hub/equipment_views.py          # hub_equipment_orientation_hours_save; _orientation_tab_context grows the windows formset + day groups; hub_equipment_hours_save regenerates when a closed tool reopens
hub/urls.py                     # /equipment/<slug>/manage/orientation/hours/
templates/hub/equipment_manage.html            # Orientation tab: new Orientation Hours card; Upcoming Slots grouped by day
templates/hub/partials/equipment_orientation.html   # day chips + time rows picker
templates/hub/partials/equipment_schedule.html      # (PR 2) orientation spans on the timeline
static/css/hub.css              # pl-orient-days__chip, pl-equip-slot--orientation
tests/membership/, tests/hub/   # *_spec.py (root tests tree)
```

---

## 4. Data model — one migration

### 4.1 `OrientationAvailability` (`membership/models.py:8606`)

| Change | Detail |
|---|---|
| `guild` → nullable | `null=True, blank=True`, CASCADE unchanged. `help_text`: "Parent guild. Empty when an equipment owns the orientation type." Denormalized like the slot/booking columns; the owner of record is `orientation_type`. |
| new `slot_minutes` | `PositiveSmallIntegerField(null=True, blank=True, help_text="Carve each occurrence of this window into slots this long. Empty keeps one slot spanning the whole window.")` |
| new `buffer_minutes` | `PositiveSmallIntegerField(default=0, help_text="Minutes left free between consecutive carved slots. Ignored when slot_minutes is empty.")` |
| new CheckConstraint | `Q(slot_minutes__isnull=True) \| Q(slot_minutes__gt=0)`, `name="ck_orientavail_slot_positive"` (under the 30 char cap; run `manage.py check`). |
| `__str__` | `"{owner_name} orientation: Saturday 10:00 (any orienter)"` via `orientation_type.owner_name` (today reads `self.guild.name`, which would crash on None). |
| queryset | `+ .for_equipment(equipment)` → `filter(orientation_type__equipment=equipment)`; existing `for_orienter` / `guild_level` unchanged. |

`OrientationAvailabilityBlock` stays guild-only and `NOT NULL` (blocks are a guild feature; not touched).

`clean()` mirrors `EquipmentHours.clean`: start/end on the half-hour grid (the Django admin's raw time inputs could otherwise write a 9:15 window the carve loop would step from). Fail loudly at every write path.

### 4.2 Carve helper (fat model, pure function)

```python
def carve_starts(self, day: date) -> list[datetime]:
    """Aware local starts this window yields on ``day`` (already known to be its weekday).

    slot_minutes None -> [window start] (legacy one slot).
    Else step from start_time by slot_minutes + buffer_minutes while start + slot_minutes <= end_time.
    Stepping happens on local wall-clock time and each start is made aware individually,
    so a DST day never shifts the whole grid (the make_aware-per-candidate rule from
    the reservation engine).
    """
```

Ends: `start + slot_minutes` (carved) or `end_time` (legacy). The migration is schema-only; every existing row keeps `slot_minutes=NULL` so nothing regenerates differently.

### 4.3 Factories (`tests/membership/factories.py`)

`OrientationAvailabilityFactory` gains `class Params: equipment_owned = factory.Trait(guild=None, orientation_type=factory.SubFactory(OrientationTypeFactory, equipment_owned=True), slot_minutes=60)`. Today's default direction (the type's guild derives from the rule's guild, `tests/membership/factories.py:488`) stays as it is, so a guild row never silently gets `None`.

---

## 5. Business logic (fat models / service)

### 5.1 `generate_slots` (`membership/orientations.py:1011`)

- Signature grows an `equipment: Equipment | None = None` kwarg (mutually exclusive with `guild`; both None = everything, as the cron does). `rules = rules.filter(orientation_type__equipment=equipment)` when set.
- The per-rule gate becomes owner-aware: replace the `GuildOrientationSettings` lookup with `if not rule.orientation_type.is_accepting: continue`. For guild rules this is byte-equivalent to today (type active AND settings row exists AND `settings.is_accepting`); for equipment rules it is `equipment.is_active and not is_closed`.
- The stale-orienter skip runs only when `rule.guild_id is not None` (equipment rules never carry an orienter; `rule.guild.leadership_members()` would crash on None).
- Inner loop: `for start_dt in rule.carve_starts(day): if start_dt <= reference: continue; get_or_create(availability=rule, starts_at=start_dt, defaults={guild: rule.guild, orientation_type, orienter, ends_at: <carved or window end>, seats, location: rule.location or type.default_location, source: GENERATED})`. Idempotency unchanged: the key is still `(rule, start)`.
- **Off-grid cleanup (equipment rules only):** before carving a rule, delete that rule's future open GENERATED slots whose `(starts_at, ends_at, seats)` are off the current grid (`retire_open_slots` narrowed to off-grid rows), so a kept old-grid slot whose booking was later cancelled cannot linger and block the new grid.
- **Slot-layer overlap (equipment rules only):** before creating a candidate, skip it when its span overlaps any uncancelled slot of this equipment's owned types other than the `(rule, start)` slot itself (a booked slot kept from an old grid, a one time slot). The overlap set is loaded once per equipment per run (one query), so this is list math, not N queries. Guild rules keep today's behavior.
- `select_related` adds `"orientation_type__equipment"` so `is_accepting` costs no extra query per rule.

### 5.2 `retire_open_slots(rule) -> tuple[int, int]` *(new)* and `retire_rule`

`retire_open_slots` is the deletion half of today's `retire_rule` (future GENERATED slots with no `seat_holding()` booking are deleted; booked ones kept) **without** deleting the rule. `retire_rule` becomes `retire_open_slots` + `rule.delete()`. Both return `(removed, kept)`.

Pause also stops **new** bookings on the slots it keeps, through §5.4's `availability.is_active` gate. Used by: delete (as today), **pause** (Active toggled off, decision 9), and **re-grid** (a window whose `slot_minutes` / `buffer_minutes` / `seats` changed keeps the same rule row but its future open slots are retired and regenerated on the new grid; booked slots on the old grid survive and show in Upcoming Slots exactly like a kept slot after a delete).

### 5.3 `Equipment.orientation_hours_windows()` / `apply_orientation_hours_windows(windows)` *(new, mirror of `hours_windows` / `apply_hours_windows`)*

- `orientation_hours_windows()` groups this equipment's rules (`OrientationAvailability.objects.for_equipment(self)`) by `(orientation_type_id, start_time, end_time, slot_minutes, buffer_minutes, seats, is_active)` into editor rows carrying every weekday they cover; ordered by start then end. Times as `"HH:MM"` choice keys.
- `apply_orientation_hours_windows(windows)` reconciles per-weekday rows keyed by `(type_id, weekday, start, end)`:
  - key no longer desired → `retire_rule` (counts accumulate);
  - key present, `is_active` flipped **off** → `retire_open_slots` + save; flipped on → save (regeneration follows);
  - key present, `slot_minutes` / `buffer_minutes` / `seats` changed → update + `retire_open_slots`;
  - new key → create with `guild=None`, `orienter=None`.
  Returns `(deleted_rules, removed, kept)` for the flash, counting retirements from pauses and re-grids too, not only deletes. Then the view calls `generate_slots(equipment=self)`.
- Both live on `Equipment` (fat model); the view is parse → formset → these two calls → message.
- **Guild parity (decision 9):** `_apply_hours_formset` (`hub/views.py:1263`) calls `retire_open_slots` for a saved rule whose `is_active` flipped from on to off, and `_hours_save_message` leads with "Hours saved." when `deleted_rules == 0` and appends the counts whenever `removed or kept` is non-zero (today it keys on `deleted_rules` only, `hub/views.py:1117`, and would read "Hours deleted." for a pause; its shared-hours branch stays keyed on `deleted_rules`). One small addition in the guild save path; no guild UI change.

### 5.4 Owner gates

- `OrientationType.is_accepting` (`:8569`) equipment leg: `equipment.is_active and not equipment.is_closed`.
- `OrientationSlotQuerySet.bookable()` (`:8883`) equipment gate adds `orientation_type__equipment__is_closed=False`. `is_bookable` inherits through `is_accepting`.
- The requirements banner's paused variant (`equipment_detail.html`, `required_orientation_paused`) already keys off `is_accepting`, so a closed tool now reads "Orientation bookings for this tool are paused" with the Book button suppressed. Nothing else to change there.
- **Paused rule gate (both owners):** `bookable()` adds `Q(availability__isnull=True) | Q(availability__is_active=True)` and `is_bookable` mirrors it, so a paused window's surviving slots stop taking new bookings. Existing bookings on them are untouched; a one time (MANUAL) slot has no rule and is unaffected. Slots a delete keeps (`availability` SET_NULL) would pass the gate, so `retire_open_slots` caps every kept slot to its taken seats (decision 9), and the hold-expiry path (`expire_payment_holds` / `release_hold_if_unpaid`) re-caps a GENERATED slot whose rule is gone or inactive and cancels it when nothing seat-holding remains. This is a deliberate, small behavior change for guilds (a paused guild rule's slots were bookable until now).
- **Reopen regenerates:** `hub_equipment_hours_save` (`hub/equipment_views.py:531`) calls `generate_slots(equipment=equipment)` when `is_closed` flips from on to off; otherwise a reopened tool would sit empty until the nightly job.

### 5.5 Permissions

`hub_equipment_orientation_hours_save` gates on `_require_can_manage` (staff row ∪ owning-guild leadership ∪ EQUIPMENT capability ∪ admin), like every manage endpoint. The form's `orientation_type` queryset is `equipment.owned_orientation_types.active()` (plus the row's own type when retired, so an existing row still validates), so a crafted POST naming another owner's type is a plain field error. Never `is_staff` / `fog_role` / `member_type`.

### 5.6 PR 2 — conflict awareness (two way)

- `Equipment.busy_spans_for_day(day)` *(rename of `_busy_spans_for_day`, now public)* merges **confirmed reservations** ∪ **uncancelled orientation slots of this equipment's owned types that hold a seat** (`PENDING_PAYMENT | REQUESTED | CONFIRMED` booking; the `_busy_spans` rule from the block engine at `models.py:8732`). Open, unbooked slots are NOT busy: a posted-but-unbooked 11:00 orientation slot must not block a member's 11:00 reservation, otherwise every window would freeze the tool. `free_intervals_for_day`, `free_starts_for_day`, and `durations_for` inherit the union, so the UI never offers a start over a booked orientation. **`ensure_reservable` does NOT read the busy spans** (it checks `EquipmentReservation.objects.overlapping` directly, `membership/models.py:10764-10777`), so it gains an explicit orientation-overlap check after the reservation check: seat-holding, uncancelled slots of this equipment's owned types, strict inequalities, raising `EquipmentError("That time overlaps a booked orientation. Please pick another time.")`. It runs under the existing Equipment row lock in `reserve()` (`membership/equipment.py:113`), so a stale or crafted POST cannot double book the machine.
- Orientation side: `is_bookable` (equipment leg) adds `not EquipmentReservation.objects.overlapping(equipment, self.starts_at, self.ends_at).exists()`; `bookable()` adds `~Exists(EquipmentReservation confirmed, equipment=OuterRef("orientation_type__equipment"), starts_at__lt=OuterRef("ends_at"), ends_at__gt=OuterRef("starts_at"))` for the equipment gate. A slot under a reservation drops out of the member picker and `book()` refuses it with the existing "not available to book" copy.
- **Race:** a reservation and an orientation request racing for one span. For an equipment-owned slot, `request_orientation` wraps `ensure_bookable_for` + `book()` in `transaction.atomic()` with `select_for_update()` on the **Equipment row** (the same lock object `reserve()` takes in `membership/equipment.py:113`), so the two paths serialize on one lock. `start_orientation_checkout` (`membership/orientations.py:460`) locks only around the guard and the `PENDING_PAYMENT` hold creation; the Stripe call happens **after** that block, never inside it (a lock held across a network round trip would stall every reservation on the tool). Guild-owned paths untouched.
- Manager side: a window or one time slot may be posted over an existing reservation (the manager might intend to bump it); such slots render muted in Upcoming Slots with "Blocked by Sam's reservation 10:00 to 12:00" and never reach members until the reservation is cancelled.
- Timeline: `equipment_schedule.html` renders busy orientation spans as `pl-equip-slot--orientation` labeled "Orientation · Sam R." (reserver-name visibility is the standing community norm, locked in the reservations spec), distinct from reservation spans.

---

## 6. UI / UX — every screen, rubric applied

Member copy: plain ELI14, short sentences, **no dashes in any copy string**. All new classes `pl-` prefixed in `hub.css`, theme tokens only. Verify **both themes** on every screen. All form controls sit under `.hub-form-group` (Rule 13). No native time inputs anywhere (Rule 20).

### 6.1 User flow — manager

1. Open the tool → **Manage** → **Orientation** tab.
2. Orientation Types card: make sure the type exists (name, 60 min, price, seats). Save.
3. **Orientation Hours** card → **+ Add Hours** → pick the orientation, Opens 10:00 AM, Closes 6:00 PM, tick Sat + Sun, slot length 60 min (pre-filled from the type), break 0, seats 1 → the preview line reads "Makes 8 slots each day: 10:00 AM, 11:00 AM … 5:00 PM" → **Save**. Flash: "Hours saved." The Upcoming Slots card now shows the next 8 weekends grouped by day.
4. Requests arrive in the Pending Requests card (and by email/in-app as today) → **Review** → confirm/decline.
5. Away next weekend? Cancel that day's slots from Upcoming Slots (booked ones notify + refund as today). Away for a month? Toggle the window's Active off → open slots vanish, booked ones stay listed for individual handling. Tool down for repair? Hours & Limits → Closed: new orientation bookings pause too.

### 6.2 User flow — member

1. Equipment page → requirements banner "You need the CNC Basics orientation" → **Book the Orientation** scrolls to the section.
2. Day chips (Sat Sep 12 · Sun Sep 13 · Sat Sep 19 …) → tap a day → time rows → **Request** on 11:00 AM → confirm modal (free: "We'll send your request to the equipment managers to confirm."; paid: existing pay-now copy) → flash + email; the section flips to the live-booking state ("Requested. Waiting for a manager to confirm.").
3. Manager confirms → email + `.ics`; after the slot passes, auto-complete flips the banner green and the reservation form opens.
4. Change of plans → **Cancel my orientation** (confirm modal; paid: automatic refund) → the seat frees, the slot returns to the picker.

### 6.3 Manage panel — Orientation tab (`templates/hub/equipment_manage.html`)

Card order, top to bottom (immediate-effect controls none; each card its own form with **Save** last, Rule 21):

1. **Pending Requests** — unchanged.
2. **Orientation Types** — unchanged.
3. **Orientation Hours** *(new card, own `<form>` POSTing `hub_equipment_orientation_hours_save`)*
   - Heading "Orientation Hours". Hint: "Post the weekly hours when a manager can give this orientation. We turn them into bookable slots automatically, eight weeks ahead. Pause a window to stop new slots without deleting it."
   - **Formset:** `EquipmentOrientationHoursWindowFormSet` (`formset_factory(EquipmentOrientationHoursWindowForm, formset=_BaseEquipmentOrientationHoursWindowFormSet, extra=0, can_delete=True)`, prefix `ohours`, `initial=equipment.orientation_hours_windows()`). Rows use the existing `hub-card pl-equip-hours-row` shell. Fields per row, via `form_field.html` unless noted:
     - **Orientation** (`ModelChoiceField`, this equipment's active types, `empty_label=None`, initial first type; each `<option>` carries `data-duration` and `data-seats` so the row can seed its defaults).
     - **Opens** / **Closes** (`equipment_hour_choices()`, half-hour, up to 11:30 PM) in the `.pl-equip-hours-row__fields` pair.
     - **Days** — the `.pl-equip-days` checkbox pills, hand-rendered exactly like the Hours tab (`CheckboxSelectMultiple` trips the toggle auto-detect). Error "Pick at least one day."
     - **Slot length** (`TypedChoiceField`, `_SLOT_DURATION_CHOICES`, initial = the selected type's `duration_minutes`; when that value is off the list it is appended as an extra choice, decision 3, and the `data-duration` seeding selects it) and **Break between slots** (choices None / 15 min / 30 min / 60 min, initial None) side by side.
     - **Seats per slot** (number, min 1, initial = type `default_seats`).
     - **Active** toggle (auto via `form_field.html`), initial on.
     - **Preview line** (client only, `x-data` on the row reading the five selects): "Makes 8 slots each day: 10:00 AM, 11:00 AM … 5:00 PM" or, when nothing fits, "This window is shorter than one slot." Muted `hub-text-muted`, updates on change. Purely informational; the server is the authority.
     - **Delete** (saved rows; `pl-btn pl-btn--danger pl-btn--sm`, `margin-top:0.75rem`) opens `components/confirm_modal.html` with `confirm_js` that flips the hidden `DELETE` and `requestSubmit()`s the card's form (the guild `guild-rule-del` idiom): title "Delete these hours?", message "Upcoming open slots from this window will be removed. Slots someone already booked stay until you cancel them." Cloned rows get **Remove** (DOM removal only).
   - **+ Add Hours** button clones the hidden `<template>` of `formset.empty_form` (swap `__prefix__`, bump `id_ohours-TOTAL_FORMS`; the type select's `change` handler seeds slot length + seats on the new row).
   - **Save** `pl-btn pl-btn--primary`, last element in the card, ≥1.5rem clearance before the next card (Rule 18). Success → redirect `?tab=orientation` + Django message from a new `_orientation_hours_save_message(deleted_rules, removed, kept)` in `hub/equipment_views.py`, keyed on `removed or kept` so a pause or re-grid that retired slots reports its counts too: "Hours saved." / "Hours saved. Removed 6 upcoming open slots. 1 booked slot kept. Cancel it from the Upcoming Slots card."
   - **Validation (form / formset `clean`):** end after start ("The end time must be after the start time."); at least one day; slot length must fit the window ("This window is shorter than one slot."); type must be this equipment's; cross-window overlap on any shared weekday across all rows regardless of type ("Those hours overlap on Saturday."). Errors re-render the tab (`_render_manage(..., orientation_hours_formset=bound, active_tab="orientation")`) with field errors inline; nothing saved.
   - **Empty state:** "No orientation hours yet. Add a window and members can start booking." (above the + Add Hours button).
   - **Dark + light:** identical widgets to the Hours tab (already token-correct), day pills reuse `.pl-equip-days`; verify the preview line contrast in both themes.
   - **Mobile:** rows stack single column (existing `pl-equip-hours-row` behavior); pills wrap; buttons full-width tap targets.
4. **Upcoming Slots + Add a Time** — same card, two changes:
   - **Group by day.** Slots render under a day header ("Sat Sep 12 · 8 slots · 2 booked") that expands (`x-show`, display in a CSS class) to the existing per-slot rows (time, type, seats booked / held, source tag `recurring` / `one time`, Cancel behind the existing confirm modal, attendee sub-rows linking to respond). Every action the shipped card exposes (per-slot Cancel with its confirm copy, attendee rows to the respond page, the held-seat note) is kept; only the grouping wrapper is new. Days with any attendee or hold open by default; empty days closed. Beyond 14 days the list shows a "Show later days" button (client only) so 8 weeks of carved slots never render as a wall.
   - **(PR 2)** slots under a confirmed reservation render muted with "Blocked by {name}'s reservation {start} to {end}".
   - **+ Add a Time** and its bound-form reveal behavior are unchanged, except `EquipmentOrientationSlotForm.clean` (`hub/forms.py:3997`) gains the slot-layer overlap check (decision 8): a time overlapping any uncancelled slot of this equipment's types errors with "That time overlaps another orientation time on this tool." Empty state: "No upcoming times. Add hours above, or add a one time slot here."
5. Every POST re-renders the tab with bound errors on invalid; every success is a message + redirect to `?tab=orientation`. Breadcrumb back-link exists.

### 6.4 Equipment page — orientation section (`templates/hub/partials/equipment_orientation.html`)

Per renderable type group, the request state's slot list becomes a picker:

- **Day chips:** a horizontally scrolling row (`pl-orient-days`, `overflow-x:auto`, contained; no page-level horizontal scroll) of every upcoming local date that has at least one slot for this type, each chip "Sat Sep 12" with a small "3 open" count; chips whose slots are all full render muted "Full". Default selection = the first day with an open slot; `x-data="{ day: '<iso>' }"` on the group, chips `@click="day = '<iso>'"`, `:class` active. The chip row scrolls the selected chip into view on load (`scrollIntoView({inline:'nearest'})`).
- **Time rows** (`x-show="day === '<iso>'"`, one `<li>` per slot, existing `pl-orient-slots__row` grid minus the with-cell): "10:00 AM to 11:00 AM" · seats line ("1 seat left" / "3 of 4 open") · **Request** (existing confirm modals, free and paid copy unchanged) or muted "Full". Rows in `starts_at` order.
- Day/slot data is server-rendered. The shared `_orientation_sections` (`hub/views.py:472`) gains a `slot_cap` parameter (guild callers keep 30; equipment passes `None` for the full 8 week set), and `_equipment_orientation_sections` post-processes each type's flat list into `days = [{iso, label, open_count, slots}]` for the template. Default chip = the first day with an open slot, else the first day (its rows all read Full and the all-full line shows), so the row never renders with nothing selected. No new endpoint, no loading state.
- **States:** empty (no slots at all) unchanged: "No orientation times are posted yet. Check back soon." + manager link. All full: chips all muted + "Every time is full right now. Check back soon." Paused/oriented/booked/hold states untouched. Race loss: existing `OrientationError` copy, redirect back to the anchor.
- **Dark + light:** chips are `pl-orient-days__chip` on `--hub-surface` / active `--color-tuscan-yellow` text on `--color-navy` (the existing primary button pairing); no form controls added.
- **Mobile:** chip row scrolls in its own region; time rows wrap the seats line under the time; Request stays a full-size tap target.
- **Accessibility:** chips are plain `<button type="button" aria-pressed="true|false">` (no tablist pattern to half-implement); the rows list carries `aria-live="polite"` so a screen reader hears the day change.

### 6.5 Equipment page — requirements banner and schedule (PR 2)

- Banner: no change beyond the closure paused variant (§5.4).
- Schedule timeline: orientation busy spans labeled "Orientation · Sam R." in `pl-equip-slot--orientation` (a distinct hue on theme tokens; verify both themes). The Book a Time selects simply omit the covered starts (engine inheritance, §5.6). Copy under the timeline legend: "Orientations booked on this tool show here too."

### 6.6 Edge cases (all handled, stated once)

| Case | Behavior |
|---|---|
| Two members request the same carved slot | Seat math as today: `seats` per slot; when seats hit 0 the row says Full. Two requests for one remaining seat: `ensure_bookable_for` + the live-per-type unique constraint; the loser gets "That slot's remaining seat is held…" / "not available" copy. |
| Overlapping windows on one weekday | Rejected in the formset (decision 8). |
| Window edited (time moved) | Old key retired (`retire_rule`), new key created, regenerated; booked slots on the old grid survive in Upcoming Slots with their bookings. Message carries counts. |
| Slot length / break / seats changed | Same rule row; open future slots regenerated on the new grid; booked survive (§5.2). |
| Buffer | Carve step = slot + buffer; the last slot must end by Closes (decision 4). |
| Window paused | Open future generated slots retired; booked stay; generation stops. Toggle on → regenerate on save. |
| Window deleted | `retire_rule` as today. |
| Orientation type deactivated | Rules stop generating (existing `orientation_type__is_active` filter); existing slots hidden by `bookable()`; live bookings pinned (shipped rule). |
| Equipment closed (maintenance) | New orientation bookings pause (decision 10); banner shows the paused copy; Upcoming Slots still lists everything for managers. |
| Equipment retired (`is_active` off) | Already fails closed everywhere. |
| Member cancels | Seat frees, slot returns to the picker (exists). |
| Manager cancels a slot | Existing `cancel_slot` fan-out (notify, release holds, refund). |
| Manager cancels a whole day | Per-slot cancel from the day group (a day-level cancel is deferred, §10). |
| Reservation overlaps a booked orientation (PR 2) | Reservation start/duration options omit it; `ensure_reservable` refuses under the row lock. |
| Orientation slot under a confirmed reservation (PR 2) | Hidden from the picker, `book()` refuses; manager row shows "Blocked by…". |
| DST weekend | Carving steps local wall-clock and makes each start aware individually (§4.2); asserted in tests. |
| Past-time generation | `start_dt <= reference` skipped (exists); a window whose remaining slots today are all past yields none for today. |
| 8 week horizon end | The nightly job tops up (exists); every hours save and a reopen regenerate immediately. |
| One time slot posted over an existing slot | Refused by the form (decision 8). |
| Re-grid leaves a booked old-grid slot | The new grid skips any candidate overlapping it; it stays in Upcoming Slots with its bookings. |
| Window paused with a kept booked slot | The kept slot takes no new bookings (availability gate); its existing booking stands; a hold that expires later frees a seat that is still not bookable. |

---

## 7. Notifications / emails / activity

No new triggers, no new templates. Request / confirm / decline / cancel emails are already owner-aware (v1.32.0). The confirm modal copy for equipment ("to the equipment managers") is unchanged. `SiteActivity` unchanged.

---

## 8. Build order — two sequential PRs, each ships green

Each PR: targeted suites + `ruff format/check` + `manage.py check` green (the pre-push hook runs real mypy), `template_comment_lint_spec`, VERSION bump (next free minor at merge time; other sessions ship concurrently), one curated no-dash changelog entry. The `e2e` lane: grep `tests/e2e/` for `equipment` and run any affected spec on Postgres 5433 before pushing (none exist today; the help/screenshot specs may snapshot the equipment page).

### PR 1 — Recurring orientation hours for equipment

1. Migration + model: nullable `guild`, `slot_minutes`, `buffer_minutes`, constraint, `clean`, `__str__`, `for_equipment`, `carve_starts`; `is_accepting` closure leg; `bookable()` / `is_bookable` closure filter **and the paused-rule availability gate** (both owners). Factories.
2. Service: `generate_slots(equipment=)`, owner-aware gate, guild-only stale-orienter skip, carve loop with the slot-layer overlap skip; `retire_open_slots` + `retire_rule` refactor; `Equipment.orientation_hours_windows` / `apply_orientation_hours_windows`; `_apply_hours_formset` retire-on-pause + its message keyed on counts (guild parity).
3. Form + formset (slot length off-list choice, one time form overlap check) + save view + URL + `_orientation_hours_save_message`; `_render_manage` / `_orientation_tab_context` context (windows formset, day groups); `_orientation_sections(slot_cap=)`; `hub_equipment_hours_save` reopen regeneration.
4. Templates: Orientation Hours card, Upcoming Slots day groups, the day-chip picker in the equipment partial; `hub.css` classes.
5. Housekeeping: VERSION + changelog:
   > *"Book equipment orientations by the hour: Equipment managers can now post weekly orientation hours for a tool, like Saturdays 10 to 6, and the app turns them into bookable time slots automatically. Pick a day, pick a time, and request it right on the tool's page."*

### PR 2 — Orientations and reservations stay out of each other's way

1. `Equipment.busy_spans_for_day` union; **`ensure_reservable` orientation-overlap check**; `is_bookable` / `bookable()` reservation legs; the Equipment row lock in the equipment-owned request path and around the hold creation only in the checkout path.
2. Timeline orientation spans; Upcoming Slots "Blocked by…" rows.
3. VERSION + changelog:
   > *"Orientations and reservations stay out of each other's way: A booked orientation now shows on the tool's schedule and blocks reservations for that time, and a reserved time can't be double booked by an orientation."*

> Spec only — do not build until approved.

---

## 9. Testing

BDD `*_spec.py`, `describe_*` / `it_*` ONLY (`context_*` is silently skipped), factory-boy, 100% branch gate, fast-merge policy. Homes: `tests/membership/` (`orienter_availability_spec.py`, `orientation_models_spec.py`, `orientations_service_spec.py`, `equipment_spec.py`, `equipment_reservations_spec.py`) and `tests/hub/` (`equipment_orientation_manage_spec.py`, `equipment_views_spec.py`, `equipment_schedule_views_spec.py`). Slot fixtures at `now + timedelta(days=2)`; window math asserted in local time. Also extend `tests/core/scheduled_jobs_spec.py` parity only if a job is added (none is).

**Model / carve**
- `carve_starts`: legacy (`slot_minutes=None`) → one start; 10:00 to 18:00 / 60 / 0 → 8 starts; 10:00 to 12:00 / 60 / 15 → `[10:00]`; 10:00 to 12:15 / 60 / 15 → `[10:00, 11:15]`; window shorter than slot → `[]`; DST-change day yields the same wall-clock list as an ordinary day.
- Constraint: `slot_minutes=0` rejected; `NULL` and positive accepted. `clean` rejects 9:15.
- `__str__` on an equipment rule (guild None) does not crash.
- `is_accepting` equipment leg: active + open True; closed False; retired False. `bookable()` excludes slots of a closed tool; the guild settings and orienter gates are unchanged (regression block); the availability gate is the only guild-side addition and is asserted in the paused-rule block below.
- **Paused-rule gate:** `bookable()` and `is_bookable` exclude a slot whose rule is inactive, for a guild rule and an equipment rule alike; a MANUAL slot (no rule) is unaffected; an existing REQUESTED / CONFIRMED booking on a paused rule's slot is untouched and still confirmable; a `PENDING_PAYMENT` hold that later expires does not make the slot bookable again while the rule is paused.

**Generation**
- Equipment rule generates carved slots with `guild=None`, `orienter=None`, `ends_at = start + slot_minutes`, `seats` from the rule, source GENERATED; re-run creates 0 (idempotent); `equipment=` scoping only touches that tool; a closed / retired / inactive-type tool generates nothing; guild rules generate exactly as today (existing specs re-run); stale-orienter skip never evaluated for guild-less rules.
- Cron path: `generate_slots()` with no scope covers both owners.
- **Slot-layer overlap:** a booked old-grid slot is skipped by the new grid; a one time slot blocks the overlapping carved candidate; a different type's slot on the same tool blocks it too; a guild rule still generates over a guild sibling's slot exactly as today (regression); the overlap set is one query per run (`assertNumQueries`).
- **Reopen:** flipping `is_closed` off in the Hours & Limits save regenerates the tool's slots; flipping it on generates nothing new.

**Reconcile / retirement**
- `orientation_hours_windows` groups per-day rows into one editor row with all days; `apply_orientation_hours_windows`: unchecked day deletes that row via `retire_rule`; deleted window retires all its days; pause retires open slots and keeps booked (counts correct); slot length change keeps the rule, retires open, regenerates; seats change likewise; PENDING_PAYMENT hold on a slot spares it (the `seat_holding` guard); manual slots never touched.
- `retire_rule` still deletes the rule; `retire_open_slots` does not.
- **Seat cap on kept slots:** a kept 1 of 4 slot is capped to 1 seat and takes no new booking; a kept slot whose only holder is a hold is capped to 1, and when that hold expires the slot is cancelled (no seat-holding booking left); a kept slot with a CONFIRMED booking plus an expiring hold is re-capped to 1, not cancelled.
- **Off-grid cleanup:** after a re-grid, an old-grid open GENERATED slot (its booking cancelled since) is deleted before carving and no longer blocks the new grid; a booked old-grid slot survives; MANUAL slots untouched.

**Forms**
- Overlap on a shared weekday across two rows (different types) rejected; touching windows accepted; missing days error; slot longer than window error; foreign-owner type rejected; end before start error; a removed clone row (no posted keys) does not block save (the `has_changed` guard, copied); a type with an off-list duration (75) round-trips as its own slot length choice; the one time slot form rejects a time overlapping an existing slot with the stated message.
- Guild parity: `_apply_hours_formset` retires open slots when a rule is paused (counts in the message), still retires on delete, never touches booked slots.

**Views / templates (`tests/hub/`)**
- Save view: permission edges (plain member 403, random guild lead 403, staff-row manager / owning-guild lead / EQUIPMENT holder 200, view_as respected); valid POST creates rows + slots + redirects to the tab with the message; invalid POST re-renders the Orientation tab with the bound formset and field errors; the success message carries retirement counts for a delete, a pause, and a re-grid.
- Tab renders the Orientation Hours card with existing windows grouped, the empty state, the + Add Hours template, and Upcoming Slots grouped by day (open-by-default days = days with attendees).
- Equipment partial: day chips only for dates with slots, first open day selected (all full → first day selected and the all-full line), full days muted, time rows for the selected day, Request modal copy, "Every time is full" state, no pager markup, the guild page still capped at 30 slots (`slot_cap` regression), no horizontal overflow class missing.
- Changelog-renders-everywhere check (no new UI copy string collides with negative assertions).

**PR 2**
- `busy_spans_for_day` unions confirmed reservations and seat-holding orientation slots; open unbooked slots are NOT busy; cancelled slots / resolved bookings free their span.
- `free_starts_for_day` omits starts overlapping a booked orientation; **`ensure_reservable` raises the orientation-overlap error for a crafted POST over a booked orientation even though the UI never offered it**; the race test (reservation vs orientation request on one span) yields exactly one winner under the shared Equipment lock; the paid path commits the hold before the (mocked) Stripe call runs, so no lock is held across it.
- `is_bookable` / `bookable()` hide an equipment slot under a confirmed reservation; guild slots unaffected; `book()` refuses with the standard copy.
- Timeline renders the orientation span with the label; Upcoming Slots renders the "Blocked by" row.

---

## 10. Open / deferred

- **Personal (per-manager) orientation hours on equipment** — the guild "with Bob" model. Deferred until a tool has several managers who want to be booked by name; the rule model already carries `orienter`, so it is a form field and a label when wanted.
- **Guild windows carved by duration** — `slot_minutes` / `buffer_minutes` exist on guild rows too (NULL). Exposing them in the guild Edit Hours modal is a two-field UI change once a guild asks.
- **Day-level cancel** ("cancel all of Saturday") — per-slot cancel covers it; add when a manager actually asks.
- **Minimum notice** ("book at least 24 hours ahead") — guilds have none; parity kept. Managers decline late requests today.
- **Custom-time requests and availability blocks for equipment** — guild-settings features; unchanged punt from v1.32.0.
- **Dashboard access for pure equipment managers** — unchanged punt; the manage tab is their surface.
