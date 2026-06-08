"""Custom allauth adapter and forms for auto-admin domain privileges and login redirect."""

from __future__ import annotations

import logging
from typing import Any

from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.forms import RequestLoginCodeForm
from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone

from core import abuse_limits

logger = logging.getLogger(__name__)
User = get_user_model()

LOGIN_CODE_TEMPLATE = "account/email/login_code"


class AdminRedirectAccountAdapter(DefaultAccountAdapter):
    """Grant admin privileges on login and redirect staff users to /admin/.

    On every login, if the user's email domain matches any domain in the
    ADMIN_DOMAINS setting (case-insensitive), the user gets is_staff=True
    and is_superuser=True.

    After login, staff users are redirected to the admin panel; everyone
    else goes to the member hub.

    Signup gating: when registration_mode is invite_only, only emails with
    a pending Invite record can sign up.
    """

    def is_open_for_signup(self, request: HttpRequest) -> bool:
        """Check whether signup is allowed for the current request.

        Public/book surface: always open. A book account is just a way to
        view your past class registrations — invite-only is a members-surface
        concept and doesn't apply here.

        Members surface: open mode allows everyone; invite-only mode checks
        whether the email from POST or GET data has a pending invite.
        """
        if getattr(request, "surface", "members") == "public":
            return True

        from core.models import Invite, SiteConfiguration

        config = SiteConfiguration.load()
        if config.registration_mode == SiteConfiguration.RegistrationMode.OPEN:
            return True

        email = request.POST.get("email", "") or request.GET.get("email", "")
        if not email:
            return False

        return Invite.objects.filter(email__iexact=email, accepted_at__isnull=True).exists()

    def login(self, request: HttpRequest, user: object) -> None:
        """Sync permissions from Member role (and admin-domain override), then log in."""
        self._sync_permissions(user)
        super().login(request, user)

    def pre_login(
        self,
        request: HttpRequest,
        user: object,
        *,
        email_verification: Any = None,
        signal_kwargs: Any = None,
        email: str | None = None,
        signup: bool = False,
        redirect_url: str | None = None,
    ) -> Any:
        """Mark matching invite as accepted when a new user signs up."""
        if signup:
            from core.models import Invite

            user_email: str = getattr(user, "email", "") or ""
            if user_email:
                Invite.objects.filter(email__iexact=user_email, accepted_at__isnull=True).update(
                    accepted_at=timezone.now()
                )

        return super().pre_login(
            request,
            user,
            email_verification=email_verification,
            signal_kwargs=signal_kwargs,
            email=email,
            signup=signup,
            redirect_url=redirect_url,
        )

    def get_login_redirect_url(self, request: HttpRequest) -> str:
        """Land on the right place based on surface and onboarding status.

        - Public/book surface, user not yet onboarded → start onboarding wizard.
        - Public/book surface, user onboarded → /account/ overview.
        - Members surface (anywhere else) → Community Calendar (existing behavior).
        """
        surface = getattr(request, "surface", "members")
        if surface == "public":
            from core.models import UserProfile

            profile = UserProfile.objects.filter(user=request.user).first()  # type: ignore[misc]
            if profile is None or not profile.is_onboarded:
                return reverse("account:onboarding_step1")
            return reverse("account:overview")
        return reverse("hub_community_calendar")

    def send_mail(self, template_prefix: str, email: str, context: dict) -> None:
        """Gate login-code emails through the global circuit breaker, then send.

        In DEBUG mode, also stash the login code on the request for display in
        the UI so devs don't have to copy it out of the console.
        """
        if template_prefix == LOGIN_CODE_TEMPLATE:
            hourly_limit = getattr(settings, "LOGIN_CODE_HOURLY_LIMIT", 100)
            daily_limit = getattr(settings, "LOGIN_CODE_DAILY_LIMIT", 500)
            allowed, reason = abuse_limits.record_send_attempt(hourly_limit=hourly_limit, daily_limit=daily_limit)
            if not allowed:
                logger.error(
                    "Login-code circuit breaker tripped (%s cap) — suppressing send to %s",
                    reason,
                    email,
                )
                return

        if settings.DEBUG and template_prefix == LOGIN_CODE_TEMPLATE and "code" in context:
            from allauth.core import context as allauth_context
            from django.contrib import messages as django_messages

            request = allauth_context.request
            if request:
                django_messages.success(request, f"[DEV] Login code: {context['code']}")
        super().send_mail(template_prefix, email, context)

    def _sync_permissions(self, user: object) -> None:
        """Sync is_staff/is_superuser from the user's Member fog_role.

        Priority order:
        1. ADMIN_DOMAINS override — matching email domain always gets full admin.
        2. fog_role mapping — admin → full access, guild_officer → staff only.
        3. Everyone else — no staff access (member hub only).
        """
        from membership.models import Member

        # 1. ADMIN_DOMAINS override (e.g. @plaza.codes always gets superuser)
        admin_domains: list[str] = getattr(settings, "ADMIN_DOMAINS", [])
        email: str = getattr(user, "email", "") or ""
        if admin_domains and email and "@" in email:
            domain = email.rsplit("@", 1)[1].lower()
            if domain in admin_domains:
                if not (user.is_staff and user.is_superuser):  # type: ignore[attr-defined]
                    user.is_staff = True  # type: ignore[attr-defined]
                    user.is_superuser = True  # type: ignore[attr-defined]
                    user.save(update_fields=["is_staff", "is_superuser"])  # type: ignore[attr-defined]
                    logger.info("Auto-admin granted to %s (domain: %s)", email, domain)
                return

        # 2. Sync from Member fog_role
        member: Member | None = getattr(user, "member", None)
        if member is not None:
            member.sync_user_permissions()
            logger.info("Permissions synced for %s (fog_role: %s)", email, member.fog_role)


