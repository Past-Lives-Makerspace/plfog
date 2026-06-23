"""Fan-out delivery: in-app (always) + push/email (opt-in) for a trigger."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from django.db.models import QuerySet

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
    html_body: str | None = None,
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
                html_body=html_body,
                best_effort=True,
            )


def active_member_users() -> "QuerySet[User]":
    """All active members' User objects — the default broadcast audience."""
    from django.contrib.auth.models import User

    return User.objects.filter(member__status="active")
