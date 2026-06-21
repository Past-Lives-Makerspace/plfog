# Plan: Instructor-authored "Welcome to my class" email (separate from the order confirmation)

> **Status: spec only.** This document is the agreed design; implementation is a follow-on.

## Context

When someone gets *into* a class, exactly one transactional email fires today — the **order
confirmation / "you're in"** email:

- `classes/emails.send_registration_confirmation(registration)` → `templates/classes/emails/confirmation.{html,txt}`.
- Triggered at two points: **free classes** synchronously in the `register` view
  (`classes/views.py`), and **paid classes** in the Stripe webhook
  (`classes/webhook_handlers.py`) once `checkout.session.completed` arrives.
- It contains: name, class title, instructor, upcoming session dates/times, location, amount paid, a
  "Manage Registration" self-serve link, and a customizable footer (`ClassSettings.confirmation_email_footer`).

**This email already exists and is correct** — it is the "order details + you're in" email the user
described. The new work is a *second, separate* email: a **welcome package** the instructor authors
("what to bring beyond materials, where to park, the door code, how to prep…"), opt-in per class.

### Email infrastructure we will build on
- Central send choke-point: `core/email.send(*, to, subject, trigger_kind, text_body, html_body=None,
  from_email=None, best_effort=False)` — renders + sends via Django mail and logs **every** attempt to
  `TransactionalEmailLog`. `best_effort=True` swallows errors (for non-critical mail).
- Sending is **synchronous inline** (no Celery/queue). Backend: console in dev, Resend in prod.
  From-address: `DEFAULT_FROM_EMAIL` (`noreply@pastlives.space`).
- Instructor authoring already happens with **plain markdown-safe textareas** (the
  `collapsible_field.html` component over `ClassOffering.description` etc.) — **no WYSIWYG** in the
  codebase. The welcome email body follows that same pattern.
- Class-edit UI: `TeachClassOfferingForm` (`classes/forms.py`) on the teach class-detail page, whose
  subtab nav lives in `templates/classes/teach/class_detail_base.html`
  (Overview / Registrations / Waitlist / Discount Codes — **no Emails tab yet**).

### Decisions to confirm before building
- **Authoring format:** freeform markdown body (recommended — consistent with every other content
  field) vs. a few structured fields (what-to-bring / parking / prep). Recommend freeform body for v1.
- **Storage:** fields on `ClassOffering` (recommended, mirrors the other per-class content fields) vs.
  a separate 1:1 `WelcomeEmail` model. Recommend fields-on-offering.
- **Who can edit:** the class's instructor + that guild's lead + admins (reuse
  `membership.permissions.can_edit_class`).

## Approach

### A. Storage — per-class fields on `ClassOffering` (`classes/models.py`)
- `welcome_email_enabled = BooleanField(default=False)` — opt-in; nothing sends unless ticked.
- `welcome_email_subject = CharField(max_length=200, blank=True)`.
- `welcome_email_body = TextField(blank=True, help_text="Markdown-safe. Sent to each new registrant.")`.
- `welcome_email_updated_at = DateTimeField(null=True, blank=True)` — stamped on save of the content.
- Property `welcome_email_ready -> bool`: `enabled and subject.strip() and body.strip()` — the single
  gate the send path checks.
- One migration (additive fields, reversible by default).

### B. Authoring UI — a new "Emails" subtab on the class-detail page
- New `teach_class_emails` view (gated by `can_edit_class`) + `TeachWelcomeEmailForm(forms.ModelForm)`
  over the four fields above; body rendered with the existing `collapsible_field.html` / textarea
  pattern. Stamp `welcome_email_updated_at` in the form's `save()`.
- `templates/classes/teach/class_detail_base.html`: add an **Emails** subtab
  (`teach_class_emails pk=offering.pk`), active when `active_subtab == 'emails'`.
- New template `templates/classes/teach/class_emails.html`: the form, an **enabled** toggle with a
  clear "off by default — tick to start sending" note, a rendered **preview**, and a **"Send a test to
  me"** button (POST → renders + sends the welcome email to the current user only).
