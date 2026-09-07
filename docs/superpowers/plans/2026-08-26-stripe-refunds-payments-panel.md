# Real Stripe Refunds + Consolidated Payments Panel — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build. Revised after adversarial UX review (2 blockers, 6 majors, 6 minors) and aligned to the orchestrator's cross-spec refund-engine contract.
**Date:** 2026-08-26
**Surface:** Admin — billing dashboard (`/billing/admin/dashboard/`), classes CMS registration detail, teach portal class registrations, member-edit Permissions tab.
**Related:** `2026-04-02-payments-dashboard.md` (the dashboard this extends), `2026-08-26-paid-orientations.md` (companion — **consumer of the shared refund engine below**; it is being written against the `PaymentRefund` contract in §4/§5, and its bookings surface in this panel).

---

## 1. Summary

Today, "refunding" a class registration is bookkeeping theater: an admin clicks **Mark Refunded** (which frees the seat and emails a receipt) and then goes to the Stripe dashboard to move the actual money by hand. This spec makes the button real — one click issues the Stripe refund (full or partial), records it, frees the seat, and sends the receipt. Refunds initiated directly in the Stripe dashboard reconcile back automatically via webhook, so the app's records never drift from Stripe's. Failed refunds are first-class: they show up, alert the billing admins, and carry a **Retry refund** action — never a dead end.

Alongside it, the existing 4-tab billing dashboard gains a fifth **Payments** tab: one ledger where an admin sees money across all sources — member Tab charges, class registration payments (currently invisible to the dashboard), and soon orientation payments — with Refund/Retry buttons on every row where the viewer holds refund authority.

The refund *engine* (model + service + webhooks) is deliberately source-neutral: it serves class registrations now and orientation bookings the moment the companion spec lands, with no schema change.

### Locked decisions (from brainstorm Q&A + pinned cross-spec contract)

