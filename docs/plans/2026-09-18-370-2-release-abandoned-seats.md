# 370 item 2: an abandoned signup stops holding a seat forever, and a stuck one can be rescued

Parent issue: https://github.com/Past-Lives-Makerspace/plfog/issues/370 (item 2)
Builds on: #419, which fixed item 1 and left this deliberately out of scope.

## Two problems, one lifecycle

### A. Nothing reaps an abandoned PENDING row

`create_class_checkout_session` (`billing/stripe_utils.py:216`) does not pass `expires_at`,
even though the shared `create_checkout_session` it delegates to already supports it
(`:173`, `:207`). Class sessions therefore live for Stripe's 24h default.

`checkout.session.expired` **is** dispatched (`billing/views.py:47, 61`) but routed only to
`membership_webhook_handlers` (`:36`). **Classes has no expired handler at all.**

`spots_remaining` counts PENDING, so an abandoned signup holds a seat indefinitely and the
class silently sells out early. Production carries 14 such rows, 7 to 115 days old.

### B. A PENDING row is a dead end with no staff exit

Every adjacent tool refuses it, because they all guard on `is_unpaid`
(`classes/models.py:3440`), which is `status == CONFIRMED and balance_due_cents > 0`:

| Tool | Refuses a PENDING row because |
|---|---|
| `registration_mark_paid` to `Registration.mark_paid` (`:3496`) | `is_unpaid` needs CONFIRMED |
| `send_payment_link_email` | same `is_unpaid` guard |
| `promote_from_waitlist` | requires WAITLISTED |
| nothing in `classes/urls.py` | there is no PENDING to CONFIRMED route at all |

So a stuck registration cannot be confirmed, cannot be emailed, cannot be sent a payment
link, and keeps holding a seat. The only staff move is cancel.

`checkout.session.async_payment_succeeded` is **not** in `_WEBHOOK_HANDLERS`
(`billing/views.py`). A delayed-notification payment method completes the session with
`payment_status` of `unpaid`; the handler logs "ignoring session" and returns, and the later
success event has no listener. Money taken, row stuck forever, seat held.

## The pattern to mirror, not reinvent

Orientations solved exactly this:

| Piece | Orientations | Classes today |
|---|---|---|
| Session lifetime | `_CHECKOUT_SESSION_LIFETIME = 1h` passed as `expires_at` (`membership/orientations.py:49, 541`) | none |
| Primary release | `handle_checkout_session_expired` deletes the holding row (`membership/webhook_handlers.py:162`) | not wired |
| Backstop | `_HOLD_SWEEP_AGE = 2h` sweep, also recovers lost webhooks (`orientations.py:50`) | none |

**Decision: mirror those numbers, 1h expiry and a 2h sweep.** A class registrant reads the
waiver before checkout, not during it, so the orientation window fits.

## The build

1. **Pass `expires_at`** on the class Checkout Session, 1h, using the constant the
   orientation flow already defines or a classes equivalent alongside it.

2. **A classes `handle_checkout_session_expired`**, subscribed in `billing/views.py`'s
   expired dispatcher alongside the membership one, filtered on
   `kind == "class_registration"`. It releases the seat.

   **Release means CANCEL, not delete.** #419 established that a registration carries
   waivers, custom answers and an audit trail, and that deleting a row that existed before
   this request throws them away. Cancel with a reason naming the expiry.

3. **A sweep backstop**, registered as a scheduled job, cancelling PENDING rows older than
   2h whose session is not live. **Registering a job means updating the cadence tuples in
   `core/spec/scheduled_jobs_spec.py`** — that parity spec goes stale every round that adds
   a job, and a missing entry is a red CI on main.

4. **Route `checkout.session.async_payment_succeeded`** to the same
   `handle_checkout_session_completed`, which is already idempotent and already gates on
   `payment_status == "paid"`. Route `async_payment_failed` to the release path.

5. **A staff confirm action for a PENDING row.** Jo's decision: **confirm with the balance
   still owed**, not as paid. It sets CONFIRMED with `payment_due_cents` intact, so the
   existing `mark_paid` and payment-link tooling immediately applies and the ledger stays
   honest. The `reg:{pk}:confirmation` emit period (`classes/emails.py:138`) already
   guarantees exactly-once delivery across this path and the webhook, so the two cannot
   double-send.

   **Do not trust `amount_paid_cents` on a PENDING row.** `classes/views.py` stamps it
   provisionally at session-mint time; it is not money received. Reading it as payment is
   the exact bug that nearly failed the #419 deploy.

## Interactions with #419 you must not break

- `_resume_pending_checkout` mints a replacement session on the same row when the stored
  one is not open. The reaper must not race it into cancelling a row a registrant is
  actively paying for. The sweep's age threshold must stay strictly greater than the
  session lifetime, which is why orientations use 2h against 1h.
- Cancelling a row frees its slot under `uq_registration_seat_email`, which is correct: a
  registrant whose seat expired may sign up again.
- The resume path's ownership gate (`_owns_registration`) and the session pk list are not
  yours to change.
- A cancelled row can still receive a late webhook. #419 built `_record_orphaned_payment`
  for exactly that. An expiry-cancelled row that then gets paid must go through it, not
  raise.

## Acceptance criteria

1. A class Checkout Session is created with `expires_at` one hour out.
2. `checkout.session.expired` for a class registration cancels the row and frees the seat,
   with a reason naming the expiry. Waivers and answers survive.
3. The sweep cancels a PENDING row older than the sweep age whose session is not live, and
   leaves alone one whose session is still open.
4. The sweep is registered and `core/spec/scheduled_jobs_spec.py`'s cadence tuples updated.
5. `async_payment_succeeded` confirms the row and sends the confirmation exactly once.
6. `async_payment_failed` releases the seat.
7. Staff can confirm a PENDING row; it becomes CONFIRMED with the balance still owed, the
   confirmation email sends once, and `mark_paid` and the payment-link tooling then work on
   it.
8. A row cancelled by expiry that later receives a paid webhook produces an orphaned-payment
   alert, not an exception.
9. Seat math is asserted at each step.

## Out of scope

- Roster filtering and the unfiltered `Count("registrations")` annotations. That is item 3.
- Normalizing registration emails (#421).
- The id-less payment asymmetry (#420).
- The audience-gate silent refusal (#415).
- Anything in #371.
- Changing the resume or ownership machinery from #419.

## Files expected to change

- `billing/stripe_utils.py`, `billing/views.py`
- `classes/webhook_handlers.py`, `classes/models.py`, `classes/views.py`, `classes/urls.py`
- a scheduled job registration plus `core/spec/scheduled_jobs_spec.py`
- templates for the staff confirm affordance
- specs under `classes/spec/`

Touching anything outside this list is a question for the orchestrator.
