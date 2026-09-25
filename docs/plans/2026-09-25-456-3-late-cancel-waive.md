# #456 part 3 of 3: waive an unpaid fee, refund a paid one, list fees in the Payments ledger

Issue: https://github.com/Past-Lives-Makerspace/plfog/issues/456. The issue body is the spec
(its Expected behavior and the "Part 3 of 3" criteria are the targets). Parts 1 and 2 are merged:
`membership/late_cancel.py` (policy), `billing/models.py` `LateCancellationFee` (UNPAID / PAID /
WAIVED / REFUNDED, `waived_by`, `waived_reason`, `waived_at` already on the row),
`billing/late_fees.py` (charge, checkout, `mark_paid`, `unpaid_fee_for`), the webhook handlers,
the hub pay and return views, the block, the emails and the activity kinds LATE_FEE_CHARGED and
LATE_FEE_PAID. Re-verify every name below against the tree: part 2 may have named things
slightly differently.

## What ships

1. **Waive** (an unpaid fee is forgiven). `billing/late_fees.py` gains
   `waive(fee, *, actor, reason) -> None`: under `select_for_update`, UNPAID only (a paid fee
   is refunded, not waived: `ValueError` with a readable message), sets WAIVED plus
   `waived_by` / `waived_reason` / `waived_at`, expires the open Checkout session best effort
   (call `_expire_session_best_effort` in `billing/late_fees.py:142`, which already exists) so a Stripe tab left
   open cannot pay a forgiven fee, logs `SiteActivity.Kind.LATE_FEE_WAIVED` (actor the admin,
   target the fee, payload amount, item and reason), and emits one member email
   `billing.late_fee_waived` ("Your late cancellation fee was waived", the item, who to thank
   is not named; the member can book again) with a unique `period`. The block lifts because
   `unpaid_fee_for` reads UNPAID only (assert it in a spec).
2. **Who may waive.** `billing/late_fees.py` `can_waive(request, fee) -> bool`: an admin
   (`is_effective_staff` / the admin tier the respond page uses), for an orientation fee the
   people `_require_can_manage_booking` (`hub/views.py:843`) lets through, for a reservation
   fee `can_manage_equipment(request, fee.reservation.equipment)`. A refusal names the
   governing owner: "Only <guild name> staff or an admin can waive this fee." /
   "Only the <equipment name> managers or an admin can waive this fee."
3. **Waive controls.**
   - The orientation respond page (`templates/hub/orientation_respond.html`, view `orientation_respond` in `hub/views.py`, url name `hub_orientation_respond`): when `booking.late_fee` exists, a "Late
     cancellation fee" card under the booking details showing the amount and state (Unpaid /
     Paid on <date> / Waived by <name> on <date> / Refunded) and, for UNPAID and a viewer who
     may waive, a Waive button opening a `components/modal.html` form with a required reason
     (`OrientationRefundForm`'s reason field is the shape to copy) posting to
     `hub_late_fee_waive` (`late-fees/<int:pk>/waive/`, POST, `@login_required`); on success a
     Django message and a redirect back; a viewer who may not waive gets a 403 whose page
     message (or toast for HTMX) names the governing owner as in 2.
   - The equipment manage page, Reservations tab (`templates/hub/equipment_manage.html:464` onward, context in `hub/equipment_views.py` `_render_manage`): a "Late cancellation fees"
     card listing this equipment's fees (reservation fees and its owned orientations' fees)
     newest first with member, item, amount, state and the same Waive control for UNPAID rows;
     empty state "No late fees here." The card renders only when the site switch is on or the
     equipment has fees.
4. **Refund a paid fee.** `LateCancellationFee` implements `RefundableSource`
   (`billing/refunds.py:39`): `refund_payment_intent_id` returns `stripe_payment_id`,
   `refundable_cents` is `amount_cents` minus succeeded refunds (mirror
   `OrientationBooking.amount_refunded_cents` / `refundable_cents`), `refund_receipt_context`
   returns the documented keys (item title "Late cancellation fee: <item>", the member's
   email and name, `manage_url` the hub fee detail page, `in_app_url` the same path),
   `on_fully_refunded` sets REFUNDED and logs `LATE_FEE_REFUNDED`, `refund_state` mirrors the
   booking's property, `issue_refund(...)` delegates to `billing.refunds.issue_refund`.
   `PaymentRefund` gains `late_fee` FK (`billing.LateCancellationFee`, null, PROTECT,
   related_name `refunds`) and the one-source CheckConstraint becomes three-way; one `billing/` migration (drop and re-add `ck_refund_one_source` by name, `billing/models.py:1309`, plus an `idx_refund_late_fee_status` index beside the two existing ones). `source_field_name`
   (`billing/refunds.py:62`) gains the branch; `source_object` / `source_kind` on
   `PaymentRefund` cover it; `_refundable_source_for_payment_intent` (`classes/webhook_handlers.py:491`, today `Registration | None` and never grown past registrations) also looks up a late fee by `stripe_payment_id`, return type widened, so a refund issued in the Stripe dashboard reconciles into the ledger. Orientation bookings stay absent from that lookup; that is a separate gap, not this part's.
