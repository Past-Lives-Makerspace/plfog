"""Custom allauth adapter and forms for auto-admin domain privileges and login redirect."""

from __future__ import annotations

import logging
import os
from typing import Any

from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.forms import ConfirmLoginCodeForm, RequestLoginCodeForm, SignupForm
from allauth.core.internal.cryptokit import compare_user_code
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

# Map an allauth template prefix to a stable ``trigger_kind`` audit label for the
# choke-point (Decision 8). Anything not listed falls back to ``auth.<leaf>``.
_AUTH_TRIGGER_KINDS: dict[str, str] = {
    "account/email/login_code": "auth.login_code",
    "account/email/unknown_account": "auth.unknown_account",
    "account/email/account_already_exists": "auth.account_already_exists",
}


def _auth_trigger_kind(template_prefix: str) -> str:
    """Derive a ``TransactionalEmailLog`` trigger label from an allauth template prefix.

    Known auth templates get a curated label; anything else gets ``auth.<leaf>``
    (the final path segment) so a future allauth email is still audited sensibly
    without a code change.
    """
    if template_prefix in _AUTH_TRIGGER_KINDS:
        return _AUTH_TRIGGER_KINDS[template_prefix]
    leaf = template_prefix.rstrip("/").rsplit("/", 1)[-1] or template_prefix
    return f"auth.{leaf}"


def _extract_bodies(msg: object) -> tuple[str, str | None]:
    """Pull the plaintext + optional HTML body out of an allauth-rendered message.

    allauth's ``render_mail`` returns an ``EmailMultiAlternatives`` whose ``body``
    is the plaintext and whose ``alternatives`` carry the ``text/html`` part (when
    a ``*_message.html`` template exists). When only an HTML template exists,
    ``render_mail`` returns an ``EmailMessage`` with ``content_subtype == "html"``
    and the HTML in ``body`` — in that case the HTML is both the text and html body
    (the choke-point still renders the HTML alternative).
    """
    body: str = getattr(msg, "body", "") or ""
    alternatives = list(getattr(msg, "alternatives", None) or [])
    html_body: str | None = None
    for content, mimetype in alternatives:
        if mimetype == "text/html":
            html_body = content
            break
    if html_body is None and getattr(msg, "content_subtype", "") == "html":
        # HTML-only message: the HTML lives in ``body``.
        return body, body
    return body, html_body


