# Notification System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the member-facing notification system — an always-on in-app bell feed, per-trigger opt-in browser push and email, a 25-trigger catalogue, a settings tab, event wiring across the app, and three scheduled (time-based) triggers.

**Architecture:** Two new `core` models (`Notification`, `NotificationPreference`). A trigger catalogue (`core/triggers.py`) describing each of the 25 triggers. A `dispatch()` function (`core/notifications.py`) that always writes in-app rows, then sends push (`core/push.py`, new `pywebpush` sender with dead-subscription cleanup) and email (through `core.email.send()` from Plan 1) to opted-in users. A topbar bell (HTMX/Alpine) and a settings tab. Inline triggers fire at workflow points; three time-based triggers run as idempotent management commands on Render cron.

**Tech Stack:** Django 5, pywebpush (new), pytest + pytest-describe, factory-boy, HTMX + Alpine in hub templates. Python 3.13, ruff (line-length 120, mccabe 10), mypy, coverage `fail_under = 98`.

**Depends on Plan 1 (audit foundation):** `core.email.send(*, to, subject, trigger_kind, text_body, html_body=None, from_email=None, best_effort=False) -> TransactionalEmailLog` must already exist. Notification emails call it with `best_effort=True`.

**Conventions for every task:**
- Tests are `tests/<app>/<name>_spec.py`, `it_*` inside `describe_*`, `pytestmark = pytest.mark.django_db` (or `@pytest.mark.django_db` per the hub-spec style).
- Run one test: `set -a && source .env && set +a && pytest tests/core/notifications_spec.py -v`.
- Before each commit: `ruff format . && ruff check . && mypy plfog/ core/ membership/ hub/`.

---

## File Structure

**Create:**
- `core/triggers.py` — `Trigger` dataclass + `TRIGGERS` list + lookup helpers. The single source of truth for the catalogue.
- `core/notifications.py` — `dispatch()`.
- `core/push.py` — `send_web_push()` (pywebpush).
- `core/management/commands/send_voting_reminders.py`, `core/management/commands/send_lease_expiry_reminders.py`.
- `templates/hub/_notification_bell.html` — topbar bell + dropdown.
- `templates/hub/_notification_feed.html` — feed partial (HTMX target).
- `templates/hub/_notifications_settings.html` — settings tab content.
- Tests: `tests/core/notification_models_spec.py`, `tests/core/triggers_spec.py`, `tests/core/push_spec.py`, `tests/core/notifications_dispatch_spec.py`, `tests/core/notification_views_spec.py`, `tests/hub/notification_settings_spec.py`, `tests/core/management/send_voting_reminders_spec.py`, `tests/core/management/send_lease_expiry_reminders_spec.py`.

**Modify:**
- `core/models.py` — add `Notification`, `NotificationPreference`, `KnownLoginSignature`, `ScheduledNotificationMarker`.
- `core/views.py` — add `notification_feed`, `notification_unread_count`, `notification_read`, `notification_read_all`.
- `core/urls.py` — add `/notifications/...` paths.
- `core/context_processors.py` — add `notification_badge` processor; register in `plfog/settings.py` TEMPLATES.
- `hub/views.py` — `user_settings` gets a `form_id="notifications"` branch + the `active_tab` whitelist gains `"notifications"`.
- `hub/urls.py` — add `settings/notifications/` POST route (or reuse `hub_user_settings`).
- `templates/hub/base.html` — insert the bell after `<div class="pl-topbar__spacer"></div>` (line ~328).
- `templates/hub/user_settings.html` — add the third tab button + tab content.
- `classes/tasks.py` — `send_due_class_reminders` also dispatches `class_reminder`.
- Workflow points across `classes/`, `billing/`, `membership/`, `core/` — add `dispatch()` calls.
- `requirements.txt` — add `pywebpush`.
- `render.yaml` — add two cron entries.
- `plfog/` allauth login signal — `new_login` detection.

---

## Task 1: `Notification` + `NotificationPreference` models

**Files:**
- Modify: `core/models.py` (add at end)
- Test: `tests/core/notification_models_spec.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/notification_models_spec.py
"""BDD-style tests for Notification + NotificationPreference."""

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError

from core.models import Notification, NotificationPreference

pytestmark = pytest.mark.django_db


def describe_Notification():
    def it_starts_unread():
        user = User.objects.create_user(username="u", email="u@example.com")
        n = Notification.objects.create(
            user=user, trigger="class_published", title="New class", body="A class went live", url="/x/",
        )
        assert n.read_at is None
        assert n.is_unread is True

    def it_marks_read():
        user = User.objects.create_user(username="u2", email="u2@example.com")
        n = Notification.objects.create(user=user, trigger="class_published", title="t", body="b")
        n.mark_read()
        n.refresh_from_db()
        assert n.read_at is not None
        assert n.is_unread is False


def describe_NotificationPreference():
    def it_is_unique_per_user_and_trigger():
        user = User.objects.create_user(username="u3", email="u3@example.com")
        NotificationPreference.objects.create(user=user, trigger="class_published", push_enabled=True)
        with pytest.raises(IntegrityError):
            NotificationPreference.objects.create(user=user, trigger="class_published")
```

- [ ] **Step 2: Run to verify it fails**

Run: `set -a && source .env && set +a && pytest tests/core/notification_models_spec.py -v`
Expected: FAIL — `cannot import name 'Notification'`.

- [ ] **Step 3: Add the models**

Append to `core/models.py`:

```python
class Notification(models.Model):
    """One in-app bell entry for one user. Always created on dispatch (non-optional)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    trigger = models.CharField(max_length=40, help_text="Trigger key from core.triggers.")
    title = models.CharField(max_length=200, help_text="Bold headline shown in the bell.")
    body = models.CharField(max_length=500, help_text="One-line detail.")
    url = models.CharField(max_length=500, blank=True, default="", help_text="Where clicking navigates.")
    read_at = models.DateTimeField(null=True, blank=True, help_text="Set when the user reads it.")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "read_at"]),
            models.Index(fields=["user", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} → {self.user.email}"

    @property
    def is_unread(self) -> bool:
        return self.read_at is None

    def mark_read(self) -> None:
        if self.read_at is None:
            self.read_at = timezone.now()
            self.save(update_fields=["read_at"])


class NotificationPreference(models.Model):
    """Per-user, per-trigger push/email opt-in. Absent row → trigger defaults apply."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_prefs")
    trigger = models.CharField(max_length=40, help_text="Trigger key from core.triggers.")
    push_enabled = models.BooleanField(default=False, help_text="Send browser push for this trigger.")
    email_enabled = models.BooleanField(default=False, help_text="Send email for this trigger.")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "trigger"], name="uq_notificationpreference_user_trigger"),
        ]

    def __str__(self) -> str:
        return f"{self.user.email}:{self.trigger} (push={self.push_enabled}, email={self.email_enabled})"
```

- [ ] **Step 4: Migrate + run**

Run: `set -a && source .env && set +a && python manage.py makemigrations core && pytest tests/core/notification_models_spec.py -v`
Expected: migration created; tests PASS.

- [ ] **Step 5: Commit**

```bash
ruff format . && ruff check core/models.py && mypy core/
git add core/models.py core/migrations/ tests/core/notification_models_spec.py
git commit -m "feat(core): add Notification + NotificationPreference models"
```

---

## Task 2: Trigger catalogue (`core/triggers.py`)

