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
