# Per-Orienter Orientation Availability — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-08-26
**Surface:** FOG hub (`pastlives.test:8000`) — guild edit Orientations tab, guild page orientation section, orientations dashboard, respond page, orientation emails
**Related:** Extends the shipped guild-orientations system (`2026-06-21-guild-orientations.md`). Designed payment-agnostic alongside `2026-08-26-paid-orientations.md` (payment at booking) — a price bolt-on attaches to the slot/settings layer and never touches the orienter model below. **Layout precedence:** both specs restructure the Orientations tab and the member slot list; **this spec's slot-row layout (the with-cell, §6.5) is the base**, and the paid spec's price chip and settings field compose onto it — whichever builds second reconciles here, not the other way around.

---

## 1. Summary

Today a guild has one shared set of orientation hours. This change lets each staff member **personally publish when they can give orientations**: Bob adds his Tuesday evenings, Alice adds her Saturday mornings, and both sets become bookable slots — including at overlapping times. A member books a **specific slot that belongs to a specific person**, sees who they're booking with ("Tue Sep 2, 6:00 pm · with Bob"), and the person who published the slot receives the request and runs the orientation. Existing guild-level hours keep working unchanged as "Guild hours (any orienter)."

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Data model | **Extend, don't replace:** add an `orienter` (Member FK) to `OrientationAvailability` and `OrientationSlot`. Existing rows stay valid with `orienter=NULL` = "guild slot, any orienter" (back-compat); new rows are personal. |
| Editing | Each staff member edits **their own** hours; leads/admins can edit anyone's in that guild. |
| Confirm flow | **Unchanged:** booking is still a request the orienter manually confirms or declines (the human gate stays). For a personal slot the request/confirm routing goes to **that orienter** (leads still see everything); `oriented_by` on confirm defaults to the **slot's orienter** instead of the guild lead. |
| Multiple staff, same time | Fine — two orienters publishing overlapping hours yield **two distinct bookable slots**; the member-facing list makes clear who each slot is with. |

---

## 2. What already exists (reuse, don't reinvent)

All confirmed in the codebase — this is threading one FK through existing plumbing, not new machinery.

| Need | Existing thing | Location |
|---|---|---|
| Guild orientation config | `GuildOrientationSettings` (is_enabled / is_closed / defaults) | `membership/models.py:7592` |
| Recurring hours rule | `OrientationAvailability` (guild FK, weekday/start/end/seats/location, `related_name="orientation_rules"`) | `membership/models.py:7687` |
| Concrete bookable slot | `OrientationSlot` (guild FK, optional `availability` FK, starts/ends, seats, `source` MANUAL/GENERATED, `book()`, `seats_taken`/`remaining`, `is_bookable`) | `membership/models.py:7747` |
| Booking lifecycle | `OrientationBooking` (requested/confirmed/declined/cancelled, `oriented_by` set on `confirm()` — defaults to `guild.guild_lead` at `:7961`) | `membership/models.py:7883` |
| Request/confirm/decline/cancel service + emails + `.ics` | `request_orientation:180`, `confirm/decline/cancel_orientation:310/330/345`, `cancel_slot:372`, `complete_orientation:380`, `auto_complete:428`, signed tokens `make_action_token:38`, `build_ics:87` | `membership/orientations.py` |
| Slot generation (the ONE place to thread orienter) | `generate_slots:443` — walks active rules over an 8-week window, `get_or_create(availability=rule, starts_at=…)`, idempotent | `membership/orientations.py` |
| Permission gate | `can_manage_orientations` (lead OR admin OR any guild staff) + hub `_require_can_manage_orientations` | `membership/permissions.py:61`; `hub/views.py:601` |
| Staff roles | `GuildStaffMembership` (`Role.ORIENTER:2036`; every staff role = full lead authority), `Guild.leadership_members()` | `membership/models.py:2022` |
| Hours editor (canonical formset pattern, cited by FRONTEND.md) | Recurring-hours formset on the Orientations tab: `extra=0`, hidden `empty_form` `<template>`, "+ Add" clone, real Delete buttons | `templates/hub/guild_edit.html:368-434`; save view `hub/views.py:824` |
| One-off slot add / slot cancel (endpoints only — see gap 4) | `guild_orientation_slot_add` / `guild_orientation_slot_cancel` + `OrientationSlotForm` | `hub/views.py:950/976`; `hub/urls.py:105/110` |
| Member slot list | `templates/hub/partials/guild_orientation.html` (paged 5-at-a-time list, Request confirm modal, custom-request form) | partial + `hub/views.py:993/1016` |
| Request fan-out | `_emit_lead_request:276` — email to `leadership_members()`, in-app via `guild_orienters` resolver | `membership/orientations.py`; `core/events/resolvers.py:266`; trigger `core/triggers.py:100` |
| Dashboard + export | `orientations_dashboard:1198`, `orientations_export:1257`, `orientation_add_member:1270`, `orientation_toggle_completed:1292` | `hub/views.py`; `templates/hub/orientations_dashboard.html` |
| Cheap member photo | `Member.profile_photo` ImageField | `membership/models.py:415` |
| Crons | `generate_orientation_slots` + `auto_complete_orientations`, registered in `core/scheduled_jobs.py` | management commands (no changes needed — logic lives in the service) |