class AdminRedirectAccountAdapter(DefaultAccountAdapter):
    """Grant admin privileges on login and route each surface to its landing page.

    On every login, if the user's email domain matches any domain in the
    ADMIN_DOMAINS setting (case-insensitive), the user gets is_staff=True
    and is_superuser=True. Plus-addressed emails are exempt from that grant
    (see ``_sync_permissions``) so demo/training aliases keep their assigned
    fog_role.

    After login (no ``?next=``), the public/book surface lands on the /account/
    overview; the members surface lands on the member Home — except a member who
    has never chosen their guild updates, who gets the one-time
    /welcome/guild-updates/ prompt; any other surface (e.g. the guilds guest
    host) lands on the member Home. See ``get_login_redirect_url``.

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

    def save_user(self, request: HttpRequest, user: object, form: Any, commit: bool = True) -> object:
        from core.allauth_state import enter_allauth_signup, exit_allauth_signup

        enter_allauth_signup()
        try:
            return super().save_user(request, user, form, commit=commit)
        finally:
            exit_allauth_signup()

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
        """Land on the right place based on surface.

        - Public/book surface → /account/ overview (no onboarding step).
        - Members surface: a member who has never chosen their guild updates (no
          answered-stamp, no subscriptions) → the one-time guild updates prompt;
          everyone else → the member Home / Dashboard. Known, accepted gap: allauth
          only consults this when no ``?next=`` is present, so a deep-linked login
          skips the prompt that session (the onboarding checklist is the backstop).
        - Any other surface (e.g. the guilds guest host) → the member Home /
          Dashboard, never the prompt: the interstitial renders members-hub chrome
          and its URL is not in the guilds-host allowlist, so routing there from
          the guest surface would 404.
        """
        surface = getattr(request, "surface", "members")
        if surface == "public":
            return reverse("account:overview")
        if surface == "members":
            # Same resolution pattern as hub.views._get_member: the reverse one-to-one
            # accessor, AttributeError-safe (RelatedObjectDoesNotExist subclasses it).
            member = getattr(request.user, "member", None)
            if member is not None and member.needs_guild_updates_prompt:
                return reverse("hub_guild_updates_prompt")
        return reverse("hub_home")

    def send_mail(self, template_prefix: str, email: str, context: dict) -> None:
        """Gate login-code emails through the global circuit breaker, then send.

        Routes the actual transport through the ``core.email.send`` choke-point
        (Decision 8) so every auth email — login codes, unknown-account, and
        account-already-exists — writes a ``TransactionalEmailLog`` row instead of
        bypassing the audit log via allauth's own ``msg.send()``. allauth's own
        templates + subject formatting are preserved (we still render through
        ``render_mail``); only the final send is re-pointed.

        The abuse/rate-limit circuit breaker and the DEBUG on-screen login-code
        toast keep working exactly as before — they run before the (now audited)
        send, and a tripped breaker still suppresses the send entirely.

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

        self._send_through_choke_point(template_prefix, email, context)

    def _send_through_choke_point(self, template_prefix: str, email: str, context: dict) -> None:
        """Render the allauth message and deliver it via ``core.email.send``.

        Mirrors allauth's :meth:`DefaultAccountAdapter.send_mail` exactly (same
        context enrichment, same ``render_mail`` templates/subject), but hands the
        rendered subject/text/html to the audited choke-point so a
        ``TransactionalEmailLog`` row is written. The ``trigger_kind`` is derived
        from the template prefix (``account/email/login_code`` → ``auth.login_code``)
        so the audit log distinguishes login codes from unknown-account / already-
        exists mail.

        Best-effort: an SMTP failure is logged (FAILED row) but never raised into
        the auth flow — a failed login-code email must not 500 the login page.
        """
        from allauth.core import context as allauth_context

        from core.email import send as send_email

        request = getattr(allauth_context, "request", None)
        from django.contrib.sites.shortcuts import get_current_site

        ctx = {
            "request": request,
            "email": email,
            "current_site": get_current_site(request),
        }
        # Absolute self-serve links for the auth email templates (relative {% url %}
        # dead-ends in a mail client). Only built when there's a request to anchor the
        # host on; the templates guard each link with {% if %} so a missing one is a no-op.
        if request is not None:
            ctx["login_url"] = request.build_absolute_uri(reverse("account_request_login_code"))
            ctx["find_account_url"] = request.build_absolute_uri(reverse("find_account"))
        ctx.update(context)
        msg = self.render_mail(template_prefix, email, ctx)
        text_body, html_body = _extract_bodies(msg)
        send_email(
            to=list(msg.to),
            subject=msg.subject,
            trigger_kind=_auth_trigger_kind(template_prefix),
            text_body=text_body,
            html_body=html_body,
            from_email=msg.from_email,
            best_effort=True,
        )

    def _sync_permissions(self, user: object) -> None:
        """Sync is_staff/is_superuser from the user's Member fog_role.

        Priority order:
        1. ADMIN_DOMAINS override — matching email domain always gets full admin.
           Plus-addressed emails (a ``+`` in the local part) are exempt: staff
           primary addresses never carry a subaddress, and the aliases are used
           for role-accurate demo/training accounts that must keep exactly the
           fog_role they were assigned.
        2. fog_role mapping — admin → full access, guild_officer → staff only.
        3. Everyone else — no staff access (member hub only).
        """
        from membership.models import Member

        # 1. ADMIN_DOMAINS override (e.g. @plaza.codes always gets superuser)
        admin_domains: list[str] = getattr(settings, "ADMIN_DOMAINS", [])
        email: str = getattr(user, "email", "") or ""
        if admin_domains and email and "@" in email:
            local_part, domain = email.rsplit("@", 1)
            domain = domain.lower()
            if domain in admin_domains and "+" not in local_part:
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


