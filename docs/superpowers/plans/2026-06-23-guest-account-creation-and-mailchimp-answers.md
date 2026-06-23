# Guest registration: account creation + Mailchimp answer sync

> ⚠️ **NOT IMPLEMENTED — planning document only.** Nothing described below has been
> built. No code from this plan exists in the codebase yet. It scopes two proposed
> future PRs; treat every "will / add / create" as a proposal, not a description of
> current behavior.

**Date:** 2026-06-23
**Status:** Proposed — not yet implemented (no code written)
**Surface:** Booking site (`book.pastlives.space` / `book.pastlives.test`)

## Summary

Two enhancements to the public class-registration flow, both for the **anonymous (not-logged-in) registrant**:

1. **Account creation during registration** — let a guest end up with a real (passwordless) account as a byproduct of booking, instead of staying an email-only guest. Their booking and their answered questions link to that account.
2. **Send the answered questions to Mailchimp** — when a registrant opts into the newsletter, push *what they actually answered* (not just the class/category tags we send today) so Mailchimp can segment them onto the right lists/automations.

These are independent and should ship as **two separate PRs**.

---

## Current state (what already exists — do not rebuild)

Traced from the live code so the plan starts from reality:

- **Questions are shown to guests.** `RegistrationForm` (`classes/forms.py`) injects the active CMS questions via `classes.questions.inject_fields(active_questions())` for everyone, logged-in or not.
- **Answers are recorded.** `RegistrationForm.save()` → `_create_custom_answers()` writes one `RegistrationAnswer` per non-empty answer, tied to the `Registration`.
- **Login is offered.** `templates/classes/public/register.html` shows an anonymous-only band: *"Booked with us before…? **Log in · Create an account** — or just continue as a guest below."* The "Create an account" link is a separate allauth signup detour (`account_signup?next=…`).
- **Newsletter opt-in → Mailchimp works.** The `wants_newsletter` checkbox → `classes.services.mailchimp_subscribe.subscribe_registration()` (opt-in gated, idempotent) → `MailchimpClient.subscribe(email, first_name, last_name, tags)`. `derive_tags()` emits `class-registrant`, `category-<slug>`, `guild-<name>`, `instructor-<slug>`, and `first-time-student`.
- **No account is created at registration.** A guest books email-only. `Registration.member` is resolved by email lookup (`_member_for_email`); if there's no match it stays null. The guest manages the booking via a `self_serve_token` link (`classes:my_registration`). Logged-in registrants get their answers cached to `UserProfile` via `_cache_registration_to_profile()`.
- **Auth is passwordless.** allauth login-code flow via `plfog.adapters.AdminRedirectAccountAdapter`. `core.SiteConfiguration.RegistrationMode` is `OPEN` or `INVITE_ONLY`; the adapter gates new signups on it.
- **Three email stores.** `Member._pre_signup_email` (Airtable mirror), `MemberEmail` (staging), allauth `EmailAddress` (source of truth for linked users). `core/signals.py::_on_signup` runs `MemberEmail.objects.migrate_to_user(user)` after allauth signup.

**Gap vs. the asks:** no account is auto-created during booking, and the question *answers* never reach Mailchimp (only class metadata tags do).

---

## Feature 1 — Account creation during registration

### Goal
A guest who books a class can leave with a real, passwordless account whose booking + answers are linked to it — without a separate signup detour.

### Behavior
- On the register form, for **anonymous users only**, add a checkbox:
  - Field: `create_account` (BooleanField, `required=False`), **default checked**.
  - Label: *"Create a Past Lives account so you can manage your bookings — no password, we'll email you a sign-in code."*
  - Hidden entirely for authenticated users (they already have one).
- Account creation fires **after the registration is confirmed**, not at form submit:
  - **Free classes:** in `classes.views.register`, in the `final_price == 0` confirm branch (next to where `subscribe_registration` is already called).
  - **Paid classes:** in the Stripe webhook (`classes/webhook_handlers.py::handle_checkout_session_completed`), after payment success — so we never create accounts for abandoned checkouts.
- Create-or-link logic (a new service, e.g. `core/services/guest_account.py::ensure_account_for_registration(registration)`):
  1. If `registration.member` already has a linked `User`, **link only** (attach the registration, no new user).
  2. Else look the email up across the three stores. If it already belongs to a `User`/`Member`, **link**, never duplicate.
  3. Else create a passwordless `User` (+ `EmailAddress`, unverified) and the matching `Member` row, reusing the existing signup plumbing so `MemberEmail.migrate_to_user` semantics hold.
  4. Backfill: link this registration (and any other guest registrations sharing the email) to the new user; seed `UserProfile` answers via the existing `_cache_registration_to_profile` path.
- The account is **unverified** until their first login-code sign-in. Booking management keeps working via the existing `self_serve_token` in the meantime.

### Decisions (recommended defaults — override if needed)
- **Opt-in, default ON.** Consensual but frictionless. (Alt: always-create — rejected: no consent; or default-off — rejected: defeats the goal.)
- **Respect `INVITE_ONLY`.** When `SiteConfiguration` registration mode is `INVITE_ONLY`, **skip** auto-create (still allow guest booking). Auto-minting accounts would bypass the invite gate. (Decision to confirm: should a paid class registration count as an implicit invite? Default: no.)
- **Create after confirmation/payment**, not at form submit — avoids ghost accounts from abandoned paid checkouts and matches where `subscribe_registration` already runs.

### Data / model
- No new model. Reuse `User`, `Member`, allauth `EmailAddress`, `MemberEmail`.
- `Registration.member` gets linked where it was previously null.