**Files:**
- Create: `core/triggers.py`
- Test: `tests/core/triggers_spec.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/triggers_spec.py
"""The notification trigger catalogue."""

from core import triggers


def describe_catalogue():
    def it_has_25_configurable_triggers_plus_forced():
        assert len(triggers.TRIGGERS) == 26  # 25 opt-in + new_login (forced)

    def it_looks_up_by_key():
        t = triggers.get("class_published")
        assert t.label == "New class published"
        assert t.audience == triggers.Audience.ALL_MEMBERS

    def it_raises_on_unknown_key():
        import pytest
        with pytest.raises(KeyError):
            triggers.get("nope")

    def it_filters_by_audience_for_a_plain_member():
        keys = {t.key for t in triggers.for_member(is_instructor=False, is_staff=False)}
        assert "class_published" in keys
        assert "instructor_class_approved" not in keys
        assert "new_member_joined" not in keys
        assert "new_login" not in keys  # forced triggers never show as a toggle

    def it_includes_instructor_triggers_for_instructors():
        keys = {t.key for t in triggers.for_member(is_instructor=True, is_staff=False)}
        assert "instructor_class_approved" in keys

    def it_includes_staff_triggers_for_staff():
        keys = {t.key for t in triggers.for_member(is_instructor=False, is_staff=True)}
        assert "new_member_joined" in keys

    def it_groups_by_category():
        grouped = triggers.by_category(is_instructor=True, is_staff=True)
        assert "Classes" in grouped
        assert any(t.key == "tab_charged" for t in grouped["Billing"])
```

- [ ] **Step 2: Run to verify it fails**

Run: `set -a && source .env && set +a && pytest tests/core/triggers_spec.py -v`
Expected: FAIL — `No module named 'core.triggers'`.

- [ ] **Step 3: Implement the catalogue**

```python
# core/triggers.py
"""The notification trigger catalogue — single source of truth.

Each Trigger describes one notifiable event: its stable key (stored in
Notification.trigger / NotificationPreference.trigger), display label and
description for the settings UI, category grouping, audience (who sees the
toggle), and defaults. `force_email` triggers always email and never show a
toggle (e.g. security new-login).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Audience(str, Enum):
    ALL_MEMBERS = "all_members"
    INSTRUCTORS_ONLY = "instructors_only"
    STAFF_ONLY = "staff_only"


@dataclass(frozen=True)
class Trigger:
    key: str
    label: str
    description: str
    category: str
    audience: Audience = Audience.ALL_MEMBERS
    force_email: bool = False
    push_default: bool = False
    email_default: bool = False


TRIGGERS: list[Trigger] = [
    # Classes — member-side
    Trigger("class_published", "New class published", "A new class or workshop goes live.", "Classes"),
    Trigger("class_reminder", "Class reminder", "24 hours before a session you're registered for.", "Classes"),
    Trigger("registration_confirmed", "Registration confirmed", "Your registration and payment cleared.", "Classes"),
    Trigger("class_cancelled", "Class cancelled", "A class you're registered for was cancelled.", "Classes"),
    Trigger("class_details_changed", "Class details changed", "Time, date, or location changed.", "Classes"),
    Trigger("waitlist_spot_available", "Waitlist spot available", "A spot opened in a class you waitlisted.", "Classes"),
    Trigger("waitlist_confirmed", "Added to waitlist", "You joined a class waitlist.", "Classes"),
    Trigger("refund_issued", "Refund issued", "A refund was processed for a registration.", "Classes"),
    # Classes — instructor-side
    Trigger("instructor_class_approved", "Your class was approved", "A reviewer approved your class.", "Teaching", Audience.INSTRUCTORS_ONLY),
    Trigger("instructor_changes_requested", "Changes requested", "A reviewer asked for edits.", "Teaching", Audience.INSTRUCTORS_ONLY),
    Trigger("instructor_new_registration", "New registration", "Someone registered for your class.", "Teaching", Audience.INSTRUCTORS_ONLY),
    Trigger("instructor_class_at_capacity", "Your class filled up", "The last spot was taken.", "Teaching", Audience.INSTRUCTORS_ONLY),
    # Guild voting
    Trigger("voting_cycle_open", "Voting cycle open", "A new monthly voting cycle started.", "Voting"),
    Trigger("voting_closing_soon", "Voting closing soon", "3 days before the monthly vote closes.", "Voting"),
    Trigger("funding_results_published", "Funding results published", "Guild allocations were finalized.", "Voting"),
    # Guild activity
    Trigger("guild_announcement", "Guild announcement", "A guild you're in posted an announcement.", "Guilds"),
    # Billing / tab
    Trigger("tab_charged", "Tab charged", "Your monthly tab was charged.", "Billing"),
    Trigger("tab_charge_failed", "Tab charge failed", "A charge failed — update your payment method.", "Billing"),
    Trigger("tab_entry_added", "Tab entry added", "An admin added a line item to your tab.", "Billing"),
    Trigger("tab_approaching_limit", "Tab approaching limit", "Your balance is near your tab limit.", "Billing"),
    # Membership
    Trigger("invite_accepted", "Invite accepted", "Someone you invited has joined.", "Membership"),
    Trigger("new_member_joined", "New member joined", "A new member signed up.", "Membership", Audience.STAFF_ONLY),
    # Spaces / leases
    Trigger("lease_expiring", "Lease expiring soon", "Your space lease ends within 30 days.", "Spaces"),
    Trigger("lease_activated", "New lease activated", "A new space lease started for you.", "Spaces"),
    # Admin broadcasts
    Trigger("site_announcement", "Makerspace-wide announcement", "Staff posted a site-wide notice.", "Announcements"),
    # Security — forced, no toggle
    Trigger("new_login", "New login detected", "Your account was accessed from a new device.", "Security", force_email=True),
]

_BY_KEY = {t.key: t for t in TRIGGERS}

# Stable category display order for the settings UI.
CATEGORY_ORDER = ["Classes", "Teaching", "Voting", "Guilds", "Billing", "Membership", "Spaces", "Announcements"]


def get(key: str) -> Trigger:
    """Return the Trigger for a key. Raises KeyError if unknown."""
    return _BY_KEY[key]


def for_member(*, is_instructor: bool, is_staff: bool) -> list[Trigger]:
    """Triggers whose toggle should be shown to this member. Excludes forced triggers."""
    out: list[Trigger] = []
    for t in TRIGGERS:
        if t.force_email:
            continue
        if t.audience == Audience.INSTRUCTORS_ONLY and not is_instructor:
            continue
        if t.audience == Audience.STAFF_ONLY and not is_staff:
            continue
        out.append(t)
    return out


def by_category(*, is_instructor: bool, is_staff: bool) -> dict[str, list[Trigger]]:
    """for_member() grouped by category, in CATEGORY_ORDER."""
    visible = for_member(is_instructor=is_instructor, is_staff=is_staff)
    grouped: dict[str, list[Trigger]] = {}
    for cat in CATEGORY_ORDER:
        rows = [t for t in visible if t.category == cat]
        if rows:
            grouped[cat] = rows
    return grouped
```

- [ ] **Step 4: Run to verify it passes**

Run: `set -a && source .env && set +a && pytest tests/core/triggers_spec.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
ruff format . && ruff check core/triggers.py && mypy core/
git add core/triggers.py tests/core/triggers_spec.py
git commit -m "feat(core): add notification trigger catalogue"
```