class MarketingOptInSignupForm(SignupForm):
    """Collect the signer-up's name and an opt-in marketing checkbox at signup.

    Name collection (issue #274): allauth's bare email signup used to leave
    ``User.first_name``/``last_name`` empty, so the Member post-save signal fell
    back to the auto-generated username — the lowercased email local part — as
    ``Member.full_legal_name``, and that garbage rendered in rosters. The form
    now requires a full name (and optionally the name the person goes by, which
    becomes ``Member.preferred_name``). ``clean`` splits the full name into
    ``first_name``/``last_name`` in ``cleaned_data`` so allauth's
    ``DefaultAccountAdapter.save_user`` stamps them on the User *before* the
    post-save signal creates or links the Member; ``save`` then re-stamps the
    Member with the exact typed string (the split-and-rejoin would collapse
    interior whitespace) and the preferred name.

    The newsletter opt-in mirrors the class-registration form
    (``classes.forms.RegistrationForm.wants_newsletter``) with the same
    "unchecked means nothing happens" default. The push is deliberately
    best-effort and runs *after* the user is committed: Mailchimp being down
    must never cost someone their account.
    """

    # Capped at 150 so each split half always fits User.first_name/last_name
    # (both varchar(150) — a longer value would raise DataError on Postgres).
    full_name = forms.CharField(
        max_length=150,
        label="Full name",
        widget=forms.TextInput(attrs={"autocomplete": "name"}),
    )
    preferred_name = forms.CharField(
        max_length=255,
        required=False,
        label="Name you go by",
        widget=forms.TextInput(attrs={"autocomplete": "nickname"}),
    )
    wants_newsletter = forms.BooleanField(
        required=False,
        initial=False,
        label="Email me about new classes, workshops, and events at Past Lives.",
    )

    def clean(self) -> dict[str, Any]:
        """Split the collected full name into first/last for allauth's ``save_user``.

        ``DefaultAccountAdapter.save_user`` reads ``cleaned_data["first_name"]`` and
        ``["last_name"]``, so populating them here gets the name onto the User before
        it is first saved — which is what the Member-creating post-save signal reads
        via ``get_full_name()``. A mononym lands entirely in ``first_name``.
        """
        cleaned: dict[str, Any] = super().clean()
        full_name = (cleaned.get("full_name") or "").strip()
        if full_name:
            cleaned["full_name"] = full_name
            first, _, last = full_name.partition(" ")
            cleaned["first_name"] = first
            cleaned["last_name"] = last.strip()
        return cleaned

    def save(self, request: HttpRequest) -> Any:
        user = super().save(request)
        self._stamp_member_names(user)
        # A required=False BooleanField always lands in cleaned_data on a valid
        # form, so index rather than .get() — a missing key is a bug, not a default.
        if self.cleaned_data["wants_newsletter"]:
            from core.services.mailchimp_account import subscribe_user

            try:
                subscribe_user(user)
            except Exception:  # noqa: BLE001 - a newsletter opt-in must never cost someone their account
                logger.exception("Mailchimp signup opt-in failed for %s; account stands.", user.email)
        return user

    def _stamp_member_names(self, user: Any) -> None:
        """Write the collected names onto the Member the post-save signal created or linked.

        The first/last split (see ``clean``) already seeded ``full_legal_name`` via
        the signal; re-stamping with the raw form value preserves the exact string
        the person typed, wins over an invite placeholder's email-as-name, and
        carries the optional preferred name (which the User model has no home for).
        A typed preferred name wins; a blank one leaves any existing value alone.
        """
        # Same AttributeError-safe reverse one-to-one access as get_login_redirect_url.
        member = getattr(user, "member", None)
        if member is None:
            # The signal couldn't create a Member (no MembershipPlan seeded) — it
            # already logged; the account itself stands.
            return
        update_fields = ["full_legal_name"]
        member.full_legal_name = self.cleaned_data["full_name"]
        preferred = self.cleaned_data["preferred_name"].strip()
        if preferred:
            member.preferred_name = preferred
            update_fields.append("preferred_name")
        member.save(update_fields=update_fields)


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

    @staticmethod
    def _create_user_idempotent(email: str) -> None:
        """Create a passwordless User, tolerating a concurrent create.

        Two login-code requests for the same new email (e.g. a double-clicked
        submit) can both pass the existence check in ``clean_email``; the
        username unique constraint then makes the loser raise IntegrityError.
        Swallow it — the user exists either way — instead of surfacing a 500.
        """
        from django.db import IntegrityError

        try:
            User.objects.create_user(username=email, email=email)
        except IntegrityError:
            logger.info("Login-code user already created concurrently for %s", email)

    def clean_email(self) -> str:
        """Auto-create User for known Members, then run normal allauth lookup."""
        from membership.models import Member, MemberEmail

        email: str = self.cleaned_data.get("email", "")
        if email and not User.objects.filter(email__iexact=email).exists():
            # Check primary email on Member
            if Member.objects.filter(_pre_signup_email__iexact=email, user__isnull=True).exists():
                self._create_user_idempotent(email)
                logger.info("Auto-created User for existing Member (primary email): %s", email)
            else:
                # Check email aliases
                try:
                    alias = MemberEmail.objects.select_related("member").get(
                        email__iexact=email, member__user__isnull=True
                    )
                    self._create_user_idempotent(email)
                    logger.info(
                        "Auto-created User for existing Member (alias email): %s -> %s",
                        email,
                        alias.member.display_name,
                    )
                except MemberEmail.DoesNotExist:
                    pass

        return super().clean_email()


