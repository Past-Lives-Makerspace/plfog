# Paid Orientations — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-08-26
**Surface:** FOG hub (`pastlives.test:8000`) — guild pages, orientation booking + respond flows, guild edit Orientations tab, orientations dashboard, admin Payments panel
**Related:**
- `2026-06-21-guild-orientations.md` — the shipped orientation system this extends.
- `2026-08-26-stripe-refunds-payments-panel.md` — **hard dependency**: the Stripe refund engine, webhook reconciliation, the per-person Refunds capability, and the consolidated Payments admin panel. The cross-spec contract is **pinned by the orchestrator** (that spec is being amended to match): a single `PaymentRefund` model with two nullable FKs (`registration` → `Registration`, `orientation_booking` → `OrientationBooking`) and a CheckConstraint that exactly one is set; a thin `issue_refund()` method on BOTH `Registration` and `OrientationBooking` delegating to a shared billing-side service; `refund_state` values `"none" | "partial" | "full" | "failed"`; webhook reconciliation (`charge.refunded` / `refund.updated`) that looks up by Stripe refund id / payment_intent across both sources; and a named **"Retry refund"** panel action on failed rows. This spec does NOT re-spec that engine; it writes against these names as settled and defines only the orientation-side hooks and panel rows.
- `2026-08-26-orienter-availability.md` — parallel spec adding per-staff slots. Price here is deliberately slot-agnostic (it lives on `GuildOrientationSettings`), so it works identically for guild slots, personal orienter slots, and custom-time requests.

---

## 1. Summary