**Genuine gaps (small, plus one shipped hole):**

1. No `orienter` on rule/slot — the two FKs this spec adds.
2. No permission distinction for "edit someone else's hours" — one new helper, `can_edit_orienter_hours` (§5).
3. Deleting a rule today **strands** its future generated slots (`availability` goes SET_NULL, slots stay bookable). This spec fixes that as part of the retirement flow (§5) — arguably a latent bug fix.
4. **The one-off slot form and slot-cancel UI were never rendered.** `guild_orientation_slot_add` / `guild_orientation_slot_cancel` shipped as views + URLs, but a repo-wide grep finds **zero** template references to either URL name — there is no slot form on the Orientations tab, no upcoming-slot list, no per-slot Cancel button anywhere in the UI. One-off slots and slot cancellation are currently unreachable except by hand-crafted POST. §6.4 builds the missing surface (an "Upcoming Slots" card + the finally-rendered add form) on the **existing, unchanged endpoints**.

**A constraint non-change worth stating:** the slot unique key is already `(availability, starts_at)` (`uq_orientationslot_rule_start`). Each orienter's rule is its **own** availability row, so overlapping rules from different people already materialize as distinct slots with **no constraint change**. The generation delta is only threading `orienter` into the `get_or_create` defaults.

---

## 3. Where the code lives

Same homes as the shipped feature — no new app, no new coverage/mypy scope.

```
membership/models.py            # + orienter FK on OrientationAvailability + OrientationSlot; confirm()/mark_completed() default change
membership/migrations/00XX_orienter_availability.py   # two nullable FKs; reverse = drop fields
membership/orientations.py      # generate_slots threads orienter; retire_rule(); retire_orienter(); routing + copy deltas
membership/permissions.py       # + can_edit_orienter_hours()
core/events/resolvers.py        # guild_orienters honors a personal slot in context
hub/forms.py                    # OrientationAvailabilityFormSet scoping; OrientationSlotForm + orienter field
hub/views.py                    # hours-save gains an orienter scope; staff_remove calls retire_orienter; dashboard select_related
templates/hub/guild_edit.html   # Orientations tab: My Hours / All Orienters overview / Guild Hours / Upcoming Slots + Add A Slot cards
templates/hub/partials/guild_orientation.html   # "with Bob" + avatar in the slot list and booking status
templates/hub/orientation_respond.html          # show the orienter
templates/hub/orientations_dashboard.html       # Orienter column
templates/membership/emails/orientation_*.{html,txt}  # copy deltas only ("with Bob", routing)
membership/orientation_exports.py               # CSV + Orienter column
```

---

## 4. Data model

### 4.1 `OrientationAvailability` — add `orienter`

| Field | Type | Notes |
|---|---|---|
| `orienter` | `FK(Member, null=True, blank=True, on_delete=CASCADE, related_name="orientation_availability_rules")` | Whose personal hours these are. **NULL = legacy guild-level rule ("any orienter").** `help_text="The staff member who personally gives orientations during this window. Empty means any orienter (legacy guild hours)."` CASCADE: if the Member row is ever hard-deleted their personal hours go with them — SET_NULL would silently convert personal hours into guild hours, which is a lie. |