5. **The Payments ledger.** `billing/payments_panel.py`: `SOURCE_LABELS["late_fee"] = "Late
   fee"`, a `_late_fee_rows(window, viewer_is_admin)` builder for PAID and REFUNDED fees (and
   fees with a pending or failed refund) with the same status derivation as
   `_orientation_rows`, `item` "Late fee: <item>", `item_url` the owner page path,
   `can_refund` when `stripe_payment_id` and `refundable_cents > 0`; `build_payments_ledger`
   accepts `source="late_fee"`; the CSV includes them; the filter chips gain "Late fee".
   Views `billing_late_fee_refund_form` / `billing_late_fee_refund` in `billing/views.py`
   mirror the orientation pair (`:486-530`) under `refund_authority_required`, with a
   `LateFeeRefundForm` mirroring `OrientationRefundForm` in `billing/forms.py`, a partial
   `billing/partials/late_fee_refund_form.html` mirroring the orientation one, and the row branch in `templates/billing/partials/payments_table.html:82-97`, and a "Late fees" chip beside the source chips in `templates/billing/admin_dashboard.html:297-302`. A refunded fee lifts nothing: it was
   already paid, so no block existed.
6. **Activity**: `SiteActivity.Kind.LATE_FEE_WAIVED` and `LATE_FEE_REFUNDED`, choices-only
   `core/` migration. The feed renders both.
7. **Reconciliation report**: out of scope (the issue says so); `_ADJUSTMENT_KINDS` unchanged.
8. **Three nits carried from the part 2 reviews**: the header comment at
   `templates/hub/partials/equipment_cards.html:5` still lists four access states; add `needs_fee`.
   `_send_orphan_fee_alert` in `billing/webhook_handlers.py` reads `primary_email` per approver;
   add `select_related("user")` to its member query. The test helpers `_event`, `_retrieved` and
   `_member_with_user` in `tests/billing/late_fee_webhooks_spec.py` (and any sibling without one)
   get full annotations. Nothing else from part 2 changes.

## Acceptance criteria (part 3, from the issue)

- An admin, a staff member of the governing guild, or a manager of the governing equipment can
  waive an unpaid fee with a reason from the orientation respond page and from the reservation's
  equipment manage tab; the block lifts; a lead of another guild gets a 403 with a message naming
  the governing owner.
- `LateCancellationFee` implements `RefundableSource`; `PaymentRefund` gains a `late_fee` source
  FK and the one source constraint covers it; a paid fee appears in the Payments dashboard
  ledger as source "Late fee" with the existing refund modal, and a full refund marks it
  refunded and lifts nothing.
- `SiteActivity` rows for waived and refunded, with the acting user.
- Specs cover: waive by each allowed role and refusal for a lead of another guild and for a
  paid fee, the block lifting, the waived email, the ledger row and filter, refund through the
  shared engine (full and partial, Stripe mocked as the orientation refund specs do), the
  dashboard-refund reconciliation lookup, activity rows, the constraint refusing a row with two
  sources or none.
- The PRs show screenshots of the respond page fee card with the Waive control and the
  Payments ledger row.

## Tree facts (verified 2026-09-25 against the part 2 head b2f29086)

| Thing | Where |
|---|---|
| `RefundableSource`, `source_field_name`, `issue_refund` | `billing/refunds.py:39-140` |
| `PaymentRefund` and its one-source constraint | `billing/models.py:1212-1320` (`ck_refund_one_source` at `:1309`) |
| Orientation refund form, views, partial | `billing/forms.py` `OrientationRefundForm`, `billing/views.py:467-530`, `templates/billing/partials/orientation_refund_form.html` |
| Ledger rows, labels, CSV | `billing/payments_panel.py:25-30`, `:250-340` |
| Ledger row template and source chips | `templates/billing/partials/payments_table.html:60-100`, `templates/billing/admin_dashboard.html:297-302` |
| Dashboard refund reconciliation lookup | `classes/webhook_handlers.py:491` |
| Respond page and its permission helper | `templates/hub/orientation_respond.html`, `hub/views.py` `orientation_respond`, `_require_can_manage_booking` `:843` |
| Equipment manage Reservations tab | `templates/hub/equipment_manage.html:464`, `hub/equipment_views.py` `_render_manage` |
| Part 2 module and model | `billing/late_fees.py` (`_expire_session_best_effort` `:142`, `unpaid_fee_for` `:291`), `billing/models.py` `LateCancellationFee` `:1516` (`waived_by` / `waived_reason` / `waived_at` `:1583-1592`) |
| Refund specs to mirror for Stripe mocking | `tests/billing/orientation_panel_spec.py`, `tests/billing/` refund specs |

## Screenshots

`mockups/screenshots/456-3-respond-late-fee-waive.png` (the respond page fee card with the
Waive modal open) and `456-3-payments-late-fee-row.png` (the Payments ledger with a Late fee row).
Throwaway e2e spec with `live_server` + `login_via_code`, deleted after.
