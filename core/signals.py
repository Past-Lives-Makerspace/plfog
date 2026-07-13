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
        # Scope the idempotency bucket to THIS signup. With the default one-shot
        # period ("") the delivery ledger would record new_member_joined once and
        # silently drop the admin alert for every later signup.
        period=f"signup:{user.pk}",
    )
