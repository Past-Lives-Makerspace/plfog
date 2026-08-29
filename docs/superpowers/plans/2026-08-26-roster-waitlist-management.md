# Roster & Waitlist Management — Spec & Implementation Plan

**Status:** Spec only — not yet approved to build.
**Date:** 2026-08-26
**Surface:** book CMS `book.pastlives.test` — teach portal class Workspace (Registrations + Waitlist tabs), classes admin (class Workspace tabs, consolidated Registrations list, registration detail), plus one new public token-rails pay page.
**Related:** `2026-08-26-stripe-refunds-payments-panel.md` (Refunds capability — referenced, not re-specced here).

---

## 1. Summary

Instructors and class admins can finally *manage* their rosters instead of just reading them. From the Registrations and Waitlist tabs they can remove a registrant (seat frees, waitlist auto-promote fires, everyone is told what will happen first), and hand-pick a waitlisted person into the class — instantly confirmed, with an optional "send them a payment link" step for paid classes and a persistent way to re-send that link or mark the payment collected by hand (cash, comped). Admins also get an instructor filter on the consolidated registrations list, with a one-click "Mine only" toggle for admins who also teach.

### Locked decisions (from brainstorm Q&A)

| Decision | Choice |
|---|---|
| Promote flow (paid class) | Promoting flips the person to CONFIRMED immediately — the seat is theirs. Right after adding, the UI asks: "Send them a payment link?" — optional, staff can decline. No expiration logic. Every unpaid promoted registrant keeps a persistent "Send payment link" button (fires or re-fires the email). Staff can also manually mark the payment paid (cash/comped/etc.). Free classes: straight to CONFIRMED, no payment step. |
| Payment state | A CONFIRMED registration can now be unpaid. Represented by ONE new field, `payment_due_cents`, stamped at promote time from the price engine; "unpaid" is derived (`amount_paid_cents < payment_due_cents`). See §4 for why this beats both pure derivation and a status flag. Roster shows a clear Paid/Unpaid badge. |
| Remove flow | Remove = `cancel()` behind a confirm modal that names the consequences: seat freed, and the existing auto-promote claim-link email to the next waitlister fires when one actually will — the modal renders the real state (§6.7). Refund is NOT part of remove; if the person paid, the modal notes the refund is handled separately and links to the refund action for staff with refund authority (fog-admin OR Refunds capability holder, per `2026-08-26-stripe-refunds-payments-panel.md`). |
| Who | Instructors for their own classes (teach portal) + class admins / fog-admins (admin views). Same actions on both surfaces, via one shared set of endpoints gated on "can edit this class" (`ClassOffering.objects.editable_by`), which also covers guild leads/staff — matching the existing read gate. |
| Over-capacity promote | Warn but allow. Staff know the room; the promote modal shows an explicit over-capacity warning when `spots_remaining == 0`. `spots_remaining` already floors at 0 (`max(0, capacity − used)`), so an over-full class simply reads "0 spots" publicly and the auto-promote path (`spots_remaining <= 0 → return None`) stays dormant — no claim links fire while over capacity. |
| Ordering fairness | Staff may pick anyone off the waitlist, in any order — it's their call. The waitlist tab keeps showing position + "Notified" so the choice is informed; no enforcement, no apology copy beyond the modal stating who's being added. |
| Promote emails | The registrant always hears exactly once. Free class → "You're in" email immediately on promote. Paid class → the email waits for the modal choice: **Send** → "You're in! Complete your payment" (with pay link); **Not now** → plain "You're in" (no pay link; the link can be sent later from the row button). |

## 2. What already exists (reuse, don't reinvent)

All scout-verified against the current tree.

| Need | Existing thing | Location |
|---|---|---|
| Instructor roster page (read-only today) | `teach_class_registrations` → `class_registrations.html` | `classes/views.py:1509-1528`, `templates/classes/teach/class_registrations.html` |
| Instructor waitlist page (read-only) | `teach_class_waitlist` → `class_waitlist.html` (shows `waitlist_notified_at`) | `classes/views.py:1562-1579`, `templates/classes/teach/class_waitlist.html` |
| Admin equivalents | `admin_class_registrations` / `admin_class_waitlist` | `classes/views.py:2099-2145` |
| Only mutation surface today | `admin_registration_detail` Admin Actions block (cancel / move / mark-refunded) | `classes/views.py:2676-2730`, `templates/classes/admin/registration_detail.html:143-171` |
| Lifecycle statuses | `Registration.Status` (PENDING / CONFIRMED / WAITLISTED / CANCELLED / REFUNDED) | `classes/models.py:1929-1934` |
| Seat math | `ClassOffering.spots_remaining` (capacity − CONFIRMED+PENDING, floored at 0) | `classes/models.py:1042-1048` |
| Cancel + auto-promote | `Registration.cancel()` — frees seat, logs activity, calls `promote_next_from_waitlist()` | `classes/models.py:2149-2183` |
| Auto claim-link path (keep as-is) | `ClassOffering.promote_next_from_waitlist()` — emails oldest un-notified waitlister a claim link, stamps `waitlist_notified_at`, does NOT confirm | `classes/models.py:882-923` |
| Status-change side effects, attributed | `Registration.save()` → `_dispatch_status_notification` with transient `_acting_user` | `classes/models.py:2030-2116` |
| Activity feed | `CmsActivity.Kind` + `classes.activity.log()` (WAITLIST_NOTIFIED, REGISTRATION_MOVED, … already exist) | `classes/models.py:2442-2459` |
| Payment fields | `amount_paid_cents`, `stripe_session_id`, `stripe_payment_id` | `classes/models.py:1970-1978` |
| Price engine | `ClassRegistrationForm.compute_final_price_cents()` — sale → member % → code | `classes/forms.py:900-907` |
| Stripe Checkout creation | `create_class_checkout_session(...)` (idempotency key required) | `billing/stripe_utils.py:162-203` |
| Payment confirm webhook | `handle_checkout_session_completed` (metadata `kind=class_registration`; early-returns on already-CONFIRMED) | `classes/webhook_handlers.py:28-107` |
| Token rails for self-serve links | `self_serve_token` + `my_registration` page | `classes/models.py:1979-1984`, `classes/views.py:770-808` |
| Claim-link email to study | `send_waitlist_spot_opened` (`emit_with_email_shell`, `_absolute_url`, minute-stable `period` dedupe) | `classes/emails.py:566-606` |
| Confirmation email (reused as paid receipt) | `send_registration_confirmation` | `classes/emails.py:82-131` |
| Teach gates | `teaching_member_required` + `_teach_class_or_404` | `classes/views.py:871-894`, `:1487-1490` |
| Registrations-list gates + scoping | `classes_registrations_access_required` + `_scoped_registrations` + `_filter_registrations` | `classes/views.py:938-990` |
| "Can edit this class" scope | `ClassOfferingQuerySet.editable_by()` / `.for_instructor()` | `classes/models.py:204-220` |
| Instructor filter pattern to mirror | `admin_classes` `?instructor=<pk>` dropdown | `classes/views.py:1857-1926` |
| Modal / confirm / toast components | `components/modal.html`, `components/confirm_modal.html`, `hub.toast.trigger_toast` | `templates/components/`, FRONTEND.md |
| Instructor messaging | `InstructorMessage` — **untouched** | `classes/models.py:2361-2428` |

