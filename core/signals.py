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

    from core.events.emit import emit
    from core.models import KnownLoginSignature

    SiteActivity.log(SiteActivity.Kind.LOGIN, actor=user)

    # Key the device signature on the browser/user-agent only — NOT the IP. IPs
    # rotate constantly (mobile data, dynamic home ISPs, VPNs, the host's proxy
    # fleet), so including the IP fired a "new login" email on every address change
    # and never actually remembered the device. User-agent stays stable per browser.
    ua = request.META.get("HTTP_USER_AGENT", "")
    signature = hashlib.sha256(ua.encode()).hexdigest()
    _, created = KnownLoginSignature.objects.get_or_create(user=user, signature=signature)
    if created:
        html_body = render_to_string(
            "core/email/new_login.html",
            {"settings_url": request.build_absolute_uri("/settings/")},
        )
        emit(
            "new_login",
            actor=user,
            context={"user": user},
            title="New login detected",
            # Scope the idempotency bucket to THIS device so each genuinely new
            # device alerts. With the default one-shot period ("") the delivery
            # ledger would record new_login once and suppress every later device.
            period=signature[:32],
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

    from core.events.emit import emit

    # emit logs the MEMBER_SIGNUP SiteActivity (registry activity_kind="member_signup")
    # with actor=user, and resolves the admin audience via FOG_ADMINS — the global
    # admin resolver that replaces the prior is_staff=True scan.
    emit(
        "new_member_joined",
        actor=user,
        context={},
        title="New member joined",
        body=f"{user.get_username()} just signed up.",
        url="/members/",
    )