---

## Task 3: `pywebpush` dependency + `core/push.py`

**Files:**
- Modify: `requirements.txt` (add `pywebpush`)
- Create: `core/push.py`
- Test: `tests/core/push_spec.py`

- [ ] **Step 1: Add the dependency**

Append to `requirements.txt`:

```
pywebpush>=2.0
```

Install: `set -a && source .env && set +a && pip install "pywebpush>=2.0"`

- [ ] **Step 2: Write the failing test**

```python
# tests/core/push_spec.py
"""Browser push sending with dead-subscription cleanup."""

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from pywebpush import WebPushException

from core import push
from core.models import PushSubscription

pytestmark = pytest.mark.django_db


def _sub(user):
    return PushSubscription.objects.create(
        user=user, endpoint="https://push.example/abc", p256dh="key", auth="auth",
    )


def describe_send_web_push():
    def it_calls_pywebpush_with_payload():
        user = User.objects.create_user(username="u", email="u@example.com")
        sub = _sub(user)
        with patch("core.push.webpush") as mock_wp:
            push.send_web_push(sub, title="Hi", body="There", url="/x/")
        mock_wp.assert_called_once()

    def it_deletes_subscription_on_410_gone():
        user = User.objects.create_user(username="u2", email="u2@example.com")
        sub = _sub(user)

        class _Resp:
            status_code = 410

        with patch("core.push.webpush", side_effect=WebPushException("gone", response=_Resp())):
            push.send_web_push(sub, title="Hi", body="There", url="/x/")
        assert not PushSubscription.objects.filter(pk=sub.pk).exists()

    def it_swallows_other_errors_without_deleting():
        user = User.objects.create_user(username="u3", email="u3@example.com")
        sub = _sub(user)

        class _Resp:
            status_code = 500

        with patch("core.push.webpush", side_effect=WebPushException("boom", response=_Resp())):
            push.send_web_push(sub, title="Hi", body="There", url="/x/")
        assert PushSubscription.objects.filter(pk=sub.pk).exists()
```

- [ ] **Step 3: Run to verify it fails**

Run: `set -a && source .env && set +a && pytest tests/core/push_spec.py -v`
Expected: FAIL — `No module named 'core.push'`.

- [ ] **Step 4: Implement the sender**

```python
# core/push.py
"""Browser push delivery via pywebpush. Best-effort; never raises to callers."""

from __future__ import annotations

import json
import logging

from django.conf import settings
from pywebpush import WebPushException, webpush

from core.models import PushSubscription

logger = logging.getLogger(__name__)


def send_web_push(subscription: PushSubscription, *, title: str, body: str, url: str) -> None:
    """Send one push to one subscription. Reaps the subscription on 404/410."""
    payload = json.dumps({"title": title, "body": body, "url": url})
    try:
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
            },
            data=payload,
            vapid_private_key=settings.WEBPUSH_SETTINGS["VAPID_PRIVATE_KEY"],
            vapid_claims={"sub": f"mailto:{settings.WEBPUSH_SETTINGS['VAPID_ADMIN_EMAIL']}"},
        )
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (404, 410):
            subscription.delete()
            logger.info("Reaped dead push subscription %s (HTTP %s).", subscription.pk, status)
        else:
            logger.warning("Push to subscription %s failed: %s", subscription.pk, exc)
```

- [ ] **Step 5: Run to verify it passes**

Run: `set -a && source .env && set +a && pytest tests/core/push_spec.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
ruff format . && ruff check core/push.py requirements.txt && mypy core/
git add core/push.py requirements.txt tests/core/push_spec.py
git commit -m "feat(core): add pywebpush sender with dead-subscription reaping"
```

---

## Task 4: `dispatch()` (`core/notifications.py`)

