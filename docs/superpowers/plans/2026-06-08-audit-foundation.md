# Audit Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the site-wide audit layer — a logged email-sending wrapper, a `TransactionalEmailLog`, a `SiteActivity` event log, instrumentation at every meaningful workflow point, and a staff `/manage/activity/` page with an Activity Feed and an Email Log.

**Architecture:** Two new `core` models (`SiteActivity`, `TransactionalEmailLog`). One new email wrapper (`core/email.py::send()`) that wraps Django's `send_mail`, writes a log row on every attempt (sent or failed), and returns it. Existing transactional `send_mail` call sites are converted to the wrapper. Workflow points across `core`/`billing`/`classes`/`membership` call `SiteActivity.log()`. A single staff view renders the two tabs from these tables.

**Tech Stack:** Django 5, pytest + pytest-describe, factory-boy, django-unfold (sidebar), HTMX/Alpine hub templates. Python 3.13, ruff (line-length 120, mccabe 10), mypy, coverage `fail_under = 98`.

**This plan is feature 3 of 3 (the foundation). It ships on its own** — you get the activity page and email audit working against existing events. Plans 2 (notifications) and 3 (guild pages) depend on `core/email.py` built here.

**Conventions for every task:**
- Tests are `tests/<app>/<name>_spec.py`, functions `it_*` inside `describe_*`, module-level `pytestmark = pytest.mark.django_db`.
- Run a single test with: `set -a && source .env && set +a && pytest tests/core/email_spec.py -v` (the `.env` load supplies `DATABASE_URL`; the pre-push hook needs it too).
- Lint/type before each commit: `ruff format . && ruff check . && mypy plfog/ core/ membership/ hub/`.
- Commit messages use Conventional Commits (`feat:`, `refactor:`, `test:`).

---

## File Structure

**Create:**
- `core/email.py` — `send()` wrapper; the single choke point for transactional email + logging.
- `core/activity.py` — thin re-export / convenience (optional; `SiteActivity.log` is the real entry point). *Not created* — `SiteActivity.log()` classmethod lives on the model, mirroring fat-model rules.
- `templates/hub/admin/activity.html` — the `/manage/activity/` page (two tabs).
- `templates/hub/admin/_activity_feed.html` — Activity Feed tab partial.
- `templates/hub/admin/_activity_emails.html` — Email Log tab partial.
- `tests/core/email_spec.py`, `tests/core/site_activity_spec.py`, `tests/core/transactional_email_log_spec.py`, `tests/core/manage_activity_spec.py`.

**Modify:**
- `core/models.py` — add `SiteActivity`, `TransactionalEmailLog`.
- `core/views.py` — add `site_activity` view.
- `core/urls.py` — add `manage/activity/` path.
- `plfog/settings.py` — add sidebar entry (`UNFOLD["SIDEBAR"]["navigation"]`).
- `billing/notifications.py`, `core/models.py` (Invite), `core/forms.py`, `classes/emails.py` — convert `send_mail` → `core.email.send`.
- `billing/notifications.py`, `billing/webhook_handlers.py`, `billing/models.py` (`Tab.add_entry`), `membership/signals.py` (or new), `membership/models.py` (`FundingSnapshot.take`), `classes/` workflow points, `plfog/` allauth signals — add `SiteActivity.log()` calls.

---

## Task 1: `TransactionalEmailLog` model

**Files:**
- Modify: `core/models.py` (add model after `UserProfile`, end of file)
- Test: `tests/core/transactional_email_log_spec.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/transactional_email_log_spec.py
"""BDD-style tests for core.models.TransactionalEmailLog."""

import pytest

from core.models import TransactionalEmailLog

pytestmark = pytest.mark.django_db


def describe_TransactionalEmailLog():
    def it_records_a_sent_email():
        log = TransactionalEmailLog.objects.create(
            to_email="member@example.com",
            subject="Receipt for $20",
            trigger_kind="billing.receipt",
            status=TransactionalEmailLog.Status.SENT,
        )
        assert log.status == "sent"
        assert log.error_message == ""

    def it_records_a_failed_email_with_error():
        log = TransactionalEmailLog.objects.create(
            to_email="member@example.com",
            subject="Receipt for $20",
            trigger_kind="billing.receipt",
            status=TransactionalEmailLog.Status.FAILED,
            error_message="SMTP timeout",
        )
        assert log.status == "failed"
        assert "timeout" in log.error_message

    def it_has_a_readable_str():
        log = TransactionalEmailLog.objects.create(
            to_email="m@example.com", subject="Hi", trigger_kind="core.invite",
            status=TransactionalEmailLog.Status.SENT,
        )
        assert "m@example.com" in str(log)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `set -a && source .env && set +a && pytest tests/core/transactional_email_log_spec.py -v`
Expected: FAIL with `ImportError: cannot import name 'TransactionalEmailLog'`.

- [ ] **Step 3: Add the model**

Add to the end of `core/models.py`:

```python
class TransactionalEmailLog(models.Model):
    """One row per transactional email attempted — sent or failed.

    Written by ``core.email.send()`` on every attempt so the admin Email Log
    tab can audit whether confirmation/receipt emails are actually going out.
    """

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    to_email = models.CharField(max_length=254, help_text="Recipient(s); comma-joined when multiple.")
    subject = models.CharField(max_length=500, help_text="Email subject line.")
    trigger_kind = models.CharField(
        max_length=100, help_text="Which workflow sent it, e.g. 'billing.receipt'."
    )
    status = models.CharField(max_length=10, choices=Status.choices, help_text="Send outcome.")
    error_message = models.TextField(blank=True, default="", help_text="Exception text when status=failed.")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_status_display()} → {self.to_email} ({self.trigger_kind})"
```

- [ ] **Step 4: Make the migration and run the test**

Run: `set -a && source .env && set +a && python manage.py makemigrations core && pytest tests/core/transactional_email_log_spec.py -v`
Expected: migration `core/migrations/00XX_transactionalemaillog.py` created; tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/models.py core/migrations/ tests/core/transactional_email_log_spec.py
git commit -m "feat(core): add TransactionalEmailLog model"
```

---

## Task 2: `core/email.py` send wrapper

