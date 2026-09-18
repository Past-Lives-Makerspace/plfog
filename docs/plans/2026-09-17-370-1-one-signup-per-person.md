# 370 item 1: one signup per person per class

Parent issue: https://github.com/Past-Lives-Makerspace/plfog/issues/370 (item 1)

## The bug

`POST /classes/<slug>/register/` lands in `register` (`classes/views.py:618`). On a
valid form it calls `form.save()` (`:688` for the paid branch, `:681` for the waitlist
branch), and `RegistrationForm.save` (`classes/forms.py:1094`) creates a **new**
`Registration` row every time. Nothing gates it:

- **No client guard.** `templates/classes/public/register.html` renders a bare
  `<button type="submit">`. Two impatient clicks are two full POSTs.
- **No server guard.** `RegistrationForm.clean` (`classes/forms.py:1062`) checks
  sold-out state and the Stripe minimum, never whether this email already has a live
  registration for this class.
- **No DB guard.** `Registration.Meta` (`classes/models.py:3182`) declares two indexes
  and no `UniqueConstraint`.

The Stripe idempotency key does not help. It is
`f"class-checkout-reg-{registration.pk}"` (`classes/views.py:734`), keyed on the row
created milliseconds earlier, so each duplicate gets its own key and its own Checkout
Session. It protects a retried request for *one* registration and is structurally
unable to see a double-submit.

Every stray row eats a seat: `spots_remaining` (`classes/models.py:1787`) counts
CONFIRMED **and** PENDING, and so does `spots_remaining_map` (`:479`).

**The parent issue misses that the waitlist branch has the same hole.** Line 681 also
calls `form.save()` unguarded, so double-clicking "Join waitlist" duplicates too.

## Confirmed on production

Queried 2026-09-17 (read only). Restricted to seat-holding statuses there are exactly
**2 violating groups, 2 extra rows**, and both are the bug reproducing itself:

| Offering | Title | Rows |
|---|---|---|
| 2 | `[DEMO] Lamp Working Fundamentals (Past)` | pk 16 + pk 17, both `pending`, both 2026-05-25 |
| 652 | `[DEMO] Watercolor Washes for Maps` | pk 59 + pk 60, both `pending`, both 2026-09-10 |

Both belong to a `counciltreasurer+…` training account on `[DEMO]` classes. **No real
member is affected.** Including WAITLISTED in the constraint adds no further
violations, and no row anywhere is both waitlisted and registered for the same class.

## The fix, in three parts

### 1. DB constraint (the real gate)

A conditional `UniqueConstraint` on `(class_offering, email)` limited to the
seat-holding statuses `CONFIRMED`, `PENDING`, `WAITLISTED`. Excluding CANCELLED and
REFUNDED means someone who cancels can legitimately sign up again.

Reuse the status set from `active_registration_count` (`classes/models.py:1586`)
rather than inlining a fourth copy of it.

Watch `Meta.indexes` naming: CI runs `manage.py check`, and system check `E034` caps
index names at 30 characters. A local `pytest` run does not catch that.

### 2. Data migration for the two existing violators

The constraint cannot be added while violating rows exist. Do **not** delete them.
CLAUDE.md requires a real reverse function on every data migration, and a delete
cannot be reversed.

Forward: for each violating `(class_offering, email)` group, keep the earliest row by
`registered_at` and set the rest to CANCELLED via a raw queryset `.update()` (bypassing
`save()` so no email or activity fires), stamping
`cancellation_reason` with an exact marker string identifying this migration.

Reverse: find rows carrying that exact marker, set them back to PENDING and clear the
reason. Genuinely reversible, nothing destroyed.

### 3. Reuse the in-flight checkout, and guard the button

**Server side.** Before creating a row, look for a live PENDING row for the same
`(offering, email)`. If one exists and carries a `stripe_session_id`, retrieve it with
`billing.stripe_utils.retrieve_checkout_session` (already exists, returns `url` and
`status`) and, when the session is still `open`, redirect the registrant back to that
same Checkout URL instead of creating a second row. This is the behaviour a user
wants: the second click lands them where the first one was taking them.

Same guard on the waitlist branch, minus the Stripe part: a second waitlist POST
returns the existing waitlist row rather than a new one.

The constraint remains the backstop for the genuine race where two POSTs arrive before
either has written. That collision must surface as a handled outcome, not a 500.

**Client side.** Disable the submit button on submit. Two existing precedents to match
rather than invent: `templates/hub/admin/site_settings.html` (note its deliberate
`setTimeout(..., 0)` so the button's `name=value` still serializes into the submission)
and `templates/billing/setup_payment_method.html`.

## Acceptance criteria

1. A spec reproduces the bug first: two POSTs of the same valid registration produce
   **one** row, not two.
2. Seat math is asserted: N duplicate attempts consume **1** seat, not N.
3. The same holds for the waitlist branch.
4. The second POST redirects to the **same** Stripe Checkout URL as the first, not a
   new session.
5. A registrant who genuinely cancels can register again for the same class.
6. The simultaneous-race case (constraint fires) is handled and produces a sensible
   response, not a 500.
7. The data migration's reverse restores the two rows it cancelled, proven by a spec.
8. `DATABASE_URL="sqlite:///tmp-check.sqlite3" .venv/bin/python manage.py check` passes
   (the E034 index-name trap).

## Out of scope

- Expiring or reaping stale PENDING rows, `expires_at` on the Checkout Session, and
  the `checkout.session.expired` wiring. That is item 2, the next PR. Do not start it.
- Roster filtering and the unfiltered `Count("registrations")` annotations. That is
  item 3.
- Event-id level dedupe in `billing/views.py`. The parent issue establishes the webhook
  is already idempotent and is not the duplicate source.
- Anything in #371.
- Cleaning the two prod rows by hand. The migration does it.

## Files expected to change

- `classes/models.py` (`Registration.Meta` constraint)
- `classes/views.py` (`register`, both branches)
- `classes/forms.py` only if the guard genuinely belongs in `clean`
- two migrations (one `AddConstraint`, one data migration with a real reverse)
- `templates/classes/public/register.html`
- specs under `classes/spec/`

Touching anything outside this list is a question for the orchestrator.