A guild can now charge for its orientation (one price per guild, e.g. $15). A member pays by card at booking time through Stripe Checkout — no payment, no booking. The paid booking is still a **request** the orienter confirms or declines, and the member is told up front: if the guild declines, or the orientation is cancelled by either side, the payment comes back automatically as a full Stripe refund. Admins can also refund manually, and every orientation payment and refund shows up in the consolidated Payments panel. Free stays the default — a guild with no price set behaves exactly as today.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| When to pay | **Pay-to-book**: Stripe Checkout at booking; no payment → no booking request. Free remains the default (price 0/unset = current flow untouched). |
| Confirm flow | **Manual confirm stays** (user's explicit choice): a paid booking is still a REQUEST the orienter confirms or declines. Decline → automatic full Stripe refund. Cancellation (member or staff) of a paid booking → automatic full refund. Member-facing copy says so at booking time ("If we can't make it work you get an automatic refund"). |
| Price scope | **One price per guild** on `GuildOrientationSettings` (`price_cents`, 0 = free). No per-slot or per-orienter pricing — YAGNI; deferred (§10). |
| Refund engine | **Reuse** the refund helper + webhook reconciliation + per-person Refunds capability from `2026-08-26-stripe-refunds-payments-panel.md`. Same for panel surfacing — this spec defines only the orientation rows/actions that plug in. |
| Custom requests | Custom-time requests on a paid guild are **also pay-to-book**, at the same guild price. |

---

## 2. What already exists (reuse, don't reinvent)

All confirmed in the codebase 2026-08-26.

| Need | Existing thing | Location |
|---|---|---|
| Orientation settings (per guild) | `GuildOrientationSettings` — `is_accepting`, `allow_custom_requests`, defaults, closed toggle | `membership/models.py:7592` |
| Bookable slot + seat math | `OrientationSlot` — `book()`, `is_bookable`, `seats_taken`/`seats_remaining` (counts REQUESTED+CONFIRMED) | `membership/models.py:7747` |
| Booking lifecycle | `OrientationBooking` — Status REQUESTED/CONFIRMED/DECLINED/CANCELLED, `uq_orientationbooking_active_per_guild` partial constraint | `membership/models.py:7883` |
| Orientation orchestration (emails, `.ics`, activity, signed tokens) | `request_orientation:180`, `request_custom_orientation:199`, `confirm/decline/cancel_orientation:310/330/345`, `cancel_slot:372`, `complete_orientation:380`, `build_ics:87`, `make_action_token:38` | `membership/orientations.py` |
| Hub views for booking + responding | `orientation_book:993`, `guild_orientation_request_custom:1016`, `orientation_respond:1072`, `orientation_action:1129` (no-login), dashboard `:1198`; settings editor `:785`/`:824` | `hub/views.py` |
| **The working paid-booking pattern (classes)** | PENDING row first → hosted Checkout (`create_class_checkout_session`, metadata `kind="class_registration"`, idempotency key `class-checkout-reg-{pk}`) → webhook flips to CONFIRMED → back-out view deletes the PENDING row | `billing/stripe_utils.py:162-203`; `classes/views.py:651-696` + `register_cancelled:740`; `classes/webhook_handlers.py:28-107` |
| Payment fields to mirror | `Registration.amount_paid_cents` / `stripe_session_id` / `stripe_payment_id` | `classes/models.py:1970-1978` |
| Webhook dispatch | `_WEBHOOK_HANDLERS` map keyed by Stripe event type (single handler per event today) | `billing/views.py:28-36` |
| Refund receipt trigger | `refund_issued` (force_email, "money movement — receipt always goes out") | `core/triggers.py:52-59` |
| Price display | `cents_as_price` filter — 0 renders as "Free", whole dollars drop decimals ("$15") | `classes/templatetags/classes_tags.py:96` |
| Scheduled-job registry | `SCHEDULED_JOBS` + `run_scheduled_tasks` 15-min dispatcher | `core/scheduled_jobs.py` |
| Refund API + panel + capability | **From the parallel spec (pinned contract, see Related)** — `PaymentRefund` model (nullable FKs to `Registration` / `OrientationBooking`, exactly-one CheckConstraint), `issue_refund()` on both payables, `refund_state` (`none`/`partial`/`full`/`failed`), `charge.refunded`/`refund.updated` reconciliation across both sources, "Refunds" per-person capability, Payments panel with a named "Retry refund" action | `2026-08-26-stripe-refunds-payments-panel.md` |

**Gaps to close (kept small):**

1. **The checkout helper is class-named but already generic.** `create_class_checkout_session`'s body has zero class knowledge — amount, product name, email, URLs, metadata, idempotency key. **Generalize rather than duplicate**: extract `create_checkout_session(...)` (same signature, generic docstring) in `billing/stripe_utils.py` and make `create_class_checkout_session` a one-line delegate so the classes app doesn't churn. A sibling copy would duplicate the Stripe client + idempotency plumbing and drift; the parameterized helper is the same function with an honest name. Callers convey purpose via `metadata["kind"]` (`"orientation_booking"` here).
2. **`checkout.session.completed` is single-handler.** The dispatch map allows one handler per event type; orientations need to hear it too. Add a tiny fan-in in `billing/views.py` that calls each registered `checkout.session.completed` handler in turn — each already self-filters on `metadata.kind`, so this is a 5-line router, not a framework.
3. **No `checkout.session.expired` handling anywhere** (the classes flow's known abandoned-PENDING gap). We add it here (§5.4) — and the classes handler can adopt the same pattern later, out of scope.
4. **No refund API call exists today** — `Registration.mark_refunded` is record-keeping only. The real Stripe refund call is the parallel spec's engine (`issue_refund()` per the pinned contract); this spec only wires orientation transitions to it.

---

## 3. Where the code lives

Same homes as the shipped orientation feature — no new app, everything inside the existing coverage/mypy scope.

```
membership/models.py                # price_cents on settings; PENDING_PAYMENT status + payment fields on OrientationBooking;
                                    # seat-hold queryset changes; constraint migration
membership/orientations.py          # checkout start/finish/back-out orchestration; refund side effects on decline/cancel
membership/webhook_handlers.py      # NEW — checkout.session.completed / .expired handlers for kind="orientation_booking"
membership/management/commands/expire_orientation_payment_holds.py   # NEW — abandoned-hold sweep
core/scheduled_jobs.py              # register the sweep
core/triggers.py                    # generalize refund_issued description (copy delta only)
billing/stripe_utils.py             # create_checkout_session extraction (class helper delegates)
billing/views.py                    # checkout.session.completed fan-in; checkout.session.expired route
hub/views.py                        # orientation_book / request_custom branch on price; checkout return, cancelled, cancel-hold, resume views
hub/urls.py                         # 4 new routes (hub_orientation_checkout_return, _checkout_cancelled, _checkout_cancel_hold, _checkout_resume)
hub/forms.py                        # price field on GuildOrientationSettingsForm
templates/hub/partials/guild_orientation.html    # price chip, paid modal copy, pending-payment state
templates/hub/orientation_checkout_return.html   # NEW — success_url landing (handles webhook lag)
templates/hub/orientation_info.html              # price + refund-promise line
templates/hub/orientation_respond.html           # paid chip, decline-refund warning, refund-failed banner
templates/hub/orientation_action.html            # refund line on the no-login decline/cancel confirm
templates/hub/guild_edit.html                    # price field on the Orientations tab
templates/hub/orientations_dashboard.html        # Paid / Refunded / Refund failed chips
templates/membership/emails/orientation_request.{html,txt}    # payment + refund-promise lines
templates/membership/emails/orientation_declined.{html,txt}   # refund confirmation + timing
templates/membership/emails/orientation_cancelled.{html,txt}  # refund confirmation + timing
```

The Payments-panel row template/queryset contribution lives wherever the parallel spec puts the panel (its registration mechanism is that spec's contract; §6.9 defines what our rows contain).

---

## 4. Data model

### 4.1 `GuildOrientationSettings` — one new field

| Field | Type | Notes |
|---|---|---|
| `price_cents` | `PositiveIntegerField(default=0)` | `help_text="Price to book an orientation, in cents. 0 = free (the default)."` One price per guild; applies to posted slots AND custom-time requests. |

Properties:

- `is_paid` → `self.price_cents > 0`.
- Price rendering in templates uses the existing `cents_as_price` filter (0 → "Free" — exactly the chip we want).

Migration: additive, reverse = drop the column. No data migration needed.

### 4.2 `OrientationBooking` — payment state

**New status** in `Status`: `PENDING_PAYMENT = "pending_payment", "Pending payment"` — the seat-hold state while Stripe Checkout is live. Free bookings never enter it.

| New field | Type | Notes |
|---|---|---|
| `amount_paid_cents` | `PositiveIntegerField(default=0)` | `help_text="Amount paid to book, in cents. 0 for free bookings."` Set provisionally at checkout start; the webhook's `amount_total` is canonical (mirrors `Registration`). |
| `stripe_session_id` | `CharField(max_length=255, blank=True, default="")` | `help_text="Stripe Checkout Session ID."` |
| `stripe_payment_id` | `CharField(max_length=255, blank=True, default="")` | `help_text="Stripe PaymentIntent ID, stamped by the webhook on payment."` |

**Refund bookkeeping is NOT duplicated here — the pinned cross-spec contract (see Related) covers it.** The engine's `PaymentRefund` model carries a nullable `orientation_booking` FK (alongside the nullable `registration` FK, with a CheckConstraint that exactly one is set), so orientation refund records need no columns on this side. `OrientationBooking` gains the contract's thin `issue_refund()` method delegating to the shared billing-side service, and exposes the contract's `refund_state` property — `"none" | "partial" | "full" | "failed"` (failed = the latest attempt failed with no successful refund covering it). Webhook reconciliation (`charge.refunded` / `refund.updated`) resolves Stripe refund ids / payment intents across both FK sources, so an orientation refund issued from the Stripe dashboard still reconciles; unknown ids log loudly per that spec.

**Seat accounting changes** (the state machine, spelled out):

- `OrientationSlot.seats_taken` counts `PENDING_PAYMENT | REQUESTED | CONFIRMED` — a live checkout holds its seat so two members can't buy the last seat at once.
- New queryset `OrientationBookingQuerySet.seat_holding()` = those three statuses. **`active()` keeps its current meaning** (REQUESTED|CONFIRMED) — the dashboard, `is_upcoming`, `slot.cancel()`, and `active_orientation_for` must NOT surface half-paid holds as real bookings.
- The partial unique constraint `uq_orientationbooking_active_per_guild` widens its condition to `status IN (pending_payment, requested, confirmed)` so a member can't open two checkouts (or a checkout plus a live booking) for the same guild. Migration: drop + re-add the constraint; reverse restores the two-status condition.
- New `Member.pending_payment_orientation_for(guild)` helper so the guild page can render the finishing-payment state (§6.1).
- `PENDING_PAYMENT` holds are **deleted**, never CANCELLED, on back-out or expiry — no fan-out ever fired for them, so nothing should remember them (mirrors `register_cancelled` deleting the PENDING registration). A custom-request hold also deletes its orphan 1-seat MANUAL slot.

State machine:

```
free guild:   (none) ──book──▶ REQUESTED ──▶ CONFIRMED / DECLINED / CANCELLED        (unchanged)
paid guild:   (none) ──checkout start──▶ PENDING_PAYMENT
              PENDING_PAYMENT ──webhook paid──▶ REQUESTED  (fan-out fires HERE)
              PENDING_PAYMENT ──back-out / session expired / sweep──▶ (row deleted)
              REQUESTED ──decline──▶ DECLINED  + auto full refund
              REQUESTED|CONFIRMED ──cancel (member/staff/slot)──▶ CANCELLED + auto full refund
```

---

## 5. Business logic (fat models / service module)

All in `membership/orientations.py` (orchestration) and the models (guards). Views stay thin. Domain errors stay `OrientationError`.

### 5.1 Checkout start — `start_orientation_checkout(slot, member, *, note, request) -> str`

For a paid guild, replaces the direct `request_orientation` call. Steps:

1. Run the `slot.book()` guards (bookable, not oriented) **with the duplicate check widened to the `seat_holding()` scope, not `active()`** — a member with a live `PENDING_PAYMENT` hold must be caught in the domain layer with a friendly `OrientationError` ("You already have a checkout in progress for this guild. Resume or cancel it first."), never fall through to the widened DB constraint as a raw `IntegrityError` 500. The constraint remains the race backstop only. Then create the booking with `status=PENDING_PAYMENT`, `amount_paid_cents=price_cents` (provisional), `member_note=note`. **No emails, no activity, no notifications** — nothing has happened yet.
2. Call `billing.stripe_utils.create_checkout_session` with:
   - `amount_cents=settings.price_cents`, `product_name=f"Orientation — {guild.name}"`,
   - `customer_email=member.primary_email`,
   - `metadata={"kind": "orientation_booking", "booking_id": str(booking.pk)}` (Checkout **and** `payment_intent_data`, like classes),
   - `idempotency_key=f"orientation-checkout-{booking.pk}"` (mirrors `class-checkout-reg-{pk}`; guards against duplicate sessions on a retried start — note Stripe idempotency **replays the original response**, so it can never be used to revive an expired session; Resume handles that case explicitly, §5.7),
   - `success_url` → `hub_orientation_checkout_return` + `?b=<signed token>`; `cancel_url` → `hub_orientation_checkout_cancelled` + same token. Orientation bookings have no `self_serve_token` column; reuse the existing signed-token pattern (`make_action_token`-style, new salt, payload = booking pk) so the return URLs authorize exactly one booking without adding a column.
   - Session `expires_at` = now + 1 hour (Stripe allows 30 min–24 h) so abandoned checkouts die server-side and fire `checkout.session.expired`.
3. On Stripe API failure: delete the hold booking (and the orphan custom slot, if any) and re-raise — mirror the classes `registration.delete()` rollback.
4. Save `stripe_session_id`, return the hosted Checkout URL for the view to redirect to.

`start_custom_orientation_checkout(guild, member, starts_at, *, note)` mirrors `request_custom_orientation`: create the 1-seat MANUAL slot, then delegate; any failure deletes slot + hold.

### 5.2 Payment lands — webhook `checkout.session.completed` (kind=`orientation_booking`)

In `membership/webhook_handlers.py`, mirroring the classes handler line for line:

- Filter on `metadata.kind`; ignore `payment_status != "paid"`.
- `select_for_update` the booking; **idempotent**: if status is not `PENDING_PAYMENT`, return (re-delivery no-op).
- Flip to `REQUESTED`, stamp `stripe_payment_id` (`payment_intent`), canonical `amount_paid_cents` (`amount_total`), keep `stripe_session_id`.
- **Then fire the full existing request fan-out** — the member "request received" email (+ TENTATIVE `.ics`), the lead/orienter request email + in-app, `SiteActivity ORIENTATION_REQUESTED` — by calling the same emit path `request_orientation` uses today, refactored so the fan-out is callable on an existing booking (`_fan_out_request(booking)`; `request_orientation` becomes `slot.book()` + `_fan_out_request`). Emails only ever go out for money in hand. The `emit` `period="booking:{pk}:request"` dedupe makes webhook re-delivery double-send-proof even beyond the status guard.
- Booking gone despite payment: log at ERROR with the session id and payment intent. Panel orientation rows come from `OrientationBooking` rows, so a deleted booking has **no in-app trace** — the payment is findable only in Stripe and in this log line, which is exactly why §5.4's release paths must verify with Stripe before deleting a hold (with that in place, this branch means a hold released while Stripe reported the session unpaid, and the refund happens from the Stripe dashboard).

### 5.3 Back-out and hold cancel — `release_hold_if_unpaid(booking)`

One shared service helper backs both member-initiated release paths, and it **verifies with Stripe before deleting** — a hold can be paid-but-webhook-lagged, and deleting it would eat the member's money:

- Retrieve the stored Checkout Session. `payment_status != "paid"` → delete the hold (and its orphan custom MANUAL slot when `seats == 1 and source == MANUAL` with no other bookings) and report *released*. `payment_status == "paid"` → keep the booking, flip it to `REQUESTED` with the §5.2 fan-out (same recovery as the sweep), and report *paid*. Stripe unreachable → keep the hold, report *unknown*.

Two thin views call it:

- **`hub_orientation_checkout_cancelled`** (the Stripe `cancel_url`, token-authorized GET → confirm POST): member clicked back on the Stripe page. *Released* → guild page with info message "No charge was made. Your card was not billed." *Paid* → guild page with "Good news, your payment already went through. Your booking request is in." Already gone → plain redirect, no message drama.
- **`hub_orientation_checkout_cancel_hold`** (NEW, session-authenticated POST, distinct from the token GET path): the guild page's pending-state Cancel (§6.1). Login required; the view loads the member's own hold for that guild and 403s on anyone else's. *Released* → "Booking cancelled. You were not charged." *Paid* → the booking stays, message "Your payment is finalizing, so this booking can't be cancelled that way. You can cancel the confirmed request instead and get an automatic refund." *Unknown* → "We couldn't check your payment status just now. Try again in a minute."

### 5.4 Abandoned holds — expired-session webhook + sweep (yes to the cron)

The classes flow has no abandoned-PENDING sweep (known gap: a back-button-less abandon strands the row and its seat until the session's 24 h default expiry, and forever after it). Seats here are scarcer than class spots — a 4-seat slot can be fully walled off by ghosts — so we close it, belt and braces:

1. **`checkout.session.expired` webhook** (new route in `_WEBHOOK_HANDLERS`): kind-filtered; deletes the still-`PENDING_PAYMENT` booking + orphan custom slot. Stripe only fires `expired` for sessions that were never completed, so this path is deletion-safe by definition. Primary release path, ~1 h after checkout start thanks to `expires_at`.
2. **Sweep cron** `expire_orientation_payment_holds` — registered in `SCHEDULED_JOBS` (`Cadence.ALWAYS`, "Every 15 min", description: "Releases orientation seats held by checkouts that were never completed."). For each `PENDING_PAYMENT` booking older than **2 hours**, it **retrieves the Checkout Session from Stripe first** and acts on Stripe's answer — never on age alone. A completed session fires no `expired` event, so a lost `checkout.session.completed` webhook would otherwise leave a PAID hold that a blind sweep deletes: money taken, booking gone, no in-app trace. So:
   - Stripe says `payment_status == "paid"` → flip the hold to `REQUESTED` with the full §5.2 fan-out (stamping the payment fields from the retrieved session). **The sweep IS the lost-webhook recovery path.**
   - Stripe says expired / unpaid → delete the hold + orphan custom slot.
   - Stripe unreachable for a session → skip it this tick and log; the next tick retries.
   The 2 h threshold (strictly after the 1 h session expiry) protects against racing a **live** checkout; it does nothing for lost webhooks — the Stripe check is what makes the sweep safe. Idempotent; returns released + recovered counts.

### 5.5 Refunds on decline / cancel

`decline_orientation`, `cancel_orientation` (which `cancel_slot` and the no-login `apply_token_action` both already route through — so email-link declines and slot cancellations get refunds for free) grow one step after the state change:

- If `amount_paid_cents > 0` and `refund_state == "none"` → call **`booking.issue_refund()`** (the pinned contract's thin method, delegating to the shared billing service) for a **full refund** against `stripe_payment_id`, creating the `PaymentRefund` row with its `orientation_booking` FK set. Attribution: the acting user for authenticated declines/cancels; for the **no-login token path**, the refund's initiator is stamped from the token's recipient — the per-recipient decline/cancel links minted in the request email gain the recipient's member pk in their signed payload, so an email-link decline credits the orienter who clicked it ("System" only when the payload predates the field, e.g. slot-cancel fan-out).
- **Refund API failure does NOT block the decline/cancel** (locked-in recommendation, adopted): the state change is already saved, the member email still goes out (with "refund on its way" softened to "your refund is being processed" — see §7), and the booking lands in the contract's **`refund_state == "failed"`** — latest attempt failed, no successful refund covering it — surfaced in the Payments panel with the engine's named **"Retry refund"** action (which re-invokes the refund with a fresh idempotency-key suffix). Rationale: blocking would hold a scheduling decision hostage to Stripe availability and strand the seat; the invariant that matters is *a member is never silently unrefunded*, and a loud, retryable failed-refund row guarantees that better than a blocked button.
- Auto-complete / thank-you / no-show flows never refund (the orientation happened).

**Manual refund** (admin, outside decline/cancel — e.g. goodwill): entirely the Payments panel's per-row Refund action from the refunds spec; orientation rows expose it like any other payment (§6.9). A manual full refund on a still-live booking does **not** auto-cancel the booking — money and scheduling stay independently controlled (the admin can cancel separately if that's the intent).

### 5.6 Staff-added members on a paid guild

`orientation_add_member` (dashboard "add member to slot") **skips payment — the booking is comped** (`amount_paid_cents=0`, straight to REQUESTED as today). A staff member physically signing someone up shouldn't need the member's card, and the alternative (emailing the member a pay link) is a new flow with real complexity for a rare case — deferred (§10). The form shows a one-line note when the target guild is paid (§6.8).

### 5.7 View branching (thin)

`orientation_book` and `guild_orientation_request_custom` branch once: `settings.is_paid` → `start_*_checkout(...)` and `redirect(checkout_url)`; else the exact current code path. Redirect-after-POST to Stripe is the same shape the classes register view uses. New thin views: `orientation_checkout_return` (§6.4), `orientation_checkout_cancelled` + `orientation_checkout_cancel_hold` (§5.3), and `orientation_checkout_resume` (session-authenticated POST, own hold only). Resume **retrieves the stored session from Stripe and branches on its status first** — replaying the original idempotency key against an expired session would hand the member a dead Checkout URL:
- session `open` → redirect to its `url` (same live session, nothing minted);
- session `complete`/paid → run the §5.3 recovery flip and land on the return page;
- session **expired** → release the hold via `release_hold_if_unpaid` and bounce to the guild page with "That checkout expired. Pick a time to start again." (releasing rather than minting keeps the guards fresh — the slot may have filled or passed since the hold was taken; re-booking re-runs them all).

---

## 6. UI / UX

Every screen the money touches, per the completeness checklist. **Verify both themes on every changed screen; all new classes use the `pl-` prefix in `hub.css`.**

### 6.1 Guild page — orientation section (`templates/hub/partials/guild_orientation.html`)

- **Layout:** existing `hub-card` section; no structural change. Free guilds render byte-identical to today.
- **Price up front:** in the booking state's heading row, a price chip next to "Get Oriented for {{ guild.name }}": `<span class="pl-price-chip">{{ orientation.price_cents|cents_as_price }}</span>` — renders "$15"; on free guilds the chip is omitted entirely (not a "Free" chip; free is the unmarked default). Chip style: `--hub-surface` background, `--color-tuscan-yellow` text, small radius — defined once in `hub.css` as `.pl-price-chip` and reused by §6.5/§6.6.
- Under the chip, one muted line (paid guilds only): "You pay when you book. If the guild can't make it work, you get an automatic full refund."
- **Slot rows:** unchanged (Date / Time / action). The paid "Request" button label stays "Request" — the price is stated in the header and modal; a per-row price would repeat the same number five times.
- **Booking confirm modal** (existing `confirm_modal.html` per slot): paid variant copy — title "Book This Orientation?", message "You'll pay {{ price }} now through our secure checkout. Your booking is a request until the guild confirms it. **If they decline, or it's cancelled, you get an automatic full refund.**", button "Continue to Payment" (`confirm_button_style="primary"`). Free variant keeps today's copy verbatim.
- **New state — finishing payment:** when `member.pending_payment_orientation_for(guild)` exists, the section shows (instead of the slot list): heading "Finishing Your Booking", line "Your payment for the {{ slot date/time }} orientation hasn't come through yet.", two controls: **"Resume payment"** (`pl-btn pl-btn--primary pl-btn--sm`, POST to `hub_orientation_checkout_resume`) and **"Cancel"** (`pl-btn pl-btn--danger pl-btn--sm`, via `confirm_modal.html`: "Cancel this booking? If you haven't paid, nothing is charged." → POST to **`hub_orientation_checkout_cancel_hold`**, the session-authenticated own-hold-only route from §5.3, distinct from the token-authorized Stripe `cancel_url`). The view verifies payment status with Stripe before deleting (§5.3): unpaid → hold deleted, "Booking cancelled. You were not charged."; paid or finalizing → hold kept, "Your payment is finalizing, so this booking can't be cancelled that way." Full-page redirect with Django message, matching the section's existing pattern. This state exists because a member can close the Stripe tab and come back days later; a dead-looking guild page would be the dead end.
- **Existing-booking state:** paid bookings show one muted line on **both** the requested card ("Requested — awaiting confirmation…") and the confirmed card ("Confirmed — see you there!"): "Paid {{ amount }}. Automatic refund if this is declined or cancelled." The member's cancel confirm modal gains "You'll get an automatic full refund." in **both** states — a morning-of cancel of a confirmed orientation refunds just the same (locked decision). Free bookings: cards and modal unchanged.
- **Custom-time form** (paid guilds): above the Send button, the same one-line payment note; button label becomes "Continue to Payment". Field components unchanged (`form_field.html`).
- **States:** empty slots / closed / oriented — all unchanged. Error: an `OrientationError` or Stripe failure lands back on the guild page as a Django error message (existing pattern).
- **Dark + light:** chip and pending-state use tokens only; no form-control inline styles introduced.
- **Mobile:** chip wraps under the heading (flex-wrap on the heading row); pending-state buttons stack via the existing button-group behavior; slot rows unchanged.

### 6.2 Orientation info page (`templates/hub/orientation_info.html`)

- Add a price line under the guild heading for paid guilds: the same `.pl-price-chip` + the refund-promise sentence from §6.1. Nothing else changes. Both themes via tokens; reflows as plain text on mobile.

### 6.3 Stripe Checkout (hosted)

- Stripe's page; nothing to build. Line item reads "Orientation — {{ guild.name }} — $15.00". Session expires after 1 h (§5.1).

### 6.4 Checkout return page (`templates/hub/orientation_checkout_return.html`, NEW)

The `success_url` landing. Dedicated page (not a toast) because the member arrives from an external redirect. Layout: single centered `hub-card`.

- **State A — webhook already landed (booking is REQUESTED):** headline "You're Booked, Pending Confirmation". Body: date/time/location, "You paid {{ amount }}. The guild will confirm or decline your request. **If they can't make it work, your {{ amount }} comes back automatically.** We've emailed you the details and a calendar invite." Primary CTA "Back to {{ guild.name }}" (`pl-btn pl-btn--primary`), secondary link to the orientation info page. No dead end.
- **State B — webhook lag (booking still PENDING_PAYMENT):** headline "Finalizing Your Payment…". Body: "This usually takes a few seconds." Auto-poll: a small HTMX fragment (`hx-get` the same view's status partial, `hx-trigger="every 3s"`, swap the card) that flips to State A when the webhook lands; after ~60 s of polling the fragment swaps to a calm fallback: "Still processing. You'll get a confirmation email the moment it clears. Safe to close this page." plus the guild-page link. The poll indicator is the loading state; no spinner-forever.
- **State C — invalid/expired token or booking gone:** friendly error card ("We couldn't find that booking. If you were charged, the payment will appear in your email receipt and we'll sort it out.") with the guild-page link, status 400. Never a 500.
- **Dark + light:** card + tokens; **mobile:** single column already.

### 6.5 Respond / decline screen (`templates/hub/orientation_respond.html`)

- **Paid indicator:** in the booking summary, a chip row: `.pl-price-chip` "Paid $15", plus a second chip driven by the contract's `refund_state` — `"full"`/`"partial"` → muted green "Refunded"; `"failed"` → red "Refund failed", linking to the booking's Payments panel row (and its "Retry refund" action) for holders of the Refunds capability, plain text for others.
- **Decline warning:** the decline form (existing note textarea + submit) gains a warning line directly above the Decline button, `.pl-refund-note`: "Declining refunds their $15 automatically." Same line on the lead-cancel confirm modal: "Cancelling refunds their $15 automatically."
- **Refund failed banner:** when `refund_state == "failed"` on a declined/cancelled booking, a full-width alert card at the top: "This member's $15 refund failed. Retry it in the Payments panel." linking straight to the row's "Retry refund" action (visible to admin/Refunds-capability holders). This is the "member is never silently unrefunded" surface for the person most likely to be looking.
- Free bookings: screen renders exactly as today. Both themes; mobile unchanged (chips wrap).

### 6.6 No-login action page (`templates/hub/orientation_action.html`)

- The GET confirmation step for `decline` and `cancel` on a **paid** booking adds the same one-liner: "Declining/Cancelling refunds their {{ amount }} automatically." The confirm button and invalid-token handling are unchanged.

### 6.7 Settings editor — guild edit, Orientations tab (`templates/hub/guild_edit.html` + `GuildOrientationSettingsForm`)

- **One new field** in the settings form, rendered via `form_field.html`, placed with the other defaults (after `default_duration_minutes`): label "Orientation price", input in **dollars**. Pinned contract: `forms.DecimalField(max_digits=6, decimal_places=2, required=False)`, mapped to `price_cents` in `clean` — leads type "15" or "15.50", not "1500"; cents are allowed. **Blank normalizes to 0 (free)**, and a free guild renders the field **empty**, not "0". Validation: ≥ 0, ≤ $500 sanity cap, error "Enter a price between $0 and $500."
- **`.pl-help` bubble** on the label: "Leave this blank for free orientations (the default). Set a price and members pay by card when they book. If you decline or the orientation is cancelled, they're refunded automatically. Applies to posted slots and custom time requests."
- Saves through the existing `guild_orientation_edit` POST → Django success message → back to the tab. Save button unchanged (already last, already says "Save"). Price changes affect **future checkouts only**; live holds and paid bookings keep the amount they paid (refunds refund `amount_paid_cents`, so a price change can never refund the wrong number).
- Gating: `_require_can_manage_orientations` as today — leads/staff set their own price; no admin approval step (YAGNI).
- Both themes: the field inherits `.hub-form-group` scoping; nothing inline.

### 6.8 Orientations dashboard (`templates/hub/orientations_dashboard.html`)

- **Table:** one new "Paid" column between member and status: `—` for free bookings; "$15" for paid; "$15 · Refunded" (muted) after a refund; "$15 · **Refund failed**" (danger color, links to the Payments panel for capability holders) on failure. Sortable is unnecessary (deferred); the column is display-only, sourced from `amount_paid_cents` + the engine's state.
- **Upcoming list:** paid bookings get the small `.pl-price-chip` after the member name.
- **Pending holds are visible, not ghosts:** holds consume seats (§4.2) but never appear in `active()` queries, so without representation a lead sees a slot mysteriously full and an add-member attempt fails unexplained. Wherever the dashboard shows a slot's capacity (the add-member slot dropdown labels and any slot row), a slot with holds gets a muted suffix line: "1 seat held by a checkout in progress" (pluralized). And when `orientation_add_member` fails because holds fill the remaining seats, the error names the cause: "That slot's remaining seat is held by a member finishing checkout. It frees up within an hour if they don't complete payment."
- **Add-member form:** when the selected slot's guild is paid, a muted note under the select (Alpine-toggled on the existing slot dropdown): "This guild charges $15 for orientations. Members you add here are not charged." (§5.6).
- **CSV export:** add `amount_paid_cents` and refund-state columns to the existing export.
- Mobile: the table already scrolls in its container; the new column adds ~70 px, acceptable. Both themes via tokens.

### 6.9 Payments panel rows (plugs into the parallel spec's panel)

This spec contributes, per the panel's registration contract:

- **Row shape:** source badge "Orientation", payer (member, links to member page), description "Orientation — {{ guild.name }}" (guild name links to the guild page), amount, paid-at, status chip mapped from the contract's `refund_state` (`none` → Paid, `full`/`partial` → Refunded, `failed` → Refund failed), and the linked booking (links to `hub_orientation_respond`). Rows are backed by the `PaymentRefund` engine's panel query resolving payments across both FK sources; orientation rows resolve through the `orientation_booking` FK.
- **Actions:** the engine's per-row **Refund** action (invokes `booking.issue_refund()`; gated by the Refunds capability; confirm modal per the engine), and its named **"Retry refund"** action on `refund_state == "failed"` rows (re-invokes the refund with a fresh idempotency-key suffix, per the pinned contract). No orientation-specific actions — cancel/decline live on the respond screen, deliberately (§5.5: money and scheduling controls stay separate).
- Filtering/sorting/pagination/dark-light/mobile: the panel's own machinery; nothing bespoke here.

---

## 7. Notifications / emails / activity

No new triggers. One copy delta and three template updates — all keep `.txt`/`.html` in sync, absolute URLs, branded shell, subject noun linked (guild name → guild page), one primary CTA, per FRONTEND.md.

| Email | Change |
|---|---|
| `orientation_request` (member, "request received") | Paid bookings add a payment block after the slot details: "**You paid $15.**" + "Your booking is a request until {{ guild.name }} confirms it. If they decline, or it's cancelled, your $15 comes back automatically as a full refund to your card." This email doubles as the receipt line (Stripe also emails its own receipt via Checkout). Free bookings: template renders exactly today's copy (guard the block on `booking.amount_paid_cents`). |
| `orientation_declined` (member) | Paid: add "Your $15 refund is on its way. Refunds usually reach your card in 5 to 10 business days." (Deliberately "is on its way", not "was issued" — true in both the success and the flagged-failure path, where the retry still makes it true; the member never gets a promise the failure path breaks.) |
| `orientation_cancelled` (member) | Same refund paragraph as declined. Fires for member-cancel, lead-cancel, and slot-cancel alike (all route through `cancel_orientation`). |
| Refund receipt | The engine's `refund_issued` email (force_email, always sends) fires when the refund actually succeeds — including a delayed "Retry refund". **Copy deltas this spec owns (per the pinned contract, which already generalizes both):** (a) the trigger description in `core/triggers.py:56` generalizes from "A refund was processed for a registration." to "A refund was processed for a payment you made." (cosmetically safe — forced triggers never render a settings toggle); (b) the **Refunds capability help text** generalizes from class payments only to "class and orientation payments"; (c) the engine's receipt template accepts a generic "what was refunded" noun + URL so an orientation refund reads "Orientation — {{ guild.name }}" linking to the guild page, not class-shaped copy. Spec'd here, implemented against the engine. |
| Lead request / confirmed / thank-you | Unchanged (the lead request email fires only after payment, which needs no copy — a request is a request). |

In-app/activity: unchanged — `ORIENTATION_REQUESTED` now logs at webhook time for paid bookings (payment in hand *is* the request). Refund activity/audit rows are the engine's.

---

## 8. Build order (phased; each phase ships green)

> Spec only — do not build until approved. Phases 3+ depend on `2026-08-26-stripe-refunds-payments-panel.md` having shipped its refund helper and panel.

1. **Billing infra (no behavior change):** extract `create_checkout_session` (class helper delegates); `checkout.session.completed` fan-in router; `checkout.session.expired` route wired to a no-op-until-Phase-2 handler list. Classes specs stay green untouched.
2. **Models + state machine:** `price_cents`, `PENDING_PAYMENT`, payment fields, `seat_holding()`, widened constraint (migration + reverse), `pending_payment_orientation_for`; `_fan_out_request` refactor of `request_orientation`; factories + model specs. No UI yet.
3. **Checkout flow:** `start_orientation_checkout` / custom variant; view branching; webhook handlers (completed + expired); `release_hold_if_unpaid` + return/cancelled/cancel-hold/resume views + `orientation_checkout_return.html`; guild-page price chip, paid modal copy, pending-payment state; request-email payment block.
4. **Refund wiring** *(needs the engine's `PaymentRefund` + `issue_refund()`)*: decline/cancel refund side effects with the flag-don't-block failure path and token-recipient attribution; respond/action-page warnings + refund-failed banner; declined/cancelled email refund copy; `refund_issued` description + Refunds-capability help text + receipt-copy generalization.
5. **Surfaces:** settings-editor price field (+ `.pl-help`); dashboard Paid column, upcoming chips, add-member comp note, CSV columns; orientation-info price line; Payments-panel orientation rows.
6. **Sweep cron:** `expire_orientation_payment_holds` (Stripe-verified releases + lost-webhook recovery, §5.4) + `SCHEDULED_JOBS` registration.
7. **Housekeeping:** bump `plfog/version.py` VERSION + **one** member-friendly CHANGELOG entry (fold into the current line's orientation entry if one is unreleased, per CLAUDE.md), e.g.: *"Guilds can now charge for orientations. You pay when you book with a secure card checkout, and if the guild declines or the orientation is cancelled your money comes back automatically."* Run `manage.py check` (constraint/migration changes).

---

## 9. Testing (BDD `*_spec.py`, `describe_*`/`it_*`, factory-boy, coverage + mutation gates)

- **State machine (`membership/spec/models/`):** paid book → PENDING_PAYMENT holds a seat (`seats_taken`, `is_full`); the **domain guard** (seat_holding scope) rejects a second checkout AND a checkout alongside a live booking with the friendly `OrientationError` — no `IntegrityError` reaches a view — while the widened constraint still blocks the concurrent race at the DB; free guild never creates PENDING_PAYMENT (regression: byte-identical flow); `active()`/dashboard querysets exclude holds; price change after checkout start doesn't alter a hold's `amount_paid_cents`.
- **Checkout orchestration:** `start_orientation_checkout` rolls back the hold (and custom slot) on Stripe failure; metadata kind + idempotency key shape; custom variant's orphan-slot cleanup; resume re-uses the idempotency key.
- **Webhook (`membership/spec/webhooks/`):** completed flips PENDING_PAYMENT→REQUESTED, stamps payment fields from `amount_total`/`payment_intent`, fires the full request fan-out (assert `TransactionalEmailLog` + `SiteActivity` + lead notification); **idempotency** — re-delivery on REQUESTED/CONFIRMED is a no-op with no second email (status guard + `period` dedupe both asserted); unpaid `payment_status` ignored; missing booking logs, doesn't raise; wrong `kind` untouched; the fan-in router still delivers class sessions to the classes handler.
- **Seat release (all Stripe-verified):** `release_hold_if_unpaid` deletes an unpaid hold + orphan custom slot and frees the seat, but **flips a Stripe-says-paid hold to REQUESTED with the full fan-out instead of deleting it**; back-out (`checkout_cancelled`) and the guild-page `checkout_cancel_hold` (own-hold 403 for anyone else) both route through it; expired-session webhook deletes (safe by definition — completed sessions never fire it); **sweep**: skips holds under 2 h, retrieves each older hold's session, deletes only on Stripe-confirmed unpaid/expired, **recovers a paid session as the lost-webhook path** (REQUESTED + fan-out + payment fields), skips-and-logs on Stripe errors, idempotent, released/recovered counts returned; swept custom slot removed. **Resume:** open session → same URL; expired session → hold released + friendly bounce (never a dead Checkout URL from idempotency replay); paid session → recovery flip + return page.
- **Pay-then-decline / cancel refunds:** decline on a paid booking calls `booking.issue_refund()` exactly once (mock the billing-service boundary, never the models); member cancel, lead cancel, slot cancel, and the **no-login token decline** all refund — the token path stamps the refund's initiator from the token's recipient payload ("System" for legacy/absent payloads); `refund_state != "none"` bookings don't double-refund; free bookings never touch the engine; **refund failure**: decline still lands, member email still sends, `refund_state == "failed"` is asserted (not a raise).
- **Views/templates:** paid guild page shows chip + refund promise; pending-payment state renders with Resume/Cancel; return page states A (REQUESTED) / B (pending, poll fragment) / C (bad token → 400 friendly); respond page paid chip + decline warning + refund-failed banner gating; settings form dollar↔cents mapping (blank → 0, free renders empty, "15.50" allowed) + bounds errors; dashboard Paid column variants; **held-seat visibility** (slot dropdown/rows show the "seat held by a checkout in progress" suffix; add-member failure on a hold-filled slot returns the hold-naming error, `amount_paid_cents=0` on comped adds); CSV columns.
- **Emails:** request payment block present for paid / absent for free, `.txt`/`.html` parity; declined/cancelled refund paragraphs; template comment lint.
- **Gotchas:** slot fixtures at `now + timedelta(days=2)` (local-tz date-window trap); respx-style boundary mocks for Stripe; run `manage.py check` in CI-shape after the constraint migration (E034-class traps).

---

## 10. Open / deferred

- **Per-slot / per-orienter pricing** — deferred (YAGNI). The single guild price lives on settings precisely so the orienter-availability spec's personal slots inherit it unchanged; a `price_cents` override on `OrientationSlot` is a small additive migration later.
- **Pay-link for staff-added members on paid guilds** — staff adds are comped (§5.6). If comped bookings get abused, an emailed pay-link flow is the follow-up.
- **Partial refunds / no-show keep-the-fee policies** — out of scope; decline/cancel is always a full refund, completion never refunds. A no-show fee is a policy decision before it's a feature.
- **Classes abandoned-PENDING sweep** — the same gap in the classes flow stays open here; the `expires_at` + expired-webhook + sweep pattern from §5.4 is the template for fixing it there.
- **Sortable Paid column / revenue reporting for orientation income** — panel + CSV cover the need today; guild-revenue reporting is its own future conversation.
