# #456 part 1 of 3: late cancellation policy, settings and copy (no charging yet)

Issue: https://github.com/Past-Lives-Makerspace/plfog/issues/456. The issue body is the spec
(read it whole; its Expected behavior and the "Part 1 of 3" criteria are the targets). This plan
pins the decisions and the tree facts. Parts 2 and 3 (the fee record, the Stripe payment, the
block, waive and refund) are later PRs; this part ships nothing that charges.

## What ships

1. **Site Settings, Features card**, three fields on `SiteConfiguration` (`core/models.py`, next
   to `my_tab_enabled` at `:634`) with one migration in `core/`:
   - `late_cancel_fees_enabled` BooleanField default False, verbose "Charge late cancellation fees",
     help "When on, guilds and equipment can set a fee for cancelling an orientation or reservation
     inside the notice window. Off means nothing charges anywhere."
   - `late_cancel_notice_hours` PositiveSmallIntegerField default 24, verbose "Cancellation notice
     (hours)", help "How far ahead members are told to cancel. Shown wherever a fee applies."
   - `late_cancel_grace_hours` PositiveSmallIntegerField default 2, verbose "Grace period (hours)",
     help "Not shown to members. A cancel this close to the notice line is still free."
   Add them to `SiteSettingsForm.Meta.fields` (`hub/forms.py`, the `my_tab_enabled` entry) and
   render them in the Features card of `templates/hub/admin/site_settings.html` right after the
   `my_tab_enabled` toggle, through `components/form_field.html`; add the three names to the
   exclusion condition on the generic loop (the long `{% if field.name != ... %}` line near `:186`)
   so they render once. Validation in the form: notice at least 1, grace less than notice.
2. **The guild's fee**: `GuildOrientationSettings.late_cancel_fee_cents` PositiveIntegerField
   default 0 (`membership/models.py:9153`), one migration in `membership/`. On
   `GuildOrientationSettingsForm` (`hub/forms.py`, `class GuildOrientationSettingsForm`) a
   `late_cancel_fee` DecimalField in dollars (max_digits 6, 2 places, required False, placeholder
   "No fee", min 0, step 0.01) mapped to cents exactly the way `OrientationTypeForm.price` does
   (`clean_price` / `save`), blank meaning 0. Rendered in the Booking card of
   `templates/hub/guild_edit.html` (the card at `:428`) after `external_signup_url`, only when the
   site switch is on (`site_config.late_cancel_fees_enabled` from the context processor; confirm
   the name in `core/context_processors.py`).
3. **The equipment's fee**: `Equipment.late_cancel_fee_cents` PositiveIntegerField default 0
   (`membership/models.py`, `class Equipment`), same migration file as the guild column. On
   `EquipmentSettingsForm` (`hub/forms.py`, `class EquipmentSettingsForm`) the same dollars field
   with the same mapping, rendered in the Limits card of `templates/hub/equipment_manage.html`
   (`:166` onward) after `max_active_reservations_per_member`, only when the site switch is on.