| Decision | Choice |
|---|---|
| Refund authority | fog-admins always; plus a NEW per-person `AdminCapability` grant ("Refunds") assignable to any member, instructor or not. No site-wide toggle — the per-person grant IS the config. |
| Capability rename | `CLASS_APPROVER` display label "Class Administrator" → **"CMS Administrator"**. Value/key `class_approver` unchanged — display-label change only, with a sweep of member-facing copy. |
| Panel access | fog-admins + holders of the existing `BILLING_APPROVER` capability. Today that capability is alert-only; it gets upgraded to actually gate the billing dashboard/panel views. |
| Mechanism | New `create_refund(payment_intent_id, amount_cents=None)` in `billing/stripe_utils.py` + `charge.refunded` / `refund.updated` webhook handlers for idempotent reconciliation. The record-only `Registration.mark_refunded()` becomes the bookkeeping half, called on success. |
| **Refund ledger (contract)** | One source-neutral **`PaymentRefund`** model in `billing` with nullable FKs to `classes.Registration` AND `membership.OrientationBooking` (exactly one set, DB-enforced). All ledger/receipt/failure machinery works off whichever side is set. |
| **Refund service (contract)** | `issue_refund()` is a thin method on BOTH `Registration` and `OrientationBooking`, delegating to a shared billing-side service that owns the Stripe call, locking, row creation, and receipt emit. |
| **Failed refunds (contract)** | `refund_state` includes a **"failed"** value; the panel ships a named **"Retry refund"** action that re-invokes `create_refund` with a fresh idempotency-key suffix. Webhook lookups span BOTH sources; unknown Stripe ids log loudly. |
| Scope | Class registration refunds now. Orientation refunds arrive via the companion paid-orientations spec on the same engine and surface in this panel. Tab charge refunds: history shown, refund **action deferred** (a Tab charge is one batched PaymentIntent covering many `TabEntry` rows with frozen revenue splits — refunding part of it requires split-reversal design that doesn't exist yet; see §10). |

## 2. What already exists (reuse, don't reinvent)

All locations verified against the current tree.

| Need | Existing thing | Location |
|---|---|---|
| Stripe client (platform key from DB) | `_get_stripe_client()` | `billing/stripe_utils.py:50-52` |
| Stripe call conventions (kwargs-only, idempotency keys, dict returns) | `create_payment_intent`, `create_class_checkout_session`, `construct_webhook_event` | `billing/stripe_utils.py:118-220` |
| Webhook signature verify + dispatch | `_WEBHOOK_HANDLERS` dict in the webhook view | `billing/views.py:28-36` |
| Idempotency-key convention for class money | `class-checkout-reg-{pk}` | `classes/views.py:687` |
| Registration payment handle | `stripe_payment_id` (PaymentIntent id), `stripe_session_id`, `amount_paid_cents` | `classes/models.py:1970-1978` |
| Refund bookkeeping (seat free, waitlist promote, audit) | `Registration.mark_refunded()` + `Status.REFUNDED` | `classes/models.py:2185-2200`, `:1929-1934` |
| Refund receipt email | `refund_issued` spine event, emitted on the REFUNDED save-transition | `classes/models.py:2102-2116`, trigger `core/triggers.py:54`, copy `core/events/copy.py:389` |
| Existing (record-only) refund UI | `admin_registration_refund` view + Admin Actions block | `classes/views.py:2723-2730`, `templates/classes/admin/registration_detail.html:145-168` |
| Capability model + grant/revoke | `AdminCapability` + `Member.sync_admin_capabilities()` | `membership/models.py:2098-2151`, `:913-926` |
| Capability grant UI (Permissions tab) | `MemberCapabilitiesForm` — one BooleanField per capability, `_FIELD_TO_CAP` map | `hub/forms.py:644-704` |
| Capability → notification routing | `_capability_recipients` / `billing_approvers` resolver | `core/events/resolvers.py:226-230` |
| Dashboard view + tab routing | `admin_tab_dashboard`, `_VALID_TABS = {overview, open-tabs, settings, stripe}` | `billing/views.py:146-256` |
| Dashboard template — **extends `hub/base.html`** (hub CSS, Alpine, HTMX, toast all loaded) with its own `pl-*` styles + tab nav | `templates/billing/admin_dashboard.html` (tab nav :108-113) | that template |
| Admin gate decorator | `fog_admin_required` | `hub/view_as.py:205` |
| Filtered report + streaming CSV pattern | `admin_reports` / `admin_reports_csv` + `billing/reports.py` | `billing/views.py:408-466` |
| Class-payments query surface | consolidated registrations list + `_scoped_registrations` | `classes/views.py:2643-2673`, `:962-978` |
| Teach portal registrations page | `teach_class_registrations` | `classes/views.py:1510` |
| Orientation booking model (companion spec's refund side) | `OrientationBooking` | `membership/models.py:7883` |
| Modal / toast / form-field components (hub stack, theme-aware) | `components/modal.html`, `trigger_toast()`, `components/form_field.html` | `templates/components/`, `hub/toast.py` |
| Stripe mocking style in specs | `@patch("billing.stripe_utils.…")` at the call site; `stripe.StripeClient` patched inside `tests/billing/stripe_utils_spec.py` | `tests/billing/webhook_handlers_spec.py:50,239`, `classes/spec/views/register_series_spec.py:61` |

**Genuine gaps to close (kept small):**

1. No refund code exists anywhere in the repo — `create_refund` is net-new.
2. No refund *ledger*: `Registration` can only say "refunded, yes/no". Partial refunds, failed refunds, and webhook reconciliation all need a row per refund → one new source-neutral model (`PaymentRefund`, §4) + a small service module (`billing/refunds.py`, §5).
3. No `Member.has_capability()` action-check helper — capabilities today only route notifications. One 3-line method + two decorators.
4. `BILLING_APPROVER` doesn't gate anything — the dashboard is `fog_admin_required` only. Decorator upgrade (§5.6, permission matrix in §6.7).

**Explicitly NOT built:** a shared "Payment" model unifying Tabs/classes/orientations. The panel is a read-time aggregation over the existing tables (§5.5). Three sources with different lifecycles don't justify a fourth table that would need backfilling, syncing, and its own bug surface — YAGNI. (`PaymentRefund` is not that table — it's the refund ledger only, and it must exist to represent partials/failures at all.)

## 3. Where the code lives

```
billing/
  models.py                  + PaymentRefund (source-neutral refund ledger)
  refunds.py                 + NEW — shared refund service: locking, Stripe call, row
                               lifecycle, receipt emit, retry; RefundableSource protocol
  stripe_utils.py            + create_refund()
  views.py                   ~ _WEBHOOK_HANDLERS += charge.refunded, refund.updated
                             ~ admin_tab_dashboard: "payments" tab context, gate upgrade
                             + admin_payments_csv, payments-table partial view,
                               payment_refund_retry endpoint
  payments_panel.py          + NEW — PaymentRow dataclass + build_payments_ledger() + CSV streamer
  management/commands/
    sweep_stale_refunds.py   + NEW — nightly stale-PENDING sweep (§5.4)
classes/
  models.py                  ~ Registration: thin issue_refund() delegating to billing.refunds,
                               refund properties, RefundableSource hooks; receipt-emit moves
                               off the save() transition
  webhook_handlers.py        + handle_charge_refunded, handle_refund_updated
  forms.py                   + PaymentRefundForm
  views.py                   ~ admin_registration_refund becomes the real-refund endpoint
                             + refund_form partial view (modal body, HTMX)
  spec/…                     + specs (views/, webhooks/)
membership/
  models.py                  ~ AdminCapability: label rename + REFUNDS value; Member.has_capability()
                             (OrientationBooking.issue_refund lands in the companion spec,
                              once its payment fields exist — the engine is ready for it)
hub/
  view_as.py                 + billing_admin_access_required, refund_authority_required
  forms.py                   ~ MemberCapabilitiesForm: label rename + cap_refunds field
core/
  triggers.py, events/…      + refund_failed trigger (admin alert), copy, matrix row;
                               refund_issued copy generalized
templates/
  billing/admin_dashboard.html            ~ Payments tab, refund modal host, tab-nav gating
  billing/partials/payments_table.html    + NEW — ledger table partial (page + HTMX refresh)
  classes/admin/registration_detail.html  ~ Refunds card replaces Mark Refunded + Stripe link
  classes/teach/… (class registrations)   ~ Refund button (capability-gated)
  classes/partials/refund_form.html       + NEW — shared modal-body form partial
tests/billing/, classes/spec/, tests/membership/, tests/hub/   + specs per §9
```

Home apps: the refund **engine** (model, service, Stripe call, sweep) lives in `billing` — it is source-neutral money plumbing serving two apps. `classes` keeps the thin per-source method, its webhook source-resolution, and the UI. Both are already inside the coverage/mypy scope.

## 4. Data model

### 4.1 `PaymentRefund` (new, `billing/models.py`)

One row per refund — the audit ledger, shared by every refundable source per the pinned contract. A dedicated model rather than fields on the source because: (a) partial refunds mean **multiple** refunds per source row; (b) webhook reconciliation needs an idempotency anchor (`stripe_refund_id` unique — re-delivered events upsert, never duplicate); (c) each refund carries its own actor, reason, attempt count, and failure state; (d) two source apps must share one ledger without either owning the other.

| Field | Type | Note |
|---|---|---|
| `registration` | FK `classes.Registration`, null, blank, `on_delete=PROTECT`, `related_name="refunds"` | Set for class-payment refunds. |
| `orientation_booking` | FK `membership.OrientationBooking`, null, blank, `on_delete=PROTECT`, `related_name="refunds"` | Set for orientation-payment refunds (companion spec). |
| `stripe_refund_id` | `CharField(255)`, unique, db_index, blank | Stripe `re_…` id. Blank only between row-create and Stripe responding; unique constraint makes webhook upserts idempotent. |
| `amount_cents` | `PositiveIntegerField` | Amount of THIS refund. |
| `status` | `TextChoices`: `PENDING`, `SUCCEEDED`, `FAILED` | Card refunds usually succeed synchronously; `refund.updated` flips late failures. |
| `attempt` | `PositiveIntegerField`, default 1 | Bumped by Retry — feeds the fresh idempotency-key suffix (§5.3). |
| `source` | `TextChoices`: `IN_APP` ("Issued in app"), `STRIPE_DASHBOARD` ("Issued in Stripe dashboard") | Reconciled dashboard refunds get the latter. |
| `reason` | `TextField`, blank | Optional internal note from the refund modal. |
| `failure_reason` | `CharField(500)`, blank | Stripe's failure reason, for the admin to read. |
| `initiated_by` | FK `AUTH_USER_MODEL`, null, `on_delete=SET_NULL`, `related_name="+"` | Null for webhook-sourced/system refunds. |
| `created_at` / `settled_at` | `DateTimeField` (auto_now_add / null) | `settled_at` stamped on entering SUCCEEDED or FAILED — it is also the "side effects already fired" guard (§5.2). |

`Meta`:
- `CheckConstraint`: **exactly one** of `registration` / `orientation_booking` is non-null (`registration__isnull=False` XOR `orientation_booking__isnull=False`), name under the 30-char cap — run `manage.py check` (E034).
- ordering `["-created_at"]`; indexes on `("registration", "status")` and `("orientation_booking", "status")`.

`help_text` on every field; `__str__` = `"$X.XX refund for {source object} ({status})"`. Properties: `source_object` (whichever FK is set), `source_kind` (`"class"` / `"orientation"`). Manager: `PaymentRefundQuerySet.succeeded()`, `.for_source(obj)`.

Migration: one new-model migration, auto-reversible (drop table).

### 4.2 `Registration` additions (properties, no schema change)

- `amount_refunded_cents` — sum of succeeded refunds.
- `refundable_cents` — `amount_paid_cents - amount_refunded_cents`.
- `refund_state` — `"none" | "partial" | "full" | "failed"`. **`"failed"`** (per contract): the latest refund attempt is FAILED and no succeeded refund has since covered that amount; otherwise derived from refunded totals. **No new `Status` enum value** — `PARTIALLY_REFUNDED` would ripple through every status filter and email guard for zero benefit; a partially-refunded registration is still `CONFIRMED` (the person is still attending). A registration whose covering refund later *failed* may sit at `Status.REFUNDED` with `refund_state == "failed"` — that combination is exactly what the Retry action exists for (§5.3).

`OrientationBooking` gets the mirror properties in the companion spec, against the same `refunds` related manager.

### 4.3 `AdminCapability` changes (`membership/models.py:2110-2117`)

```python
class Capability(models.TextChoices):
    CLASS_APPROVER = "class_approver", "CMS Administrator"      # label only — value unchanged
    ...
    BILLING_APPROVER = "billing_approver", "Billing Administrator"
    REFUNDS = "refunds", "Refunds"                              # NEW
```

The `AdminCapability` class docstring (which currently asserts every capability "both routes … notifications … and grants the action") gains a qualifying sentence: most capabilities do both, but `REFUNDS` is **action-only** (routes nothing) and `BILLING_APPROVER` additionally **gates the Payments dashboard**. The `MemberCapabilitiesForm` docstring in `hub/forms.py` makes the same claim and gets the same amendment. No migration needed for the label; the new choice may emit a no-op `choices=` state migration — include it, auto-reversible.

## 5. Business logic (fat models, shared service)

### 5.1 `billing/stripe_utils.create_refund`

```python
def create_refund(*, payment_intent_id: str, amount_cents: int | None = None,
                  idempotency_key: str) -> dict[str, Any]:
    """Refund a PaymentIntent — full when amount_cents is None. Returns
    {'id', 'status', 'amount'}. idempotency_key REQUIRED."""
```

Thin wrapper over `client.v1.refunds.create(params={"payment_intent": …, "amount": …}, options={"idempotency_key": …})`, matching every sibling in the file. Stripe errors propagate — the caller handles them loudly.

### 5.2 The shared refund service — `billing/refunds.py`

Per the contract, ONE service owns the refund lifecycle for every source. It works against a small `RefundableSource` protocol each source model implements:

```python
class RefundableSource(Protocol):          # structural typing — no base class
    pk: int
    def refund_payment_intent_id(self) -> str: ...
    def refundable_cents(self) -> int: ...
    def refund_receipt_context(self) -> dict[str, Any]:   # item_title, recipient email/name, manage URL
    def on_fully_refunded(self, reason: str, actor: User | None) -> None: ...
```

`Registration` implements it now (`on_fully_refunded` = `mark_refunded` — the bookkeeping half: status → REFUNDED, seat freed, waitlist promoted, audit-feed attribution). `OrientationBooking` implements it in the companion spec.

**`issue_refund(source, *, amount_cents=None, reason="", actor=None) -> PaymentRefund`** — the one entry point, called by the thin `Registration.issue_refund(...)` / `OrientationBooking.issue_refund(...)` delegates:

1. **Guards** (domain exceptions in `billing/exceptions.py` — new `RefundError(Exception)` family):
   - no payment-intent id → `RefundNotPossibleError` ("No Stripe payment on file.")
   - `refundable_cents == 0` → `AlreadyRefundedError`.
   - `amount_cents` (when given) must be `0 < amount <= refundable_cents` → `InvalidRefundAmountError`. `None` means full remaining.
   - **Deliberately NO status-list guard** (review blocker 1): the old draft's `CONFIRMED|CANCELLED`-only guard would make a registration whose refund later *failed* (status already `REFUNDED`, refundability restored) permanently unretryable. Refundability — `refundable_cents > 0` with a payment id — IS the guard. `PENDING`/`WAITLISTED` rows never took money, so they fail the refundable check naturally.
2. **Race-proof creation** (review major 4 — `charge.refunded` also fires for API-created refunds, and can land before our row is stamped): inside ONE `transaction.atomic()` block, `select_for_update()` the **source row**, create the `PaymentRefund` (`PENDING`, `source=IN_APP`, `initiated_by=actor`), call Stripe, and stamp `stripe_refund_id` + final status **before commit**. The webhook handlers (§5.4) also lock the source row first, so a webhook for our own refund serializes behind this transaction and, on entry, finds the already-stamped row by `stripe_refund_id` — no duplicate row, no duplicate receipt. Holding a single-row lock across one short Stripe call is a deliberate, documented tradeoff; it is the entire race guard.
3. Idempotency key: **`pay-refund-{refund.pk}-a{refund.attempt}`** — per-refund-row (multiple partials each get their own row/key) and per-attempt (Retry gets a fresh key by design, so Stripe doesn't replay the failed attempt's cached error). This supersedes the earlier class-shaped `class-refund-reg-{pk}` sketch with a source-neutral convention.
4. **Stripe failure** → stamp the row `FAILED` + `failure_reason` + `settled_at`, re-raise as `RefundError(stripe_message)`. The view surfaces Stripe's message loudly; no bookkeeping happens. The FAILED row remains as audit and as the Retry anchor.
5. **Stripe success** → `_mark_succeeded(refund)` (§ below).

**`retry_refund(refund, *, actor=None) -> PaymentRefund`** — the panel's **Retry refund** action (contract). Guards: row is `FAILED` (retrying a PENDING/SUCCEEDED row → `RefundError`). Under the same source-row lock: `attempt += 1`, status → `PENDING`, clear `failure_reason`, re-invoke `create_refund` with the fresh `-a{attempt}` key, then the same failure/success handling. Same row, not a new one — the ledger shows one refund with N attempts, which is the truth.

**`_mark_succeeded(refund)`** — side effects fire on the **transition into SUCCEEDED, exactly once** (review major 3), guarded by `settled_at`/prior-status, NOT on row creation. Wherever the transition comes from — service success path, webhook upsert arriving already-succeeded, or a webhook flipping an old PENDING row to succeeded later — the same effects run:
- stamp `SUCCEEDED` + `settled_at`;
- emit the **`refund_issued`** receipt to the payer with the *actual* refunded amount, context from `source.refund_receipt_context()`, and `period=f"pay-refund:{refund.pk}"` (unique per refund row, so a second partial actually delivers — the old `reg:{pk}:refund` period would dedupe it away);
- if the source is now fully refunded (`refundable_cents == 0`), call `source.on_fully_refunded(reason, actor)`. For a Retry that re-succeeds on an already-`REFUNDED` registration, `mark_refunded` runs again but is harmless: `promote_next_from_waitlist` is guarded by `previously_held_a_spot` (a REFUNDED row no longer holds one), so no double promotion — §9 pins this with a test.

**Change to `Registration.save()`:** the receipt-emit block at `classes/models.py:2102-2116` is **removed** from the save-transition — receipts now live with the refund row (correct amount, correct dedupe). The save-transition audit log stays put. `mark_refunded` keeps its signature and everything else.

### 5.3 Refund state semantics

| `refund_state` | Meaning | Panel badge | Action shown (to refund authority) |
|---|---|---|---|
| `none` | No succeeded or failed refunds | Paid | Refund |
| `partial` | Some, not all, refunded | Partially refunded | Refund (remainder) |
| `full` | Fully refunded | Refunded | — |
| `failed` | Latest attempt FAILED, not since covered | Refund failed | **Retry refund** |

### 5.4 Webhook reconciliation

Registered in `_WEBHOOK_HANDLERS` (`billing/views.py:28-36`); handlers live in `classes/webhook_handlers.py` and delegate row lifecycle to `billing/refunds.py`:

- **`charge.refunded`** → `handle_charge_refunded(event)`. Resolve the source by `payment_intent` **across BOTH sources** (contract): `Registration.stripe_payment_id`, then the orientation booking's payment-intent field (companion spec; a documented lookup seam until then). No match → not a refundable source we know (a Tab charge or unknown) → **log loudly** (`logger.warning` with the payment-intent id; Tab reconciliation deferred, §10). With a match: `transaction.atomic()` + `select_for_update()` on the source row (serializing behind any in-flight `issue_refund`, closing the race in §5.2), then for each refund on the charge, `update_or_create` by `stripe_refund_id` — idempotent under Stripe's re-delivery. New rows get `source=STRIPE_DASHBOARD`, `initiated_by=None`. Any row **transitioning into SUCCEEDED** (newly created succeeded, or a known PENDING flipping) goes through `_mark_succeeded` — so a refund issued by hand in the Stripe dashboard flips the local record, frees the seat, and emails the payer exactly like an in-app one, and a pending-then-succeeded refund is not silently swallowed (review major 3). Rows already SUCCEEDED are left untouched.
- **`refund.updated`** → `handle_refund_updated(event)`. Lookup by `stripe_refund_id` across the ledger (source-neutral — the row knows its side). `status == "failed"` → flip the row to FAILED + `failure_reason` + `settled_at`, and emit the **`refund_failed`** admin alert (§7). If the source had been auto-marked fully refunded by that refund, do **not** silently unwind seat/waitlist — the alert + panel Retry is the recovery path, and the alert copy tells the admin the payer already holds a receipt (review major 8). `status == "succeeded"` on a PENDING row → `_mark_succeeded`. Unknown `stripe_refund_id` → **`logger.warning`**, return.

**Stale-PENDING sweep** (review major 4's "forever-PENDING" answer): a `PENDING` row with a **blank** `stripe_refund_id` means the process died between row-create and Stripe answering. New management command `billing/management/commands/sweep_stale_refunds.py`: rows PENDING > 24h with no `stripe_refund_id` → FAILED, `failure_reason="Never reached Stripe. Retry from the Payments panel."` (which lights up the normal Retry path). Rows PENDING **with** an id are left alone — `refund.updated` owns their fate. Wired into the existing nightly Render cron alongside the other sync jobs. The panel shows Pending rows with their age so a stuck one is visible before the sweep runs.

### 5.5 Panel aggregation (`billing/payments_panel.py`)

A read-only module — no model. `PaymentRow` frozen dataclass with a **source-neutral identity** (contract):

`source_kind` (`"tab"` / `"class"` / `"orientation"`), `source_pk`, `payer_name`, `payer_url` (nullable — see linking rule below), `item`, `amount_cents`, `status`, `date`, `refund_rows`, `can_refund` (bool), `failed_refund_pk` (nullable — powers Retry).

**Per-source status derivation** (review minor 10 — "Failed" means different things per source, so the badge says which):

| Source | Paid | Partially refunded | Refunded | Failed badge text | Pending |
|---|---|---|---|---|---|
| Class | confirmed, `refund_state=none` | `partial` | `full` | **"Refund failed"** (`refund_state=failed`) | "Refund pending" (+ age) |
| Tab | `TabCharge` SUCCEEDED | — (until reconciliation ships) | — | **"Charge failed"** (`TabCharge.Status.FAILED`) | — |
| Orientation | (companion spec, same shape as Class) | | | | |

The **Failed** filter chip matches both meanings (any red row); the badge text disambiguates.

Row builders:
- **Tabs**: `TabCharge.objects.exclude(status=PENDING).select_related("tab__member")` in window. `can_refund=False` always (deferred).
- **Classes**: `Registration.objects.filter(amount_paid_cents__gt=0).exclude(stripe_payment_id="")` in window (by `confirmed_at`), `select_related("class_offering", "member")` + `prefetch_related("refunds")`. `can_refund = refundable_cents > 0` (template additionally gates on viewer authority). **Guest payers** (review minor 14): `payer_name = member.display_name if member else f"{first_name} {last_name}".strip() or email` — the detail page is keyed by registration pk, not member, so the payer link resolves for guest rows identically.
- **Orientation**: `_orientation_rows()` returning `[]` today — the documented seam the companion spec fills. Its filter chip ships hidden until then.

**Payer linking rule** (review major 5): the CMS registration detail is gated by `classes_registrations_access_required` (admins, or editors of that class) — a non-admin Billing Administrator would 403. So `payer_url` is set **only for fog-admin viewers**; for everyone else the payer renders as plain text. Tab rows keep the existing tab-detail modal opener for all panel viewers (it's a billing surface).

Merged in Python, sorted date-desc. Default window = current month (same default as `admin_reports`); 500-row cap with a "narrow the date range" notice. No pagination machinery — YAGNI at this volume; the date filter is the natural pager. `stream_payments_csv(...)` mirrors `stream_report_csv` — same streaming pattern, columns matching the table plus refund detail columns (refund status, amount, attempt, source, settled date).

### 5.6 Permissions plumbing

- `Member.has_capability(capability: str) -> bool` — `self.admin_capabilities.filter(capability=capability).exists()`.
- `hub/view_as.py`:
  - `billing_admin_access_required` — passes fog-admins (actual role, preview-independent, like `fog_admin_required`) **or** `BILLING_APPROVER` holders. Applied to: `admin_tab_dashboard`, `billing_admin_tab_detail_api`, `billing_admin_retry_charge` (charge retry is the billing administrator's day job — they already get the failure alerts), `admin_reports`, `admin_reports_csv`, the payments-table partial view, and `admin_payments_csv`.
  - `refund_authority_required` — passes fog-admins **or** `REFUNDS` holders. Applied to `admin_registration_refund`, the refund-form partial view, and `payment_refund_retry`.
- Views that touch Stripe credentials or billing config stay **`fog_admin_required`**: `billing_admin_save_settings`, `billing_test_platform_connection`, `billing_save_platform_connection`. A Billing Administrator sees Overview / Open Tabs / Payments; Settings and Stripe tabs stay admin-only (nav links hidden AND views 403).
- `admin_registration_refund` drops `classes_admin_access_required` in favor of `refund_authority_required`. **A `REFUNDS` holder may refund any registration** — that is what the grant means; it's a trusted duty handed to a person, and surfaces control discovery. Spelling this out so nobody later "fixes" it into per-class scoping without deciding to.
- **The grant opens no pages** (review major 5): `REFUNDS` adds Refund/Retry buttons to payment surfaces the holder can *already* reach (teach portal if they teach or lead, CMS detail if they can edit the class, the panel if they also hold Billing Administrator or admin). A plain member holding only `REFUNDS` has working endpoints and zero buttons — by design, and the capability help text says exactly that (§6.5) so an admin granting it pairs it appropriately. The full reachable-surface matrix is §6.7.

**`REFUNDS` routes no notifications** — the first action-only capability (docstring amendments per §4.3). Refund-failure alerts route to the existing `BILLING_APPROVERS` recipients (§7). No new recipients group, no settings-matrix row for REFUNDS.

### 5.7 The CMS Administrator rename sweep

Value `class_approver` untouched everywhere. Display-string sweep, verified by grep:

| File | What |
|---|---|
| `membership/models.py:2113` | Choice label |
| `hub/forms.py:660` | `cap_class_approver` field label |
| `core/events/copy.py:91,93` | Recipient descriptions ("The CMS Administrators (holders only).") |
| `core/events/resolvers.py:170,177`, `registry.py:285-287`, `settings_matrix.py:135` | Docstrings/comments |
| `classes/emails.py:385-391` (docstring, incl. :389) and `:410` (validation-request docstring) | Docstrings |
| `membership/CLAUDE.md:75` | Doc table |
| `tests/membership/admin_capability_spec.py:30`, `tests/hub/member_admin_capabilities_spec.py:76` | Assertions updated to "CMS Administrator" |
| `classes/spec/emails_review_spec.py:167,187` | Spec docstrings referencing "the Class Administrators" |

**Leave alone:** the historical `CHANGELOG` entry in `plfog/version.py:254` — released changelog text is a record, not live copy. (And remember the changelog renders into every hub page's context — do not add "Class Administrator" negative assertions anywhere.)

## 6. UI / UX

### 6.1 Payments tab — `templates/billing/admin_dashboard.html`

- **Layout & container:** fifth entry in the existing `.pl-tab-nav` (`?tab=payments`), between **Open Tabs** and **Settings**. Server-rendered like every other tab (`{% if active_tab == "payments" %}`); `_VALID_TABS` gains `"payments"`. The template **extends `hub/base.html`** — hub CSS, Alpine, HTMX, and the toast component are all present (correcting the earlier draft's claim that this was an Unfold page without them). Its dark `pl-*` literal-color styling remains the page's look; new markup follows it, and the hub component set is fully available where needed (modal, toast, `.pl-help`).
- **Tab-nav gating:** for a non-admin Billing Administrator, the Settings and Stripe links are absent (context flag `viewer_is_fog_admin`); the views 403 regardless.
- **Header row (`.pl-stats pl-stats--3`):** *Collected* · *Refunded* · *Net* — respecting the active filters.
- **Toolbar (`.pl-toolbar`):** source chips (`.pl-filter`): All / Tabs / Classes (+ Orientation, hidden until that source exists) · status chips: All / Paid / Refunded / Partial / Failed (Failed matches both "Refund failed" class rows and "Charge failed" tab rows — §5.5) · date range: two `<input type="date">` fields + Apply (GET form preserving `tab=payments`). Rule-14 treatment on the pickers: `filter: invert(1)` with the `[data-theme="light"]` reset, and `showPicker()` in a try/catch — the page lives under the hub's theme toggle even though its palette is dark-fixed. · Right: **Export CSV** (`.pl-btn--secondary pl-btn--sm`, links `admin_payments_csv` with the current query string).
- **Table:** rendered by the new partial `billing/partials/payments_table.html`, included by the page AND served standalone by a `billing_admin_payments_table` partial view — the same markup powers the initial render and the post-refund HTMX refresh (§6.2). The table wrapper: `<div class="pl-table-scroll" hx-get="{% url 'billing_admin_payments_table' %}?{{ query_string }}" hx-trigger="refund-done from:body" hx-swap="innerHTML">`. Columns: Source badge · Payer (link rule §5.5; guest fallback name) · Item · Amount (refund lines beneath, muted: "− $25.00 refunded Jun 3") · Status badge (per-source text, §5.5) · Date · Action.
- **The controls, named explicitly:**
  - **Refund** — `.pl-btn--danger pl-btn--sm` on class rows where `can_refund` AND the viewer passes refund authority. Opens the refund modal (§6.2).
  - **Retry refund** — same styling, on class rows with `refund_state == "failed"` (same authority gate). Opens a slim confirm step in the same modal: the failed attempt's amount, Stripe's failure reason, and a danger **"Retry refund"** submit posting to `payment_refund_retry` — no editable fields (the amount is the failed row's; a different amount is a new Refund).
  - Billing Administrators without the Refunds grant see rows, badges, and history — no action buttons.
  - Tab rows: no refund button; a `.pl-help` "?" on the tab-source badge explains: "Tab charges are refunded in Stripe for now. Refunds made there will appear here once tab reconciliation ships." Plus a muted "Stripe ↗" payment link. (The earlier "no `.pl-help` on this page" workaround is deleted — the hub CSS is loaded.)
  - No list editing here — read-only table; no Add/Delete/formset machinery applies.
- **States:** *Empty* — "No payments in this window. Widen the date range or clear filters." centered muted row. *Loading* — initial render is server-side; the HTMX table refresh and modal body show the standard in-flight treatment (htmx `.htmx-request` opacity on the wrapper; "Loading…" placeholder in the modal). *Error* — a failed refund POST keeps the modal open and raises an **error toast** with Stripe's message (§6.2). *Success* — success toast + table refresh via `refund-done`; the row now shows the refund line and updated badge. *Pending rows* — "Refund pending" badge with age ("2 h"), so a stuck refund is visible before the nightly sweep acts. *Cap hit* — "Showing the most recent 500 of N payments — narrow the date range." banner.
- **Dark + light:** the page's own `pl-*` chrome is dark-fixed by existing design, but everything it now *hosts* from the hub component set (modal, toast, form fields, `.pl-help`) is theme-aware and correct in both themes — that is the §6.2 host rule. Date inputs handled per Rule 14 above. Verify both themes.
- **Mobile:** stat grid already collapses under 768px; `.pl-table-scroll { overflow-x: auto; }` keeps the ledger scrolling within itself; Refund/Retry stay full-size tap targets.

### 6.2 Refund modal (one pattern, three hosts)

- **Screen / partial:** `templates/classes/partials/refund_form.html`, served by `classes:admin_registration_refund_form` (GET, gated `refund_authority_required`).
- **Layout & container:** 2 fields → modal (FRONTEND interaction table). **All three hosts use the hub `components/modal.html` pattern** — dashboard included, since it extends `hub/base.html` (review major 7): one `{% include "components/modal.html" with modal_id="refund-modal" modal_title="Refund Payment" modal_size="sm" %}` per host page; each Refund button does `@click="$dispatch('open-modal', 'refund-modal')"` + `hx-get` of the partial into `#refund-modal-body`. This kills the earlier two-pattern split (hub modal here, vanilla `pl-modal` there) and its theming bug: the partial's `.hub-form-group` fields are theme-aware, and so is `modal.html`'s card — a light-theme viewer gets light fields on a light modal card, on every host, including atop the dark dashboard (the modal overlays the page; it does not inherit the page's fixed palette). No host parameter needed.
- **Components used:** `modal.html`; fields via `components/form_field.html`; toasts via `trigger_toast()`.
- **Form:** `PaymentRefundForm` (`classes/forms.py`) — validation lives here, not the view:
  - `amount` — DecimalField, **pre-filled with the full refundable amount** (full refund is the default; editing it down makes it partial). `field_hint`: "Up to $X.XX. Edit for a partial refund." `clean_amount` enforces `0 < amount <= refundable`; error: "Enter an amount between $0.01 and $X.XX."
  - `reason` — CharField, optional, hint "Internal note. The payer never sees this." Stored on `PaymentRefund.reason`.
- **Header content** (review minor 11): payer name · class title · **"Paid $Y.YY on Jun 3 · Class starts Jul 10"** (paid-on = `confirmed_at`, plus first session date) so same-titled offerings are distinguishable · "$Z.ZZ already refunded" when nonzero.
- **The controls:** danger-styled submit **"Refund $X.XX"** (label live-updates to the amount field; falls back to "Issue Refund"), disabled while submitting (idempotency key is the backstop). Cancel/× closes with no action. Submit is the last element. Consequence line above it: "This sends money back to the payer's card. A full refund frees their seat and promotes the waitlist." The modal IS the confirmation — no second `confirm_modal` layered on top.
- **Success / refresh mechanism** (review minor 12 — named once, used by every host): the refund and retry POSTs return **`204` + `trigger_toast(...)` + `HX-Trigger: refund-done`**. A 204 has no body, so no OOB swaps; instead each host wraps its refund-affected region in a container bearing `hx-get="<its partial url>" hx-trigger="refund-done from:body"`: the dashboard's payments-table wrapper (§6.1), the detail page's Refunds card (§6.3), the teach portal's registrations table (§6.4). A tiny shared listener on `refund-done` also dispatches `close-modal`. §6.1/§6.3/§6.4 all agree on this one mechanism.
- **States:** *Loading* — "Loading…" while the partial fetches. *Validation error* — partial re-renders in place with the field error (form POST targets the modal body for non-2xx form re-render). *Stripe failure* — loud: error toast carrying Stripe's message, modal stays open, nothing bookkept (the FAILED audit row excepted — it is the Retry anchor). *Success* — toast + `refund-done` refresh + modal close.

### 6.3 CMS registration detail — `templates/classes/admin/registration_detail.html`

- The **"Mark Refunded" form, its reason input, and the "Issue refund in Stripe ↗" link die** in this feature.
- **Placement** (review major 6 — the Admin Actions block at `:143` renders only for `actual_is_admin`, but instructors/leads reach this page, and the roster/waitlist sibling spec deep-links Refunds holders straight here): refunds move into their own **"Refunds" card**, OUTSIDE the `actual_is_admin` gate, rendered whenever the registration has a paid amount. Contents by viewer:
  - **Refund authority (fog-admin or REFUNDS holder):** the **Refund** button (`hub-btn hub-btn--sm hub-btn--danger`, shown while `refundable_cents > 0`) opening the shared modal (§6.2); **Retry refund** on a failed refund; and the **full refund history** — per refund: amount, status badge, attempt count when > 1, date, source ("in app by {name}" / "Stripe dashboard"), **internal reason**, failure reason in muted red on failed rows.
  - **Page-access viewers without refund authority** (instructor/lead of this class): the refund history **without the internals** — amount, status, date only; no reason, no initiated-by (internal notes are for refund authority + admins only). Where the button would sit, the muted line (review minor 13): **"Refunds require the Refunds permission. Ask an admin."**
  - Helper line under the button: "Refunds are sent to the payer's card through Stripe. A full refund frees the spot and promotes the waitlist."
- The Admin Actions block keeps Move/Cancel unchanged (still admin-gated) and simply loses its refund form. Fully-refunded/cancelled registrations keep the "no further actions" fallback there, with the Refunds card still telling the money story above it. The Refunds card wraps in the `refund-done` refresh container (§6.2). Buttons keep ≥ `0.75rem` clearance (Rule 18).
- **Dark + light:** theme-aware page — tokens only (`--hub-*`), existing badge classes. Verify both. **Mobile:** the card is a stacked list, not a table; actions already `flex-wrap`.

### 6.4 Teach portal — class registrations page (`teach_class_registrations`, `classes/views.py:1510`)

- **Refund button appears ONLY when the acting user holds `REFUNDS`** (or is an admin visiting the teach portal). No capability → no button, no empty column — instructors without the grant see the page exactly as today (no "ask an admin" line here; the teach portal stays uncluttered, the CMS detail page carries that explainer).
- Per paid registration row (`refundable_cents > 0`): `pl-btn pl-btn--danger pl-btn--sm` "Refund" → shared modal via `hx-get` (one `refund-modal` include per page). Failed-refund rows show **Retry refund** instead, same gate. Refunded registrants get the same badge treatment as the CMS list.
- **States:** success toast + table refresh via the `refund-done` container (§6.2 — the registrations table gets a small partial endpoint for the re-fetch); Stripe failure → error toast, modal open. Page-level empty/loading states unchanged.
- **Dark + light:** tokens only; verify both. **Mobile:** existing table behavior unchanged; the button is a real tap target.

### 6.5 Member edit — Permissions tab (capability grant UI)

- `MemberCapabilitiesForm` (`hub/forms.py`) gains:
  ```
  cap_refunds = BooleanField(required=False, label="Refunds",
      help_text="Can send Stripe refunds for class and orientation payments. "
                "Adds Refund buttons on payment pages this member can already reach. "
                "It does not open any new pages, so pair it with Billing Administrator "
                "for the Payments panel.")
  ```
  plus the `_FIELD_TO_CAP` entry. The tab renders each field as a toggle via `form_field.html` auto-detection — no template change; `sync_admin_capabilities` already grants/revokes idempotently on Save.
- `cap_class_approver` label becomes **"CMS Administrator"**; `cap_billing_approver` help text updated: "Sees the admin Payments dashboard and gets an alert when a member's automatic payment fails." Both form and model docstrings amended per §4.3.
- Save button stays last, still "Save" (Rule 21). States/themes/mobile: unchanged page.

### 6.6 Notification settings page

CMS Administrator strings update per §5.7. The `refund_failed` alert (§7) appears in the Billing Administrators' matrix group automatically once registered. No REFUNDS row appears in notification settings — it routes nothing.

### 6.7 Reachable-surface matrix (who sees what, where)

| Viewer | Payments panel | Settings/Stripe tabs | Refund/Retry buttons | CMS registration detail | Teach portal button |
|---|---|---|---|---|---|
| fog-admin | Yes | Yes | Everywhere | Full page + Refunds card w/ internals; payer links live | Yes (admins pass the authority gate) |
| BILLING_APPROVER only | Yes (Overview / Open Tabs / Payments) | No (links hidden, views 403) | No — sees rows/history, no buttons | No access → panel payer names render as plain text (§5.5) | No |
| REFUNDS only | No | No | On pages they can already reach | Only if they can edit that class (existing gate); then Refunds card **with** internals | Yes, on their own classes |
| REFUNDS + BILLING_APPROVER | Yes | No | Panel + reachable pages | As above | As above |
| Instructor/lead, no grants | No | No | No — "Refunds require the Refunds permission. Ask an admin." on the detail page | Their classes only (existing); history without internals | Page yes, button no |

A REFUNDS-only plain member (no classes, no billing grant) has authority and zero buttons — deliberate, documented in the grant's help text (§6.5).

## 7. Notifications / emails / activity

| Event | Change |
|---|---|
| `refund_issued` (exists, `force_email=True`) | Emission moves from the `Registration.save()` REFUNDED-transition to the service's succeeded-transition (§5.2). Context `amount` = the **actual refund amount**; `period` = `pay-refund:{refund.pk}` (unique per refund, so partials deliver). Copy **generalized to class and orientation payments** (contract): the `class_title` placeholder becomes `item_title` (a class title, or "Makerspace orientation" from the source's `refund_receipt_context()`), and `registration_url` becomes the source's manage URL — subject "Refund issued for {{ item_title }}", body otherwise unchanged (amount, 5-10 business day window, manage link; already absolute-URL'd, branded, guest-safe). `.txt` and `.html` stay in sync. |
| `refund_failed` (NEW trigger, `core/triggers.py`, category Billing, `force_email=True`) | Fired on an async refund failure (§5.4). Recipients: `BILLING_APPROVERS` (existing resolver — no new group). Copy (`core/events/copy.py`): subject "A refund failed"; body names payer, item, amount, Stripe's failure reason, links the registration's CMS detail page (absolute URL — subject noun linked, one CTA: "Review and retry"), and includes the line (review major 8, payer holds a receipt for money that never arrived): **"They've already received a refund receipt. Contact them after retrying."** Registered in `settings_matrix` under the staff group like the other billing alert. |
| Activity | The existing REFUNDED save-transition audit log stays. `CmsActivity` additionally logs `REGISTRATION_PARTIAL_REFUND` from the succeeded-transition when the refund doesn't flip status, and `REGISTRATION_REFUND_FAILED` on a failure — both visible in the class activity feed. |

## 8. Build order (phased; each phase ships green)

1. **Permissions groundwork** — `Capability.REFUNDS` + label rename + choices migration; docstring amendments (model + form); `Member.has_capability`; `billing_admin_access_required` / `refund_authority_required` in `hub/view_as.py`; decorator swaps on the billing views (§5.6); `MemberCapabilitiesForm` field + rename + help texts; §5.7 copy sweep incl. spec assertions; dashboard tab-nav gating flag. Run `manage.py check` (E034) + full suite.
2. **Refund engine (billing-side)** — `PaymentRefund` model + constraint + migration; `create_refund` in `stripe_utils`; `billing/refunds.py` service (protocol, issue, retry, succeeded-transition, locking) + domain exceptions; `Registration` protocol hooks + thin `issue_refund` + refund properties; receipt-emit relocation; `refund_failed` trigger/copy/matrix + `refund_issued` generalization; webhook handlers + `_WEBHOOK_HANDLERS` wiring; `sweep_stale_refunds` command + cron wiring. No UI yet — engine fully specced and green.
3. **Refund UI** — `PaymentRefundForm`; refund-form partial view + template; upgraded `admin_registration_refund` endpoint + `payment_refund_retry`; the `refund-done` 204/HX-Trigger contract; registration detail Refunds card (§6.3); teach portal button + table partial (§6.4).
4. **Payments panel** — `billing/payments_panel.py` (+ orientation seam); `admin_tab_dashboard` "payments" context; `payments_table.html` partial + partial view; template tab + toolbar + modal host; `admin_payments_csv`.
5. **Housekeeping & release** — `plfog/version.py` VERSION bump to the next `1.6.x`; **ONE** member-facing changelog entry stamped at that VERSION (edit/fold per the changelog rules if this line already has an unshipped entry), plain language, roughly: *"Refunds are now handled right in the app — when we refund a class payment, the money goes back to your card automatically and you get a receipt email. Admins also have one place to see all payments."* No jargon, no PR numbers.

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py`, `describe_*`/`it_*` (never `context_*` — it silently skips), factory-boy, 100% branch coverage, mutation-clean. Stripe is mocked the way the suite already does it: `@patch("billing.stripe_utils.create_refund")` at call sites (like `register_series_spec.py:61`), and `stripe.StripeClient` patched inside `tests/billing/stripe_utils_spec.py` for the wrapper itself. Never mock models or the DB. New factory: `PaymentRefundFactory` (billing factories, defaulting to the registration side).

- `tests/billing/stripe_utils_spec.py` — `describe_create_refund`: full vs partial params, idempotency key passthrough, return-dict shape, error propagation.
- `tests/billing/payment_refund_spec.py` — CheckConstraint (neither / both FKs set → IntegrityError); `source_object` / `source_kind`; queryset helpers; `__str__`.
- `tests/billing/refunds_service_spec.py` —
  - guards: no payment id, zero refundable, over-amount → each domain exception; **REFUNDED-status registration with a failed refund IS refundable again** (blocker 1's regression test);
  - partial keeps status CONFIRMED, emits with partial amount + unique period; second partial's receipt delivers (distinct period);
  - full calls `on_fully_refunded` → seat freed, waitlist promoted;
  - Stripe failure: row FAILED + reason, `RefundError` re-raised, no status change;
  - **transition-only side effects**: a PENDING row flipped to SUCCEEDED (webhook path) fires receipt + bookkeeping exactly once; an already-SUCCEEDED row re-processed fires nothing (settled_at guard);
  - **retry**: FAILED-only guard, attempt bump, fresh `-a{n}` key, re-success runs bookkeeping without double waitlist promotion (the `previously_held_a_spot` guard, pinned);
  - receipt no longer double-sends on the save transition (relocation regression test).
- `classes/spec/webhooks/refund_webhooks_spec.py` — `charge.refunded`: dashboard-sourced upsert creates SUCCEEDED row + bookkeeping; re-delivery no-op (idempotent by `stripe_refund_id`); **own in-app refund not duplicated when the webhook lands after the stamped commit** (simulate ordering: stamped row exists → upsert finds it); unknown payment intent → `logger.warning`, no raise; PENDING→SUCCEEDED flip fires effects. `refund.updated`: FAILED flip + `refund_failed` emitted to billing approvers with the "already received a receipt" line; succeeded flip on pending row; unknown refund id → warning.
- `tests/billing/sweep_stale_refunds_spec.py` — id-less PENDING > 24h → FAILED with the retry-pointing reason; id-bearing PENDING untouched; fresh PENDING untouched.
- `tests/billing/payments_panel_spec.py` — merge/sort across sources; source-neutral `source_kind`/`source_pk`; per-source status derivation incl. "Refund failed" vs "Charge failed" and the Failed chip matching both; guest payer-name fallback (member-less rows) and payer link still resolving; payer_url None for non-admin viewers; 500-row cap; CSV streams same rows + refund columns; orientation seam returns empty.
- View gating specs: dashboard reachable by fog-admin and BILLING_APPROVER, 403 for plain member; Settings/Stripe tabs + credential views 403 for the approver; refund + retry endpoints 403 without REFUNDS/admin, 200 with either; refund POST returns 204 + `HX-Trigger: refund-done` + toast header; teach button rendered only for holders; detail-page Refunds card outside the admin gate (instructor sees history without reasons + the "ask an admin" line; REFUNDS holder sees button + internals); tab-nav links hidden for non-admin approvers.
- `PaymentRefundForm` spec — amount bounds, prefill, optional reason.
- Membership/hub: `cap_refunds` grant/revoke via `sync_admin_capabilities`; renamed labels asserted ("CMS Administrator").
- Gotchas: date-window filters compare in the project timezone (Portland) — a `confirmed_at` just past UTC midnight must not leak into the wrong month bucket; run `manage.py check` after the model migration (constraint/index name caps).

## 10. Open / deferred

- **Tab charge refund action** — deferred by locked decision. A `TabCharge` is one batched PaymentIntent spanning many `TabEntry` rows with frozen `TabEntrySplit` revenue splits; a money refund must decide *which entries* (and which guilds' revenue) it reverses — a split-reversal design of its own. History displays now; the Stripe link covers the rare manual case.
- **Tab charge webhook reconciliation** — a dashboard refund of a tab PaymentIntent is currently warned-and-skipped by `handle_charge_refunded`. Reconcile it (flip `TabCharge` status, adjust reports, then let `PaymentRefund` grow a `tab_charge` FK under the same exactly-one constraint) together with the item above.
- **Orientation payments** — the engine (model FK, service, webhook lookup seam, panel `_orientation_rows()`, hidden filter chip) ships orientation-ready; `OrientationBooking.issue_refund`, its `RefundableSource` implementation, and its panel rows land in `2026-08-26-paid-orientations.md`, which is written against this contract.
- **Record-only refunds** (cash-in-hand, outside Stripe) — the old "Mark Refunded" behavior has no button anymore. If a real need appears, it's a `PaymentRefund` with a `MANUAL` source, not a revival of the untracked path.
- **Ledger pagination** — deliberately skipped at current volume; the date window is the pager. Revisit if the 500-cap banner is ever actually seen.
- **Per-row "can this viewer open this registration" payer links** — the panel links payers for fog-admins only (§5.5); computing the exact per-row CMS access for leads/instructors viewing the panel isn't worth the query cost for an audience that is overwhelmingly admins. Revisit if a non-admin panel audience materializes.