**Genuine gaps to close (kept minimal):**
1. No way to represent "confirmed but owes money" → one field, `payment_due_cents` (§4).
2. No manual-promote model method → `Registration.promote_from_waitlist()` (§5).
3. No pay-for-my-registration page → one token-rails page + one webhook metadata kind (§5.4).
4. Three emails: promoted-with-pay-link, promoted-plain, removal notice (§7).
5. `confirm_modal.html` gains an opt-in free-text note input (mirrors the existing opt-in typed-confirmation pattern) so Remove can carry an internal reason (§6.7).

## 3. Where the code lives

```
classes/
    models.py            # payment_due_cents, payment_link_sent_at; promote_from_waitlist(),
                         # mark_paid(), remove_by_staff(), compute_promote_price_cents();
                         # new CmsActivity kinds
    migrations/00XX_registration_payment_due.py   # additive, auto-reversible
    emails.py            # send_waitlist_promoted(), send_payment_link(), send_removal_notice()
    views.py             # registration_action endpoints, pay page, instructor filter, mine toggle
    urls.py              # new routes (see §5.6)
    webhook_handlers.py  # handle kind=class_payment_link
    spec/models/registration_roster_spec.py
    spec/views/roster_actions_spec.py
    spec/views/registration_pay_spec.py
templates/classes/
    partials/registration_row.html      # shared roster row (teach + admin)
    partials/waitlist_row.html          # shared waitlist row (teach + admin)
    partials/roster_modals.html         # promote confirm, pay-link follow-up shell, mark-paid, remove confirm
    partials/promote_followup_body.html # per-row body hx-loaded into the follow-up modal (name, amount, notify URLs)
    teach/class_registrations.html      # action tables (modified)
    teach/class_waitlist.html           # action tables (modified)
    admin/class_registrations.html      # action tables (modified)
    admin/class_waitlist.html           # action tables (modified)
    admin/registrations.html            # instructor filter + Mine only toggle (modified)
    admin/registration_detail.html      # paid/unpaid line + payment actions + duplicate-payment banner (modified)
    public/registration_pay.html        # NEW token-rails pay page
    public/my_registration.html         # modified: ?paid=1 processing banner + already-in claim-guard message
    emails/promoted_pay.{txt,html}      # "You're in! Complete your payment"
    emails/promoted.{txt,html}          # "You're in"
    emails/removed.{txt,html}           # removal notice
templates/components/confirm_modal.html # opt-in note-input params (small extension)
```

Everything stays inside the `classes` app — already in coverage/mypy scope.

## 4. Data model

Two additive fields on `Registration`. No new models.

| Field | Type | Note |
|---|---|---|
| `payment_due_cents` | `PositiveIntegerField(default=0)` | `help_text="What this registration owes, stamped at promote time. 0 = nothing owed (normal flow, free class, or fully settled at registration)."` |
| `payment_link_sent_at` | `DateTimeField(null=True, blank=True)` | `help_text="Last time a payment-link email was sent for this registration. Display-only ('Link sent Aug 26'); dedupe lives in the emit period."` |

