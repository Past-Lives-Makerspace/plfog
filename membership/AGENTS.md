# membership app

Core domain models for Past Lives Makerspace.

## Models

| Model | Key fields | Notes |
|-------|-----------|-------|
| `MembershipPlan` | name, monthly_price, deposit_required | Tiers (e.g. "Standard $50/mo") |
| `Member` | `_pre_signup_email`, status, member_type, fog_role, membership_plan | Primary actor; links 1:1 to User. Read emails via `member.primary_email` — see the Email Model section below. |
| `MemberEmail` | member FK, email | Pre-signup staging table; migrated to allauth EmailAddress on User link |
| `Guild` | name, is_active, guild_lead FK, about | Interest guild; receives funding votes |
| `VotePreference` | member 1:1, guild_1st/2nd/3rd FK | One per member; auto-syncs to Airtable |
| `FundingSnapshot` | cycle_label, funding_pool, results JSON | Immutable calc; created via `FundingSnapshot.take()` |
| `Space` | space_id, space_type, status, size_sqft | Physical space; read from Airtable |
| `Lease` | GenericFK tenant (Member or Guild), space FK | Active when start_date≤today and end_date null/≥today |
| `Location` | name, guild FK, note, shares_space_with (symmetric M2M), is_active | A named area of the building (#616). `ClassOffering`, `CommunityEvent`, `OrientationType` and `Equipment` carry a nullable `area` FK to it (label "Location"); slots and reservations take their type's or equipment's. `services/location_status.guild_location_statuses` lights a guild's locations on its page in a fixed number of queries. Managed at `/manage/locations/`. |
| `EventRSVP` | event FK, member FK (nullable), name, email, phone, party_size (1 to 6), wants_updates, source, checked_in_at, note, eventbrite_order_id | One person's RSVP to a `CommunityEvent`, one row per series (#831 part 4, migration `0238`). A member row (unique on event and member) or a guest row with no member (unique on event and lowercased email; `ck_eventrsvp_member_or_email` requires one). `CommunityEvent.rsvp_guest` upserts a guest by email, `member_rsvp_for_email` finds a going member by primary email, `set_member_party_size` resizes a member's row, `rsvp_people_count` sums `party_size` with hidden members left out, `guest_list` is the staff roster. A guest's name never reaches other members: the event page and `attendees_field` fold guests into "and N guests". `reconcile_interested` touches member rows only. |
| `OrientationDayOff` | orienter FK (Member), date, note, created_at; unique on (orienter, date) | A day an orienter is away (#767). It belongs to the person, not a guild: `orientations.generate_slots` skips the day for every rule of theirs (`_horizon_spans`) in any guild or on any equipment, a shared rule with no orienter is unaffected, and saving one runs `orientations.cancel_day_off` (fixed slots through `cancel_slot`, open windows cancelled with their booked segments; a generated time nobody was emailed about is detached so deleting the day off lets the next run recreate it). Managed from the Days Off modal on the Orientation Schedule card (`hub/day_off_views.py`), gated like Edit Hours. |

## Email Model — Three Stores (IMPORTANT)

Three places an email can live for a Member. Future agents MUST understand which is authoritative when. See `docs/superpowers/specs/2026-04-07-user-email-aliases-design.md` for the full rationale.

| Store | Role |
|---|---|
| `Member._pre_signup_email` (DB column `email`, accessed via `db_column="email"`) | Source of truth ONLY when `Member.user` is None (Airtable-imported members who haven't signed up yet). |
| `allauth.account.EmailAddress` | Source of truth for any Member with a linked User. Owns login, verification, and the primary flag. |
| `User.email` | Mirror kept in sync by allauth. Never read or write directly from app code. |

### Reading "the" email
- Use `member.primary_email` (property). It returns the live value: primary `EmailAddress.email` for linked members, `_pre_signup_email` otherwise, with a final fallback to `user.email`.
- **Exception:** `airtable_sync/` reads `_pre_signup_email` directly because Airtable is the external source of truth for unlinked members and we don't want sync to re-enter allauth.

### Writing (user-facing)
- Members manage their own emails at `/accounts/email/` (themed `templates/account/email.html` over allauth's built-in `account_email` view).
- Admin: the `MemberEmailInline` is **only** shown for unlinked members. Once linked, it's hidden because allauth.EmailAddress is now the truth.

### Login
- Allauth login-by-code looks up any verified `EmailAddress` row, so any verified alias works automatically.
- Pre-signup aliases imported into `MemberEmail` are promoted to `EmailAddress` when the user signs up — handled by `MemberEmail.objects.migrate_to_user(user)`, called from the `ensure_user_has_member` signal.

## Airtable Sync

`Member`, `Space`, and `Lease` are **read from Airtable** via `airtable_pull` management command. They do NOT sync back — treat as read-only from Django's perspective. `airtable_record_id` fields link to Airtable rows.

`VotePreference` and `FundingSnapshot` sync **to Airtable** on save (outbound only).

## Fog Roles → Django Permissions

`Member.set_fog_role()` calls `sync_user_permissions()` which sets:
- `admin` → `is_superuser=True`, `is_staff=True`
- `guild_officer` → `is_superuser=False`, `is_staff=True`
- `member` → both False

Never set `is_staff`/`is_superuser` directly — always go through `set_fog_role()`.

## Guild Leads — edit authority

A guild's lead is **only** the `Guild.guild_lead` FK (→ Member). That FK alone grants edit rights — no FOG role, Django group, or `is_staff` flag required. `fog_role` (admin / guild_officer) is a *separate*, cross-guild staff tier and keeps working independently.

- **One source of truth for "who may edit what":** `membership/permissions.py` — `can_edit_guild(request, guild)`, `can_edit_class(request, offering)`, `can_edit_category(request, category)` (all `view_as`-aware). For role-based checks (commands, model logic, tests) use `Member.can_edit_guild` / `Member.can_edit_class` and `ClassOffering.objects.editable_by(member)`. Don't reimplement these inline in views.
- Never gate a guild-lead surface on `is_staff`, `fog_role`, or `member_type` — that reintroduces the drift that left real leads unable to save. `MemberType.GUILD_LEAD` is an Airtable label only and grants nothing.
- Guild was removed from the Django admin (v1.6.0). Assign a lead with `manage.py set_guild_lead --guild <name|id> --member <email>` (it warns if that member has no linked user). Detect drift with `manage.py audit_guild_leads` (leads with no login, inactive leads, guilds with no lead).

### Guild Staff (co-leads, secretaries, treasurers, orienters)

Beyond the single `guild_lead` FK, a guild has `GuildStaffMembership` rows (`role` ∈ co_lead / secretary / treasurer / orienter), managed by leads/staff on the **Staff tab** of the guild edit page. **Every staff role grants the same authority as the lead, except class review** — `can_edit_guild`/`can_edit_class`/`can_manage_orientations` all treat staff like the lead, and `editable_by` includes staffed guilds. Lead-facing orientation requests fan out to `Guild.leadership_members()` (lead + all staff, deduped). Class review (#816) belongs to the lead and the staff marked `can_review_classes` (Class Reviewer, set per member on the Staff tab by the lead or an admin through `Guild.set_class_reviewer`, gate `permissions.can_set_class_reviewers`): `Guild.class_reviewers(offering)` (the class's own instructors left out unless one is the lead) drives the `class_review_requested` resolver and Remind lead, `Member.class_reviewed_guilds` drives `awaiting_guild_lead`, `Guild.reviews_classes` drives `ClassApproval.may_be_decided_by` on the tokenized review page. The former orientation-only `Guild.orienters` M2M was folded into the `orienter` staff role (migration `0049`); orienters now get full lead permissions. Use `Member.is_guild_staff` / `Member.staffed_guilds` and `Guild.is_staffed_by` / `staff_by_role` / `leadership_members`.

**Vocabulary (keep it straight in copy and code):** *lead* = the single `Guild.guild_lead` holder; *staff* = a `GuildStaffMembership` role holder; *guild leadership* = lead + staff together (the term the resolvers use). These are **per-guild**. Never call any of them a "Guild Administrator" — "Administrator" is reserved for the site-wide Admin Capabilities (CMS / Space & Cubby / Discount Code / Calendar / Billing Administrator, plus the action-only Refunds grant). A surface whose audience is lead + staff should say "lead or staff," not "lead" (that undersold who can act and hid a real authorization bug on the orientations dashboard).

## Admin Capabilities

`AdminCapability` grants a member a **scoped admin duty** without promoting them to the full `fog_role=admin` tier. Each capability does two things at once: it **routes** the matching approval/alert notifications to the holder, and it **grants the action** (the holder can actually approve/decline that object type).

| Capability (`AdminCapability.Capability`) | Routes these notifications | Grants this action |
|---|---|---|
| `CLASS_APPROVER` (CMS Administrator) | `class_review_requested` (lead-less categories), `class_validation_requested` | approve/validate classes |
| `SPACE_APPROVER` (Space & Cubby Administrator) | `space.lease_requested`, `space.cubby_requested` | review space requests (`hub._map_reviewer_scope` grants admin-level review) |
| `DISCOUNT_APPROVER` (Discount Code Administrator) | `discount_code.requested` | approve any discount code (`DiscountCode.approver_for` → `approves_any`) |
| `EVENTS_APPROVER` (Calendar Administrator) | `event.submitted`, `meeting.item_proposed` (site-wide/council) | review calendar proposals (`hub._reviewer_guild_scope` grants admin-level review); open Manage Events (`events_admin_access_required`, #831) with its Duplicate and Unpublish; edit, delete and retry sync on site-wide events (`can_administer_events`) |
| `BILLING_APPROVER` (Billing Administrator) | `billing.charge_failed_admin`, `refund_failed`, `billing.late_fee_orphan_payment`, `membership.orientation_orphan_payment` | sees the admin Payments dashboard (`billing_admin_access_required`) |
| `REFUNDS` (Refunds) | (action-only — routes nothing) | issue/retry Stripe refunds (`refund_authority_required`); opens no new pages on its own |
| `EQUIPMENT` (Equipment Administrator) | only the fallback of `orientation_requested`, `equipment.reservation_made` and `equipment.reservation_needs_approval` (#748) for equipment with no managers and no guild leadership (#746); a holder hears nothing about equipment someone else runs | create equipment of every kind and manage all of it site-wide (`can_create_equipment` / `can_manage_equipment` site tier) |
| `SPACE_MANAGER` (Space Manager) | (action-only — routes nothing) | create rooms and spaces only (`creatable_equipment_kinds`, #502); manages only what they add, through an `EquipmentStaffMembership` row the add view writes |
| `EVENT_CREATOR` (Can create events) | (action-only — routes nothing) | open the Calendar's event composer (`/events/add/`, the same "+ Add event" button an admin sees) whatever `member_event_policy` says; every event they add is review-first (`permissions.event_create_mode` → `REVIEW`): it enters the existing review queue and an admin or Calendar Administrator's approval announces it. Admins and `EVENTS_APPROVER` holders publish directly (`DIRECT`) and may edit, delete and retry sync on site-wide events (`can_administer_events`). |
| `WEBMASTER` (Webmaster) | `automation.failed` (a scheduled job raised; sent from `core.scheduled_jobs.record_run`, at most once per job per Pacific day) | (alert-only — grants nothing; the alert links to the Automations tab, which stays `fog_admin_required`) |

Helpers on `Member`: `has_admin_capability(cap)` (the authorization gate) and `sync_admin_capabilities([...], granted_by=…)` (reconcile-to-set, used by the admin member-edit form). Assign/revoke on the **Permissions tab** of the hub Member edit page (`hub.forms.MemberCapabilitiesForm`, admin-only); a full admin can also self-grant from the View As dropdown (`hub.views.view_as_capability_set`).

**Routing mechanics (`core/events/resolvers.py`):** a capability resolver (e.g. `class_approvers`) returns ONLY the capability holders, tagged `capability:<name>`. The capability is the master switch: a `fog_role=admin` member who does not hold it receives nothing (and does not see the row on their settings page — see below); they can self-grant it to opt in. The `guild_leadership_or_{class,events}_approvers` resolvers **compose** (guild present → that guild's leadership ONLY; guild absent → the capability holders) — never a union. Migration `0118` backfills every existing admin with every capability so nobody loses a blanket notification on rollout.

**Settings-page grouping (`core/events/settings_matrix.py`):** every staff/leadership/admin event is collected into one **"Admin / Permissions"** section (rendered first), out of the member-facing categories, and grouped inside it by the permission that brings it (Admin, the eight capability groups by their labels, Guild leadership, Equipment manager; a row shows once, under the first group the viewer holds, `_GROUPS_BY_RECIPIENT`). Each row is shown per-recipient: a capability row only to a holder of that capability, a role row only to that role (`_eligible_for`) — so what the page shows equals what the send path delivers. Plain members never see the section; full admins get a "Manage your admin duties" link to their own capability checkboxes. A forced email never moves a row out of its topic; it only padlocks the Email cell.

**Meeting-item proposal decide is NOT capability-gated:** `hub_meeting_proposal_decide` runs through `can_edit_meeting` (full meeting-workspace edit), so an `EVENTS_APPROVER` who isn't also a guild lead/staff/admin is notified but can't act there — left as-is because widening `can_edit_meeting` would over-grant guild-page/class authority.

## Key QuerySet Methods

- `Member.objects.active()` — status=ACTIVE
- `CommunityEvent.objects.published()` / `scheduled()` / `awaiting_review()` / `past()` / `declined()` — one moderation state each (`past()` is the published one-offs that have ended). Every live surface (the Calendar, Home, the public event page, the Google and Discord pushes, the reminders, the Interested sync) keys on one of them, so a `DRAFT` row (#831: a copy from `CommunityEvent.duplicate`, or a live event taken down by `CommunityEvent.unpublish`) is invisible by construction and shows only on Manage Events, the guild page's Events tab (with a Draft pill) and in the composer, where saving it runs `schedule_or_go_live` (`CommunityEventForm.saves_announce`).
- `Member.objects.instructors()` — active members approved to teach, listed on any class or holding an instructor page, signed in or not; the All Instructors announcement audience (#806)
- `Member.objects.paying()` — member_type=STANDARD
- `Member.objects.with_lease_totals()` — annotates active_lease_count, total_monthly_rent
- `Space.objects.available()` — status=AVAILABLE
- `Space.objects.with_revenue()` — annotates active_lease_rent_total
- `Lease.objects.active(as_of=date)` — start_date≤date and (end_date null or ≥date)
- `EquipmentReservation.objects.holding()` / `confirmed()` (#748) — availability reads (overlap, `ensure_blockable`, free starts, the day timeline, the per member cap, the upcoming lists) use `holding()`, which counts a request awaiting approval, and a priced reservation awaiting payment (#749), because each holds its time; "in use right now" reads (`Equipment.availability_line`, the index card badge, the guild location lights, the Reservations calendar feed) use `confirmed()`, because an undecided request is not the member at the machine.

## Priced equipment (#749)

`Equipment.pricing` is Free, Donation based or Hourly (`hourly_rate_cents`, `donation_minimum_cents`, `donation_suggested_cents`, the orientation donation floor and ceiling). `Equipment.charge_cents_for` is the one price engine (rate times booked minutes over 60, half up to the cent) and `checkout_amount_cents` the one amount check. `membership/equipment.py` copies the paid orientation flow by name: `start_reservation_checkout` holds the time `PENDING_PAYMENT` under the equipment lock and opens Checkout (`kind=equipment_reservation`); `finalize_paid_reservation` is the one money in hand transition (webhook, return page, Pay now, the `release_reservation_payment_holds` sweep); an unpaid hold is deleted, never cancelled. `EquipmentReservation` is a `RefundableSource` (`PaymentRefund.reservation`), and `refund_if_paid` refunds a paid row in full on every decline and cancel, apart from any late fee.

`Equipment.payee` (part 2) is Who gets paid: one of the item's managers, or nobody (the whole payment stays with Past Lives). `EquipmentStaffMembership.delete()` clears the pick when that manager is removed. The books and payouts read it in `billing/` (see `billing/AGENTS.md`). `reserve()` re-runs `checkout_amount_cents` under its lock, so a price or a donation minimum raised after the page read never books free.

## Hourly credit (#787)

`EquipmentCredit` is an append only ledger of minutes per member per hourly item; the balance is the sum (`EquipmentCredit.objects.balance_for`). Managers write GRANTED (the Give Credit card) or UNUSED (Credit Unused Time on a started, confirmed booking, never past its length) and TAKEN_BACK (the whole balance); `reserve()` and `start_reservation_checkout()` read the balance under the Equipment lock, store `EquipmentReservation.credit_minutes` and write a negative SPENT row, charging the rate on the rest (`Equipment.checkout_amount_cents(..., credit_minutes=)`), so a covered booking takes the free road. An unpaid hold's SPENT row cascades with the deleted hold; `cancel()` and `decline()` add a RESTORED row in their transaction and refund the paid part as before. Credit is never money: no refund, reconciliation line or payout reads it. Free and donation items show none.

End Early (part 2): with `Equipment.early_end_credit` on (the Pricing card switch, saved only while Hourly), `EquipmentReservation.end_early` lets the member end their own started, confirmed booking under the Equipment lock: `ends_at` moves back to the last half hour mark (never inside the first half hour) and an ENDED_EARLY row credits the time left rounded up to the half hour, in the member's favour (Felix, 2026-10-12). ENDED_EARLY and UNUSED are `EquipmentCredit.UNUSED_KINDS`: they share the per booking cap, and either one blocks the other and any later cancel or decline. No fee, refund or email.

## Moving an orientation booking (#853)

A leader who may run a booking (`hub._require_can_manage_booking`) moves a requested or confirmed, not yet oriented booking (`OrientationBooking.is_movable`, guarded by `ensure_movable`) from Move to Another Time on the request page or the Bookings tab row menu (`hub_orientation_move`, `hub.forms.OrientationMoveForm`). `orientations.move_orientation` (Move it now) puts it on a fresh one-off slot from `_create_custom_slot` with the old slot's orienter and place, confirms it, stamps `moved_by_guild_at` and emails `orientation_moved` with an ICS of the same UID and a later `SEQUENCE`; the slot it left goes through `_tidy_left_slot` in the same transaction: a posted time (MANUAL, GENERATED) is recapped exactly as a decline does, while a booking's own one-off (`OrientationSlot.OWN_SOURCES`: CUSTOM, the source `_create_custom_slot` now stamps on custom and moved times, and FROM_BLOCK) is deleted when empty or cancelled when it keeps history, so a move never leaves it open. A member cancel or a decline still only recaps it, as before. Every move, proposal and answer re-reads the booking under `locked_booking_queryset()` and decides on that row (`proposal_verdict`: already, stale, passed), so a stale instance can never revive a cancelled booking or accept twice; emails go out after commit. A proposal whose time has passed is no longer open (`has_open_proposal`). `propose_orientation_time` (Ask the member first) only records `proposed_starts_at`, `proposed_at`, `proposed_by` and emails `orientation_time_proposed` with Accept and Keep links: `make_action_token` actions `accept_time` / `keep_time` carrying `proposal_key`, so a replaced or answered proposal's link lands on "no longer open". The member's card buttons post to `hub_orientation_proposal_answer` with the same key; both roads run `answer_proposal`, and both answers bell the request's leaders with no email. A new proposal replaces the open one; decline and cancel clear it. Nothing on any of these roads touches money. `moved_by_guild_at` is the late fee switch: `billing.late_fees._charging_policy` and `late_cancel.booking_cancel_warning` never charge or warn on a moved booking, while its paid amount still refunds in full on cancel.

## Event announcement after the response (#845)

`CommunityEvent.publish()` never sends the member announcement in the request: `owe_announcement` stamps `announcement_owed_since` and hands `finish_announcement` to `core.deferred.defer`, which runs it on a background thread once the save commits. A site-wide send is about 30 seconds of email, bell and push, and gunicorn killed the request at 30 after members were already told, so creators saved again (events 29, 30, 31 on 2026-10-18). `finish_announcement` runs `announce()`, then clears the stamp only if it still holds the value it read; the `finish_event_announcements` job (every 15 minutes, not pausable) finishes any event still stamped after `ANNOUNCEMENT_FINISH_GRACE`. Exactly once rests on the `event:{pk}:published` delivery ledger, which every runner shares. Discord `/create`'s email escalation counts its addresses for the reply (`email_announcement_addresses`) and sends through `send_email_announcement` on its own deferred thread, with no cron backstop. Two known gaps: a hard kill between one member's ledger claim and its send strands that one slot, and an interrupted escalation email is not finished.

**Publishing without announcing.** `publish(announce=False)` and `schedule_or_go_live(announce=False)` still run `push_live` but owe nothing, and set `announcement_held` on an event that was never announced (`was_announced`: the #calendar stamp, its message id, or the `event:{pk}:published` delivery ledger). The composer never writes the hold, only releases it on an untick; `CommunityEventForm.announces` carries the switch to `schedule_or_go_live`. A held event stays quiet through every later publish, the #calendar announcer and the reminders skip it, and only `announce_now` (Manage Events' Announce now, one conditional UPDATE) releases it and owes the announcement. The composer offers the switch to `can_administer_events` only, and the API admits `announce: false` on the same capability (`api.permissions.MayPublishWithoutAnnouncing`); the API's `announce` action on a held event goes through `announce_now`. The weekly #calendar digest leaves a held event out, while the portal's Calendar lists it. Before rolling back to a release without 0241, unpublish held events, or the older announcer announces them. `channel_announced_at` alone cannot say "held": the 15 minute announcer posts every published event whose stamp is blank.

## Vote Calculator

`membership/vote_calculator.py` — `calculate_results(votes, paying_voter_count, pool_override)` returns a dict with per-guild allocations. Called by `FundingSnapshot.take()`.

`membership/cycle.py` — `get_cycle_context()` returns current cycle label/dates for template display.

## Signals

`membership/signals.py` — post-save signal on User to sync permissions when user is updated outside of fog_role flow. Prefer calling `member.sync_user_permissions()` directly.

## Factories

`tests/membership/factories.py` — `MemberFactory`, `GuildFactory`, `LeaseFactory`, `MembershipPlanFactory`, etc.
