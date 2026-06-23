"""Auth → SiteActivity instrumentation."""

from __future__ import annotations

from typing import Any

from allauth.account.signals import user_logged_in, user_logged_out, user_signed_up
from django.dispatch import receiver
from django.template.loader import render_to_string

from core.models import SiteActivity


@receiver(user_logged_in)
def _on_login(sender: Any, request: Any, user: Any, **kwargs: Any) -> None:
    import hashlib

    from core import notifications
    from core.models import KnownLoginSignature

    SiteActivity.log(SiteActivity.Kind.LOGIN, actor=user)

    ua = request.META.get("HTTP_USER_AGENT", "")
    ip = request.META.get("REMOTE_ADDR", "")
    signature = hashlib.sha256(f"{ua}|{ip}".encode()).hexdigest()
    _, created = KnownLoginSignature.objects.get_or_create(user=user, signature=signature)
    if created:
        html_body = render_to_string(
            "core/email/new_login.html",
            {"settings_url": request.build_absolute_uri("/settings/")},
        )
        notifications.dispatch(
            "new_login",
            [user],
            title="New login detected",
            body="Your account was accessed from a new browser or device.",
            url="/settings/",
            html_body=html_body,
        )


@receiver(user_logged_out)
def _on_logout(sender: Any, request: Any, user: Any, **kwargs: Any) -> None:
    SiteActivity.log(SiteActivity.Kind.LOGOUT, actor=user)


@receiver(user_signed_up)
def _on_signup(sender: Any, request: Any, user: Any, **kwargs: Any) -> None:
    from membership.models import MemberEmail

    # migrate_to_user was intentionally skipped in post_save during allauth signup
    # (to avoid setup_user_email's assertion). Run it now that allauth has finished
    # its own email setup.
    MemberEmail.objects.migrate_to_user(user)

    SiteActivity.log(SiteActivity.Kind.MEMBER_SIGNUP, actor=user)

    from django.contrib.auth.models import User

    from core import notifications

    staff = User.objects.filter(is_staff=True)
    notifications.dispatch(
        "new_member_joined",
        staff,
        title="New member joined",
        body=f"{user.get_username()} just signed up.",
        url="/members/",
    )