**Files:**
- Create: `core/notifications.py`
- Test: `tests/core/notifications_dispatch_spec.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/notifications_dispatch_spec.py
"""dispatch() — in-app always; push/email per preference; force_email override."""

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User

from core import notifications
from core.models import Notification, NotificationPreference, PushSubscription, TransactionalEmailLog

pytestmark = pytest.mark.django_db


def _user(n):
    return User.objects.create_user(username=f"u{n}", email=f"u{n}@example.com")


def describe_dispatch():
    def it_always_creates_in_app_rows():
        users = [_user(1), _user(2)]
        notifications.dispatch("class_published", users, title="New class", body="b", url="/x/")
        assert Notification.objects.count() == 2

    def it_sends_push_only_to_opted_in_users():
        u = _user(3)
        NotificationPreference.objects.create(user=u, trigger="class_published", push_enabled=True)
        PushSubscription.objects.create(user=u, endpoint="https://p/x", p256dh="k", auth="a")
        with patch("core.notifications.send_web_push") as mock_push:
            notifications.dispatch("class_published", [u], title="t", body="b")
        mock_push.assert_called_once()

    def it_does_not_push_without_a_preference():
        u = _user(4)
        PushSubscription.objects.create(user=u, endpoint="https://p/y", p256dh="k", auth="a")
        with patch("core.notifications.send_web_push") as mock_push:
            notifications.dispatch("class_published", [u], title="t", body="b")
        mock_push.assert_not_called()

    def it_emails_opted_in_users_through_core_email():
        u = _user(5)
        NotificationPreference.objects.create(user=u, trigger="class_published", email_enabled=True)
        notifications.dispatch("class_published", [u], title="t", body="b")
        assert TransactionalEmailLog.objects.filter(trigger_kind="notification.class_published").exists()

    def it_force_emails_regardless_of_preference():
        u = _user(6)
        notifications.dispatch("new_login", [u], title="New login", body="b")
        assert TransactionalEmailLog.objects.filter(trigger_kind="notification.new_login").exists()

    def it_skips_users_without_email_when_emailing():
        u = User.objects.create_user(username="noemail", email="")
        NotificationPreference.objects.create(user=u, trigger="class_published", email_enabled=True)
        notifications.dispatch("class_published", [u], title="t", body="b")
        assert not TransactionalEmailLog.objects.exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `set -a && source .env && set +a && pytest tests/core/notifications_dispatch_spec.py -v`
Expected: FAIL — `No module named 'core.notifications'`.

- [ ] **Step 3: Implement dispatch**

```python
# core/notifications.py
"""Fan-out delivery: in-app (always) + push/email (opt-in) for a trigger."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from core import triggers
from core.email import send as send_email
from core.models import Notification, NotificationPreference, PushSubscription
from core.push import send_web_push


def dispatch(
    trigger_key: str,
    users: Iterable[Any],
    *,
    title: str,
    body: str,
    url: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    """Notify users of an event.

    In-app rows are always created. Browser push and email go only to users
    whose NotificationPreference opts in — except force_email triggers, which
    always email. Users without a usable email are skipped for email.
    """
    trigger = triggers.get(trigger_key)
    user_list = [u for u in users if getattr(u, "pk", None)]
    if not user_list:
        return

    Notification.objects.bulk_create(
        [Notification(user=u, trigger=trigger_key, title=title, body=body, url=url) for u in user_list]
    )

    prefs = {
        (p.user_id, p.trigger): p
        for p in NotificationPreference.objects.filter(trigger=trigger_key, user__in=user_list)
    }

    for user in user_list:
        pref = prefs.get((user.pk, trigger_key))
        if pref is not None and pref.push_enabled:
            for sub in PushSubscription.objects.filter(user=user):
                send_web_push(sub, title=title, body=body, url=url)

        wants_email = trigger.force_email or (pref is not None and pref.email_enabled)
        if wants_email and user.email:
            send_email(
                to=user.email,
                subject=title,
                trigger_kind=f"notification.{trigger_key}",
                text_body=body,
                best_effort=True,
            )
```

- [ ] **Step 4: Run to verify it passes**

Run: `set -a && source .env && set +a && pytest tests/core/notifications_dispatch_spec.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
ruff format . && ruff check core/notifications.py && mypy core/
git add core/notifications.py tests/core/notifications_dispatch_spec.py
git commit -m "feat(core): add notification dispatch fan-out"
```

---

## Task 5: Unread-count context processor

**Files:**
- Modify: `core/context_processors.py` (add `notification_badge`), `plfog/settings.py` (register it)
- Test: `tests/core/context_processors_spec.py` (extend)

- [ ] **Step 1: Add the processor**

In `core/context_processors.py`:

```python
def notification_badge(request: HttpRequest) -> dict[str, int]:
    """Unread notification count for the topbar bell. 0 for anonymous users."""
    if not getattr(request.user, "is_authenticated", False):
        return {"unread_notification_count": 0}
    from core.models import Notification

    count = Notification.objects.filter(user=request.user, read_at__isnull=True).count()
    return {"unread_notification_count": count}
```

(Match the file's existing import style for `HttpRequest`.)

- [ ] **Step 2: Register it**

In `plfog/settings.py` `TEMPLATES[0]["OPTIONS"]["context_processors"]`, add after `"hub.context_processors.hub_sidebar",`:

```python
                "core.context_processors.notification_badge",
```

- [ ] **Step 3: Write the test**

```python
# add to tests/core/context_processors_spec.py
def describe_notification_badge():
    def it_counts_unread_for_authenticated_user(client):
        from django.contrib.auth.models import User
        from core.models import Notification
        from core.context_processors import notification_badge
        from django.test import RequestFactory

        user = User.objects.create_user(username="b", email="b@example.com")
        Notification.objects.create(user=user, trigger="x", title="t", body="b")
        request = RequestFactory().get("/")
        request.user = user
        assert notification_badge(request)["unread_notification_count"] == 1
```

Add `pytestmark = pytest.mark.django_db` to the file if not present.

- [ ] **Step 4: Run + commit**

Run: `set -a && source .env && set +a && pytest tests/core/context_processors_spec.py -v`
Expected: PASS.

```bash
ruff format . && ruff check core/ && mypy core/
git add core/context_processors.py plfog/settings.py tests/core/context_processors_spec.py
git commit -m "feat(core): expose unread notification count to templates"
```

---

## Task 6: Bell feed endpoints (views + urls)

**Files:**
- Modify: `core/views.py` (4 views), `core/urls.py` (4 paths)
- Test: `tests/core/notification_views_spec.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/core/notification_views_spec.py
"""Bell feed endpoints."""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Notification

pytestmark = pytest.mark.django_db


def _login(client, n):
    user = User.objects.create_user(username=f"u{n}", email=f"u{n}@example.com", password="pw12345!")
    client.login(username=f"u{n}", password="pw12345!")
    return user


def describe_notification_feed():
    def it_lists_recent_notifications(client):
        user = _login(client, 1)
        Notification.objects.create(user=user, trigger="x", title="Hello", body="b")
        resp = client.get(reverse("notification_feed"))
        assert resp.status_code == 200
        assert b"Hello" in resp.content

    def it_only_shows_my_notifications(client):
        user = _login(client, 2)
        other = User.objects.create_user(username="other", email="o@example.com")
        Notification.objects.create(user=other, trigger="x", title="Secret", body="b")
        resp = client.get(reverse("notification_feed"))
        assert b"Secret" not in resp.content


def describe_unread_count():
    def it_returns_the_count(client):
        user = _login(client, 3)
        Notification.objects.create(user=user, trigger="x", title="t", body="b")
        resp = client.get(reverse("notification_unread_count"))
        assert b"1" in resp.content


def describe_mark_read():
    def it_marks_one_read_and_redirects_to_url(client):
        user = _login(client, 4)
        n = Notification.objects.create(user=user, trigger="x", title="t", body="b", url="/tab/")
        resp = client.post(reverse("notification_read", args=[n.pk]))
        n.refresh_from_db()
        assert n.read_at is not None
        assert resp.status_code == 302
        assert resp.url == "/tab/"

    def it_marks_all_read(client):
        user = _login(client, 5)
        Notification.objects.create(user=user, trigger="x", title="t", body="b")
        Notification.objects.create(user=user, trigger="y", title="t2", body="b")
        client.post(reverse("notification_read_all"))
        assert Notification.objects.filter(user=user, read_at__isnull=True).count() == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `set -a && source .env && set +a && pytest tests/core/notification_views_spec.py -v`
Expected: FAIL — `NoReverseMatch`.

- [ ] **Step 3: Add the views**

In `core/views.py`:

```python
@login_required
def notification_feed(request: HttpRequest) -> HttpResponse:
    """HTMX partial: the user's 15 most recent notifications."""
    from .models import Notification

    items = Notification.objects.filter(user=request.user)[:15]
    return render(request, "hub/_notification_feed.html", {"notifications": items})


@login_required
def notification_unread_count(request: HttpRequest) -> HttpResponse:
    """Plain-text unread count for the badge (HTMX polling target)."""
    from .models import Notification

    count = Notification.objects.filter(user=request.user, read_at__isnull=True).count()
    return HttpResponse(str(count))


@require_POST
@login_required
def notification_read(request: HttpRequest, pk: int) -> HttpResponse:
    """Mark one notification read and redirect to its url (or the home page)."""
    from .models import Notification

    note = Notification.objects.filter(user=request.user, pk=pk).first()
    if note is None:
        return redirect("home")
    note.mark_read()
    return redirect(note.url or "home")


@require_POST
@login_required
def notification_read_all(request: HttpRequest) -> HttpResponse:
    """Mark all the user's notifications read."""
    from django.utils import timezone

    from .models import Notification

    Notification.objects.filter(user=request.user, read_at__isnull=True).update(read_at=timezone.now())
    return HttpResponse(status=204)
```

- [ ] **Step 4: Add the URLs**

In `core/urls.py`, inside `urlpatterns`:

```python
    path("notifications/", views.notification_feed, name="notification_feed"),
    path("notifications/unread-count/", views.notification_unread_count, name="notification_unread_count"),
    path("notifications/<int:pk>/read/", views.notification_read, name="notification_read"),
    path("notifications/read-all/", views.notification_read_all, name="notification_read_all"),
