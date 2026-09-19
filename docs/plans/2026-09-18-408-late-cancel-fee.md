# 408: A late equipment reservation cancel adds a fee to the member's tab

Issue: https://github.com/Past-Lives-Makerspace/plfog/issues/408
Branch: fog/late-cancel-fee
Brief for this round: surgical, pragmatic, YAGNI.

## Policy decisions
Jo has not answered the issue's policy questions. These are the conservative defaults, and
the PR body states every one so he can flip any of them.
- P1. Two site settings on `SiteConfiguration`: `equipment_late_cancel_fee`
  (DecimalField max_digits=6, decimal_places=2, default 0; "0 = no fee") and
  `equipment_late_cancel_notice_hours` (PositiveIntegerField, default 48). The fee defaults to
  OFF: nobody is charged until an admin sets an amount. Decimal dollars because the tab rail
  (`Tab.add_entry(amount=Decimal)`, `Product.price`) is Decimal dollars.
- P2. A manager cancel NEVER charges, including a manager cancelling their own row from the
  manage tab (`as_manager=True`). Only the self-cancel path can.
- P3. No-show charging: out of scope.
- P4. Waiving is whatever can already remove or void a `TabEntry` today. No new mechanism.
- P5. The fee is a plain charge with nothing to net against.
- P6. If the tab refuses the entry (`TabLockedError`, `TabLimitExceededError`) the cancel
  still goes through and the refusal is logged at WARNING. A member backing out is never
  blocked by their own tab state.

## Changes
- `core/models.py` `SiteConfiguration`: the two fields (P1) with help_text; migration.
- `hub/forms.py` `SiteSettingsForm.Meta.fields`: add both.
  `templates/hub/admin/site_settings.html`: render them in a small "Equipment Reservations"
  card next to the reservations webhook (~636) AND add both names to the exclusion condition
  on line 178 so the generic loop does not render them a second time.
- `membership/models.py` `EquipmentReservation`:
  - `late_cancel_fee` property returning `Decimal`: the configured fee when `fee > 0` and
    `starts_at - timezone.now() < timedelta(hours=notice_hours)`, else `Decimal("0")`.
    Exactly on the boundary is NOT late (strict `<`).
  - `cancel()`: in the self-cancel branch only, read `fee = self.late_cancel_fee` BEFORE the
    save (the window is measured against now). After the save, when `fee`:
    `Tab.objects.get_or_create(member=self.member)` then
    `tab.add_entry(description=f"Late cancellation fee: {equipment.name}, {local starts_at:%b j}",
    amount=fee, added_by=actor.user,
    splits=[{"recipient_type": "admin", "guild": None, "percent": Decimal("100")}])`
    inside a try for the two tab errors (P6). `add_entry` already emails the member and logs
    SiteActivity; add no second notification.
- `templates/hub/partials/equipment_schedule.html`:
  - Book a Time: when the fee is on, a `hub-text-muted` line under the form: "Cancel at least
    48 hours ahead. Cancelling later adds a $35.00 late fee to your tab." (numbers from the
    settings), and the same sentence appended to the Reserve confirm modal message.
  - Your reservations cancel modal: when `reservation.late_cancel_fee` is nonzero the message
    reads "This is inside the 48 hour notice window, so a $35.00 late cancellation fee will be
    added to your tab. The time opens up for someone else." Otherwise unchanged. Check whether
    `site_config` already reaches the partial via the context processor before adding it.
- Confirmation email: `core/events/copy.py` ~1995 declares the merge fields for
  `equipment.reservation_confirmed`. Add `cancellation_policy` to `placeholders` and the
  sample values; set it in `_placeholder_context` in `membership/equipment.py` (the booking
  sentence when the fee is on, else ""); append `{{ cancellation_policy }}` to the default
  body_text and html. PR note: seeded copy on prod is a DB row, so the default reaches only
  fresh copy; an admin adds the line to live copy from the editor.

## Acceptance (specs)
- `tests/membership/equipment_reservations_spec.py`, fee 35.00 / 48h: self cancel 24h ahead
  yields exactly one TabEntry (amount 35.00, description names the equipment and date, tab
  balance 35.00); self cancel 72h ahead yields none; fee 0 yields none; manager cancel 24h
  ahead yields none; manager own-row `as_manager=True` 24h ahead yields none; a locked tab
  leaves the reservation CANCELLED with no entry and a warning logged.
- `late_cancel_fee`: exactly 48h ahead is not late.
- `tests/hub/equipment_schedule_views_spec.py`: the policy line renders only when the fee is
  on; the cancel modal carries the fee sentence only for a late row.
- The site settings page saves both fields (extend the existing site settings spec).
- `manage.py check` clean; migrations formatted.

## Out of scope
No-show charging, per-equipment overrides, class refunds, editing live email copy on prod.