**Files:**
- Create: `core/email.py`
- Test: `tests/core/email_spec.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/email_spec.py
"""BDD-style tests for core.email.send — the logged email wrapper."""

from unittest.mock import patch

import pytest

from core import email as core_email
from core.models import TransactionalEmailLog

pytestmark = pytest.mark.django_db


def describe_send():
    def it_sends_and_logs_a_sent_row():
        with patch("core.email.send_mail") as mock_send:
            log = core_email.send(
                to="member@example.com",
                subject="Receipt",
                trigger_kind="billing.receipt",
                text_body="body",
                html_body="<p>body</p>",
            )
        mock_send.assert_called_once()
        assert log.status == TransactionalEmailLog.Status.SENT
        assert log.to_email == "member@example.com"
        assert TransactionalEmailLog.objects.count() == 1

    def it_joins_multiple_recipients_into_one_row():
        with patch("core.email.send_mail"):
            log = core_email.send(
                to=["a@example.com", "b@example.com"],
                subject="Hi", trigger_kind="x", text_body="b",
            )
        assert log.to_email == "a@example.com, b@example.com"

    def describe_when_send_mail_raises():
        def it_logs_failed_and_reraises_by_default():
            with patch("core.email.send_mail", side_effect=RuntimeError("SMTP down")):
                with pytest.raises(RuntimeError):
                    core_email.send(
                        to="m@example.com", subject="Hi", trigger_kind="x", text_body="b",
                    )
            log = TransactionalEmailLog.objects.get()
            assert log.status == TransactionalEmailLog.Status.FAILED
            assert "SMTP down" in log.error_message

        def it_swallows_when_best_effort():
            with patch("core.email.send_mail", side_effect=RuntimeError("SMTP down")):
                log = core_email.send(
                    to="m@example.com", subject="Hi", trigger_kind="x",
                    text_body="b", best_effort=True,
                )
            assert log.status == TransactionalEmailLog.Status.FAILED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `set -a && source .env && set +a && pytest tests/core/email_spec.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.email'`.

- [ ] **Step 3: Implement the wrapper**

```python
# core/email.py
"""Single choke point for transactional email — sends and audits every attempt.

Every transactional email in the app routes through ``send()`` so a
``TransactionalEmailLog`` row is written whether the send succeeds or fails.
The returned row can be attached to a ``SiteActivity`` via its ``email_log`` FK.
"""

from __future__ import annotations

from django.conf import settings
from django.core.mail import send_mail

from core.models import TransactionalEmailLog


def send(
    *,
    to: str | list[str],
    subject: str,
    trigger_kind: str,
    text_body: str,
    html_body: str | None = None,
    from_email: str | None = None,
    best_effort: bool = False,
) -> TransactionalEmailLog:
    """Send a transactional email and log the attempt.

    Args:
        to: One recipient or a list of them.
        subject: Subject line.
        trigger_kind: Workflow identifier, e.g. "billing.receipt".
        text_body: Plain-text body.
        html_body: Optional HTML alternative.
        from_email: Overrides DEFAULT_FROM_EMAIL when given.
        best_effort: When True, swallow send failures (still logged) instead of
            re-raising. Use for non-critical sends (e.g. notification emails).

    Returns:
        The TransactionalEmailLog row written for this attempt.

    Raises:
        Exception: Re-raises the underlying send error unless best_effort=True.
    """
    recipients = [to] if isinstance(to, str) else list(to)
    joined = ", ".join(recipients)
    try:
        send_mail(
            subject=subject,
            message=text_body,
            from_email=from_email or settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            html_message=html_body,
        )
    except Exception as exc:  # noqa: BLE001 — we log then re-raise unless best_effort
        log = TransactionalEmailLog.objects.create(
            to_email=joined,
            subject=subject,
            trigger_kind=trigger_kind,
            status=TransactionalEmailLog.Status.FAILED,
            error_message=str(exc),
        )
        if not best_effort:
            raise
        return log
    return TransactionalEmailLog.objects.create(
        to_email=joined,
        subject=subject,
        trigger_kind=trigger_kind,
        status=TransactionalEmailLog.Status.SENT,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `set -a && source .env && set +a && pytest tests/core/email_spec.py -v`
Expected: PASS (all 5 tests).

- [ ] **Step 5: Lint, type, commit**

```bash
ruff format . && ruff check core/email.py && mypy core/
git add core/email.py tests/core/email_spec.py
git commit -m "feat(core): add logged transactional email wrapper"
```

---

## Task 3: `SiteActivity` model + `Kind` + `log()`

**Files:**
- Modify: `core/models.py` (add after `TransactionalEmailLog`; needs `GenericForeignKey` imports)
- Test: `tests/core/site_activity_spec.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/site_activity_spec.py
"""BDD-style tests for core.models.SiteActivity."""

import pytest
from django.contrib.auth.models import User

from core.models import SiteActivity, TransactionalEmailLog

pytestmark = pytest.mark.django_db


def describe_SiteActivity():
    def describe_log():
        def it_creates_a_row_with_actor_and_kind():
            user = User.objects.create_user(username="u1", email="u1@example.com")
            activity = SiteActivity.log(SiteActivity.Kind.LOGIN, actor=user)
            assert activity.kind == "login"
            assert activity.actor == user

        def it_accepts_a_null_actor_for_system_events():
            activity = SiteActivity.log(SiteActivity.Kind.FUNDING_SNAPSHOT_TAKEN, actor=None)
            assert activity.actor is None

        def it_ignores_an_unsaved_actor():
            ghost = User(username="ghost")  # no pk
            activity = SiteActivity.log(SiteActivity.Kind.LOGIN, actor=ghost)
            assert activity.actor is None

        def it_attaches_a_generic_target():
            user = User.objects.create_user(username="u2", email="u2@example.com")
            target_log = TransactionalEmailLog.objects.create(
                to_email="x@example.com", subject="s", trigger_kind="t",
                status=TransactionalEmailLog.Status.SENT,
            )
            activity = SiteActivity.log(
                SiteActivity.Kind.TAB_CHARGED, actor=user, target=target_log,
            )
            assert activity.target == target_log

        def it_links_an_email_log():
            email_log = TransactionalEmailLog.objects.create(
                to_email="x@example.com", subject="s", trigger_kind="billing.receipt",
                status=TransactionalEmailLog.Status.SENT,
            )
            activity = SiteActivity.log(SiteActivity.Kind.TAB_CHARGED, email_log=email_log)
            assert activity.email_log == email_log

        def it_defaults_payload_to_empty_dict():
            activity = SiteActivity.log(SiteActivity.Kind.LOGOUT)
            assert activity.payload == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `set -a && source .env && set +a && pytest tests/core/site_activity_spec.py -v`
