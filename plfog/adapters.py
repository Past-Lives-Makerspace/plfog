"""Custom allauth adapters for auto-admin domain privileges."""

import logging

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.http import HttpRequest

logger = logging.getLogger(__name__)


class AutoAdminSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Grant admin privileges to users whose email domain is in ADMIN_DOMAINS.

    On social login, if the user's email domain matches any domain in the
    ADMIN_DOMAINS setting (case-insensitive), the user gets is_staff=True
    and is_superuser=True.

    This works via two hooks:
    - ``save_user()``: fires on NEW user creation (first social login).
    - ``pre_social_login()``: fires on EVERY social login, catching
      existing users whose accounts predate the ADMIN_DOMAINS feature.

    Users with non-matching domains are not modified — existing admin users
    who log in from a non-listed domain keep whatever privileges they had.
    """

    def save_user(self, request: HttpRequest, sociallogin: object, form: object = None) -> object:
        """Save user and grant admin privileges if email domain matches."""
        user = super().save_user(request, sociallogin, form=form)
        self._maybe_grant_admin(user)
        return user

    def pre_social_login(self, request: HttpRequest, sociallogin: object) -> None:
        """Promote existing users to admin if their email domain matches.

        This hook fires on every social login (before ``save_user``).
        For existing users whose accounts predate the ADMIN_DOMAINS feature,
        this is the only chance to upgrade their privileges.
        """
        user = sociallogin.user  # type: ignore[attr-defined]
        if user.pk is not None:
            self._maybe_grant_admin(user)

    def _maybe_grant_admin(self, user: object) -> None:
        """Check user's email domain and grant admin if it matches ADMIN_DOMAINS.

        Skips the database save if the user already has both ``is_staff``
        and ``is_superuser`` set to ``True``.
        """
        admin_domains: list[str] = getattr(settings, "ADMIN_DOMAINS", [])
        if not admin_domains:
            return

        email: str = getattr(user, "email", "") or ""
        if not email or "@" not in email:
            return

        domain = email.rsplit("@", 1)[1].lower()
        if domain in admin_domains:
            if user.is_staff and user.is_superuser:  # type: ignore[attr-defined]
                return
            user.is_staff = True  # type: ignore[attr-defined]
            user.is_superuser = True  # type: ignore[attr-defined]
            user.save(update_fields=["is_staff", "is_superuser"])  # type: ignore[attr-defined]
            logger.info("Auto-admin granted to %s (domain: %s)", email, domain)


class AdminRedirectAccountAdapter(DefaultAccountAdapter):
    """Redirect staff users to /admin/ after login when no explicit next URL is set."""

    def get_login_redirect_url(self, request: HttpRequest) -> str:
        if request.user.is_staff:
            return "/admin/"
        return super().get_login_redirect_url(request)