```

- [ ] **Step 5: Create the feed partial**

```html
{# templates/hub/_notification_feed.html #}
{% for n in notifications %}
<form method="post" action="{% url 'notification_read' n.pk %}" class="pl-note{% if n.is_unread %} pl-note--unread{% endif %}">
  {% csrf_token %}
  <button type="submit" class="pl-note__btn">
    <span class="pl-note__title">{{ n.title }}</span>
    <span class="pl-note__body">{{ n.body }}</span>
    <span class="pl-note__time">{{ n.created_at|timesince }} ago</span>
  </button>
</form>
{% empty %}
<p class="pl-note__empty hub-text-muted">You're all caught up.</p>
{% endfor %}
```

- [ ] **Step 6: Run to verify it passes**

Run: `set -a && source .env && set +a && pytest tests/core/notification_views_spec.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
ruff format . && ruff check core/ && mypy core/
git add core/views.py core/urls.py templates/hub/_notification_feed.html tests/core/notification_views_spec.py
git commit -m "feat(core): add notification bell feed endpoints"
```

---

## Task 7: Topbar bell UI

**Files:**
- Create: `templates/hub/_notification_bell.html`
- Modify: `templates/hub/base.html` (insert after `<div class="pl-topbar__spacer"></div>`, line ~328), `static/css/hub.css` (bell + dropdown + `.pl-note` styles)

- [ ] **Step 1: Create the bell component**

```html
{# templates/hub/_notification_bell.html #}
<div class="pl-bell" x-data="{ open: false }" @keydown.escape.window="open = false">
  <button type="button" class="pl-bell__btn" @click="open = !open"
          hx-get="{% url 'notification_feed' %}" hx-target="#pl-bell-feed" hx-trigger="click"
          aria-label="Notifications">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>
    </svg>
    {% if unread_notification_count %}<span class="pl-bell__badge">{{ unread_notification_count }}</span>{% endif %}
  </button>
  <div class="pl-bell__panel" x-show="open" x-transition @click.away="open = false" style="display:none;">
    <div class="pl-bell__header">
      <span>Notifications</span>
      <button type="button" class="pl-bell__mark"
              hx-post="{% url 'notification_read_all' %}" hx-swap="none"
              @click="$el.closest('.pl-bell').querySelectorAll('.pl-note--unread').forEach(e => e.classList.remove('pl-note--unread'))">
        Mark all read
      </button>
    </div>
    <div id="pl-bell-feed" class="pl-bell__feed">
      <p class="hub-text-muted" style="padding:1rem;">Loading…</p>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Insert into base.html**

In `templates/hub/base.html`, immediately after line 328 (`<div class="pl-topbar__spacer"></div>`):

```html
            {% include "hub/_notification_bell.html" %}
```

- [ ] **Step 3: Add CSS**

Append to `static/css/hub.css`:

```css
.pl-bell { position: relative; }
.pl-bell__btn { position: relative; background: none; border: none; color: var(--hub-text); cursor: pointer; padding: 0.4rem; border-radius: 6px; }
.pl-bell__btn:hover { background: rgba(255,255,255,0.06); }
.pl-bell__badge { position: absolute; top: 0; right: 0; min-width: 16px; height: 16px; padding: 0 4px; border-radius: 8px; background: #ef4444; color: #fff; font-size: 0.625rem; line-height: 16px; text-align: center; font-weight: 700; }
.pl-bell__panel { position: absolute; right: 0; top: calc(100% + 8px); width: 340px; max-height: 420px; overflow-y: auto; background: var(--hub-card-bg); border: 1px solid var(--hub-border); border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.3); z-index: 50; }
.pl-bell__header { display: flex; justify-content: space-between; align-items: center; padding: 0.75rem 1rem; border-bottom: 1px solid var(--hub-border); font-weight: 600; }
.pl-bell__mark { background: none; border: none; color: var(--color-tuscan-yellow); cursor: pointer; font-size: 0.8125rem; }
.pl-note { border-bottom: 1px solid var(--hub-border); }
.pl-note__btn { display: flex; flex-direction: column; gap: 0.15rem; width: 100%; text-align: left; background: none; border: none; padding: 0.75rem 1rem; cursor: pointer; color: var(--hub-text); }
.pl-note__btn:hover { background: rgba(255,255,255,0.04); }
.pl-note--unread .pl-note__btn { background: rgba(238,180,75,0.08); }
.pl-note__title { font-weight: 600; font-size: 0.875rem; }
.pl-note__body { font-size: 0.8125rem; color: var(--hub-text-muted); }
.pl-note__time { font-size: 0.6875rem; color: var(--hub-text-muted); }
.pl-note__empty { padding: 1.5rem 1rem; text-align: center; }
```

- [ ] **Step 4: Manual smoke test**

Run the dev server (`make server`), log in, confirm the bell renders top-right, the badge shows when notifications exist, clicking loads the feed, and "Mark all read" clears the badge on next load.

- [ ] **Step 5: Commit**

```bash
git add templates/hub/_notification_bell.html templates/hub/base.html static/css/hub.css
git commit -m "feat(hub): add topbar notification bell"
```

---

## Task 8: Notifications settings tab

**Files:**
- Modify: `hub/views.py` `user_settings` (add `form_id="notifications"` branch + whitelist `"notifications"` + pass catalogue), `templates/hub/user_settings.html` (third tab)
- Create: `templates/hub/_notifications_settings.html`
- Test: `tests/hub/notification_settings_spec.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/hub/notification_settings_spec.py
"""Notifications settings tab saves NotificationPreference rows."""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.models import NotificationPreference

pytestmark = pytest.mark.django_db


def describe_notifications_tab():
    def it_saves_push_and_email_toggles(client):
        User.objects.create_user(username="m", email="m@example.com", password="pw12345!")
        client.login(username="m", password="pw12345!")
        client.post(
            reverse("hub_user_settings"),
            {"form_id": "notifications", "push_class_published": "on", "email_tab_charged": "on"},
        )
        user = User.objects.get(username="m")
        assert NotificationPreference.objects.get(user=user, trigger="class_published").push_enabled is True
        assert NotificationPreference.objects.get(user=user, trigger="tab_charged").email_enabled is True

    def it_clears_unchecked_toggles(client):
        user = User.objects.create_user(username="m2", email="m2@example.com", password="pw12345!")
        NotificationPreference.objects.create(user=user, trigger="class_published", push_enabled=True)
        client.login(username="m2", password="pw12345!")
        client.post(reverse("hub_user_settings"), {"form_id": "notifications"})  # nothing checked
        assert NotificationPreference.objects.get(user=user, trigger="class_published").push_enabled is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `set -a && source .env && set +a && pytest tests/hub/notification_settings_spec.py -v`
Expected: FAIL (preferences not saved).

- [ ] **Step 3: Add the view branch**

In `hub/views.py` `user_settings`, after the `email_prefs` branch and before `add_email_form = ...`:

```python
    if request.method == "POST" and request.POST.get("form_id") == "notifications":
        from core import triggers
        from core.models import NotificationPreference

        is_instructor = bool(member and member.is_instructor)
        is_staff = request.user.is_staff
        for t in triggers.for_member(is_instructor=is_instructor, is_staff=is_staff):
            NotificationPreference.objects.update_or_create(
                user=request.user,
                trigger=t.key,
                defaults={
                    "push_enabled": request.POST.get(f"push_{t.key}") == "on",
                    "email_enabled": request.POST.get(f"email_{t.key}") == "on",
                },
            )
        messages.success(request, "Notification preferences updated.")
        return redirect(f"{request.path}?tab=notifications")
```

> **Note on validation:** trigger keys come from the server-side catalogue (`triggers.for_member`), never from request data, so there is no untrusted-key risk — this is why a dynamic 50-field Django form is unnecessary here.

Then widen the `active_tab` whitelist:

```python
    active_tab = tab_param if tab_param in {"profile", "emails", "notifications"} else "profile"
```

And build the grouped catalogue + current prefs for rendering, adding to the render context dict:

```python
    from core import triggers as _triggers
    from core.models import NotificationPreference as _NP

    _is_instructor = bool(member and member.is_instructor)
    notif_groups = _triggers.by_category(is_instructor=_is_instructor, is_staff=request.user.is_staff)
    notif_prefs = {
        p.trigger: p for p in _NP.objects.filter(user=request.user)
    }
```

Add to the render context: `"notif_groups": notif_groups, "notif_prefs": notif_prefs,`.

- [ ] **Step 4: Add the tab button**

In `templates/hub/user_settings.html`, after the "Emails" tab button (line ~20):

```html
        <button type="button"
                @click="tab = 'notifications'"
                :class="{ 'vote-tab--active': tab === 'notifications' }"
                class="vote-tab">
            Notifications
        </button>
```

- [ ] **Step 5: Add the tab content**

After the emails tab's closing `</div>` (the `x-show="tab === 'emails'"` block), add:

```html
    <div x-show="tab === 'notifications'" x-cloak>
        {% include "hub/_notifications_settings.html" %}
    </div>
```

- [ ] **Step 6: Create the settings partial**

```html
{# templates/hub/_notifications_settings.html #}
<div class="hub-card">
  <h2 style="font-size:1rem;font-weight:600;margin:0 0 0.25rem;">Notifications</h2>
  <p class="hub-text-muted" style="margin-bottom:1rem;font-size:0.85rem;">
    The bell always shows everything. Choose which events also reach you by browser push or email.
  </p>
  <form method="post" class="hub-form">
    {% csrf_token %}
    <input type="hidden" name="form_id" value="notifications">
    {% for category, rows in notif_groups.items %}
      <h3 class="hub-detail-label" style="margin:1.25rem 0 0.5rem;">{{ category }}</h3>
      <table class="pl-notif-table" style="width:100%;border-collapse:collapse;">
        <thead>
          <tr>
            <th style="text-align:left;font-weight:500;color:var(--hub-text-muted);font-size:0.75rem;">Event</th>
            <th style="width:64px;color:var(--hub-text-muted);font-size:0.75rem;">Push</th>
            <th style="width:64px;color:var(--hub-text-muted);font-size:0.75rem;">Email</th>
          </tr>
        </thead>
        <tbody>
          {% for t in rows %}
          <tr style="border-top:1px solid var(--hub-border);">
            <td style="padding:0.6rem 0;">
              <div style="font-weight:500;font-size:0.875rem;">{{ t.label }}</div>
              <div class="hub-text-muted" style="font-size:0.75rem;">{{ t.description }}</div>
            </td>
            <td style="text-align:center;">
              <input type="checkbox" name="push_{{ t.key }}" {% if notif_prefs|get_item:t.key and notif_prefs|get_item:t.key.push_enabled %}checked{% endif %}>
            </td>
            <td style="text-align:center;">
              <input type="checkbox" name="email_{{ t.key }}" {% if notif_prefs|get_item:t.key and notif_prefs|get_item:t.key.email_enabled %}checked{% endif %}>
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    {% endfor %}
    <div style="margin-top:1.25rem;">
      <button type="submit" class="hub-btn hub-btn--primary">Save Preferences</button>
    </div>
  </form>
</div>
```

- [ ] **Step 7: Add the `get_item` template filter (if absent)**

Check `hub/templatetags/hub_tags.py` for a `get_item` dict-lookup filter (`grep -n "get_item" hub/templatetags/hub_tags.py`). If missing, add:

```python
@register.filter
def get_item(mapping: dict, key: str):
    """Dict lookup by variable key in templates."""
    return mapping.get(key)
```

Ensure the template loads it: add `{% load hub_tags %}` at the top of `_notifications_settings.html` if the parent doesn't already.

- [ ] **Step 8: Run to verify it passes**

Run: `set -a && source .env && set +a && pytest tests/hub/notification_settings_spec.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
ruff format . && ruff check hub/ core/ && mypy plfog/ core/ membership/ hub/
git add hub/views.py templates/hub/user_settings.html templates/hub/_notifications_settings.html hub/templatetags/hub_tags.py tests/hub/notification_settings_spec.py
git commit -m "feat(hub): add notification preferences settings tab"
```

---

## Task 9: Wire inline (event-driven) triggers

Each workflow point that already exists gets a `dispatch()` call. Use this **recipe** at each site: import locally (`from core import notifications`), gather the recipient `User` objects, and call `notifications.dispatch(key, users, title=..., body=..., url=...)`.

**Recipient helper:** for broadcasts to all members, use
`User.objects.filter(member__status="active", member__isnull=False)` — i.e. active members with a linked user. Add a small helper in `core/notifications.py`:

```python
def active_member_users():
    """All active members' User objects — the default broadcast audience."""
    from django.contrib.auth.models import User

    return User.objects.filter(member__status="active")
```

- [ ] **Step 1: Class published → all members**

In the classes workflow point that publishes a class (find it: `grep -rn "Status.PUBLISHED\|def publish\|class_published" classes/`), after publish succeeds:

```python
    from core import notifications

    notifications.dispatch(
        "class_published",
        notifications.active_member_users(),
        title="New class published",
        body=offering.title,
        url=f"/classes/{offering.slug}/" if hasattr(offering, "slug") else "/classes/",
    )
```

- [ ] **Step 2: Registration confirmed / waitlist / refund / cancel → the affected member**

At the registration confirmation point (`grep -rn "REGISTRATION_CONFIRMED\|registration_confirmed\|status = .*CONFIRMED" classes/`), dispatch to that registrant's user (when the registration is linked to a member/user; skip anonymous registrants who have no User):

```python
    from core import notifications

    user = getattr(registration.member, "user", None) if getattr(registration, "member_id", None) else None
    if user is not None:
        notifications.dispatch(
            "registration_confirmed", [user],
            title="Registration confirmed", body=registration.class_offering.title, url="/classes/account/",
        )
```

Repeat the same pattern with keys `waitlist_confirmed`, `waitlist_spot_available`, `refund_issued`, `class_cancelled`, `class_details_changed` at their respective workflow points (search the same files for each event).

- [ ] **Step 3: Instructor-side → the instructor's user**

At the class-approved / changes-requested / new-registration / at-capacity points, dispatch to `offering.instructor.user` (guarded) with keys `instructor_class_approved`, `instructor_changes_requested`, `instructor_new_registration`, `instructor_class_at_capacity`.

- [ ] **Step 4: Billing → the member**

In `billing/notifications.py::send_receipt` (after the SiteActivity.log added in Plan 1):

```python
    from core import notifications

    notifications.dispatch("tab_charged", [member.user], title="Tab charged",
                           body=f"${charge.amount} was charged to your tab.", url="/tab/")
```

In `notify_admin_charge_failed` path, dispatch `tab_charge_failed` to `[member.user]`. In `Tab.add_entry`, dispatch `tab_entry_added` to `[self.member.user]` when an admin added it. In `Tab.add_entry`, after computing the new balance, if balance ≥ 80% of the tab limit, dispatch `tab_approaching_limit`.

- [ ] **Step 5: Voting & funding → all members**

In `FundingSnapshot.take` (after the SiteActivity.log from Plan 1):

```python
    from core import notifications

    notifications.dispatch("funding_results_published", notifications.active_member_users(),
                           title="Funding results published", body=f"Results for {snapshot.cycle_label} are in.",
                           url="/guilds/voting/history/")
```

- [ ] **Step 6: Membership → inviter / staff / member**

`invite_accepted` → the inviter's user at `Invite.mark_accepted`. `new_member_joined` → staff users (`User.objects.filter(is_staff=True)`) on signup (in the `user_signed_up` handler from Plan 1's `core/signals.py`). `lease_activated` → the tenant member's user when a Lease is created with a current start_date (post_save on Lease, guarded to `created and instance.tenant` being a Member with a user).

- [ ] **Step 7: Tests**

For each wired trigger, add an `it_*` test asserting `Notification.objects.filter(trigger=<key>).exists()` after the workflow runs. Group them by app under `tests/<app>/`. Use existing factories. Mock nothing in `dispatch` — it writes real rows and uses `best_effort` email + patched/absent push.

- [ ] **Step 8: Run the relevant suites + commit**

Run: `set -a && source .env && set +a && pytest tests/classes/ tests/billing/ tests/membership/ -k "notif or dispatch or notify" -v`
Expected: PASS.

```bash
ruff format . && ruff check . && mypy plfog/ core/ membership/ hub/
git add classes/ billing/ membership/ core/ tests/
git commit -m "feat: dispatch notifications at class, billing, voting, membership events"
```

---

## Task 10: Scheduled triggers (class reminder, voting, lease)

**Files:**
- Modify: `classes/tasks.py` (`send_due_class_reminders` also dispatches), `core/models.py` (`ScheduledNotificationMarker`)
- Create: `core/management/commands/send_voting_reminders.py`, `core/management/commands/send_lease_expiry_reminders.py`
- Modify: `render.yaml` (two cron entries)
- Test: `tests/core/management/send_voting_reminders_spec.py`, `tests/core/management/send_lease_expiry_reminders_spec.py`

- [ ] **Step 1: Add the idempotency marker model**

Append to `core/models.py`:

```python
class ScheduledNotificationMarker(models.Model):
    """Idempotency guard for time-based notification jobs.

    A unique ``key`` records that a given scheduled notification already fired,
    e.g. "voting_closing:2026-06" or "lease_expiring:42". Jobs get_or_create
    the key and skip when it already exists.
    """

    key = models.CharField(max_length=120, unique=True, help_text="Stable per-notification idempotency key.")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.key
```

Run: `set -a && source .env && set +a && python manage.py makemigrations core`

- [ ] **Step 2: Extend the class-reminder task to dispatch**

In `classes/tasks.py::send_due_class_reminders`, inside the loop right after `send_reminder_email(registration, session)`:

```python
            user = getattr(registration.member, "user", None) if getattr(registration, "member_id", None) else None
            if user is not None:
                from core import notifications

                notifications.dispatch(
                    "class_reminder", [user],
                    title="Class reminder",
                    body=f"{session.class_offering.title} starts soon.",
                    url="/classes/account/",
                )
```

The existing `RegistrationReminder.get_or_create` guard already prevents duplicates.

- [ ] **Step 3: Write the voting-reminder command test**

```python
# tests/core/management/send_voting_reminders_spec.py
"""send_voting_reminders fires once per cycle, 3 days before month end."""

from datetime import datetime

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.models import Notification, ScheduledNotificationMarker
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def describe_send_voting_reminders():
    def it_dispatches_inside_the_window_and_is_idempotent(monkeypatch):
        # Pin "now" to 2026-06-28 (June has 30 days → 3 days before close).
        fixed = timezone.make_aware(datetime(2026, 6, 28, 9, 0))
        monkeypatch.setattr("core.management.commands.send_voting_reminders.timezone.now", lambda: fixed)

        member = MemberFactory()
        member.user = __import__("django.contrib.auth.models", fromlist=["User"]).User.objects.create_user(
            username="v", email="v@example.com"
        )
        member.save()

        call_command("send_voting_reminders")
        call_command("send_voting_reminders")  # second run must no-op

        assert Notification.objects.filter(trigger="voting_closing_soon").count() == 1
        assert ScheduledNotificationMarker.objects.filter(key="voting_closing:2026-06").exists()

    def it_does_nothing_outside_the_window(monkeypatch):
        fixed = timezone.make_aware(datetime(2026, 6, 10, 9, 0))
        monkeypatch.setattr("core.management.commands.send_voting_reminders.timezone.now", lambda: fixed)
        call_command("send_voting_reminders")
        assert Notification.objects.filter(trigger="voting_closing_soon").count() == 0
```

- [ ] **Step 4: Implement the voting-reminder command**

```python
# core/management/commands/send_voting_reminders.py
"""Notify members 3 days before the monthly funding vote closes. Daily cron; idempotent."""

from __future__ import annotations

import calendar
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from core import notifications
from core.models import ScheduledNotificationMarker


class Command(BaseCommand):
    help = "Dispatch the 'voting closing soon' notification when 3 days remain in the cycle."

    def handle(self, *args: Any, **options: Any) -> None:
        now = timezone.now()
        last_day = calendar.monthrange(now.year, now.month)[1]
        if now.day != last_day - 3:
            self.stdout.write("Not in the voting-reminder window; nothing to do.")
            return
        key = f"voting_closing:{now:%Y-%m}"
        _, created = ScheduledNotificationMarker.objects.get_or_create(key=key)
        if not created:
            self.stdout.write("Already sent this cycle.")
            return
        notifications.dispatch(
            "voting_closing_soon",
            notifications.active_member_users(),
            title="Guild voting closes soon",
            body="The monthly funding vote closes in 3 days. Cast or update your vote.",
            url="/guilds/voting/",
        )
        self.stdout.write(self.style.SUCCESS("Sent voting-closing reminders."))
```

- [ ] **Step 5: Implement the lease-expiry command + test**

```python
# core/management/commands/send_lease_expiry_reminders.py
"""Notify tenants 30 days before a lease end_date. Daily cron; idempotent per lease."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from core import notifications
from core.models import ScheduledNotificationMarker
from membership.models import Lease, Member


class Command(BaseCommand):
    help = "Dispatch 'lease expiring' notifications for leases ending in 30 days."

    def handle(self, *args: Any, **options: Any) -> None:
        target = (timezone.now().date() + timedelta(days=30))
        leases = Lease.objects.filter(end_date=target)
        sent = 0
        for lease in leases:
            key = f"lease_expiring:{lease.pk}"
            _, created = ScheduledNotificationMarker.objects.get_or_create(key=key)
            if not created:
                continue
            tenant = lease.tenant
            user = getattr(tenant, "user", None) if isinstance(tenant, Member) else None
            if user is None:
                continue
            notifications.dispatch(
                "lease_expiring", [user],
                title="Your space lease is expiring",
                body=f"Your lease for {lease.space} ends on {lease.end_date:%b %d, %Y}.",
                url="/",
            )
            sent += 1
        self.stdout.write(self.style.SUCCESS(f"Sent {sent} lease-expiry reminder(s)."))
```

```python
# tests/core/management/send_lease_expiry_reminders_spec.py
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.utils import timezone

from core.models import Notification
from tests.membership.factories import LeaseFactory, MemberFactory

pytestmark = pytest.mark.django_db


def describe_send_lease_expiry_reminders():
    def it_notifies_the_tenant_30_days_out():
        user = User.objects.create_user(username="t", email="t@example.com")
        member = MemberFactory(user=user)
        LeaseFactory(tenant=member, end_date=timezone.now().date() + timedelta(days=30))
        call_command("send_lease_expiry_reminders")
        assert Notification.objects.filter(trigger="lease_expiring").count() == 1

    def it_is_idempotent():
        user = User.objects.create_user(username="t2", email="t2@example.com")
        member = MemberFactory(user=user)
        LeaseFactory(tenant=member, end_date=timezone.now().date() + timedelta(days=30))
        call_command("send_lease_expiry_reminders")
        call_command("send_lease_expiry_reminders")
        assert Notification.objects.filter(trigger="lease_expiring").count() == 1
```

Confirm `LeaseFactory` accepts a `tenant` kwarg (it sets the GenericFK). If it instead uses `content_type`/`object_id`, set the tenant after creation in the test: `lease = LeaseFactory(); lease.tenant = member; lease.save()`.

- [ ] **Step 6: Add Render cron entries**

In `render.yaml`, after the `airtable-pull` cron block:

```yaml
  - type: cron
    name: send-voting-reminders
    runtime: python
    schedule: "0 16 * * *"  # daily 16:00 UTC
    buildCommand: pip install -r requirements.txt
    command: python manage.py send_voting_reminders
    envVars:
      - key: PYTHON_VERSION
        value: "3.13.0"
  - type: cron
    name: send-lease-expiry-reminders
    runtime: python
    schedule: "0 16 * * *"
    buildCommand: pip install -r requirements.txt
    command: python manage.py send_lease_expiry_reminders
    envVars:
      - key: PYTHON_VERSION
        value: "3.13.0"
```

(Match the env-var style of the existing `airtable-pull` cron; add `DATABASE_URL` and any secrets the way that job declares them.)

- [ ] **Step 7: Run + commit**

Run: `set -a && source .env && set +a && pytest tests/core/management/ tests/classes/ -k "reminder or voting or lease" -v`
Expected: PASS.

```bash
ruff format . && ruff check . && mypy plfog/ core/ membership/ hub/
git add core/ classes/tasks.py render.yaml tests/core/management/
git commit -m "feat(core): add scheduled voting + lease + class-reminder notifications"
```

---

## Task 11: `new_login` detection (forced email)

**Files:**
- Modify: `core/models.py` (`KnownLoginSignature`), `core/signals.py` (extend the `_on_login` handler from Plan 1)
- Test: `tests/core/signals_spec.py` (extend)

- [ ] **Step 1: Add the signature model**

Append to `core/models.py`:

```python
class KnownLoginSignature(models.Model):
    """Records (user, signature) pairs already seen, to detect new-device logins."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="login_signatures")
    signature = models.CharField(max_length=64, help_text="Hash of (user-agent, IP).")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "signature"], name="uq_loginsignature_user_signature"),
        ]