**Why an explicit `payment_due_cents` rather than pure derivation or a boolean:**
- Pure derivation (`amount_paid_cents` vs the class's *current* price) breaks the moment the price, a sale window, or a member discount changes after promote — the person's debt would silently drift. Stamping the owed amount at promote time freezes the deal like `TabEntrySplit` freezes splits.
- A boolean (`is_unpaid`) can't answer "how much?" — which the pay page, the payment-link email, and the roster badge all need.
- `default=0` means **every existing row is already correct with zero backfill**: nothing owed → never "unpaid". Migration is a plain additive schema migration, auto-reversible (drop the columns); no data migration, so no reverse-function question.

**Derived properties** (cheap → `@property`, per CLAUDE.md):

- `Registration.balance_due_cents -> int` — `max(0, payment_due_cents - amount_paid_cents)`.
- `Registration.is_unpaid -> bool` — `status == CONFIRMED and balance_due_cents > 0`.

**Interaction with existing paths:** the normal register flow never sets `payment_due_cents` (stays 0), so nothing changes for standard registrations. The `checkout.session.completed` handler already writes `amount_paid_cents = amount_total`; the new balance-payment handler (§5.4) does the same, which flips `is_unpaid` off naturally.

**New `CmsActivity.Kind` values** (extend the enum; the feed template gets three label rows):

| Kind | Label | Written by |
|---|---|---|
| `WAITLIST_PROMOTED` | "Promoted from waitlist" | `promote_from_waitlist()` (replaces the generic REGISTRATION_CONFIRMED row for this transition — see §5.1) |
| `PAYMENT_LINK_SENT` | "Payment link sent" | `send_payment_link_email()` |
| `REGISTRATION_MARKED_PAID` | "Marked paid" | `mark_paid()` (payload carries the optional method note) |
| `DUPLICATE_PAYMENT` | "Duplicate payment received" | balance webhook when the row was already settled (§5.4 check 2 — payload carries `payment_intent`, `amount_cents`, session id) |

## 5. Business logic (fat models)

All on `Registration` / thin wrappers in views. Domain guard failures raise a new `classes.exceptions.RegistrationStateError(Exception)` — views catch it and toast the message; never a 500.

### 5.1 `promote_from_waitlist(actor: User | None) -> None`

- **Guard:** `status == WAITLISTED`, else `RegistrationStateError("Only waitlisted registrations can be added to the class.")` (protects against double-click / stale row races — first click wins, second click toasts this).
- Stamps `payment_due_cents = self.compute_promote_price_cents()`, `status = CONFIRMED`, `confirmed_at = now`, `_acting_user = actor`, saves.
- Sets a transient `_promoting = True` before save; `_dispatch_status_notification`'s CONFIRMED branch checks it and logs `WAITLIST_PROMOTED` (payload: `{"due_cents": …}`) instead of `REGISTRATION_CONFIRMED` — a staff promote is not a "Payment confirmed" event, and without this the feed would double-row.
- **Does NOT email.** The caller (view) sends the right promoted email per the locked flow (§7) — free/`Not now` → plain "You're in"; `Send` → pay-link email. This keeps "exactly one email, chosen by staff" honest.
- **Does NOT trigger claim links.** Confirming *consumes* a seat; `promote_next_from_waitlist` only runs from `cancel()`/`mark_refunded()`. Verified no other path fires it.
- Over-capacity: no guard — allowed per locked decision (the UI warns).
- Discount interplay: `compute_promote_price_cents()` (new model method) mirrors `ClassRegistrationForm.compute_final_price_cents` using **stored** state: `offering.sale_price_cents` → member % if `self.member` is set → `self.discount_code.apply_to(...)` if one was stored at waitlist join **and** `not offering.sale_blocks_codes` — the form engine (`classes/forms.py:905`) applies a code only when the sale doesn't block codes, and the mirror must carry that rule or a promote during a code-blocking sale under-charges (§9 has the case). When it does apply, the code is applied as stored — no re-validation; the person entered it in good faith when they joined. Code `use_count` bumps when payment is actually recorded (webhook / `mark_paid`), matching the existing confirm-time bump.

### 5.2 `mark_paid(actor: User | None, note: str = "") -> None`

- **Guard:** `is_unpaid`, else `RegistrationStateError("This registration has no outstanding balance.")`.
- Sets `amount_paid_cents = payment_due_cents`, saves (`update_fields`), logs `REGISTRATION_MARKED_PAID` with `actor` and `payload={"note": note}` (who/when live on the activity row — no extra model fields).
- No email — the staff member is standing next to the cash box; the activity feed is the record.
- **Race with an in-flight online payment:** if the registrant completes Checkout around the same time staff marks the row paid, the balance webhook (§5.4, check 2) detects the already-settled row, records the duplicate distinctly, and alerts staff that a refund is owed — never silent. The Mark as Paid modal also carries a warning line when a checkout session exists (§6.6) so staff can pause instead of colliding.

### 5.3 `remove_by_staff(actor: User | None, reason: str = "") -> None`

- **Guard:** `status in (CONFIRMED, PENDING, WAITLISTED)`, else `RegistrationStateError("This registration is already {status}.")`.
- Captures `was_waitlisted` before delegating to the existing `cancel(reason=reason, actor=actor)` — which frees the seat, logs `REGISTRATION_CANCELLED` / `WAITLIST_LEFT`, and fires `promote_next_from_waitlist()` when a seat was held (the claim-link email the modal warned about).
- Then sends the removal-notice email (§7) — from this method, NOT from `cancel()`, so self-serve cancels and refund flows keep their current email behavior untouched.

### 5.4 Payment link + pay page (the "pay-for-my-registration" rails)

**`send_payment_link_email(registration, actor)`** (in `classes/emails.py`, invoked from the view):
- Guard `is_unpaid` (same `RegistrationStateError` surface).
- Emits the `waitlist_promoted_pay` event (§7) with CTA → the pay page URL built on `self_serve_token` via `_absolute_url`.
- Stamps `payment_link_sent_at = now`, logs `PAYMENT_LINK_SENT`.
- **Dedupe without blocking re-sends:** `period=f"reg:{pk}:paylink:{now:%Y%m%d%H%M}"` — a double-click inside the same minute collapses into one delivery (the emit spine's `EventDelivery` dedupe), while a deliberate re-send tomorrow gets a fresh period. (Same trick family as the reminder periods.)

**Pay page** — `GET/POST /classes/my/<token>/pay/` (`classes:my_registration_pay`), no login required (token IS the auth, exactly like `my_registration`):
- **GET:** if not `is_unpaid` → redirect to `classes:my_registration` with a **state-aware** message: settled → "Nothing owed. You're all set."; cancelled/refunded → "This registration is no longer active." (a removed registrant clicking a stale pay link must never be told they're all set). Otherwise render `public/registration_pay.html`: class title (linked), sessions, amount due, one "Pay Now" button. **GET never creates a Stripe session** — mail-scanner prefetch must not mint Checkout sessions. When `stripe_session_id` is already set on a still-due row, the page shows a note above the button: "If you just paid, give it a minute. Payments can take a moment to land." (see the double-POST window below).
- **POST:** if `0 < balance_due_cents < STRIPE_MIN_CHARGE_CENTS` → friendly error ("This balance is under $0.50, which we can't charge online. Please contact the studio.") and staff falls back to Mark as paid. Otherwise `create_class_checkout_session(amount_cents=balance_due_cents, product_name=f"{offering.title} (balance)", metadata={"kind": "class_payment_link", "registration_id": …}, idempotency_key=f"class-paylink-reg-{pk}-{balance_due_cents}", success_url=<my_registration ?paid=1>, cancel_url=<pay page>)` → store `stripe_session_id` → redirect to Checkout.
- Success lands on `my_registration` with `?paid=1` → banner: "Thanks! Your payment is processing. You'll get a receipt by email." (webhook is async; don't promise instant state).

**Webhook:** `handle_checkout_session_completed` currently early-returns on CONFIRMED rows, so balance payments need their own branch: a `kind == "class_payment_link"` path (same handler module, same `select_for_update` transaction). **Ordered checks:**
1. `stripe_payment_id` already equals this session's `payment_intent` → Stripe re-delivery of an already-recorded payment (including a duplicate one), return.
2. **Row already settled** — `balance_due_cents == 0` at webhook time, because staff hit Mark as Paid (or an earlier link payment landed) while this Checkout was in flight. The studio has now collected twice, and silence is unacceptable. Do **NOT** overwrite `amount_paid_cents` (the ledger keeps showing the first settlement on purpose) and send **no** receipt. Instead: record `stripe_payment_id` (so check 1 makes re-deliveries idempotent), log a `DUPLICATE_PAYMENT` activity row (payload: `payment_intent`, `amount_cents`, session id), and send an admin alert email to the `_admin_recipients` rails: subject "Duplicate payment: {name}, {class title}", body stating the person paid ${amount} online after the balance was already settled, **a refund is owed**, with links to the registration detail page and the Stripe payment. The detail page surfaces a persistent warning banner while a `DUPLICATE_PAYMENT` row exists (§6.8).
3. Otherwise the normal path: set `amount_paid_cents = amount_total` (Checkout charged the full balance — no partials possible), `stripe_session_id` / `stripe_payment_id`, save, bump the stored discount code's `use_count` if present (once — guard on prior `amount_paid_cents == 0`), then call `send_registration_confirmation(registration)` as the receipt — its `period=f"reg:{pk}:confirmation"` has never fired for a promoted row, so it delivers exactly once even across webhook retries.

**Pending-webhook double-POST window:** a second Pay Now POST before the webhook lands reuses the unchanged idempotency key, so Stripe returns the original **completed** session whose URL dead-ends. Mitigation is copy, not machinery: the pay-page note above ("If you just paid, give it a minute. Payments can take a moment to land.") renders whenever `stripe_session_id` is set on a still-due row — no new timestamp field; a session id on a still-due row is itself the signal.

### 5.5 Claim-link collision guard

A manually promoted person may still hold an un-clicked auto claim link (`register?waitlist_token=…`). The register view's waitlist-token branch gains one guard: token's registration not WAITLISTED → redirect to `my_registration` with "Good news! You're already in this class." Prevents a duplicate registration row from a stale email.

### 5.6 Views, URLs, gating

One shared surface-agnostic gate, used by all four action endpoints:

```python
def _registration_manageable_or_403(request, pk) -> Registration:
    # actual admin (view_as.has_actual("admin")) → any registration;
    # else member must have the class in ClassOffering.objects.editable_by(member)
```

This is deliberately the same population as the existing read gate (`classes_registrations_access_required` + `_scoped_registrations`): instructors for their own classes, guild leads/staff for their guild's classes — and note that `editable_by` **short-circuits to every class for guild officers as well as fog-admins** (`classes/models.py:214`), so officers get the same full reach here that they already have on the read side. The detail page's cancel and move actions stay admin-only as today; the **refund** action's gating is owned by the sibling refunds spec (`2026-08-26-stripe-refunds-payments-panel.md`), which re-gates that endpoint to "fog-admin OR Refunds holder" — see the ordering note in §10.

| URL (all POST, HTMX) | Name | Does |
|---|---|---|
| `registrations/<pk>/remove/` | `registration_remove` | `remove_by_staff(actor, reason)` → row partial + toast |
| `registrations/<pk>/promote/` | `registration_promote` | `promote_from_waitlist(actor)`; then branch on the **computed** `payment_due_cents == 0` — not the class's sticker price — so a 100%-discount-code promote is "free" here and never sees a $0.00 payment modal. Due 0 → plain promoted email + toast; due > 0 → toast + `HX-Trigger: {"promote-followup": {"pk": …}}` opening the pay-link modal |
| `registrations/<pk>/promote/followup/` (GET) | `registration_promote_followup` | Renders `partials/promote_followup_body.html` (name, amount due, both notify forms) into the follow-up modal body (§6.6) |
| `registrations/<pk>/promote/notify/` | `registration_promote_notify` | The modal's two buttons post here with `choice=send|skip`; `send` → pay-link email, `skip` → plain promoted email. **Server guard:** `choice=skip` is a 204 no-op when `payment_link_sent_at` is set OR the `reg:{pk}:promoted` `EventDelivery` exists — the modal-close fallback can never stack a second email onto an explicit Send (§6.6). |
| `registrations/<pk>/send-payment-link/` | `registration_send_payment_link` | `send_payment_link_email` → row partial + toast ("Payment link sent to jane@…") |
| `registrations/<pk>/mark-paid/` | `registration_mark_paid` | `mark_paid(actor, note)` → row partial + toast |
| `my/<token>/pay/` | `my_registration_pay` | §5.4 pay page |

Roster views (`teach_class_registrations`, `teach_class_waitlist`, admin twins) pass `can_manage=True` and `spots_remaining` into context so the shared row partials render the buttons; the pages themselves stay skinny.

**Admin registrations list** (`admin_registrations`): mirror the `admin_classes` pattern — accept `?instructor=<pk>` in `_filter_registrations` (filter `class_offering__instructor_id`, validated as int, silently ignored if bogus), pass `instructors` (Members with `instructor_slug__gt=""`) + `selected_instructor` into context. "Mine only": when the acting **actual admin** has a Member row with `instructor_slug` (or any class as instructor — use `instructor_slug__gt=""` for consistency with the dropdown), render a toggle pill that links to `?instructor=<own member pk>` preserving other GET params; active state when selected. Non-admin visitors (instructors/guild-leads on this page) are already scoped to their own classes by `_scoped_registrations` — the instructor filter UI renders for actual admins only. The CSV export reuses `_filter_registrations`, so the new filter applies there for free.

## 6. UI / UX

Design language: these tabs live in the book CMS shell (`hub-btn` button family, `admin-table-wrap` scroll container, theme tokens). New reusable styles get `pl-` classes in `components.css` (the paid badge, the actions cell). Verify **both themes** on every screen below.

### 6.1 Teach → class Workspace → Registrations tab (`templates/classes/teach/class_registrations.html` → shared `partials/registration_row.html`)

- **Layout:** existing table inside `.admin-table-wrap`, columns become: Name · Email · Status · **Paid** · Registered · **Actions**. Rows render via the shared partial with `id="reg-row-{{ reg.pk }}"`.
- **Paid column:** for a row with `payment_due_cents > 0`: `is_unpaid` → `pl-badge pl-badge--warn` reading **"Unpaid · $45.00"** (amount = balance due; warn color = existing warn token pair, readable in both themes); paid → `pl-badge pl-badge--ok` "Paid $45.00". Rows with `payment_due_cents == 0` keep today's plain `{{ amount_paid_cents|cents_as_dollars }}`. If `payment_link_sent_at`, a muted sub-line under the badge: "Link sent Aug 26". If **neither** promoted email has gone out (`payment_link_sent_at` null AND no `reg:{pk}:promoted` delivery — annotated on the roster queryset with one `Exists()` subquery on `core.models.EventDelivery`, no per-row N+1), the sub-line instead reads **"No email sent yet"** in the warn tone. This is the §6.6 abandoned-modal backstop: staff can always see who was never told, and clicking Send Payment Link resolves it.
- **Paid column header help:** a `.pl-help` "?" bubble on the header (per FRONTEND.md rule 19): "Unpaid means this person was added from the waitlist and still owes for the class. Their seat is held either way. You can send or resend a payment link, or record a cash payment with Mark as Paid."
- **Actions column (inline buttons, not overflow — max three, all small):**
  - CONFIRMED/PENDING rows: **Remove** — `hub-btn hub-btn--sm hub-btn--danger`, opens the remove confirm modal (§6.7) via `$dispatch('open-confirm', 'remove-reg-{{ reg.pk }}')`.
  - Unpaid confirmed rows additionally: **Send Payment Link** (`hub-btn--sm hub-btn--ghost`, `hx-post` → `registration_send_payment_link`, `hx-target="#reg-row-{{ reg.pk }}"`, `hx-swap="outerHTML"`, `hx-disabled-elt="this"`) and **Mark as Paid** (`hub-btn--sm`, opens the mark-paid modal §6.6).
  - CANCELLED/REFUNDED rows: no buttons (row already renders at reduced opacity today — keep).
- **States:** empty → existing "No registrations yet." (kept). Loading → HTMX `hx-disabled-elt` disables the clicked button; htmx's `.htmx-request` opacity on the row is enough — no spinner needed for sub-second swaps. Error → view catches `RegistrationStateError` and returns the *fresh* row partial + an error toast with the guard's message (stale rows self-heal). Success → updated row swaps in + success toast ("Jane removed from the class." / "Marked paid." / "Payment link sent to jane@…").
- **Dark + light:** badge and buttons use tokens only; no new inline color styles.
- **Mobile:** `.admin-table-wrap` horizontal scroll contains the wider table; buttons stay full-size tap targets; the Actions column is last so the essentials (name/status) read first without scrolling.

### 6.2 Teach → class Workspace → Waitlist tab (`teach/class_waitlist.html` → shared `partials/waitlist_row.html`)

- **Columns:** # · Name · Email · Joined · Notified · **Actions**. Row `id="wl-row-{{ reg.pk }}"`.
- **Actions:** **Add to Class** — primary (`hub-btn hub-btn--sm hub-btn--primary`), opens the promote confirm modal (§6.5). **Remove** — `hub-btn--sm hub-btn--danger`, opens the remove confirm modal (waitlist copy variant — no seat is freed, no claim-link warning).
- On successful promote the row swaps to a confirmed-styled stub ("Added to class ✓. See the Registrations tab.") rather than vanishing — the person the staff just acted on shouldn't disappear mid-thought; it renders as a normal waitlist row minus buttons, gold accent. Page reload shows them gone from the waitlist and present on Registrations, both tabs' Workspace counts refresh on next load (counts are server-rendered; acceptable staleness for one click).
- **States:** empty → existing "No one is on the waitlist." Loading/error/success as §6.1. If someone else promoted/removed the row first, the guard toast explains and the swap shows current reality.
- **Notified column** stays exactly as today (gold timestamp / muted "Not yet") — it's the fairness signal for out-of-order picks.

### 6.3 Admin → class Workspace → Registrations + Waitlist tabs (`admin/class_registrations.html`, `admin/class_waitlist.html`)

Identical tables — they include the same two row partials and the same modals include (`partials/roster_modals.html`). Only the surrounding chrome (admin base, export button) differs. No admin-only extras on the rows; the locked decision is "same actions both surfaces."

### 6.4 Admin → Registrations list (`admin/registrations.html`)

- **Filter bar** (existing status + class selects): add an **Instructor** `<select name="instructor">` — "All instructors" + one option per instructor, same markup/styling as the existing selects (they carry theme-correct inline styles today; match them — do not introduce a new pattern here).
- **"Mine Only" toggle:** a pill button (`hub-btn hub-btn--sm`, `--primary` when active) beside the selects, rendered only when the acting admin has an instructor profile. Click → same page with `?instructor=<own pk>` (preserving status/class/search/sort params); active click → clears the instructor param. It's a link styled as a toggle, not a checkbox — it's a filter shortcut, not a boolean field.
- Rendered for **actual admins only** (non-admin visitors are pre-scoped to their own classes; a filter that can't widen anything would just confuse).
- **States:** filtered-to-empty → existing empty table treatment plus the filter bar still visible so the user can back out (no dead end). Export button honors the filter (free via `_filter_registrations`).
- **Rows are unchanged** — this list stays a finder; actions live on the rosters and the detail page.

### 6.5 Promote confirm modal (`components/confirm_modal.html`, `confirm_button_style="primary"`, one per waitlist row: `confirm_id="promote-reg-{{ reg.pk }}"`)

- Title: "Add Jane Doe to the Class?"
- Which variant renders is decided by the **computed promote price** (`compute_promote_price_cents()`, the same `payment_due_cents == 0` branch the endpoint uses) — a 100%-discount-code promote gets the free copy, never a $0.00 payment mention.
- Message (owes money): "Jane takes a seat immediately. This class costs **$45.00** for her (member discount applied). You'll choose whether to send a payment link next. No payment is required to hold the seat."
- Message (nothing owed): "Jane takes a seat immediately and gets a confirmation email."
- **Over-capacity warning** (when `spots_remaining == 0`), an extra highlighted line: "⚠ This class is already full ({{ offering.capacity }} seats). Adding Jane puts it over capacity. Your call; make sure the room can take it."
- Confirm button: **"Add to Class"** (primary). Posts to `registration_promote` (HTMX, `hx-target` the waitlist row).

### 6.6 "Send them a payment link?" follow-up modal + Mark as Paid modal (`components/modal.html`, in `partials/roster_modals.html`)

- **Follow-up modal** (`modal_id="promote-followup"`, size `sm`) — ONE modal per page, per-row content loaded on demand. **Plumbing, concretely:** the promote response fires `HX-Trigger: {"promote-followup": {"pk": …}}`; a page-level Alpine listener (`@promote-followup.window`) runs `htmx.ajax('GET', /classes/registrations/<pk>/promote/followup/, '#promote-followup-body')` and then `$dispatch('open-modal', 'promote-followup')`. The loaded partial, `partials/promote_followup_body.html` (same gate as the action endpoints), carries the registrant's name, the amount due, and both `registration_promote_notify` POST forms — so the single modal shell never needs template-time knowledge of every row. Copy: "**Jane's in!** She owes $45.00. Send her a payment link now? You can always send it later from the roster." Buttons: **Send Payment Link** (primary, posts `choice=send`, toast "Payment link sent") and **Not Now** (ghost, posts `choice=skip`, which sends the plain "You're in" email, toast "Confirmation sent, no payment link"). Both paths email; the copy makes clear "Not now" still tells Jane she's in.
- **Close/abandon behavior — no double email, no silent seat:** clicking either button sets an Alpine `choiceMade` flag *before* its post; the modal's `@close` fallback (backdrop / X / Esc) posts `choice=skip` only when `choiceMade` is false, so an explicit Send is never chased by the fallback's skip. Server-side backstop regardless of client state: `registration_promote_notify` treats `choice=skip` as a 204 no-op when `payment_link_sent_at` is set or the `reg:{pk}:promoted` delivery exists (§5.6) — the two-email failure mode is closed even if the client misbehaves. If the browser dies before *any* post fires, the person holds a seat with zero emails; the roster's "No email sent yet" chip (§6.1) makes that visible and the row's Send Payment Link button is the remedy — the "hears exactly once" promise degrades to "staff can see they haven't heard yet," never to a silent double-send.
- **Mark as Paid modal** (`modal_id="markpaid-reg-{{ reg.pk }}"`, size `sm`): one optional text field — label "How was this paid?" with `field_hint` "For the activity log. For example cash, comped, check." — rendered via `components/form_field.html`; then a primary **"Mark as Paid"** button posting to `registration_mark_paid` (HTMX). When `stripe_session_id` is set on the still-due row, a warn line renders above the button: "Heads up: an online payment was started for this registration. If they also pay online, you will get an alert to refund one of the payments." 1-field quick action → modal + toast, per the FRONTEND interaction table. Cancel = modal close, no dead end.

### 6.7 Remove confirm modal (`components/confirm_modal.html` + the new note-input extension, `confirm_id="remove-reg-{{ reg.pk }}"`)

- **Component extension** (small, opt-in, mirrors the typed-confirmation params): `confirm_note_name` / `confirm_note_placeholder` — when set, an optional single-line text input renders above the buttons and posts under that name. Omitted → renders exactly as before.
- Title: "Remove Jane Doe from This Class?"
- Message, confirmed/pending seat-holder — the claim-link sentence renders from **real waitlist state**, computed once per page in the roster view (a claim link will actually fire only when the removal frees a seat that leaves `spots_remaining > 0` after the cancel — an over-full class can free a seat and still be full — AND an un-notified WAITLISTED row exists):
  - Claim link will fire: "Jane's seat is freed and she'll be emailed that her registration was cancelled. **The next person on the waitlist is automatically emailed a claim link for the open seat.**"
  - It won't: "Jane's seat is freed and she'll be emailed that her registration was cancelled. No waitlist claim email will go out right now (no one is waiting who hasn't already been notified, or the class is still full)."
- Plus, when `amount_paid_cents > 0`: "Jane paid $45.00. Removing does **not** refund her. Refunds are handled separately{% if user has refund authority %} from the [registration's refund action]{% endif %}." Refund authority = fog-admin OR Refunds capability holder, matching the sibling spec's authority model (`2026-08-26-stripe-refunds-payments-panel.md`) — a fog-admin without the capability grant must not lose the link. This modal only links to that action; the action itself lives in the sibling spec.
- Message, waitlist variant: "Jane is removed from the waitlist and emailed. No seats change."
- Note input: `confirm_note_name="reason"`, placeholder "Reason (internal, optional)".
- Confirm button: **"Remove"** (danger). Posts to `registration_remove`.

### 6.8 Registration detail page (`admin/registration_detail.html`)

- Payment table gains a **Balance** row when `payment_due_cents > 0`: "Owes $45.00 · Unpaid" badge or "Paid in full", plus "Payment link last sent Aug 26" when stamped.
- **Duplicate-payment banner:** while a `DUPLICATE_PAYMENT` activity row exists for this registration, a danger-tone banner renders above the payment table: "This person paid online after the balance was already settled. A refund is owed." — with links to the Stripe payment and the activity log. It stays until the refund is handled (resolution mechanics belong to the sibling refunds spec).
- Admin Actions block gains, for unpaid rows: **Send Payment Link** and **Mark as Paid** buttons wired to the same endpoints (full-page fallback: these two accept non-HTMX POSTs and redirect back with a Django message, same dual behavior as `admin_registration_cancel`). Cancel/move/refund forms stay untouched.

### 6.9 Public pay page (`public/registration_pay.html`)

- Branded public-classes shell (like `my_registration`). Card: class title **linked** to the public class page, session date(s), "Amount due: **$45.00**", one primary **"Pay Now"** button (a plain POST form — full-page redirect to Stripe, no HTMX), and a secondary link "View your registration". Fields none — nothing to validate.
- **States:** nothing owed → GET redirects to `my_registration` with the state-aware message from §5.4 (settled vs no-longer-active — a stale link never dead-ends and never congratulates a removed registrant). Balance under $0.50 → POST re-renders with the friendly error: "This balance is under $0.50, which we can't charge online. Please contact the studio." Session already started and still due → the "If you just paid, give it a minute" note (§5.4). Bad token → existing 404 handling on token lookup.
- Both themes: public-classes token scope (`.reg-field` family not needed — no inputs); no inline colors.

## 7. Notifications / emails / activity

All three new emails follow the FRONTEND.md Email Templates rules: branded shell via `emit_with_email_shell`, linked subject noun (class title → public class page), one primary CTA + helpful secondaries, absolute URLs via `_absolute_url`, `.txt` + `.html` in sync, Portland timezone throughout. Recipient is `registration.email` (`email_to` — guest-safe); the event resolver posts the in-app bell row to the linked member when one exists, exactly the `waitlist_spot_available` pattern.

| Event key | Subject | Primary CTA | Secondaries | Period (dedupe) |
|---|---|---|---|---|
| `waitlist_promoted_pay` | "You're in! Complete your payment for {title}" | **Pay Now** → pay page | Class details; manage registration | `reg:{pk}:paylink:{YYYYMMDDHHMM}` — minute-bucketed so double-clicks collapse but deliberate re-sends deliver |
| `waitlist_promoted` | "You're in! {title}" | **View Class Details** | Manage registration | `reg:{pk}:promoted` — once ever |
| `registration_removed` | Seat-holder: "Your registration for {title} was cancelled". Waitlist variant: "You've been removed from the waitlist for {title}" | **Browse Upcoming Classes** | Contact the studio (mailto); seat-holder variant adds "if you paid, your refund is handled separately and we'll be in touch" when `amount_paid_cents > 0` | `reg:{pk}:removed` |

Body content: both promoted emails carry session date/time/location and the instructor's welcome note when set (`offering.welcome_email_ready` guard — surface the human content). The pay variant states the amount due plainly. The removal email is short, kind, and blame-free — one template pair forked on a `was_waitlisted` context flag (captured by `remove_by_staff` before the cancel): the waitlist variant carries no seat, cancellation-of-a-confirmed-spot, or refund language, because none of it is true for someone who never held a seat.

Existing emails reused, not duplicated: the auto claim link (`send_waitlist_spot_opened`) fires unchanged from `cancel()`'s auto-promote; the standard confirmation (`send_registration_confirmation`) becomes the paid-in-full receipt when the balance webhook lands (its `reg:{pk}:confirmation` period has never been used for a promoted row → exactly-once even across Stripe retries).

Activity rows per §4's new kinds; `WAITLIST_PROMOTED`, `PAYMENT_LINK_SENT`, and `REGISTRATION_MARKED_PAID` all carry `actor` so the feed names the staff member, never "System".

## 8. Build order (phased; each phase ships green)

1. **Model + logic.** Fields + migration, `balance_due_cents`/`is_unpaid`, `compute_promote_price_cents()`, `promote_from_waitlist()` (+ `_promoting` dispatch branch), `mark_paid()`, `remove_by_staff()`, `RegistrationStateError`, new `CmsActivity` kinds + feed labels. Run `manage.py check` after the migration (CI runs system checks pytest skips). Specs for all of it.
2. **Emails + events.** Three template pairs, three senders, `send_payment_link_email` with the minute-bucketed period, `payment_link_sent_at` stamping, register-view claim-link guard (§5.5). Verify the shell styles the copy (cream-on-dark, gold links).
3. **Pay page + webhook.** `my_registration_pay` GET/POST, `registration_pay.html`, the `class_payment_link` webhook branch + receipt send. Specs incl. idempotent re-delivery.
4. **Roster UI.** Shared row partials + `roster_modals.html`, the four action endpoints + gate, `confirm_modal.html` note-input extension, wire all four Workspace tabs (teach + admin), toasts, `pl-badge` styles in `components.css`. Template comment lint + both themes checked.
5. **Admin list filter + detail page.** Instructor param in `_filter_registrations`, dropdown + Mine Only toggle, detail-page balance row + payment actions with non-HTMX fallback.
6. **Housekeeping.** Bump `plfog/version.py` VERSION → next minor (1.7.0 from today's 1.6.1) + ONE member-friendly CHANGELOG entry stamped at that version, e.g. **"Roster Management for Instructors"** — "Instructors and class admins can now manage their class rosters directly: add someone from the waitlist with one click, send them a payment link (or record a cash payment), and remove a registrant. The next person on the waitlist is invited automatically." (Announce fires automatically on merge when VERSION changes — curate before merging.)

> Spec only — do not build until approved.

## 9. Testing

BDD `*_spec.py` under `classes/spec/`, `describe_*`/`it_*` (never `context_*`), factory-boy, respx-free (Stripe calls mocked at `billing.stripe_utils` boundary — mock external services, never models/DB), 100% branch coverage.

**`spec/models/registration_roster_spec.py`**
- `describe_promote_from_waitlist`: waitlisted → CONFIRMED with `confirmed_at`; stamps `payment_due_cents` (cases: base price, sale price, member-linked discount, stored discount code, free class → 0); logs `WAITLIST_PROMOTED` not `REGISTRATION_CONFIRMED`; attributes actor; raises `RegistrationStateError` for each non-WAITLISTED status; **over-capacity allowed** (full class still promotes; `spots_remaining` stays 0); **no claim-link email fires** (assert `send_waitlist_spot_opened` not called); sends no email itself.
- `describe_is_unpaid_and_balance`: derivations across (due, paid) combos; legacy rows (due=0) never unpaid; PENDING with due>0 is not "unpaid" (property requires CONFIRMED).
- `describe_mark_paid`: settles balance; logs kind + note payload + actor; raises when nothing owed; double-call raises (state machine: unpaid → paid is one-way).
- `describe_remove_by_staff`: confirmed → cancelled, seat freed, auto-promote claim link fires to next un-notified waitlister; waitlisted variant → `WAITLIST_LEFT`, no claim link; removal email sent with refund line only when `amount_paid_cents > 0`; guard on already-cancelled; self-serve `cancel()` still sends **no** removal email (regression).
- `describe_compute_promote_price_cents`: mirrors form engine ordering (sale → member % → code), floors at 0; **stored code is ignored when `sale_blocks_codes`** (a promote during a code-blocking sale charges the sale/member price, never the code-discounted one); 100% code with no blocking sale → 0.
- `describe_remove_by_staff` (additional): waitlist-variant removal email uses the waitlist subject/body fork (no seat or refund language); seat-holder variant includes the refund line only when paid.

**`spec/views/roster_actions_spec.py`**
- Gating for every endpoint: own-class instructor 200; other instructor 403; guild lead of the class's guild 200; plain member 403; anonymous → login; actual admin 200 (including while previewing as another role).
- Promote endpoint: **branch is on computed due, not sticker price** — a paid class with a stored 100% discount code takes the free path (plain promoted email, no `promote-followup` trigger); paid-due class response carries the HX-Trigger; stale row (already promoted) → error toast + fresh partial, no 500. Followup GET renders the row's name/amount and is gated identically.
- `promote_notify`: `choice=send` → pay-link email + `payment_link_sent_at` stamped + `PAYMENT_LINK_SENT` logged; `choice=skip` → plain promoted email; **skip-after-send guard:** `choice=skip` after a send (`payment_link_sent_at` set) or after a prior skip (`reg:{pk}:promoted` delivery exists) is a 204 no-op with zero emails — the modal-close fallback can never double-email; **email dedupe:** two `send` posts in the same minute deliver ONE email (period bucket), a send in a later minute delivers again.
- Roster "No email sent yet" chip: renders only when `payment_link_sent_at` is null AND no `reg:{pk}:promoted` delivery exists; disappears after either email; the `Exists()` annotation keeps the roster at a constant query count (assert with `django_assert_num_queries`).
- Remove modal conditional copy: claim-link sentence renders when an un-notified waitlister exists and the removal opens a seat; the no-email variant renders for an empty/all-notified waitlist and for an over-capacity class whose removal still leaves it full.
- Mark-paid + remove endpoints: happy path row partial + toast; reason/note threaded to activity payload; non-HTMX POST on the detail-page pair redirects with a message.
- Admin registrations list: `?instructor=` filters; bogus value ignored; Mine Only renders only for admins with an instructor profile; toggle preserves other GET params; export honors the filter; non-admin instructor never sees the filter UI and stays scoped.

**`spec/views/registration_pay_spec.py`**
- GET: unpaid renders page **without** creating a Stripe session; settled redirects with "Nothing owed. You're all set."; cancelled/refunded redirects with "This registration is no longer active." (never the all-set copy); bad token 404; "give it a minute" note renders when `stripe_session_id` is set on a still-due row and not otherwise.
- POST: creates Checkout with balance amount + `class_payment_link` metadata + stable idempotency key; sub-50¢ balance renders the friendly error.
- Webhook `class_payment_link`: records amount/session/payment ids; flips `is_unpaid`; sends the confirmation receipt once; **idempotent** on re-delivery (same `payment_intent` → no double receipt, no double code bump); discount `use_count` bumps exactly once.
- **Webhook duplicate-payment (mark-paid race):** row settled by `mark_paid` before the webhook lands → `amount_paid_cents` untouched, no receipt, `DUPLICATE_PAYMENT` activity logged with intent/amount payload, admin alert email sent to `_admin_recipients`, `stripe_payment_id` recorded; Stripe re-delivery of that same duplicate is then a full no-op (check 1); detail page renders the duplicate-payment banner while the activity row exists.
- Claim-link guard: `register?waitlist_token=` for a promoted (non-WAITLISTED) registration redirects to `my_registration`, creates nothing.

**Templates:** run `tests/template_comment_lint_spec.py` (multi-line `{# #}` guard) after template work. Changelog wording: mind the `project_changelog_renders_everywhere` gotcha — keep the entry free of strings that negative test assertions grep for.

**Tz gotcha:** promote/paid emails render session times — assert Portland-local formatting in both subject-adjacent copy and body (one timezone, per FRONTEND.md).

## 10. Open / deferred

- **PENDING (started checkout, never paid) rows:** Remove works on them; "Send payment link"/"Mark as paid" deliberately do NOT (their `payment_due_cents` is 0 and their checkout flow is the register rails). If staff want to nudge abandoned checkouts, that's its own small feature.
- **Refund mechanics:** entirely in `2026-08-26-stripe-refunds-payments-panel.md`. This spec only links to the capability from the remove modal and the removal email's "handled separately" line.
- **Partial payments / payment plans:** out — Checkout charges the full balance, `mark_paid` settles in full. No installments.
- **Claim-window expiry for auto-promote:** untouched; the existing `waitlist_claim_window_hours` copy/behavior is not this feature's problem.
- **Workspace tab-count live refresh after an HTMX action:** counts update on next page load; an OOB count swap is a polish item if staff notice.
- **Reordering the waitlist / drag-to-prioritize:** explicitly out — staff picking a specific person covers the need (YAGNI).
- **Detail-page cancel and move opening to instructors:** unchanged (admin-only today); revisit only if instructors ask. The refund action is deliberately NOT bundled into that "unchanged" claim — the sibling refunds spec re-gates the refund endpoint to fog-admin OR Refunds holders, so this spec takes no position on refund gating. **Ordering dependency:** whichever spec builds second wires the remove-modal refund link to the sibling's authority helper; if this spec ships first, the link renders for fog-admins and switches to the helper when it lands.