class GoldenTicketConfirmLoginCodeForm(ConfirmLoginCodeForm):
    """Accept a single fixed login code for any account that requested one.

    App-store reviewers cannot receive our emailed one-time codes: review is
    asynchronous and they cannot read a member's inbox. When ``PLAY_REVIEW_CODE``
    is set, that value is accepted *in addition to* the real emailed code, for
    whichever account is mid-login. Nothing about code generation changes —
    every member still gets a random emailed code, and the fixed value is never
    sent to anyone.

    Checking at verification time rather than generation time is what makes this
    robust: it holds no matter how the code was issued, so "Resend code" can no
    longer strand a reviewer with a code that silently stopped working.

    SECURITY: this is a master key. Anyone holding ``PLAY_REVIEW_CODE`` can sign
    in as any account that has an email address, including addresses on
    ``ADMIN_DOMAINS``, which :meth:`AdminRedirectAccountAdapter._sync_permissions`
    auto-grants ``is_staff`` and ``is_superuser``. Treat the value like a root
    password: long, random, set only in the deploy environment, and rotated the
    moment app-store review is finished. Leaving the env var unset disables the
    behaviour entirely.
    """

    @staticmethod
    def _pending_login_user() -> Any | None:
        """The user allauth is mid-login for, or None if there isn't one.

        Deliberately *not* keyed off ``expected_code``: whether a code was
        successfully generated and stashed is exactly the thing that can go wrong,
        and the golden ticket has to keep working when it does.
        """
        from allauth.core import context as allauth_context

        request = getattr(allauth_context, "request", None)
        stage = getattr(request, "_login_stage", None)
        login = getattr(stage, "login", None)
        return getattr(login, "user", None)

    def clean_code(self) -> str:
        """Accept the golden ticket, otherwise fall back to allauth's own check."""
        code: str = self.cleaned_data["code"]
        golden = os.environ.get("PLAY_REVIEW_CODE", "").strip()

        if golden and compare_user_code(actual=code, expected=golden):
            # Requiring a pending user keeps the enumeration-prevention path (unknown
            # email -> fake process with no user) from reaching a login with nobody to
            # log in as, which would otherwise assert deep inside allauth.
            user = self._pending_login_user()
            if user is not None and user.is_active:
                # Defense in depth: never hand the master key to a deactivated account
                # (e.g. one that self-service-deleted). ``pre_login`` already blocks
                # inactive users, but the guard keeps the golden path from ever
                # authenticating one.
                # A master key that leaves no trace is not auditable. Name the account
                # every time it is used, at WARNING so it survives prod log levels.
                logger.warning(
                    "Golden-ticket login code accepted for %s (pk=%s)",
                    getattr(user, "email", "") or "<no email>",
                    getattr(user, "pk", "?"),
                )
                return code

        return super().clean_code()