class AutoCreateUserLoginCodeForm(RequestLoginCodeForm):
    """Extend the login-by-code form to auto-create a User for known Members.

    When a member enters their email on the login page and no User exists,
    but a Member record does exist (from Airtable sync or admin invite),
    auto-create the User so they can receive a login code immediately.
    The post_save signal (ensure_user_has_member) links the Member automatically.

    Also carries a honeypot field: a hidden 'website' input that real browsers
    won't populate. Bots that auto-fill every field trip the same validation
    error allauth uses for rate-limited submissions, so the response stays
    indistinguishable from a normal throttle.
    """

    website = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={"autocomplete": "off", "tabindex": "-1", "aria-hidden": "true"},
        ),
        label="",
    )

    def clean_website(self) -> str:
        value: str = self.cleaned_data.get("website", "") or ""
        if value.strip():
            from allauth.account.adapter import get_adapter

            logger.warning("Login honeypot triggered (value=%r)", value[:80])
            raise get_adapter().validation_error("too_many_login_attempts")
        return value

    def clean_email(self) -> str:
        """Auto-create User for known Members, then run normal allauth lookup."""
        from membership.models import Member, MemberEmail

        email: str = self.cleaned_data.get("email", "")
        if email and not User.objects.filter(email__iexact=email).exists():
            # Check primary email on Member
            if Member.objects.filter(_pre_signup_email__iexact=email, user__isnull=True).exists():
                User.objects.create_user(username=email, email=email)
                logger.info("Auto-created User for existing Member (primary email): %s", email)
            else:
                # Check email aliases
                try:
                    alias = MemberEmail.objects.select_related("member").get(
                        email__iexact=email, member__user__isnull=True
                    )
                    User.objects.create_user(username=email, email=email)
                    logger.info(
                        "Auto-created User for existing Member (alias email): %s -> %s",
                        email,
                        alias.member.display_name,
                    )
                except MemberEmail.DoesNotExist:
                    pass

        return super().clean_email()