```

Run: `set -a && source .env && set +a && python manage.py makemigrations core`

- [ ] **Step 2: Write the failing test**

```python
# add to tests/core/signals_spec.py
def describe_new_login_detection():
    def it_notifies_on_a_first_time_signature():
        from django.contrib.auth.models import User
        from allauth.account.signals import user_logged_in
        from django.test import RequestFactory
        from core.models import Notification

        user = User.objects.create_user(username="nl", email="nl@example.com")
        request = RequestFactory().get("/", HTTP_USER_AGENT="Firefox", REMOTE_ADDR="1.2.3.4")
        user_logged_in.send(sender=User, request=request, user=user)
        assert Notification.objects.filter(trigger="new_login", user=user).count() == 1

    def it_does_not_notify_on_a_known_signature():
        from django.contrib.auth.models import User
        from allauth.account.signals import user_logged_in
        from django.test import RequestFactory
        from core.models import Notification

        user = User.objects.create_user(username="nl2", email="nl2@example.com")
        request = RequestFactory().get("/", HTTP_USER_AGENT="Firefox", REMOTE_ADDR="1.2.3.4")
        user_logged_in.send(sender=User, request=request, user=user)
        user_logged_in.send(sender=User, request=request, user=user)  # same signature
        assert Notification.objects.filter(trigger="new_login", user=user).count() == 1
