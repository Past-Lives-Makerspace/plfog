# #456 part 2 of 3: the fee, the Stripe payment and the block until paid

Issue: https://github.com/Past-Lives-Makerspace/plfog/issues/456. The issue body is the spec
(its Expected behavior and the "Part 2 of 3" criteria are the targets); part 1 (merged) gave us
`membership/late_cancel.py` (`policy_for`, `policy_for_type`, `policy_for_equipment`,
`booking_sentence`, `cancel_sentence`) and the settings. Part 3 (waive and refund) is a later
PR: this part creates no waive control and no refund path.

## What ships

1. **The fee record** `LateCancellationFee` in `billing/models.py` (it is money; billing already
   points at membership rows by string FK): `member` FK `membership.Member` (same `on_delete` as
   `OrientationBooking.member`), `orientation_booking` OneToOne `membership.OrientationBooking`
   (null, PROTECT, related_name `late_fee`), `reservation` OneToOne
   `membership.EquipmentReservation` (null, PROTECT, related_name `late_fee`), a CheckConstraint
   that exactly one of the two is set (`ck_latefee_one_source`), `amount_cents`
   PositiveIntegerField, `status` TextChoices UNPAID / PAID / WAIVED / REFUNDED (WAIVED and
   REFUNDED exist now so part 3 adds no migration on the column), `stripe_session_id`,
   `stripe_payment_id` (the payment intent), `checkout_attempts` PositiveSmallIntegerField
   default 0, `paid_at`, `waived_by` FK User (null, SET_NULL), `waived_reason` CharField blank,
   `waived_at`, `created_at`. One migration in `billing/`. The OneToOnes are the "charge at most
   once" guard at the database, on top of the code guard below.
2. **The service** `billing/late_fees.py`:
   - `charge_if_late(target, *, now=None) -> LateCancellationFee | None`: reads
     `policy_for(target)`; returns None unless `policy.applies` and
     `policy.is_late(target.starts_at)`; otherwise `get_or_create` the fee on the OneToOne
     (a second call returns the existing row, never a second fee) with `amount_cents` from the
     policy and logs `SiteActivity.Kind.LATE_FEE_CHARGED` (actor the member's user, target the
     fee, payload amount and item). Called from inside the cancel transaction (below).
   - `start_fee_checkout(fee) -> str`: mints a Stripe Checkout session through
     `billing.stripe_utils.create_checkout_session` with `metadata={"kind": "late_cancel_fee",
     "fee_id": str(fee.pk)}`, `product_name` "Late cancellation fee: <item>", the member's
     primary email, `success_url` and `cancel_url` to the two hub views below (signed token,
     mirror `make_checkout_token` / `read_checkout_token` in `membership/orientations.py:469`),
     `idempotency_key=f"late-fee-{fee.pk}-{fee.checkout_attempts}"`, `expires_at` 24 hours out.
     Increments `checkout_attempts`, stores `stripe_session_id`, returns the URL. Refuses
     (ValueError) on a fee that is not UNPAID.
   - `mark_paid(fee, *, payment_intent, session_id, amount_total) -> str`: under
     `select_for_update`, flips UNPAID to PAID once (`"paid"` / `"already"` / `"gone"` like
     `finalize_paid_booking`, `membership/orientations.py:599`), stamps the ids and `paid_at`,
     logs `LATE_FEE_PAID`, and emits the receipt (below) outside the lock.
   - `reconcile_landed_checkout(fee) -> str`: retrieves the session
     (`stripe_utils.retrieve_checkout_session`) and calls `mark_paid` when `payment_status ==
     "paid"`, mirroring `membership.orientations.reconcile_landed_checkout` (`:759`).
   - `unpaid_fee_for(member) -> LateCancellationFee | None`: the oldest UNPAID fee, the one
     input to the block.
3. **Webhook**: `billing/webhook_handlers.py` gains `handle_late_fee_checkout_completed`
   (self-filters on `kind == "late_cancel_fee"`, ignores `payment_status != "paid"`, calls
   `mark_paid`; a paid session whose fee is missing is logged at ERROR and alerted the way
   `membership/webhook_handlers.py:32` `_send_orphan_payment_alert` does) and
   `handle_late_fee_checkout_expired` (a no-op beyond a log line: the fee stays UNPAID and the
   Pay button mints a fresh session). Register both in `_CHECKOUT_COMPLETED_HANDLERS` and
   `_CHECKOUT_EXPIRED_HANDLERS` in `billing/views.py:31-40`.