4. **One resolver**, new module `membership/late_cancel.py`:
   ```
   @dataclass(frozen=True)
   class LateCancelPolicy:
       fee_cents: int          # 0 means no fee
       notice_hours: int       # what members are told
       grace_hours: int        # never shown
       def applies(self) -> bool: return self.fee_cents > 0
       def is_late(self, starts_at: datetime, *, now: datetime | None = None) -> bool
       @property
       def fee_display(self) -> str   # "$15.00"
   def policy_for(target: OrientationBooking | EquipmentReservation) -> LateCancelPolicy
   def booking_sentence(policy) -> str   # "" when no fee
   def cancel_sentence(policy) -> str    # the late-cancel modal line, "" when no fee
   ```
   `policy_for` reads `SiteConfiguration.load()`; when the switch is off, fee 0. A guild
   orientation booking (type with `guild` set) takes `guild.orientation_settings.late_cancel_fee_cents`
   (0 when no settings row); an equipment owned booking (type with `equipment` set) and an
   `EquipmentReservation` take `equipment.late_cancel_fee_cents`. `is_late` is
   `now < starts_at and starts_at - now < timedelta(hours=notice - grace)`: exactly at the line is
   not late, a started booking is not late. Nothing else computes lateness. Port the shape (not
   the tab wording) of `late_cancel_policy()` and `late_cancel_warning()` from the closed branch
   (`git show origin/fog/late-cancel-fee:membership/equipment.py`) into these two sentence helpers:
   - booking: "Cancel at least 24 hours ahead. Cancelling later costs a $15.00 late fee."
   - cancel (used in part 2's modals; write and spec it now): "This is inside the 24 hour notice
     window, so a $15.00 late cancellation fee applies. You'll get a link to pay it."
   Hours read "1 hour" / "24 hours". No dashes in any sentence.
5. **The copy on the surfaces**, all through `booking_sentence` and only when it is non-empty:
   - Guild page and equipment page orientation booking prompts: the `book-slot-*` confirm modals in
     `templates/hub/partials/guild_orientation.html` (`:127`, `:132`, `:135`) and the equivalents in
     `equipment_orientation.html`; append the sentence to the modal message. The section builder
     `_orientation_sections` (`hub/views.py:524`) puts `late_cancel_sentence` on each section from
     `policy_for`-equivalent inputs (a type, not a booking: add a `policy_for_type(orientation_type)`
     that resolves the owner the same way), so the templates do no policy logic.
   - Equipment booking form and its Reserve prompt in `templates/hub/partials/equipment_schedule.html`
     (`:95-117`): a `hub-text-muted` line under the form and the sentence appended to the
     `equip-reserve-confirm` message. The schedule context is built in `hub/equipment_views.py`
     (find where `equipment_schedule.html` gets its context) and gains `late_cancel_sentence`.
   - Orientation confirmed email: `templates/membership/emails/orientation_confirmed.txt` and `.html`
     gain a guarded line `{% if cancellation_policy %}{{ cancellation_policy }}{% endif %}` after the
     "Can't make it?" line; `_context` in `membership/orientations.py:160` supplies it from the
     booking's policy.
   - Reservation confirmed email: `core/events/copy.py:1994` `equipment.reservation_confirmed` gains
     `cancellation_policy` in `placeholders` and the sample values, and both default bodies end with
     `{{ cancellation_policy }}`; `_placeholder_context` in `membership/equipment.py` supplies it.
     Note for the PR: seeded copy on production is a DB row, so the default reaches only fresh copy;
     an admin adds the merge field to live copy from the editor (say this in the PR body).
6. **Nothing charges.** No fee record, no Stripe, no change to `cancel_orientation` or
   `EquipmentReservation.cancel()` in this part. The double cancel guard from the closed branch is
   part 2's.

## Acceptance criteria (part 1, from the issue)

- Site Settings, Features card: the toggle (default off), notice hours (default 24) and grace hours
  (default 2), saved with the existing form and read through `SiteConfiguration.load()`.
- Guild settings Orientations tab, Booking card: the fee in dollars, blank meaning no fee, saved by
  `GuildOrientationSettingsForm`; rendered only when the site toggle is on.
- Equipment manage page, Hours & Limits tab, Limits card: the same field on `EquipmentSettingsForm`;
  rendered only when the site toggle is on.
- `membership.late_cancel.policy_for` returns the governing fee and lateness for a guild booking, an
  equipment owned booking and a reservation; toggle off means fee 0.
- With a fee set, the sentence shows on the orientation booking prompts on both pages, on the
  equipment booking form and its Reserve prompt, and in both confirmation emails; with no fee, none
  of it renders.
- Specs cover: the resolver for all three owners and the boundary (exactly notice minus grace is not
  late; a started booking is not late), the toggle off, both settings forms including the dollars to
  cents mapping and blank, the site settings form validation, the copy on each surface present and
  absent (anchor on the factory's fee amount string like "$15.00", never on prose the changelog could
  carry).

## Tree facts (verified 2026-09-25 on main; re-verify, two PRs merged since)

| Thing | Where |
|---|---|
| `SiteConfiguration.my_tab_enabled` and the Features fields | `core/models.py:634` |
| `SiteSettingsForm` | `hub/forms.py` `class SiteSettingsForm` (fields list holds `my_tab_enabled`) |
| Site settings template, generic loop exclusion, Features card | `templates/hub/admin/site_settings.html:186`, `:509` |
| `GuildOrientationSettings` | `membership/models.py:9153`; form `class GuildOrientationSettingsForm` in `hub/forms.py` |
| Guild Booking card | `templates/hub/guild_edit.html:428` |
| `Equipment`, `EquipmentReservation.cancel()` | `membership/models.py` `class Equipment`, `class EquipmentReservation` |
| `EquipmentSettingsForm`, Limits card | `hub/forms.py` `class EquipmentSettingsForm`; `templates/hub/equipment_manage.html:166` |
| `_orientation_sections` | `hub/views.py:524` |
| Booking prompts | `templates/hub/partials/guild_orientation.html:127-135`, `equipment_orientation.html` |
| Reserve form and prompt | `templates/hub/partials/equipment_schedule.html:95-117` |
| Orientation confirmed email and its context | `templates/membership/emails/orientation_confirmed.{txt,html}`, `membership/orientations.py:160` `_context` |
| Reservation confirmed copy and placeholders | `core/events/copy.py:1994`, `membership/equipment.py` `_placeholder_context` |
| The closed branch's helpers to port the shape of | `git show origin/fog/late-cancel-fee:membership/equipment.py` |
| Site settings specs | `tests/hub/site_settings_form_spec.py`, `site_settings_features_spec.py` |
| Guild orientation settings specs | `tests/hub/orientation_settings_spec.py`, `guild_edit_spec.py` |
| Equipment settings and schedule specs | `tests/hub/equipment_orientation_manage_spec.py`, `equipment_schedule_views_spec.py` |
| Copy registry specs | `tests/core/events/` (find the spec that pins `placeholders` per event) |

## Screenshots

`mockups/screenshots/456-1-site-settings-late-fees.png` (the three fields in the Features card),
`456-1-guild-booking-card-fee.png`, `456-1-equipment-limits-fee.png`, and
`456-1-reserve-prompt-with-policy.png`. Throwaway e2e spec with `live_server` + `login_via_code`,
deleted after; the site switch on and a $15 fee seeded.