Expected: FAIL with `ImportError: cannot import name 'SiteActivity'`.

- [ ] **Step 3: Add imports and the model**

At the top of `core/models.py`, ensure these imports exist (add what's missing):

```python
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
```

Add the model after `TransactionalEmailLog`:

```python
class SiteActivity(models.Model):
    """Append-only event log for every meaningful site-wide happening.

    Written via ``SiteActivity.log()`` from each workflow point (auth,
    profile, voting, billing, classes, membership) so the admin
    ``/manage/activity/`` feed shows one chronological stream. ``payload``
    carries free-form per-kind detail; ``email_log`` links to the email that
    this event triggered, if any.
    """

    class Kind(models.TextChoices):
        LOGIN = "login", "Logged in"
        LOGOUT = "logout", "Logged out"
        PROFILE_UPDATED = "profile_updated", "Updated profile"
        VOTE_SUBMITTED = "vote_submitted", "Submitted vote"
        VOTE_CHANGED = "vote_changed", "Changed vote"
        TAB_CHARGED = "tab_charged", "Tab charged"
        TAB_CHARGE_FAILED = "tab_charge_failed", "Tab charge failed"
        TAB_ENTRY_ADDED = "tab_entry_added", "Tab entry added"
        CLASS_REGISTERED = "class_registered", "Registered for class"
        CLASS_REGISTRATION_CANCELLED = "class_registration_cancelled", "Cancelled registration"
        CLASS_WAITLIST_JOINED = "class_waitlist_joined", "Joined waitlist"
        CLASS_PUBLISHED = "class_published", "Class published"
        CLASS_SUBMITTED = "class_submitted", "Class submitted"
        CLASS_APPROVED = "class_approved", "Class approved"
        CLASS_CANCELLED = "class_cancelled", "Class cancelled"
        REFUND_ISSUED = "refund_issued", "Refund issued"
        FUNDING_SNAPSHOT_TAKEN = "funding_snapshot_taken", "Funding snapshot taken"
        MEMBER_INVITED = "member_invited", "Member invited"
        INVITE_ACCEPTED = "invite_accepted", "Invite accepted"
        MEMBER_SIGNUP = "member_signup", "Member signed up"
        GUILD_ANNOUNCEMENT = "guild_announcement", "Guild announcement"
        LEASE_ACTIVATED = "lease_activated", "Lease activated"
        SITE_ANNOUNCEMENT = "site_announcement", "Site announcement"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="User who triggered this. Null for system events.",
    )
    kind = models.CharField(max_length=50, choices=Kind.choices, help_text="What happened.")
    target_ct = models.ForeignKey(
        ContentType, null=True, blank=True, on_delete=models.SET_NULL,
        help_text="Content type of the related object, when applicable.",
    )
    target_id = models.PositiveIntegerField(null=True, blank=True, help_text="PK of the related object.")
    target = GenericForeignKey("target_ct", "target_id")
    payload = models.JSONField(default=dict, blank=True, help_text="Free-form per-kind detail.")
    email_log = models.ForeignKey(
        "core.TransactionalEmailLog",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="activity",
        help_text="The email this event sent, if any. Source of the ✉ badge.",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["kind", "-created_at"]),
            models.Index(fields=["actor", "-created_at"]),
        ]
        verbose_name_plural = "Site activity"

    def __str__(self) -> str:
        return f"{self.get_kind_display()} @ {self.created_at:%Y-%m-%d %H:%M}"

    @classmethod
    def log(
        cls,
        kind: str,
        *,
        actor: Any | None = None,
        target: models.Model | None = None,
        email_log: Any | None = None,
        payload: dict[str, Any] | None = None,
    ) -> "SiteActivity":
        """Append one activity row. Safe to call from views, signals, or model methods."""
        activity = cls(
            kind=kind,
            actor=actor if (actor is not None and getattr(actor, "pk", None)) else None,
            email_log=email_log,
            payload=payload or {},
        )
        if target is not None:
            activity.target = target
        activity.save()
        return activity
```

- [ ] **Step 4: Make the migration and run the test**

Run: `set -a && source .env && set +a && python manage.py makemigrations core && pytest tests/core/site_activity_spec.py -v`
Expected: migration created; all 6 tests PASS.

- [ ] **Step 5: Lint, type, commit**

```bash
ruff format . && ruff check core/models.py && mypy core/
git add core/models.py core/migrations/ tests/core/site_activity_spec.py
git commit -m "feat(core): add SiteActivity event log model"
```

---

## Task 4: Convert `billing/notifications.py` to the wrapper

**Files:**
- Modify: `billing/notifications.py:18-44` (`send_receipt`), `billing/notifications.py:47-66` (`notify_admin_charge_failed`)
- Test: existing `tests/billing/` notification specs (update mocks)

- [ ] **Step 1: Find and read the existing test**

Run: `grep -rl "send_receipt\|notify_admin_charge_failed" tests/billing/`
Read the matched spec(s). They currently patch `billing.notifications.send_mail`. They must now patch `billing.notifications.core_email_send` (or assert on `TransactionalEmailLog`).

- [ ] **Step 2: Update `send_receipt`**

Replace the `send_mail(...)` block (lines ~37-43) with:

```python
    from core import email as core_email

    core_email.send(
        to=member.primary_email,
        subject=f"Past Lives Makerspace — Receipt for ${charge.amount}",
        trigger_kind="billing.receipt",
        text_body=text_body,
        html_body=html_body,
    )
```

Remove the now-unused `from django.core.mail import send_mail` import **only if** no other function in the file uses it (it won't, after the next step).

- [ ] **Step 3: Update `notify_admin_charge_failed`**

Replace its `send_mail(...)` (lines ~61-66) with:

```python
    from core import email as core_email

    core_email.send(
        to=admin_emails,
        subject=f"[Billing] Failed charge for {member.display_name} — ${charge.amount}",
        trigger_kind="billing.charge_failed_admin",
        text_body=text_body,
    )
```

Note `admin_emails` is already a list; the wrapper handles lists.

- [ ] **Step 4: Update the tests**

Change the patch target in the billing notification spec(s) from `billing.notifications.send_mail` to `core.email.send_mail`, OR rewrite assertions to check `TransactionalEmailLog.objects.filter(trigger_kind="billing.receipt").exists()`. Prefer the latter — it tests the real outcome.

- [ ] **Step 5: Run tests**

Run: `set -a && source .env && set +a && pytest tests/billing/ -k "receipt or charge_failed or notification" -v`
Expected: PASS.

- [ ] **Step 6: Lint, type, commit**

```bash
ruff format . && ruff check billing/notifications.py && mypy billing/
git add billing/notifications.py tests/billing/
git commit -m "refactor(billing): route receipt + failure emails through core.email.send"
```

---

## Task 5: Convert `core` email sites (Invite + find-account)

**Files:**
- Modify: `core/models.py:243-264` (`Invite.send_invite_email`), `core/forms.py:49-76` (`FindAccountForm.send_login_email`)
- Test: `tests/core/models_spec.py`, `tests/core/find_account_spec.py`

- [ ] **Step 1: Update `Invite.send_invite_email`**

Replace its `send_mail(...)` call with (keep the `signup_url` build above it):

```python
        from core import email as core_email

        core_email.send(
            to=self.email,
            subject="You're invited to Past Lives Makerspace",
            trigger_kind="core.invite",
            text_body=(
                f"You've been invited to join Past Lives Makerspace!\n\n"
                f"Click the link below to create your account:\n\n"
                f"{signup_url}\n\n"
                f"If you didn't expect this invite, you can ignore this email."
            ),
        )
```

- [ ] **Step 2: Update `FindAccountForm.send_login_email`**

Replace its `send_mail(...)` call with:

```python
        from core import email as core_email

        core_email.send(
            to=member.primary_email,
            subject="Your Past Lives Account",
            trigger_kind="core.find_account",
            text_body=(
                f"Hi {member.preferred_name or member.full_legal_name},\n\n"
                f"Your account email is: {member.primary_email}\n\n"
                f"You can log in here:\n{login_url}\n\n"
                f"If you didn't request this, you can safely ignore this email."
            ),
        )
```

Remove the top-level `from django.core.mail import send_mail` from `core/models.py` and `core/forms.py` **only if** unused elsewhere in each file (grep first: `grep -n send_mail core/models.py core/forms.py`).

- [ ] **Step 3: Update tests**

In `tests/core/models_spec.py` and `tests/core/find_account_spec.py`, change any `patch("core.models.send_mail")` / `patch("core.forms.send_mail")` to `patch("core.email.send_mail")`, or assert on `TransactionalEmailLog` rows (`trigger_kind="core.invite"` / `"core.find_account"`).

- [ ] **Step 4: Run tests**

Run: `set -a && source .env && set +a && pytest tests/core/models_spec.py tests/core/find_account_spec.py -v`
Expected: PASS.

- [ ] **Step 5: Lint, type, commit**

```bash
ruff format . && ruff check core/ && mypy core/
git add core/models.py core/forms.py tests/core/
git commit -m "refactor(core): route invite + find-account emails through core.email.send"
```

---

## Task 6: Convert `classes/emails.py` (9 call sites)

**Files:**
- Modify: `classes/emails.py` — 9 `send_mail()` calls at lines 60, 84, 107, 153, 172, 226, 253, 286, 310
- Test: existing `tests/classes/` email specs

This is a mechanical, repeated conversion. **Recipe** for each call site:

```
send_mail(
    subject=SUBJECT,
    message=TEXT_BODY,
    from_email=...,            # delete
    recipient_list=[RECIPIENT],
    html_message=HTML,         # may be absent
)
```
becomes:
```
from core import email as core_email
core_email.send(
    to=RECIPIENT,              # str or the existing list
    subject=SUBJECT,
    trigger_kind=TRIGGER,      # from the table below
    text_body=TEXT_BODY,
    html_body=HTML,            # omit the kwarg if there was no html_message
)
```

- [ ] **Step 1: Apply the recipe at each site**, using these `trigger_kind` values:

| Line | Function | trigger_kind |
|------|----------|--------------|
| 60 | `send_registration_confirmation` | `classes.registration_confirmation` |
| 84 | `send_instructor_registration_notification` | `classes.instructor_registration` |
| 107 | `send_admin_registration_notification` | `classes.admin_registration` |
| 153 | `send_class_review_requests` (reviewer) | `classes.review_request` |
| 172 | `send_class_review_requests` (instructor) | `classes.review_request_instructor` |
| 226 | `send_class_review_decision` | `classes.review_decision` |
| 253 | `send_waitlist_joined_confirmation` | `classes.waitlist_joined` |
| 286 | `send_waitlist_spot_opened` | `classes.waitlist_spot_opened` |
| 310 | `send_reminder_email` | `classes.reminder` |

Add a single module-level `from core import email as core_email` import at the top (replace the `from django.core.mail import send_mail` import). For the two sends inside the `send_class_review_requests` loop (lines 153, 172), keep them inside the loop unchanged in structure.

- [ ] **Step 2: Update tests**

Run: `grep -rln "classes.emails.send_mail\|emails.send_mail\|patch.*send_mail" tests/classes/`
For each matched spec, switch the patch target to `core.email.send_mail`, or assert on `TransactionalEmailLog.objects.filter(trigger_kind=...)`.

- [ ] **Step 3: Run the classes email tests**

Run: `set -a && source .env && set +a && pytest tests/classes/ -k "email or confirmation or reminder or waitlist or review" -v`
Expected: PASS. If a test asserted exact `send_mail` kwargs, rewrite it to assert the `TransactionalEmailLog` row + that the recipient matches.

- [ ] **Step 4: Lint, type, commit**

```bash
ruff format . && ruff check classes/emails.py && mypy plfog/ core/ membership/ hub/
git add classes/emails.py tests/classes/
git commit -m "refactor(classes): route all transactional emails through core.email.send"
```

> **Out of scope here:** `classes/forms.py:822,889` (bulk `EmailMessage` blasts) and `hub/forms.py:146` (beta feedback) and `plfog/adapters.py:142` (allauth) — left untouched per the spec.

---

## Task 7: Instrument auth events (login, logout, signup)

**Files:**
- Create: `core/signals.py`
- Modify: `core/apps.py` (connect signals in `ready()`)
- Test: `tests/core/signals_spec.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/signals_spec.py
"""SiteActivity is written on auth events."""

import pytest
from django.contrib.auth.models import User
from django.test import RequestFactory

from core.models import SiteActivity

pytestmark = pytest.mark.django_db


def describe_auth_activity():
    def it_logs_login(client):
        user = User.objects.create_user(username="u", email="u@example.com", password="pw12345!")
        from allauth.account.signals import user_logged_in
        request = RequestFactory().get("/")
        user_logged_in.send(sender=User, request=request, user=user)
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.LOGIN, actor=user).exists()

    def it_logs_signup():
        user = User.objects.create_user(username="s", email="s@example.com")
        from allauth.account.signals import user_signed_up
        request = RequestFactory().get("/")
        user_signed_up.send(sender=User, request=request, user=user)
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.MEMBER_SIGNUP, actor=user).exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `set -a && source .env && set +a && pytest tests/core/signals_spec.py -v`
Expected: FAIL (no rows created — signals not wired).

- [ ] **Step 3: Create the signal handlers**

```python
# core/signals.py
"""Auth → SiteActivity instrumentation."""

from __future__ import annotations

from typing import Any

from allauth.account.signals import user_logged_in, user_logged_out, user_signed_up
from django.dispatch import receiver

from core.models import SiteActivity


@receiver(user_logged_in)
def _on_login(sender: Any, request: Any, user: Any, **kwargs: Any) -> None:
    SiteActivity.log(SiteActivity.Kind.LOGIN, actor=user)


@receiver(user_logged_out)
def _on_logout(sender: Any, request: Any, user: Any, **kwargs: Any) -> None:
    SiteActivity.log(SiteActivity.Kind.LOGOUT, actor=user)


@receiver(user_signed_up)
def _on_signup(sender: Any, request: Any, user: Any, **kwargs: Any) -> None:
    SiteActivity.log(SiteActivity.Kind.MEMBER_SIGNUP, actor=user)
```

- [ ] **Step 4: Connect signals in `core/apps.py`**

In `CoreConfig.ready()` (add the method if absent):

```python
    def ready(self) -> None:
        from core import signals  # noqa: F401  — registers receivers
```

- [ ] **Step 5: Run test to verify it passes**

Run: `set -a && source .env && set +a && pytest tests/core/signals_spec.py -v`
Expected: PASS.

- [ ] **Step 6: Lint, type, commit**

```bash
ruff format . && ruff check core/ && mypy core/
git add core/signals.py core/apps.py tests/core/signals_spec.py
git commit -m "feat(core): log login/logout/signup to SiteActivity"
```

---

## Task 8: Instrument profile updates (in the settings view)

**Files:**
- Modify: `hub/views.py` `user_settings` view, profile branch (after `profile_form.save()`)
- Test: `tests/hub/` settings spec (find with `grep -rl "user_settings\|form_id.*profile" tests/hub/`)

- [ ] **Step 1: Add the log call after a successful profile save**

In the `if request.method == "POST" and request.POST.get("form_id") == "profile":` branch, immediately after `profile_form.save()` succeeds:

```python
            from core.models import SiteActivity

            SiteActivity.log(SiteActivity.Kind.PROFILE_UPDATED, actor=request.user, target=member)
```

- [ ] **Step 2: Write the test**

Add to the hub settings spec:

```python
def it_logs_a_profile_update(client, db):
    # arrange: a logged-in member (reuse the spec's existing member/login fixture pattern)
    # act: POST the profile form with form_id=profile and valid data
    # assert:
    from core.models import SiteActivity
    assert SiteActivity.objects.filter(kind=SiteActivity.Kind.PROFILE_UPDATED).exists()
```

Fill the arrange/act using the existing fixtures in that spec file (match how other `it_` tests there build and authenticate a member, then POST to `reverse("hub_user_settings")`).

- [ ] **Step 3: Run the test**

Run: `set -a && source .env && set +a && pytest tests/hub/ -k "settings and profile" -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
ruff format . && ruff check hub/views.py && mypy hub/
git add hub/views.py tests/hub/
git commit -m "feat(hub): log profile updates to SiteActivity"
```

---

## Task 9: Instrument voting (VotePreference post_save)

**Files:**
- Modify: `membership/signals.py` (add receiver) — confirm the file exists; if not, create it and wire in `membership/apps.py::ready()`
- Test: `tests/membership/` signals spec

- [ ] **Step 1: Write the failing test**

```python
# in tests/membership/ (new or existing signals spec)
import pytest
from core.models import SiteActivity
from tests.membership.factories import MemberFactory, GuildFactory
from membership.models import VotePreference

pytestmark = pytest.mark.django_db

def describe_vote_activity():
    def it_logs_vote_submitted_on_create():
        member = MemberFactory()
        g1, g2, g3 = GuildFactory(), GuildFactory(), GuildFactory()
        VotePreference.objects.create(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.VOTE_SUBMITTED).exists()

    def it_logs_vote_changed_on_update():
        member = MemberFactory()
        g1, g2, g3 = GuildFactory(), GuildFactory(), GuildFactory()
        vp = VotePreference.objects.create(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        vp.guild_1st = g3
        vp.save()
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.VOTE_CHANGED).exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `set -a && source .env && set +a && pytest tests/membership/ -k vote_activity -v`
Expected: FAIL.

- [ ] **Step 3: Add the receiver**

In `membership/signals.py`:

```python
from django.db.models.signals import post_save
from django.dispatch import receiver

from membership.models import VotePreference


@receiver(post_save, sender=VotePreference)
def _log_vote_activity(sender, instance, created, **kwargs):
    from core.models import SiteActivity

    kind = SiteActivity.Kind.VOTE_SUBMITTED if created else SiteActivity.Kind.VOTE_CHANGED
    actor = instance.member.user if instance.member_id else None
    SiteActivity.log(kind, actor=actor, target=instance.member)
```

Confirm `membership/apps.py::ready()` imports `membership.signals` (it likely already does for existing signals — grep `grep -n "signals" membership/apps.py`). If not, add `from membership import signals  # noqa: F401`.

- [ ] **Step 4: Run to verify it passes**

Run: `set -a && source .env && set +a && pytest tests/membership/ -k vote_activity -v`
Expected: PASS.

> **Airtable caveat:** `VotePreference.save()` also triggers an outbound Airtable sync. The autouse `_disable_airtable_sync` fixture neutralizes it in tests. No extra mocking needed.

- [ ] **Step 5: Commit**

```bash
ruff format . && ruff check membership/ && mypy membership/
git add membership/signals.py membership/apps.py tests/membership/
git commit -m "feat(membership): log vote submit/change to SiteActivity"
```

---

## Task 10: Instrument billing (charged, failed, entry added) + funding snapshot

**Files:**
- Modify: `billing/notifications.py` (`send_receipt`, `notify_admin_charge_failed`), `billing/models.py` (`Tab.add_entry`), `membership/models.py` (`FundingSnapshot.take`)
- Test: targeted specs in `tests/billing/`, `tests/membership/`

- [ ] **Step 1: Log on successful charge**

In `send_receipt`, capture the email log and attach it to the activity. Replace the Task 4 `core_email.send(...)` call so it assigns the return value:

```python
    email_log = core_email.send(
        to=member.primary_email,
        subject=f"Past Lives Makerspace — Receipt for ${charge.amount}",
        trigger_kind="billing.receipt",
        text_body=text_body,
        html_body=html_body,
    )
    from core.models import SiteActivity

    SiteActivity.log(
        SiteActivity.Kind.TAB_CHARGED, actor=member.user, target=charge, email_log=email_log,
    )
```

- [ ] **Step 2: Log on failed charge**

In `notify_admin_charge_failed`, after the `core_email.send(...)`:

```python
    from core.models import SiteActivity

    SiteActivity.log(SiteActivity.Kind.TAB_CHARGE_FAILED, actor=member.user, target=charge)
```

- [ ] **Step 3: Log on tab entry added**

Read `Tab.add_entry` in `billing/models.py` (`grep -n "def add_entry" billing/models.py`). At the end, after the entry is created and before returning:

```python
        from core.models import SiteActivity

        SiteActivity.log(SiteActivity.Kind.TAB_ENTRY_ADDED, actor=actor_user, target=entry)
```

If `add_entry` has no actor parameter, add `actor_user=None` kwarg and pass `request.user` from the admin add-entry view; default `None` keeps existing callers working. (Check callers: `grep -rn "\.add_entry(" billing/ hub/`.)

- [ ] **Step 4: Log on funding snapshot**

Read `FundingSnapshot.take` (`grep -n "def take" membership/models.py`). After the snapshot row is saved:

```python
        from core.models import SiteActivity

        SiteActivity.log(SiteActivity.Kind.FUNDING_SNAPSHOT_TAKEN, target=snapshot)
```

- [ ] **Step 5: Write/extend tests**

Add `it_*` cases asserting the corresponding `SiteActivity` rows exist after: a successful charge (with `email_log` set), a failed charge, an `add_entry` call, and `FundingSnapshot.take()`. Reuse existing factories (`tests/billing/factories.py`, `tests/membership/factories.py`).

- [ ] **Step 6: Run tests**

Run: `set -a && source .env && set +a && pytest tests/billing/ tests/membership/ -k "charge or add_entry or snapshot or activity" -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
ruff format . && ruff check billing/ membership/ && mypy billing/ membership/
git add billing/ membership/ tests/billing/ tests/membership/
git commit -m "feat(billing,membership): log charges, entries, snapshots to SiteActivity"
```

---

## Task 11: Instrument class + membership events

**Files:**
- Modify: `classes/activity.py` (mirror into SiteActivity), `core/models.py` (`Invite.create_and_send`, `Invite.mark_accepted`)
- Test: `tests/classes/`, `tests/core/models_spec.py`

- [ ] **Step 1: Mirror CMS class events into SiteActivity**

`classes/activity.py::log()` already records class lifecycle to `CmsActivity`. Extend it to *also* write the subset the site feed cares about. At the end of `log()`, before `return`:

```python
    _mirror_to_site_activity(kind, class_offering, registration, actor, payload)
    return CmsActivity.objects.create(...)  # existing
```

Add a module function:

```python
_SITE_KIND_MAP = {
    "class_published": "class_published",
    "class_submitted": "class_submitted",
    "class_approved": "class_approved",
    "class_archived": "class_cancelled",        # archived ≈ cancelled for the site feed
    "registration_created": "class_registered",
    "registration_cancelled": "class_registration_cancelled",
    "registration_refunded": "refund_issued",
    "waitlist_joined": "class_waitlist_joined",
}


def _mirror_to_site_activity(kind, class_offering, registration, actor, payload):
    site_kind = _SITE_KIND_MAP.get(kind)
    if site_kind is None:
        return
    from core.models import SiteActivity

    target = registration or class_offering
    SiteActivity.log(site_kind, actor=actor, target=target, payload=payload or {})
```

CmsActivity stays the source of the class-specific detail view; SiteActivity gets the cross-site subset. Events not in the map (e.g. discount codes) are intentionally omitted from the site feed.

- [ ] **Step 2: Log invite sent + accepted**

Read `Invite.create_and_send` and `Invite.mark_accepted` (`grep -n "def create_and_send\|def mark_accepted" core/models.py`). In `create_and_send`, after the invite is sent:

```python
        SiteActivity.log(SiteActivity.Kind.MEMBER_INVITED, actor=invited_by, payload={"email": email})
```

In `mark_accepted`, after marking:

```python
        SiteActivity.log(SiteActivity.Kind.INVITE_ACCEPTED, actor=self.member.user if self.member_id else None, target=self.member)
```

Add `from core.models import SiteActivity` locally inside each method (avoid import cycles at module load).

- [ ] **Step 3: Write tests**

Add `it_*` cases: publishing a class writes a `class_published` SiteActivity; creating a registration writes `class_registered`; `Invite.create_and_send` writes `member_invited`; `mark_accepted` writes `invite_accepted`. Use existing factories.

- [ ] **Step 4: Run tests**

Run: `set -a && source .env && set +a && pytest tests/classes/ tests/core/ -k "activity or invite or publish or registration" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
ruff format . && ruff check classes/ core/ && mypy plfog/ core/ membership/ hub/
git add classes/activity.py core/models.py tests/
git commit -m "feat: mirror class + invite events into SiteActivity"
```

---

## Task 12: `/manage/activity/` view + URL + sidebar

**Files:**
- Modify: `core/views.py` (add `site_activity`), `core/urls.py` (add path), `plfog/settings.py` (sidebar)
- Test: `tests/core/manage_activity_spec.py`

- [ ] **Step 1: Write the failing access test**

```python
# tests/core/manage_activity_spec.py
"""Access + rendering for the /manage/activity/ staff page."""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.models import SiteActivity

pytestmark = pytest.mark.django_db


def describe_manage_activity():
    def it_redirects_anonymous_users(client):
        resp = client.get(reverse("manage_activity"))
        assert resp.status_code in (302, 301)

    def it_forbids_non_staff(client):
        User.objects.create_user(username="m", email="m@example.com", password="pw12345!")
        client.login(username="m", password="pw12345!")
        resp = client.get(reverse("manage_activity"))
        assert resp.status_code in (302, 403)

    def it_renders_for_staff(client):
        staff = User.objects.create_user(
            username="a", email="a@example.com", password="pw12345!", is_staff=True,
        )
        client.login(username="a", password="pw12345!")
        SiteActivity.log(SiteActivity.Kind.LOGIN, actor=staff)
        resp = client.get(reverse("manage_activity"))
        assert resp.status_code == 200
        assert b"Site Activity" in resp.content

    def it_filters_the_feed_by_kind(client):
        staff = User.objects.create_user(
            username="a2", email="a2@example.com", password="pw12345!", is_staff=True,
        )
        client.login(username="a2", password="pw12345!")
        SiteActivity.log(SiteActivity.Kind.LOGIN, actor=staff)
        SiteActivity.log(SiteActivity.Kind.LOGOUT, actor=staff)
        resp = client.get(reverse("manage_activity"), {"kind": "login"})
        assert resp.status_code == 200
```

- [ ] **Step 2: Run to verify it fails**

Run: `set -a && source .env && set +a && pytest tests/core/manage_activity_spec.py -v`
Expected: FAIL with `NoReverseMatch: 'manage_activity'`.

- [ ] **Step 3: Add the view**

In `core/views.py`, add the import at the top:

```python
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from .models import SiteActivity, TransactionalEmailLog
```

Add the view:

```python
@staff_member_required
def site_activity(request: HttpRequest) -> HttpResponse:
    """Staff dashboard: a chronological site-wide event feed and an email audit log."""
    tab = request.GET.get("tab", "feed")

    activities = SiteActivity.objects.select_related("actor", "target_ct", "email_log").all()
    kind = request.GET.get("kind", "").strip()
    if kind:
        activities = activities.filter(kind=kind)
    actor_q = request.GET.get("actor", "").strip()
    if actor_q:
        activities = activities.filter(actor__email__icontains=actor_q)
    feed_page = Paginator(activities, 50).get_page(request.GET.get("page"))

    emails = TransactionalEmailLog.objects.all()
    status = request.GET.get("status", "").strip()
    if status:
        emails = emails.filter(status=status)
    email_page = Paginator(emails, 50).get_page(request.GET.get("epage"))

    return render(
        request,
        "hub/admin/activity.html",
        {
            "active_tab": tab,
            "feed_page": feed_page,
            "email_page": email_page,
            "kinds": SiteActivity.Kind.choices,
            "kind": kind,
            "actor_q": actor_q,
            "status": status,
        },
    )
```

- [ ] **Step 4: Add the URL**

In `core/urls.py`, add inside `urlpatterns`:

```python
    path("manage/activity/", views.site_activity, name="manage_activity"),
```

- [ ] **Step 5: Run access tests (template missing → will error on render)**

Run: `set -a && source .env && set +a && pytest tests/core/manage_activity_spec.py -k "anonymous or non_staff" -v`
Expected: the access tests PASS; the render tests will fail until Task 13 adds the template.

- [ ] **Step 6: Commit (view + url only)**

```bash
ruff format . && ruff check core/ && mypy core/
git add core/views.py core/urls.py tests/core/manage_activity_spec.py
git commit -m "feat(core): add staff /manage/activity view (templates next)"
```

---

## Task 13: Activity page templates (feed + email tabs)

**Files:**
- Create: `templates/hub/admin/activity.html`, `templates/hub/admin/_activity_feed.html`, `templates/hub/admin/_activity_emails.html`

- [ ] **Step 1: Create the page shell**

```html
{# templates/hub/admin/activity.html #}
{% extends "hub/base.html" %}
{% block title %}Site Activity — Past Lives{% endblock %}

{% block content %}
<div x-data="{ tab: '{{ active_tab|default:"feed" }}' }">
  <h1 class="hub-page-title">Site Activity</h1>

  <div style="display:flex;border-bottom:1px solid var(--hub-border);gap:0;margin-bottom:1.25rem;flex-wrap:wrap;">
    <button type="button" @click="tab = 'feed'"
            :class="{ 'vote-tab--active': tab === 'feed' }" class="vote-tab">Activity Feed</button>
    <button type="button" @click="tab = 'emails'"
            :class="{ 'vote-tab--active': tab === 'emails' }" class="vote-tab">Email Log</button>
  </div>

  <div x-show="tab === 'feed'" x-cloak>
    {% include "hub/admin/_activity_feed.html" %}
  </div>
  <div x-show="tab === 'emails'" x-cloak>
    {% include "hub/admin/_activity_emails.html" %}
  </div>
</div>
{% endblock %}
```

- [ ] **Step 2: Create the feed partial**

```html
{# templates/hub/admin/_activity_feed.html #}
<form method="get" class="hub-card" style="display:flex;gap:0.75rem;flex-wrap:wrap;align-items:end;margin-bottom:1rem;">
  <input type="hidden" name="tab" value="feed">
  <label style="display:flex;flex-direction:column;gap:0.25rem;">
    <span class="hub-detail-label">Event</span>
    <select name="kind">
      <option value="">All events</option>
      {% for value, label in kinds %}
        <option value="{{ value }}" {% if value == kind %}selected{% endif %}>{{ label }}</option>
      {% endfor %}
    </select>
  </label>
  <label style="display:flex;flex-direction:column;gap:0.25rem;">
    <span class="hub-detail-label">Actor email</span>
    <input type="text" name="actor" value="{{ actor_q }}" placeholder="name@example.com">
  </label>
  <button type="submit" class="pl-btn pl-btn--secondary">Filter</button>
</form>

<div class="hub-card">
  {% for a in feed_page %}
  <div class="hub-member-row" style="justify-content:space-between;">
    <div style="display:flex;align-items:center;gap:0.75rem;">
      <div class="hub-member-avatar">{% if a.actor %}{{ a.actor.email|make_list|first|upper }}{% else %}•{% endif %}</div>
      <div>
        <div class="hub-member-name">{{ a.get_kind_display }}</div>
        <div class="hub-text-muted" style="font-size:0.8125rem;">
          {% if a.actor %}{{ a.actor.email }}{% else %}System{% endif %} · {{ a.created_at|date:"M j, Y g:i A" }}
        </div>
      </div>
    </div>
    {% if a.email_log %}
      <span class="hub-badge" style="{% if a.email_log.status == 'failed' %}color:#ef4444;{% endif %}">
        ✉ {{ a.email_log.get_status_display }}
      </span>
    {% endif %}
  </div>
  {% empty %}
  <p class="hub-text-muted">No activity yet.</p>
  {% endfor %}
</div>

{% include "hub/admin/_activity_pagination.html" with page=feed_page param="page" %}
```

- [ ] **Step 3: Create the email-log partial**

```html
{# templates/hub/admin/_activity_emails.html #}
<form method="get" class="hub-card" style="display:flex;gap:0.75rem;flex-wrap:wrap;align-items:end;margin-bottom:1rem;">
  <input type="hidden" name="tab" value="emails">
  <label style="display:flex;flex-direction:column;gap:0.25rem;">
    <span class="hub-detail-label">Status</span>
    <select name="status">
      <option value="">All</option>
      <option value="sent" {% if status == 'sent' %}selected{% endif %}>Sent</option>
      <option value="failed" {% if status == 'failed' %}selected{% endif %}>Failed</option>
    </select>
  </label>
  <button type="submit" class="pl-btn pl-btn--secondary">Filter</button>
</form>

<div class="hub-card">
  {% for e in email_page %}
  <div class="hub-member-row" style="justify-content:space-between;{% if e.status == 'failed' %}background:rgba(239,68,68,0.06);{% endif %}">
    <div>
      <div class="hub-member-name">{{ e.subject|truncatechars:60 }}</div>
      <div class="hub-text-muted" style="font-size:0.8125rem;">
        {{ e.to_email }} · {{ e.trigger_kind }} · {{ e.created_at|date:"M j, Y g:i A" }}
      </div>
      {% if e.error_message %}<div style="color:#ef4444;font-size:0.8125rem;">{{ e.error_message|truncatechars:120 }}</div>{% endif %}
    </div>
    <span class="hub-badge" style="{% if e.status == 'failed' %}color:#ef4444;{% endif %}">{{ e.get_status_display }}</span>
  </div>
  {% empty %}
  <p class="hub-text-muted">No emails logged yet.</p>
  {% endfor %}
</div>

{% include "hub/admin/_activity_pagination.html" with page=email_page param="epage" %}
```

- [ ] **Step 4: Create the shared pagination partial**

```html
{# templates/hub/admin/_activity_pagination.html #}
{% if page.has_other_pages %}
<div style="display:flex;gap:0.75rem;justify-content:center;margin-top:1rem;align-items:center;">
  {% if page.has_previous %}
    <a class="pl-btn pl-btn--secondary" href="?{{ param }}={{ page.previous_page_number }}">Previous</a>
  {% endif %}
  <span class="hub-text-muted">Page {{ page.number }} of {{ page.paginator.num_pages }}</span>
  {% if page.has_next %}
    <a class="pl-btn pl-btn--secondary" href="?{{ param }}={{ page.next_page_number }}">Next</a>
  {% endif %}
</div>
{% endif %}
```

- [ ] **Step 5: Run the full activity-page spec**

Run: `set -a && source .env && set +a && pytest tests/core/manage_activity_spec.py -v`
Expected: all PASS (including `it_renders_for_staff`, `it_filters_the_feed_by_kind`).

- [ ] **Step 6: Commit**

```bash
git add templates/hub/admin/activity.html templates/hub/admin/_activity_feed.html templates/hub/admin/_activity_emails.html templates/hub/admin/_activity_pagination.html
git commit -m "feat(core): render /manage/activity feed + email log tabs"
```

---

## Task 14: Add the sidebar entry

**Files:**
- Modify: `plfog/settings.py` `UNFOLD["SIDEBAR"]["navigation"]` — add above "Manage Classes"

- [ ] **Step 1: Add the nav item**

In the first navigation group's `items` list, **before** the `"Manage Classes"` dict:

```python
                    {
                        "title": "Site Activity",
                        "icon": "monitoring",
                        "link": reverse_lazy("manage_activity"),
                        "permission": lambda request: request.user.is_staff,
                    },
```

- [ ] **Step 2: Smoke-check the admin loads**

Run: `set -a && source .env && set +a && python manage.py check`
Expected: `System check identified no issues`.

- [ ] **Step 3: Commit**

```bash
git add plfog/settings.py
git commit -m "feat: add Site Activity to the admin sidebar"
```

---

## Task 15: Full-suite + coverage gate

- [ ] **Step 1: Run the whole suite with coverage**

Run: `set -a && source .env && set +a && pytest`
Expected: all green; coverage ≥ 98% (the `--cov=.` addopts enforce `fail_under = 98`). If new lines are uncovered, add `it_*` cases for the missing branches (most likely: the `best_effort` swallow path, the `actor` null guard, an empty-feed render).

- [ ] **Step 2: Lint + type the whole tree**

Run: `ruff format . && ruff check . && mypy plfog/ core/ membership/ hub/`
Expected: clean.

- [ ] **Step 3: Final commit (if coverage tests were added)**

```bash
git add tests/
git commit -m "test(core): cover audit-foundation edge branches to 98%"
```

---

## Self-Review Checklist (run before handing off)

- [ ] **Spec coverage:** `TransactionalEmailLog` ✓ (T1), `core.email.send` wrapper ✓ (T2), `SiteActivity` + `log()` ✓ (T3), all transactional `send_mail` sites converted ✓ (T4–T6), every instrumentation point in the spec ✓ (T7–T11: login/logout/signup, profile, vote, tab charged/failed/entry, funding snapshot, class events, invite sent/accepted), `/manage/activity` page with both tabs + filters + pagination ✓ (T12–T13), sidebar ✓ (T14).
- [ ] **Deferred to later plans (intentional):** `site_announcement`, `lease_activated`, `guild_announcement` SiteActivity kinds exist in the enum but are *fired* by Plans 2/3 — the enum values are defined here so those plans only add call sites.
- [ ] **Placeholder scan:** no "TBD"/"handle edge cases"; every code step shows real code.
- [ ] **Type consistency:** `core.email.send(*, to, subject, trigger_kind, text_body, html_body, from_email, best_effort) -> TransactionalEmailLog` is the one signature used everywhere; `SiteActivity.log(kind, *, actor, target, email_log, payload)` is used consistently in T7–T11.

---

## Execution Handoff

Plan complete. Two execution options:

1. **Subagent-Driven (recommended)** — a fresh subagent per task, with review between tasks.
2. **Inline Execution** — execute tasks in one session with checkpoints.

**Recommended: run this plan in a fresh window** (per the token discussion) so execution context is clean.