4. **When a self cancel is late.**
   - Reservations: `EquipmentReservation.cancel()` (`membership/models.py`, the self branch)
     wraps the conditional status update and the fee creation in one `transaction.atomic()`:
     port the closed branch's conditional `update(...)` guard
     (`git show origin/fog/late-cancel-fee:membership/models.py`, read only) so two overlapping
     cancels flip the row once and only the flipper reaches `charge_if_late`; a manager cancel
     (`as_manager=True` or another member's row) never calls it. `cancel()` returns the fee
     (or None) so the view can act. Then the new member email (below) is sent for a self
     cancel, with or without a fee.
   - Orientations: `cancel_orientation` (`membership/orientations.py:1004`) gains
     `self_cancel: bool = False`; `orientation_cancel_mine` (`hub/views.py`) and the emailed
     token cancel (`membership/orientations.py:108`) pass True. Only a CONFIRMED booking can
     carry a fee (check the status before `booking.cancel()`); the fee is created inside a
     `transaction.atomic()` with `booking.cancel()`. The paid orientation still refunds in full
     through `_refund_if_paid`, untouched, and the fee is never netted. Returns the fee or None.
   - A lead cancel, a slot cancel, a decline, a manager cancel and `release_hold_if_unpaid`
     never create a fee: specs prove each.
5. **Straight to Checkout, and the Pay link.** After a late self cancel: the orientation cancel
   view redirects to `start_fee_checkout(fee)`'s URL (a full redirect; the Stripe page opens);
   the reservation cancel view (HTMX) answers the schedule swap with an `HX-Redirect` header to
   that URL (see `core/htmx.py` for helpers). Hub routes in `hub/urls.py` beside the orientation
   checkout ones (`:164`): `late-fees/<int:pk>/pay/` POST `hub_late_fee_pay` (the member's own
   UNPAID fee only, else 404; mints a fresh session and redirects), `late-fees/return/<token>/`
   `hub_late_fee_return` (reconciles synchronously, renders "Paid. Thank you." or "Stripe has
   not confirmed it yet; refresh in a moment." with the Pay button; a bad token renders 400),
   `late-fees/cancelled/<token>/` `hub_late_fee_checkout_cancelled` (a message that the fee is
   still due, then the owner page). Templates under `templates/hub/late_fee_return.html`
   through `components/page_header.html`, no inline styles.
6. **The block until paid.** `OrientationSlot.ensure_bookable_for` (`membership/models.py`,
   after the "already completed" guard; `by_staff` bypasses it) raises
   `OrientationError("Pay your late cancellation fee to book again.")` when
   `unpaid_fee_for(member)` is set; `Equipment.booking_blockers` appends the same sentence with
   the amount ("Pay your $15.00 late cancellation fee to book again."). Verify the custom request
   and block booking paths reach `ensure_bookable_for`; where one does not, add the same check
   there. The guild page and the equipment page render the sentence with a Pay button (a POST
   form to `hub_late_fee_pay`, `pl-btn pl-btn--primary pl-btn--sm`) in the orientation section
   header and the equipment requirements banner (`templates/hub/equipment_detail.html:39-50`
   region and `hub/partials/guild_orientation.html`), fed by an `unpaid_late_fee` context value
   from `_orientation_sections`' callers and `_schedule_context`.
7. **Emails.**
   - `templates/membership/emails/orientation_cancelled.txt` and `.html` gain a guarded block:
     "A $15.00 late cancellation fee applies to this cancellation. Pay it here: <pay url>. You'll
     get a receipt once it's paid." `_context` supplies `late_fee` (the row or None) and
     `late_fee_pay_url` (absolute, to a GET landing that POSTs? No: the email link is a GET to
     `hub_late_fee_return`'s sibling `late-fees/<int:pk>/` which shows the fee and the Pay
     button; add that GET view `hub_late_fee_detail`, the member's own fee only).
   - New copy `equipment.reservation_cancelled` in `core/events/copy.py` next to
     `equipment.reservation_cancelled_by_manager` (`:2035`): the member's own cancel
     confirmation ("You cancelled your <equipment> reservation for <when>."), placeholders
     `member_name`, `equipment_name`, `reservation_when`, `equipment_url`, `late_fee_line`
     (the sentence above with the Pay link, "" when no fee), sample values, both channels.
     Emitted by a new `membership.equipment.notify_self_cancelled(reservation, fee)` in the
     self path only. Update the spec that pins placeholders per event.
   - Receipt: new copy `billing.late_fee_paid` ("Your late cancellation fee is paid",
     amount, item, paid date), emitted by `mark_paid` with `period=f"late_fee:{pk}:paid"`.
   - Every email through `emit` / `emit_with_email_shell` with a unique `period`, absolute
     URLs, `.txt` and `.html` both.
8. **The self cancel modals.** `cancel_sentence(policy)` appended to the member's cancel modals
   only when the cancel would be late right now: the orientation cancel modals on
   `guild_orientation.html:82-84` and `equipment_orientation.html:67`, and the reservation cancel
   modal `equipment_schedule.html:143`. The section builder puts `late_cancel_warning` on each
   section for the member's live booking (`policy.is_late(booking.slot.starts_at)`), and
   `_schedule_context` puts one per reservation in `my_reservations` (annotate the objects or
   pass a dict). Unchanged otherwise.
9. **Activity**: `SiteActivity.Kind.LATE_FEE_CHARGED` and `LATE_FEE_PAID` (`core/models.py`,
   next to the orientation kinds), choices-only migration in `core/`. The feed renders them.
10. **Site switch off**: `charge_if_late` returns None (the policy's fee is 0), so nothing is
    created; existing UNPAID fees still block and can still be paid (a fee owed is owed).

## Acceptance criteria (part 2, from the issue)

- A `LateCancellationFee` row is created only by a late self cancel of a confirmed orientation
  booking or an equipment reservation, inside the same transaction as the cancel, at most once
  per booking or reservation (two overlapping cancels charge once).
- The member is sent to a Stripe Checkout session tagged `kind=late_cancel_fee` after the
  cancel; the `checkout.session.completed` handler marks the fee paid and emails a receipt. A
  `checkout.session.expired` session leaves the fee unpaid and a fresh session can be minted
  from the Pay button.
- The orientation cancelled email and the new reservation cancelled email say a fee applies and
  carry the Pay link when the cancel was late, and say nothing about fees otherwise.
- Both self cancel modals explain the fee when late and are unchanged otherwise.
- A lead, manager or admin cancel never creates a fee: a lead cancelling from the respond page,
  a slot cancel, a decline, a manager cancelling a member's row or their own row from the
  manage tab, and releasing an unpaid payment hold.
- A paid orientation still refunds in full automatically on cancel; the fee is separate and
  never netted.
- While a member has an unpaid fee, `OrientationSlot.ensure_bookable_for` and
  `Equipment.booking_blockers` refuse with "Pay your late cancellation fee to book again", and
  the guild and equipment pages show the sentence with a Pay button.
- Specs cover: fee created on a late self cancel of each kind and not on an early one, the once
  only guard, every staff cancel path, the webhook marking paid, the expired session, the block
  and its release on payment, both cancellation emails, the receipt, the modals, and that no fee
  is created while the site toggle is off.
- The PR shows screenshots of a self cancel modal with the fee sentence and the equipment page
  blocked with the Pay button.

## Constraints (from the issue)

No tab, no `TabEntry`, no saved card. Stripe only through `billing.stripe_utils` and the
registered dispatch; `metadata["kind"]` is `late_cancel_fee`. Only a confirmed booking or a
confirmed reservation can carry a fee. Mock Stripe with `respx` or by patching
`billing.stripe_utils` the way the orientation checkout specs do (find them under
`tests/hub/orientation_checkout_views_spec.py` and `tests/membership/`), never our own models.

## Tree facts (verified 2026-09-25; re-verify, part 1 merged since)

| Thing | Where |
|---|---|
| Checkout dispatch lists and the webhook map | `billing/views.py:31-82` |
| Orientation checkout handlers to mirror | `membership/webhook_handlers.py:78-192` |
| `finalize_paid_booking`, `reconcile_landed_checkout`, tokens, `_CHECKOUT_SESSION_LIFETIME` | `membership/orientations.py:469-487`, `:599`, `:759` |
| `create_checkout_session`, `retrieve_checkout_session`, `expire_checkout_session` | `billing/stripe_utils.py:178`, `:284`, `:259` |
| Orientation checkout return view and routes | `hub/views.py` `orientation_checkout_return`, `hub/urls.py:164-176` |
| `cancel_orientation`, `release_hold_if_unpaid`, `_refund_if_paid` | `membership/orientations.py:1004`, `:731`, `:840` |
| `EquipmentReservation.cancel()` | `membership/models.py` `class EquipmentReservation` |
| Reservation self cancel view (HTMX) and manager path | `hub/equipment_views.py` `hub_equipment_reservation_cancel` |
| Orientation self cancel view and lead cancel | `hub/views.py` `orientation_cancel_mine`, `orientation_lead_cancel` |
| `ensure_bookable_for`, `booking_blockers`, `reserve()` | `membership/models.py`, `membership/equipment.py:95` |
| Manager cancelled copy and `notify_manager_cancelled` | `core/events/copy.py:2035`, `membership/equipment.py:172` |
| Cancel modals | `templates/hub/partials/guild_orientation.html:82-84`, `equipment_orientation.html:67`, `equipment_schedule.html:143` |
| Cancelled email templates | `templates/membership/emails/orientation_cancelled.{txt,html}` |
| Equipment banner | `templates/hub/equipment_detail.html:33-72` |
| `SiteActivity.Kind` | `core/models.py:1458` |
| Part 1 resolver | `membership/late_cancel.py` |

## Screenshots

`mockups/screenshots/456-2-cancel-modal-late-fee.png` (the reservation cancel modal with the fee
sentence) and `456-2-equipment-blocked-pay.png` (the equipment page banner with the unpaid fee
and Pay button). Throwaway e2e spec with `live_server` + `login_via_code`, deleted after.