`orienter` is **write-once from the UI**: which editor section a row is created in stamps it (my hours → me; lead editing Bob's → Bob; legacy guild rows stay NULL). No orienter-switcher on a row — moving hours between people is delete + re-add, which keeps slot provenance honest.

`__str__` gains the person: `"Woodshop orientation: Tuesday 18:00 (Bob)"` / `"… (any orienter)"`.

New queryset helpers: `OrientationAvailability.objects.for_orienter(member)` and `.guild_level()` (`orienter__isnull=True`) — the two filters the editor sections and specs use.

### 4.2 `OrientationSlot` — add `orienter`

| Field | Type | Notes |
|---|---|---|
| `orienter` | `FK(Member, null=True, blank=True, on_delete=SET_NULL, related_name="orientation_slots_offered")` | Who this concrete slot is with. NULL = guild slot, any orienter. SET_NULL (not CASCADE): a historical slot with completed bookings must survive its orienter's deletion — it degrades to a guild slot, and `oriented_by` on the booking (already SET_NULL) keeps the record. `help_text="The staff member this slot is booked with. Empty means any orienter."` |

Property: `with_label -> str` — `"with Bob"` or `""` for guild slots; the single place templates and email copy get the phrase, so it can't drift. **Defensive:** first token of `display_name`, falling back to the full `display_name` when it is empty or has no splittable token (never an IndexError, never `"with "`). **Duplicate first names:** when two *current* orienters in the same guild share a first name, disambiguate with a last initial ("with Bob P.") — the property stays cheap (first-name form); the disambiguation map is computed once where the slot list context is built (guild page view / dashboard), since only a guild-wide view can know about the collision.

**Departed-orienter guard (prevents zombie re-booking):** `is_bookable` (and the member-facing `bookable()` queryset via an EXISTS exclusion against `guild_lead` / `staff_memberships`) gains one more condition — a **personal** slot whose orienter is no longer in the guild's `leadership_members()` is **not bookable**. Without this, a departed staffer's surviving booked slot would reappear in the member list "with Bob" the moment its booking is declined or cancelled (the stale-rule skip in `generate_slots` only stops *new* slot creation; `is_bookable` at `models.py:7816` never checks staff status). Existing bookings on such a slot are untouched — the guard blocks *new* bookings only, and `book()` inherits it via `is_bookable`.

No constraint changes (see §2). No index needed — slot queries are already guild-scoped and small.

### 4.3 `OrientationBooking` — no schema change

`oriented_by` default shifts in behavior only (§5): on `confirm()` / `mark_completed()` the fallback chain becomes `explicit arg → slot.orienter → guild.guild_lead`.

### 4.4 Migration

One migration, two nullable FK adds. Fully reversible (`RemoveField` twice) — no data migration; every existing row is already correct as `NULL` = guild-level. Run `manage.py check` after (index-name cap, E034).

---

## 5. Business logic (fat models / service)

### Model deltas

- `OrientationBooking.confirm(*, oriented_by=None)` — default becomes `oriented_by or self.slot.orienter or self.guild.guild_lead`. Same in `mark_completed()`'s fallback. The respond view keeps passing the acting member explicitly, so a co-lead who confirms on Bob's behalf is still credited correctly; the change matters for the no-login token confirm (no actor) and the auto-complete cron.

### Service deltas (`membership/orientations.py`)

- **`generate_slots`** — two additions, both inside the existing rule loop:
  1. Thread the orienter: `defaults={…, "orienter": rule.orienter}` in the `get_or_create`.
  2. Skip **stale personal rules**: if `rule.orienter` is set but that member is no longer in `rule.guild.leadership_members()`, skip the rule (belt-and-braces for a lead-FK change or any staff removal that didn't go through the hub view). Guild-level rules (`orienter=NULL`) generate exactly as today.
  Idempotency is untouched — the key stays `(availability, starts_at)`.

- **`retire_rule(rule) -> tuple[int, int]`** *(new)* — the delete path for any rule (personal or guild): delete the rule's **future, GENERATED slots that hold no seat-holding bookings**, leave the rest in place (they keep their `orienter`; `availability` goes SET_NULL) to be handled individually via the Upcoming Slots card (§6.4) and the existing `cancel_slot` flow, then delete the rule. Returns `(open_slots_removed, kept_with_bookings)` so the §6.1 success message can carry real counts. This replaces today's silent slot-stranding.
  **Paid-spec seam (deliberate):** `2026-08-26-paid-orientations.md` introduces `PENDING_PAYMENT` checkout holds that are **excluded from `active()`** and visible only through its `seat_holding()` scope, and `OrientationBooking.slot` is `on_delete=CASCADE` (`models.py:7898`) — so a "zero `active()` bookings" test would delete a slot whose only booking is a live paid checkout, **cascading the hold away and leaving the payment webhook with money and no booking**. The keep/delete guard is therefore `slot.bookings.seat_holding().exists()` once that scope lands; until then (this spec building first) the guard is `active()`, with a required follow-up in the paid spec's build to switch it — named in both specs so neither builder misses it.

- **`retire_orienter(guild, member)`** *(new)* — run `retire_rule` over the member's personal rules in that guild. **Trigger gate:** called by `guild_staff_remove` (`hub/views.py:931`) *after* the row delete, and **only when the member is no longer in `guild.leadership_members()`** — staff can hold multiple role rows (Bob = Treasurer *and* Orienter), and removing his Treasurer row must not nuke his hours while his Orienter row stands. Their **booked** future slots stay theirs — the ex-staffer may still honor them, or a lead cancels each via the §6.4 slot Cancel (which emails affected members). The staff-remove flash message gains a sentence when booked future slots remain: "They still have N upcoming booked orientation(s). Cancel them from the Upcoming Slots card on the Orientations tab if they won't be run."

- **Request routing (`_emit_lead_request`)** — personal slot (`booking.slot.orienter` set): email recipients become **the slot's orienter + the guild lead** (deduped) instead of the full leadership fan-out; guild slot: unchanged (full `leadership_members()`). Pass `{"slot": booking.slot}` into the emit context; the `guild_orienters` resolver (`core/events/resolvers.py:266`) gains: when the context carries a slot with an orienter, return `[slot.orienter, guild_lead]` (deduped) — otherwise its current behavior. This satisfies "goes to that orienter, leads still see everything" without a new trigger key, so preferences and the settings matrix are untouched.

- **Custom requests** (`request_custom_orientation`) — unchanged: a member proposing their own time isn't picking a person, so the manual slot stays `orienter=NULL` and fans out to the full leadership as today. Whoever confirms claims it (`oriented_by` = the confirmer).

- **Copy deltas** (builders only; see §7 for the exact strings): member request/confirmed emails and the `.ics` description gain the `with_label`.

### Permissions (`membership/permissions.py`)

New helper, mirroring the house style:

```python
def can_edit_orienter_hours(request, guild, orienter: Member | None) -> bool:
    """Own hours: anyone who may manage the guild's orientations. Someone else's
    hours, or the guild-level rows: admin/officer or the guild_lead holder."""
```

- `orienter == request member` → `can_manage_orientations(request, guild)`.
- Anyone else's rows, or `orienter is None` (guild rows) → `is_effective_staff(request)` or `member.pk == guild.guild_lead_id`.

**Flagged honestly:** this is the first intra-staff authority distinction in the codebase (the standing rule is "every staff role = lead authority", `membership/CLAUDE.md`). It is a locked decision here; if it later chafes, widening others'-hours editing to all staff is a one-line change in this helper, and §10 records the tension.

---

## 6. UI / UX

The Orientations tab of `guild_edit.html` is restructured into ordered cards: settings form (unchanged) → My Orientation Hours (§6.1) → All Orientation Hours (§6.2, leads) → Guild Hours legacy (§6.3, conditional) → Upcoming Slots + Add A Slot (§6.4). Verify **both themes** on every screen below.

**Entry path for a plain orienter** (stated so nobody has to guess): log in → the guild's page → "Manage" → **Orientations** tab → My Orientation Hours. Plus one nudge where orienters already look: §6.6's dashboard banner for staff with zero personal rules.

### 6.1 Staff hours editor — "My Orientation Hours"

- **Screen / partial:** `templates/hub/guild_edit.html`, Orientations tab (`section === 'orientations'`). The existing "Recurring hours" card is retitled and rescoped.
- **Layout & container:** inline `hub-card` form (4+ fields per row → inline, per the interaction table), its own `<form>` POSTing to `hub_guild_orientation_hours_save` exactly like today, now with a hidden `orienter` scope field.
- **Who sees it:** every viewer who passes `can_manage_orientations`. Card heading **"My Orientation Hours"**, hint: "Weekly windows when you personally can give orientations. We turn them into bookable slots automatically — members book you by name."
- **Components used:** `components/form_field.html` for weekday/start/end/seats, the formset pattern verbatim from the current editor (`guild_edit.html:368-434`): `extra=0`, hidden `empty_form` `<template>`, management form.
- **Controls, named explicitly:**
  - **"+ Add hours"** button — clones the template, swaps `__prefix__`, bumps `id_rules-TOTAL_FORMS` (existing inline JS, unchanged).
  - **Per-row Delete** — real `pl-btn pl-btn--danger pl-btn--sm`, `margin-top:0.75rem` (saved rows); cloned rows get **Remove** (DOM removal only). **Deviation from the canonical instant-delete pattern, on purpose:** because delete now actively removes future open slots via `retire_rule`, the button first opens a `components/confirm_modal.html` — "Delete these hours? Upcoming open slots from this window will be removed. Slots someone already booked stay until you cancel them." — whose Confirm flips the hidden `DELETE` field and `requestSubmit()`s (so the whole page still saves, no lost work). The success message carries `retire_rule`'s counts: "Hours deleted. Removed 6 upcoming open slots. 1 booked slot kept. Cancel it from the Upcoming Slots card."
  - **Save** — `pl-btn pl-btn--primary`, labeled **"Save"**, last thing in the form (Rule 21), sitting next to "+ Add hours" (the existing footer rhythm). Success → redirect back to the tab + Django message "Hours saved."
  - The `is_active` toggle per row stays (`form_field.html` auto-toggle) — "pause without deleting."
  - **Time fields:** already compliant — `OrientationAvailabilityForm` (`hub/forms.py:1580`) renders start/end as half-hour `<select>` dropdowns via `half_hour_time_choices`, with `_seed_time_choice` preserving off-grid values. Rule 20 is satisfied; nothing to change.
- **Server side:** the save view **reads the posted scope field first** (hidden `orienter_scope`), then binds **only the matching prefix + queryset** (`rules` scoped to `orienter=target`, or `guild_rules` scoped to `orienter__isnull=True`) — binding the wrong prefix against a mismatched management form is a crash, not a validation error, so scope selection precedes any formset construction. The view stamps `form.instance.orienter = target` on new rows before save; gate is `can_edit_orienter_hours`. A POST whose scope the viewer can't edit → 403.
- **States:** empty — "No hours yet. Add your first window and members can start booking you." Validation error (end ≤ start, from the existing CheckConstraint's form mirror) **re-renders `guild_edit` with the Orientations tab active and the `?orienter` scope echoed back into context** (from the posted scope field), so an invalid edit-on-behalf save still shows "Editing Bob's Hours" with its field errors — never a silent snap back to "My Hours" with Bob's broken rows bound to the wrong heading. Nothing saved. Success — message + regenerated slots visible immediately (`generate_slots(guild=guild)` on save, as today).
- **Dark + light:** tokens only; the row cards keep the existing `rgba` inset styling; no new controls needing input-token scoping beyond what the current editor already has.
- **Mobile:** rows already `flex-wrap`; four fields wrap to two lines; buttons full-width tap targets. Unchanged behavior, verified.

### 6.2 Lead overview — "All Orientation Hours" + edit-on-behalf

Decision: **read-only overview grouped by person, with a per-person "Edit Hours" link that reloads the tab scoped to that person** (`?tab=orientations&orienter=<pk>`) — chosen over an inline person-switcher because it reuses the §6.1 formset unchanged (one formset on the page, one save path, no cloned-template collisions between prefixes), and leads do this rarely.

- **Screen / partial:** same tab, a `hub-card` **below** My Hours, visible only when `can_edit_orienter_hours(request, guild, None)` (lead/admin).
- **Layout:** heading **"All Orientation Hours"**, hint "Everyone on staff who can give orientations, and when." One group per `leadership_members()` entry: name + small `profile_photo` avatar (initials fallback), then their rules as read-only lines ("Tuesday · 6:00–8:00 pm · 4 seats" with a muted "paused" tag when `is_active=False`), or muted "No hours published" — so a lead sees coverage gaps at a glance.
- **Controls:** per person, an **"Edit Hours"** `hub-btn hub-btn--sm` linking to `?tab=orientations&orienter=<pk>`.
- **Edit-on-behalf mode:** with `?orienter=<pk>` (and permission), the §6.1 card renders as **"Editing Bob's Hours"** with a "← Back To My Hours" link (drops the param). Same formset, same save endpoint, hidden scope field carries the pk. Without permission the param is ignored (falls back to self) — no error page needed.
- **Former Staff group:** when orphan personal rules exist (`orienter` set but that member no longer in `leadership_members()` — e.g. removed through Django admin, which never runs `retire_orienter`), the overview appends a **"Former Staff"** group listing those people and their rules with the same "Edit Hours" links — visible and retirable through the normal editor, not magic-URL-only. Hint under the group: "These hours belong to people no longer on staff. They no longer generate slots. Delete them when you are ready."
- **States:** guild with no staff → the overview shows just the lead's row. Empty overall → each row's "No hours published." Former Staff group absent when no orphans exist.
- **Dark + light / mobile:** avatar + name row wraps above the rules list on narrow screens; read-only lines are plain text, no tables to degrade.

### 6.3 Legacy guild hours — "Guild Hours (Any Orienter)"

- **Screen / partial:** same tab, a third `hub-card`, rendered **only when legacy rows exist** (`orientation_rules.guild_level()` non-empty) — new guilds never see it, so the feature reads as purely personal going forward.
- **Who:** leads/admins get the full §6.1 formset pattern scoped to `orienter=NULL` (`?orienter=` handled as the guild scope via a distinct prefix `guild_rules`, hidden scope field empty), **without a "+ Add" button** — keep/edit/retire only; new guild-level rows can't be created. Non-lead staff see the card read-only with hint "These are shared guild hours any orienter can run. Only the guild lead can change them."
- **Controls:** per-row Delete (retires via `retire_rule`, behind the same `confirm_modal` as §6.1 but with the one-way wording below), `is_active` toggle, **"Save"** button last.
- **The one-way door, named out loud:** once the last guild-level rule is deleted this card disappears and **shared recurring hours can never be recreated** — new recurring hours are always personal. That sunset is deliberate: every orientation should have a named person accountable for running it (the whole point of this feature). Genuine shared coverage still exists as the **"Any orienter" one-off slot** in the Upcoming Slots card (§6.4). The UI says all of this where it matters:
  - Card hint: "These shared hours predate personal hours and cannot be recreated once deleted. New recurring hours always belong to a person. For occasional shared coverage, add an Any orienter slot in Upcoming Slots."
  - Delete confirm modal: "Delete these shared hours? Shared recurring hours cannot be recreated. Upcoming open slots from this window will be removed; booked slots stay until you cancel them."
  - Success message on deleting the **last** guild rule: "Shared hours deleted. From now on recurring hours are personal. Use an Any orienter one-off slot for shared coverage."
- **States:** section absent when no legacy rows (its empty state is nonexistence). Save/validation identical to §6.1.

### 6.4 Upcoming Slots + Add A Slot (new card — closes the shipped hole, gap 4)

`guild_orientation_slot_add` / `guild_orientation_slot_cancel` exist as views + URLs but were never rendered anywhere (§2 gap 4). This card is their first UI; **both endpoints are reused unchanged**. It is also the destination every retirement message in §5/§6.1/§6.3 points at, so it must exist for those flows to make sense.

- **Screen / partial:** last `hub-card` on the Orientations tab, heading **"Upcoming Slots"**, hint: "Every bookable slot for the next 8 weeks, from everyone's hours plus one-offs."
- **The list:** `guild.orientation_slots.upcoming()` ordered by `starts_at`. Each row: date + time, the with-chip (avatar + name, or muted "Any orienter"), seats as "2 of 4 booked" (`seats_taken`/`seats`), a muted source tag ("recurring" / "one-off"). Long lists reuse the member partial's Alpine 5-per-page pager pattern (`guild_orientation.html:36`) so the card stays bounded.
- **Per-row Cancel:** `pl-btn pl-btn--danger pl-btn--sm` opening `components/confirm_modal.html` (per-slot `confirm_id`, same `slot.pk` suffix idiom as the member partial's book modals) → POST to the existing `hub_guild_orientation_slot_cancel`. Modal message names the consequence: "Cancel this slot? Anyone booked on it will be emailed that it is off." Rows with zero bookings get the lighter message "Cancel this open slot?". Success = the existing redirect + message; the cancelled slot drops from the list (`upcoming()` excludes cancelled).
- **Who cancels:** anyone passing `can_manage_orientations` (the endpoint's existing gate) — cancelling a colleague's slot is operational triage, not hours-editing, so the §5 own-hours restriction deliberately does not apply here.
- **Add A Slot:** below the list, a toggle-revealed inline form (`x-show`, closed by default — secondary form per the interaction table), button "**+ Add A Slot**". Fields via `form_field.html`: date (Rule 14 dark-mode handling), start time + duration (the shared half-hour / duration `<select>` choices, Rule 20), seats, location, and the **Orienter** select — choices = the guild's `leadership_members()` plus "Any orienter (guild slot)", **default = the acting member**. The select is shown to lead/admin; plain staff get it fixed to themselves (hidden field + static line "Runs with: you") — consistent with the §5 editing scope. `clean_orienter` validates leadership membership: "Pick someone on this guild's staff." `OrientationSlotForm` (`hub/forms.py`) grows the field; since the form has never been surfaced, its time inputs are brought onto the shared half-hour choices as part of rendering it. Submit button "**Save**" (last, Rule 21) + a "Cancel" that collapses the form. POSTs to the existing `hub_guild_orientation_slot_add`; feedback = existing message + redirect to the tab.
- **States:** empty — "No upcoming slots yet. They appear as soon as hours are saved, or add a one-off below." Validation errors surface via the view's existing per-field messages. A full slot still lists (it is real inventory) with its "4 of 4 booked" count.
- **Dark + light:** with-chip and source tag are `pl-` classes on theme tokens; the date input gets the Rule 14 picker treatment; selects rely on the `.hub-form-group` field scope.
- **Mobile:** rows flex-wrap (chip drops under the time); the add form is single-column at narrow widths; Cancel buttons stay full-size tap targets.

### 6.5 Member slot list (guild page)

- **Screen / partial:** `templates/hub/partials/guild_orientation.html`.
- **Change:** each row gains a **"with" cell** between time and action: small avatar (`profile_photo`, 20px, initials fallback) + "with Bob" from `slot.with_label`; guild slots show nothing extra (not "with anyone" — silence is cleaner). The header row gains a blank third label cell to keep the grid. **Ordering stays by `starts_at`** (model default) — date-grouped in reading order, never grouped by orienter; two people's 6:00 pm slots sit adjacent and the name disambiguates.
- **Booking status card** (member has a live booking): line gains "· with Bob" when personal; the "awaiting confirmation from the guild lead" copy becomes "awaiting confirmation from **Bob**" (personal) / "from the guild" (guild slot).
- **Confirm modal copy:** "We'll send your request to **Bob** to confirm." (personal) / current copy (guild slot).
- **States:** empty (no bookable slots) — sharpen the existing line to "No one has posted orientation times yet — check back soon." Full slots keep the "Full" label. Paused/oriented/booked branches unchanged.
- **Dark + light:** avatar is an `<img>`/initials chip with a `pl-` class using theme tokens; no form controls added.
- **Mobile:** the 5-per-page pager already bounds height; the with-cell wraps under the time on narrow widths (flex-wrap on the row class, existing `pl-orient-slots__*` styles extended).

### 6.6 Respond page + dashboard

- **`orientation_respond.html`:** add a detail line "Runs with: Bob" (or "Any orienter") so a co-lead confirming on someone's behalf sees whose slot it is. Actions unchanged.
- **`orientations_dashboard.html`:**
  - Upcoming cards: append "· with Bob" after the guild name (muted, only when personal).
  - Main table: new **"Orienter"** column (plain `<th>`, like "Oriented by") showing `b.slot.orienter.display_name|default:"—"`; sits between Guild and Status. `select_related` gains `"slot__orienter"` (no N+1).
  - Filters: **no new filter** — the existing "Mine" scope plus search covers the need; a per-orienter filter is deferred (§10).
  - CSV export (`orientation_exports.py`): new "Orienter" column in the same position.
  - **"Post your hours" nudge (the second entry point, §6 intro):** for a viewer who staffs or leads at least one guild but has **zero personal rules anywhere**, a slim dismissable-free banner above the Upcoming section: "You have not posted any orientation hours yet. **Post your orientation hours**" — the link goes to that guild's Orientations tab (one staffed guild: direct; several: one link per guild, comma-separated guild names). Disappears the moment they have a rule.
  - Mobile: the table already lives in the dashboard's scrolling container; one more column rides along.

---

## 7. Notifications / emails / activity

No new triggers, no new templates — **routing + copy deltas only** on the existing seven emails. `.txt` and `.html` change together in every case.

| Email | Delta |
|---|---|
| Request received (member) | Body line gains "with Bob" (guarded on `slot.orienter`); `.ics` DESCRIPTION gains "With Bob." Subject unchanged. |
| New request (lead/orienter) | **Routing:** personal slot → the slot's orienter + guild lead (deduped) instead of full leadership; guild slot → unchanged. Body opens "Sam requested an orientation **with you**" when the recipient is the orienter (single shared body is fine: "…requested one of Bob's orientation slots"). Accept/decline signed links unchanged. |
| Confirmed (member) | "Bob confirmed your orientation" / body "with Bob"; `.ics` as above. |
| Declined / Cancelled (member) | "with Bob" in the detail line where the slot is named. Routing unchanged (member-facing). |
| Cancelled (orienter in-app ping) | Already routes through `guild_orienters` — the same resolver change scopes it to the slot's orienter + lead for personal slots. |
| Thank-you / guild-welcome | Untouched. |

In-app: the `orientation_requested` bell row for a personal slot goes to the orienter + lead via the resolver change (§5); `orientation_update` (member-facing) unchanged. `SiteActivity` kinds unchanged — the booking target already reaches the orienter via `oriented_by`.

All subject-noun links, absolute URLs, branded shell, tz agreement: already in place; the copy edits ride inside existing templates.

---

## 8. Build order (phased; each phase ships green — full suite + lint + `manage.py check`; local mypy is broken, CI runs it)

1. **Models + logic.** The two `orienter` FKs + migration; queryset helpers; `with_label`; `confirm`/`mark_completed` default chain; `generate_slots` threading + stale-rule skip; `retire_rule` / `retire_orienter`; `can_edit_orienter_hours`; resolver + `_emit_lead_request` routing. Factories updated. Model/service specs (§9). No UI change yet — everything stays NULL-safe, so the app behaves exactly as today.
2. **Editor UI.** Orientations tab restructure: My Hours (scoped formset + hidden scope field, delete confirm modals with counts), All Orienters overview + `?orienter=` edit-on-behalf + Former Staff group, Guild Hours legacy card (one-way door copy), **the new Upcoming Slots + Add A Slot card** on the existing slot endpoints; hours-save view scope-first binding + gate + scope-preserving invalid re-render; staff-remove hook to `retire_orienter` (leadership-after-delete gate). View/template specs.
3. **Member-facing + dashboard + email copy.** Slot-list "with Bob" + avatar, status/modal copy, respond-page line, dashboard column + CSV, the seven email copy deltas (`.txt` + `.html` in lockstep), `.ics` description. Template-state specs.
4. **Housekeeping.** Bump `plfog/version.py` VERSION + **one** member-friendly CHANGELOG entry for the whole feature (fold into the current unreleased line's entry if this ships inside one), e.g.: *"Book your orientation with a real person — guild staff now post their own orientation hours, and when you book a time you'll see exactly who you're meeting."* Remember: the Discord announce fires automatically on merge when VERSION changes — curate before merging.

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py` under `membership/spec/` + `hub/spec/`, `describe_*`/`it_*` only (never `context_*` — it silently collects nothing), factory-boy, 100% branch gate. Slot fixtures at `now + timedelta(days=2)` (the tz date-window gotcha).

- **Generation:** two active rules, same guild, same weekday/time, different orienters → two slots, each carrying its rule's orienter; re-run → 0 created (idempotent); guild-level rule still generates with `orienter=None`; rule whose orienter left leadership → skipped; closed guild still skipped.
- **Retirement:** `retire_rule` deletes only future+generated+unbooked slots and returns correct `(removed, kept)` counts (past slots, manual slots, and booked slots survive; booked slot keeps its orienter, `availability` nulls); it **spares a slot whose only booking is a seat-holding hold** (written against the paid spec's `seat_holding()` scope when present, the `active()` fallback otherwise — the §5 seam); `retire_orienter` sweeps all the member's rules in one guild and no other guild's; staff-remove **does not** fire it when the member still holds another staff row in the guild (Treasurer + Orienter case), fires it when the last row goes, and flags remaining booked slots in the message.
- **Departed-orienter guard:** a personal slot whose orienter left leadership disappears from the member-facing bookable list and `book()` on it raises; its existing booking is untouched; declining/cancelling that booking does **not** resurrect the slot in the list; guild slots unaffected.
- **Confirm defaults:** token-confirm on a personal slot → `oriented_by == slot.orienter`; guild slot → guild lead; explicit actor still wins; `auto_complete` credits the slot orienter.
- **Routing:** personal-slot request email recipients == {orienter, lead} exactly (dedup when the lead is the orienter); guild-slot request unchanged (full leadership, byte-identical recipients); in-app resolver same split; cancel ping follows.
- **Permission edges:** staff saves own hours OK; staff POST with another's `orienter` scope → 403, nothing saved; lead and admin can save anyone's + guild scope; non-lead staff POST to `guild_rules` scope → 403; `?orienter=` GET without permission falls back to self; slot-add: plain staff can't set another orienter (form forces self), lead can, non-leadership orienter rejected with the form error.
- **Save-view mechanics:** the view reads the scope field before binding and binds only the matching prefix (a POST carrying the `guild_rules` scope never constructs the `rules` formset — no management-form crash); an invalid edit-on-behalf POST re-renders with the Orientations tab active, the "Editing Bob's Hours" heading, and the field errors attached to Bob's rows.
- **`with_label`:** single-token and empty `display_name` fall back safely (no IndexError, never a bare "with"); two current orienters sharing a first name render with last initials in the slot-list context; no collision → plain first names.
- **Upcoming Slots card:** lists upcoming slots with with-chip, seat counts, and source tags; per-row Cancel POSTs to `hub_guild_orientation_slot_cancel` behind the confirm modal (booked slots get the heavier copy); Add A Slot renders, posts to `hub_guild_orientation_slot_add`, defaults orienter to self; empty state.
- **Templates:** slot list shows "with Bob" only for personal slots; status card + confirm-modal copy branch; empty state; overview shows "No hours published", the lead-only Edit links, and the Former Staff group exactly when orphan rules exist; legacy card absent when no guild rules and its last-delete success message names the one-way door; dashboard Orienter column + CSV column + the zero-rules nudge banner (present for ruleless staff, absent otherwise); changelog-renders-everywhere check (no UI-copy string collisions).
- **Emails:** each delta asserted in both `.txt` and `.html`; `.ics` description carries the name; guarded (guild slot emails contain no "with").

## 10. Open / deferred

- **Intra-staff authority split.** `can_edit_orienter_hours` is the first place a plain staff member has less power than the lead. Locked here; if it fights the "staff = lead authority" doctrine in practice, widening is one line — revisit after real use.
- **Per-orienter dashboard filter / member-facing orienter filter.** Deferred until a guild actually has enough orienters to need it; the Mine scope + search cover today.
- **Orienter bios/photos on a picker page** ("choose your orienter" browsing UX) — out of scope; the date-ordered list with names is the whole ask.
- **Recurring shared hours are a one-way door, on record:** deleting the last guild-level rule permanently ends shared *recurring* hours for that guild (§6.3) — deliberate (named accountability per orientation), surfaced in the delete copy, with "Any orienter" one-off slots as the surviving shared mechanism. Revisit only if a guild demonstrates a real recurring shared-coverage need.
- **Capacity coordination between overlapping slots** (two orienters, one physical shop) — explicitly not handled; leads coordinate humanly, as they would a calendar.
- **Payment fields** — none here by design; `2026-08-26-paid-orientations.md` owns price-at-booking and attaches at the slot/settings layer without touching `orienter`. Two named seams for whichever builds second: the `retire_rule` guard switches from `active()` to `seat_holding()` (§5), and this spec's slot-row layout is the base the price chip composes onto (header Related note).