```

- [ ] **Step 3: Extend the login handler**

In `core/signals.py`, replace `_on_login` body:

```python
@receiver(user_logged_in)
def _on_login(sender: Any, request: Any, user: Any, **kwargs: Any) -> None:
    import hashlib

    from core import notifications
    from core.models import KnownLoginSignature, SiteActivity

    SiteActivity.log(SiteActivity.Kind.LOGIN, actor=user)

    ua = request.META.get("HTTP_USER_AGENT", "")
    ip = request.META.get("REMOTE_ADDR", "")
    signature = hashlib.sha256(f"{ua}|{ip}".encode()).hexdigest()
    _, created = KnownLoginSignature.objects.get_or_create(user=user, signature=signature)
    if created:
        notifications.dispatch(
            "new_login", [user],
            title="New login detected",
            body="Your account was accessed from a new browser or device.",
            url="/settings/",
        )
```

- [ ] **Step 4: Run + commit**

Run: `set -a && source .env && set +a && pytest tests/core/signals_spec.py -v`
Expected: PASS.

```bash
ruff format . && ruff check core/ && mypy core/
git add core/models.py core/signals.py core/migrations/ tests/core/signals_spec.py
git commit -m "feat(core): detect new-device logins and force a security email"
```

---

## Task 12: Full suite + coverage gate

- [ ] **Step 1: Run everything**

Run: `set -a && source .env && set +a && pytest`
Expected: all green; coverage ≥ 98%. Likely missing-branch culprits: the `dispatch` no-recipients early return, `send_web_push` non-410 branch, the lease command's `user is None` skip, the voting command's outside-window branch. Add `it_*` cases for any uncovered lines.

- [ ] **Step 2: Lint + type**

Run: `ruff format . && ruff check . && mypy plfog/ core/ membership/ hub/`
Expected: clean.

- [ ] **Step 3: Final commit**

```bash
git add tests/
git commit -m "test(core): cover notification edge branches to 98%"
```

---

## Self-Review Checklist (run before handing off)

- [ ] **Spec coverage:** models ✓ (T1), 25+1 trigger catalogue ✓ (T2), push build ✓ (T3), dispatch ✓ (T4), badge ✓ (T5), bell endpoints ✓ (T6), bell UI ✓ (T7), settings tab ✓ (T8), inline triggers ✓ (T9), scheduled triggers ✓ (T10), new_login ✓ (T11).
- [ ] **Trigger wiring completeness:** every `key` in `core/triggers.py` is fired somewhere — inline (T9), scheduled (T10), or forced (T11). `guild_announcement`, `site_announcement`, and a guild-scoped audience are completed in **Plan 3 (guild pages)**; `class_published` etc. fire here. If executing this plan standalone, `guild_announcement`/`site_announcement` simply have no caller yet — that's expected.
- [ ] **Type consistency:** `notifications.dispatch(trigger_key, users, *, title, body, url="", payload=None) -> None` is the one signature; `triggers.get/for_member/by_category` names match T2; `send_web_push(subscription, *, title, body, url)` matches T3.
- [ ] **Placeholder scan:** all code steps show real code; the only "find the workflow point" instructions (T9) include the exact `grep` to locate the site and the exact dispatch call to add.

---

## Execution Handoff

Plan complete. Run it **in a fresh window**, after Plan 1 (audit foundation) is merged — `core.email.send` must exist first.

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks.
2. **Inline Execution** — one session with checkpoints.
