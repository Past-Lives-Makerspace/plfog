# billing app

Stripe tab billing system. Members accumulate charges on a tab; a management command batches and charges them.

## Models

| Model | Key fields | Notes |
|-------|-----------|-------|
| `BillingSettings` | charge_frequency, charge_time, default_tab_limit, default_admin_percent, max_retry_attempts, connect_platform_* | Singleton (pk=1); load via `BillingSettings.load()` |
| `Product` | name, price, guild FK | Purchasable item offered by a guild |
| `ProductRevenueSplit` | product FK, recipient_type, guild FK (nullable), percent | One row per recipient; per-product percents sum to 100% |
| `Tab` | member 1:1, stripe_customer_id, stripe_payment_method_id, is_locked | One per member; accumulates entries |
| `TabEntry` | tab FK, tab_charge FK (null=pending), amount, voided_at | Single line item; splits stored on `TabEntrySplit` |
| `TabEntrySplit` | entry FK, recipient_type, guild FK (nullable), percent, amount | Frozen split snapshot — reports SELECT from here |
| `TabCharge` | tab FK, status, amount, stripe_payment_intent_id | Batched charge — one per tab per billing cycle |
| `LateCancellationFee` | member FK, orientation_booking / reservation OneToOne (exactly one), amount_cents, status (unpaid / paid / waived / refunded), stripe_session_id, stripe_payment_id | A late self cancel's fee (#456). Never the tab: paid through a Stripe Checkout tagged `kind=late_cancel_fee`; `billing/late_fees.py` owns charge, checkout, mark paid, the block until paid and `waive` (guild staff, equipment managers or an admin forgive an unpaid fee). A paid fee is a `RefundableSource`: `PaymentRefund.late_fee` points at it and the Payments dashboard refunds it through `billing/refunds.py` |
| `PayoutAccount` | member FK, stripe_account_id, livemode, status (needs info / on / paused) | A payee's Stripe Express account (#662). ID and status only: no bank, SSN or tax field exists anywhere. One row per member per Stripe mode, since a test `acct_` is not a live one. `billing/payouts.py` owns the switch (`BillingSettings.connect_enabled`, read by no charge path), the payee rule and the Settings, Payouts state; status arrives from `account.updated` on the second, Connected accounts webhook endpoint (same URL, its own secret) and on the return from signup |
| `Payout` | registration / orientation_booking OneToOne (exactly one), payee, amount_cents, due_at, status (pending / sent / failed / owed at month end / taken back), owed_reason, stripe_transfer_id, attempt | One instructor or orientor share (#662). `billing/payouts.py` `run_payouts` (the `send_payouts` job, every 15 min) records each share 48h after the first session or slot starts (or after payment if later), using `reconciliation.ShareSource` for the amount, and sends it with `source_transaction` and key `payout-<pk>-a<attempt>`. `goes_through_stripe` is the one rule (paid through Stripe, payee active before the payment, payouts on before it fell due) that the job, the Payouts tab and the Reconciliation "Sent through Stripe / Owed manually" split all read. A rejected transfer retries daily, alerts admins once, and is owed by hand once its month is snapshotted |
| `PaymentRefund` sources | registration / orientation_booking / late_fee / reservation (exactly one, `ck_refund_one_source`) | `reservation` (#749) is a priced equipment reservation's payment, refunded automatically on decline and cancel by `membership.equipment.refund_if_paid`. The Payments panel rows and reconciliation for it arrive in #749 part 2 |
| `PaymentRefund.share_decision` | take back / Past Lives covers / not asked, share_reversed_cents, stripe_transfer_reversal_id, share_reversal_error | #662 part 3. Refunding a payment whose share was already sent requires the choice on both refund forms (`RefundShareDecisionForm`). On success `payouts.settle_refund_share` reverses this refund's portion of the share (key `payout-reversal-<refund pk>`, looked up first since keys expire) and marks the `Payout` Taken back; a refusal falls back to Past Lives covering it with one alert; refunds nobody was asked about (dashboard, automatic orientation refund) record Not asked. Reconciliation flags every refund where the payee kept a sent share |

## Revenue split

Each `Product` has 1+ `ProductRevenueSplit` rows that name a recipient (Admin or a Guild) and a percentage. Per-product percentages must sum to exactly 100%, validated in `ProductForm.clean()`.

When a `TabEntry` is created via `Tab.add_entry()`, the splits are frozen onto `TabEntrySplit` rows by `TabEntry.snapshot_splits()`. Reports SELECT directly from `TabEntrySplit` — never recomputed.

Penny rounding: each split's amount is `round(entry.amount * percent / 100, 2, ROUND_HALF_UP)`. The row with the largest percent absorbs the +/-1c remainder so the children sum exactly to the entry total.

Guild payouts are reconciled manually via the admin Reports page — no automated Stripe Connect transfers. Instructor and orientor shares are moving to Stripe Connect Express (#662); guild shares stay manual.

## Tab Flow

1. Member adds payment method → `Tab.set_payment_method()` attaches to Stripe customer
2. Entries accumulate via `Tab.add_entry()` (race-safe with `select_for_update`). Snapshots `TabEntrySplit` rows onto each entry.
3. `bill_tabs` management command: for each tab with pending entries, creates ONE `TabCharge` with the sum of all pending amounts → calls `TabCharge.execute_stripe_charge()`
4. Webhook handlers update charge status on Stripe events
5. On failure: `BillingSettings.max_retry_attempts` retries, then `Tab.lock()`

## Single Stripe Account

All charges route through one platform Stripe account — credentials live on `BillingSettings` (encrypted). No per-guild Stripe accounts, no destination charges, no direct-keys Checkout. This was simplified in v1.5.0.

**Previous account (#702).** Payments made before the switch to the Past Lives Member Portal account live on PLM FOG. `BillingSettings.previous_*` / `test_previous_*` hold its secret key and webhook secret; `stripe_utils._on_owning_account` retries a refund, refund list or Checkout Session retrieve/expire on it once when the active account answers `resource_missing`, and `construct_webhook_event` tries its webhook secret last. Creation calls never fall back. The Stripe tab's "Put new account credentials" modal (`NewStripeAccountForm`, `billing_switch_stripe_account`) calls `BillingSettings.switch_to_new_account`, which moves the current mode's secret key and webhook secret into Previous account in one transaction; the normal save never moves credentials. Once PLM FOG closes, remove those fields, the "Previous account" block in `stripe_utils.py` the Stripe tab's Previous Account section and the switch modal in one PR.

**Prereq**: `Tab.can_add_entry` requires a saved payment method on file. Off-session PaymentIntents don't work without one.

## Exceptions (billing/exceptions.py)

- `TabLockedError` — tab is locked (failed payment)
- `NoPaymentMethodError` — no payment method on file
- `TabLimitExceededError` — entry would exceed `Tab.effective_tab_limit`

## Stripe Utils (billing/stripe_utils.py)

Thin wrapper around stripe SDK. All functions use the single platform Stripe client (`_get_stripe_client()`).

- `create_customer()` — creates Stripe customer
- `attach_payment_method()` / `detach_payment_method()`
- `retrieve_payment_method()` → `{id, last4, brand}`
- `create_payment_intent()` — standard off-session PaymentIntent (the only charge path)
- `create_setup_intent()` — for collecting payment method without charging
- `construct_webhook_event()` — verify incoming Stripe webhook
- `verify_platform_credentials()` — test a pasted platform secret key from the Settings tab

### Encryption key

The Fernet encryption key (`STRIPE_FIELD_ENCRYPTION_KEY`) encrypts `BillingSettings.connect_platform_secret_key` and `connect_platform_webhook_secret`. Generate with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set on local, Hetzner, and Render. **Losing this key bricks the stored Stripe credentials.**

## URLs (prefix: /billing/)

- `payment-method/setup/` → renders Stripe Payment Element iframe
- `api/setup-intent/` → AJAX endpoint returns client_secret
- `payment-method/confirm/` → saves PM to Tab
- `payment-method/remove/` → detaches PM
- `webhooks/stripe/` → handles payment_intent.succeeded/.payment_failed
- `admin/dashboard/` → multi-tab admin payments page
- `admin/add-entry/` → admin add-charge-to-tab
- `admin/connect-platform/test/` → AJAX verify pasted platform secret
- `admin/connect-platform/save/` → persist platform credentials
- `payouts/start/` → POST: Stripe Express signup (Set up payouts, Finish setup, Fix in Stripe)
- `payouts/return/` → Stripe's return and refresh URL: reads the account back, lands on Settings, Payouts
- `payouts/dashboard/` → single use login link to the payee's Express dashboard

## Management Command

`billing/management/commands/send_payouts.py` — records and sends due instructor and orientor shares; idle while payouts are off.

`billing/management/commands/bill_tabs.py` — creates one `TabCharge` per tab with pending entries and executes the Stripe charge. Run on schedule per `BillingSettings.charge_frequency`.

## Factories

`tests/billing/factories.py` — `TabFactory`, `TabEntryFactory`, `TabEntrySplitFactory`, `TabChargeFactory`, `ProductFactory`, `ProductRevenueSplitFactory`, `BillingSettingsFactory`.