- Admin parity: surface the same tab/form on the admin class-detail
  (`admin_class_detail` / `admin_class_email` area) so admins can edit any class's welcome email.

### C. Send trigger — piggyback on the confirmation points (`classes/emails.py`)
- New `send_class_welcome_email(registration) -> None`:
  - return early unless `registration.class_offering.welcome_email_ready`;
  - build context (registrant, offering, instructor name, upcoming sessions, self-serve link, the
    rendered instructor body);
  - `core.email.send(..., trigger_kind="classes.welcome_email", best_effort=True)` — **best-effort** so
    a welcome-email problem never fails the registration or the Stripe webhook.
- Call it **immediately after** `send_registration_confirmation(registration)` at the *same two*
  trigger points (free-class path in `register`, paid path in the webhook). Because both points fire
  only on genuine confirmation (not waitlist joins), the welcome email correctly reaches only people
  who are actually *in* the class.
- Optional dedupe: stamp `Registration.welcome_email_sent_at`; skip if already set. The webhook already
  skips already-confirmed registrations, so this is a belt-and-suspenders guard — include only if we
  see double-sends in testing.

### D. Email templates — `templates/classes/emails/welcome.{html,txt}`
- Same branded shell as `confirmation.html` (Past Lives header/footer, location), but framed as **"A
  note from {instructor} about {class}"** with the instructor's authored body as the payload, plus the
  session schedule and the manage-registration link.
- Render the markdown body the **same way the public class description is rendered** (reuse that filter
  / helper — do not introduce a new markdown path). `.txt` uses the raw body.

### E. Housekeeping
- `plfog/version.py`: member-friendly changelog bullet (instructors can send a custom welcome email to
  everyone who signs up).

## Critical files
- `classes/models.py` — welcome fields + `welcome_email_ready` (+ migration) (A)
- `classes/forms.py` — `TeachWelcomeEmailForm` (B)
- `classes/views.py` — `teach_class_emails` (+ send-test) + admin parity (B)
- `templates/classes/teach/class_detail_base.html` — Emails subtab (B)
- `templates/classes/teach/class_emails.html` — authoring + preview + test send (B)
- `classes/emails.py` — `send_class_welcome_email` (C)
- `classes/views.py` + `classes/webhook_handlers.py` — call after confirmation (C)
- `templates/classes/emails/welcome.{html,txt}` — the email (D)
- `plfog/version.py` — changelog (E)

## Reuse (don't reinvent)
- `core.email.send` (logging + best-effort) — the only send path.
- `send_registration_confirmation`'s context-building (sessions, self-serve link, footer) — mirror it.
- `collapsible_field.html` / markdown-safe textarea — authoring widget.
- `membership.permissions.can_edit_class` — who may edit a class's welcome email.
- The existing markdown-render helper used for `ClassOffering.description` — body rendering.

## Testing / verification (BDD `*_spec.py`, ≥98% gate)
- **Model:** `welcome_email_ready` truth table (off / enabled-but-empty / fully ready).
- **Form:** saves the four fields + stamps `welcome_email_updated_at`.
- **Send:** `send_class_welcome_email` no-ops when not ready; sends with the right subject/recipient/
  `trigger_kind` when ready; is best-effort (a send error doesn't propagate); logs to
  `TransactionalEmailLog`.
- **Triggers:** free-class registration sends *both* confirmation and welcome; paid webhook sends both;
  a waitlist join sends neither; a class with the welcome email disabled sends only the confirmation.
- **View:** Emails subtab renders for an editor and 403s for a non-editor; "send test to me" sends only
  to the requester.
- **Manual** on `book.pastlives.test:8000` (never localhost): author + enable a welcome email, register
  as a test user, confirm two distinct emails arrive (order confirmation, then the instructor welcome);
  disable it and confirm only the order confirmation arrives.

## Notes / scope
- Welcome email is **opt-in and off by default** ("obviously needs to be enabled").
- It never blocks or delays the registration — best-effort send, logged either way.
- Structured helper fields (parking / what-to-bring) are deferred; the freeform markdown body covers
  them. Revisit if instructors ask for guided fields.