### Touch points
- `classes/forms.py` — add `create_account` field; hide for authed users (mirror the existing `_user_already_opted_in` pop pattern).
- `classes/views.py::register` — call the new service in the free-confirm branch.
- `classes/webhook_handlers.py` — call it in the paid-confirm path.
- `core/services/guest_account.py` — **new** create-or-link service (the only real logic; keep it fat-service, idempotent, never raises into the request path).
- `templates/classes/public/register.html` — render the checkbox; soften the existing band copy so "we can make you one below" is clear.

### Edge cases
- Email already has an account but the user booked as a guest (not logged in) → link the registration; consider surfacing "we found your account — log in to see all your bookings." Do **not** silently merge into a foreign account without an ownership signal beyond the typed email (the email is unverified at this point — linking a booking is fine; exposing *other* bookings is not until they verify).
- Duplicate submissions / webhook redelivery → idempotent (guard on existing user/link, like `subscribed_to_mailchimp`).
- `INVITE_ONLY` mode → skip.
- Account creation failure must **not** block the booking confirmation (fail closed, log, move on).

### Testing
- Free-class register as guest with box checked → user+member created, registration linked, profile answers seeded.
- Box unchecked → no account, guest token still works (unchanged path).
- Email already linked → linked, not duplicated.
- `INVITE_ONLY` → no account created.
- Paid flow → account created only after webhook confirm, not at checkout start.
- Failure path → booking still confirmed.

---

## Feature 2 — Send answered questions to Mailchimp

### Goal
When a registrant opts into the newsletter, push the content of their answers so Mailchimp can segment/route them ("relevant lists"), not just the class metadata we send today.

### Behavior
- Extend the Mailchimp subscribe payload with the registrant's `RegistrationAnswer`s.
- **Phase 1 (recommended first):** answers as **tags**, alongside the existing tags. For each answered active question, emit a normalized tag. Format options:
  - Auto: `q-<question-slug>-<answer-slug>` (e.g. `q-experience-beginner`), or
  - Admin-controlled: add an optional `RegistrationQuestion.mailchimp_tag` prefix so staff name the tag.
  - Yes/No questions → emit the tag only on "yes" (e.g. `wants-tool-orientation`).
- Mailchimp **segments/automations** keyed on these tags are what "sign them up to the relevant mailing lists" means in a single-audience setup — no separate audiences required.

### Decision: how answers map into Mailchimp
- **Recommended: tags (Phase 1).** Works with the current single-audience `MailchimpClient.subscribe(tags=…)`; no Mailchimp restructuring. Staff drive list membership via Mailchimp segments on the tags.
- **Optional later (Phase 2):** a configured mapping from specific answers → Mailchimp **interest groups** or **separate audiences/lists**. This is a bigger lift (the client only knows one audience + FNAME/LNAME merge fields + tags today) and needs a per-answer→group config UI. Only build if tag-driven segments prove insufficient.
- Merge fields are possible but worse for segmentation than tags; skip unless a specific field is needed.

### Touch points
- `classes/services/mailchimp_subscribe.py::derive_tags` — read `registration.custom_answers` (prefetch) and append answer tags. This is the core change.
- `classes/models.py::RegistrationQuestion` — *(only if admin-controlled tags chosen)* add optional `mailchimp_tag` CharField + a `tag_for(answer)` helper. Migration required (one field, reversible).
- `core/integrations/mailchimp.py` — no change for Phase 1 (tags already supported). Phase 2 only if groups/audiences are added.
- Slug/normalization helper for turning free-text/single-choice answers into safe tag tokens (cap length, slugify, drop empties).

### Edge cases
- Long-text answers make poor tags → either skip `long_text`/`short_text` from tagging (recommend: only tag `yes_no` and `single_choice`, which are the segmentable types) or truncate+slugify. **Recommend: only `yes_no` and `single_choice` become tags;** free-text is recorded on the registration but not pushed as a tag.
- Re-subscribe / idempotency → unchanged; `subscribe_registration` already guards on `subscribed_to_mailchimp` and Mailchimp's upsert is safe.
- No answers / opt-out → unchanged (still gated on `wants_newsletter`).

### Testing
- Opt-in + single-choice answer → tag present in the `derive_tags` output and in the `client.subscribe` call.
- Yes/No "no" → no tag; "yes" → tag.
- Free-text answer → recorded, not tagged.
- Opt-out → no Mailchimp call at all (existing behavior preserved).
- `derive_tags` unit coverage for the new normalization.

---

## Sequencing & rollout

1. **PR A — Mailchimp answer tags (Feature 2, Phase 1).** Smaller, self-contained, no new model if we auto-derive tags. Immediate segmentation value.
2. **PR B — Guest account creation (Feature 1).** Larger; touches auth, the three email stores, and the Stripe webhook. Higher risk → ship second, on its own.
3. Each PR: bump `plfog/version.py` + member-friendly changelog (per repo convention), 98%+ coverage, BDD `*_spec.py` tests.
4. Phase 2 of Mailchimp (answer→group/audience mapping) only if tag-driven segments aren't enough.

## Open questions for confirmation
- Account creation: opt-in default **ON** acceptable? Skip under `INVITE_ONLY`, or treat a paid registration as an implicit invite?
- Mailchimp: auto-derived tag names, or an admin-set `mailchimp_tag` per question? Tag only `yes_no`/`single_choice` (recommended), or all types?

## Non-goals
- No changes to the CMS "Questions" admin/CRUD or to the question types themselves.
- No reintroduction of the removed first-login onboarding step.
- No new Mailchimp audiences in Phase 1.
- No password-based auth (the makerspace is passwordless by design).
