"""BDD-style tests for plfog.adapters module — auto-admin, admin redirect, and signup gating."""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Invite, SiteConfiguration
from membership.models import Member

pytestmark = pytest.mark.django_db


def _make_request_with_user(rf: RequestFactory, *, is_staff: bool, is_superuser: bool) -> object:
    """Create a GET request with an attached user having the given flags."""
    request = rf.get("/accounts/login/")
    user = MagicMock()
    user.is_staff = is_staff
    user.is_superuser = is_superuser
    # No linked Member — the guild-updates prompt routing only fires for real members.
    user.member = None
    request.user = user
    return request


def _create_user_with_fog_role(username: str, fog_role: str) -> User:
    """Create a User (which auto-creates a Member via signal), then set the fog_role."""
    user = User.objects.create_user(username=username, email=f"{username}@other.com", password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    return user


def describe_AdminRedirectAccountAdapter():
    def describe_login():
        def it_calls_sync_permissions_then_super_login(rf):
            from plfog.adapters import AdminRedirectAccountAdapter

            adapter = AdminRedirectAccountAdapter()
            request = rf.get("/")
            user = MagicMock()
            user.email = "admin@example.com"
            user.is_staff = False
            user.is_superuser = False

            with patch.object(
                AdminRedirectAccountAdapter.__bases__[0],
                "login",
            ) as mock_super_login:
                adapter._sync_permissions = MagicMock()  # type: ignore[method-assign]
                adapter.login(request, user)

                adapter._sync_permissions.assert_called_once_with(user)
                mock_super_login.assert_called_once_with(request, user)

        def it_grants_admin_before_login_for_matching_domain(rf, settings):
            from plfog.adapters import AdminRedirectAccountAdapter

            settings.ADMIN_DOMAINS = ["example.com"]
            adapter = AdminRedirectAccountAdapter()
            request = rf.get("/")

            user = User.objects.create_user(
                username="admin",
                email="admin@example.com",
                password="testpass",
            )
            assert user.is_staff is False

            with patch.object(
                AdminRedirectAccountAdapter.__bases__[0],
                "login",
            ):
                adapter.login(request, user)

            user.refresh_from_db()
            assert user.is_staff is True
            assert user.is_superuser is True

    def describe_get_login_redirect_url():
        def it_lands_staff_on_hub_home(rf):
            from plfog.adapters import AdminRedirectAccountAdapter

            adapter = AdminRedirectAccountAdapter()
            request = _make_request_with_user(rf, is_staff=True, is_superuser=False)

            url = adapter.get_login_redirect_url(request)

            assert url == reverse("hub_home")

        def it_lands_non_staff_on_hub_home(rf):
            from plfog.adapters import AdminRedirectAccountAdapter

            adapter = AdminRedirectAccountAdapter()
            request = _make_request_with_user(rf, is_staff=False, is_superuser=False)

            url = adapter.get_login_redirect_url(request)

            assert url == reverse("hub_home")

        def it_lands_superusers_on_hub_home(rf):
            from plfog.adapters import AdminRedirectAccountAdapter

            adapter = AdminRedirectAccountAdapter()
            request = _make_request_with_user(rf, is_staff=True, is_superuser=True)

            url = adapter.get_login_redirect_url(request)

            assert url == reverse("hub_home")

        @override_settings(LOGIN_REDIRECT_URL="/dashboard/")
        def it_ignores_custom_url_setting(rf):
            from plfog.adapters import AdminRedirectAccountAdapter

            adapter = AdminRedirectAccountAdapter()
            request = _make_request_with_user(rf, is_staff=True, is_superuser=False)

            url = adapter.get_login_redirect_url(request)

            assert url == reverse("hub_home")

    def describe_sync_permissions():
        """Tests for _sync_permissions — ADMIN_DOMAINS override + Member role mapping."""

        def describe_admin_domain_override():
            def it_sets_is_staff_and_is_superuser(settings):
                from plfog.adapters import AdminRedirectAccountAdapter

                settings.ADMIN_DOMAINS = ["example.com"]
                adapter = AdminRedirectAccountAdapter()

                user = MagicMock()
                user.email = "admin@example.com"
                user.is_staff = False
                user.is_superuser = False
                user.member = None  # no member attr needed for domain override
                adapter._sync_permissions(user)

                assert user.is_staff is True
                assert user.is_superuser is True
                user.save.assert_called_once_with(update_fields=["is_staff", "is_superuser"])

            def it_does_not_grant_admin_with_empty_admin_domains(settings):
                from plfog.adapters import AdminRedirectAccountAdapter

                settings.ADMIN_DOMAINS = []
                adapter = AdminRedirectAccountAdapter()

                user = MagicMock()
                user.email = "user@example.com"
                user.is_staff = False
                user.is_superuser = False
                user.member = None
                adapter._sync_permissions(user)

                user.save.assert_not_called()

            def it_does_not_grant_admin_when_domain_does_not_match(settings):
                from plfog.adapters import AdminRedirectAccountAdapter

                settings.ADMIN_DOMAINS = ["example.com"]
                adapter = AdminRedirectAccountAdapter()

                user = MagicMock()
                user.email = "user@other.com"
                user.is_staff = False
                user.is_superuser = False
                user.member = None
                adapter._sync_permissions(user)

                user.save.assert_not_called()

            def it_grants_admin_for_any_matching_domain(settings):
                from plfog.adapters import AdminRedirectAccountAdapter

                settings.ADMIN_DOMAINS = ["pastlives.space", "roaming-panda.com"]
                adapter = AdminRedirectAccountAdapter()

                user = MagicMock()
                user.email = "mark@roaming-panda.com"
                user.is_staff = False
                user.is_superuser = False
                adapter._sync_permissions(user)

                assert user.is_staff is True
                assert user.is_superuser is True

            def it_matches_uppercase_email_domain(settings):
                from plfog.adapters import AdminRedirectAccountAdapter

                settings.ADMIN_DOMAINS = ["pastlives.space"]
                adapter = AdminRedirectAccountAdapter()

                user = MagicMock()
                user.email = "User@PASTLIVES.SPACE"
                user.is_staff = False
                user.is_superuser = False
                adapter._sync_permissions(user)

                assert user.is_staff is True
                assert user.is_superuser is True

            def it_exempts_plus_addressed_emails_from_the_domain_grant(settings):
                from plfog.adapters import AdminRedirectAccountAdapter

                settings.ADMIN_DOMAINS = ["pastlives.space"]
                adapter = AdminRedirectAccountAdapter()

                user = MagicMock()
                user.email = "counciltreasurer+member@pastlives.space"
                user.is_staff = False
                user.is_superuser = False
                user.member = None
                adapter._sync_permissions(user)

                user.save.assert_not_called()

            def it_syncs_fog_role_for_plus_addressed_emails_on_an_admin_domain(settings):
                """A plus-alias falls through to the Member fog_role mapping instead of the grant."""
                from plfog.adapters import AdminRedirectAccountAdapter

                settings.ADMIN_DOMAINS = ["pastlives.space"]
                adapter = AdminRedirectAccountAdapter()

                user = MagicMock()
                user.email = "counciltreasurer+admin@pastlives.space"
                user.is_staff = False
                user.is_superuser = False
                adapter._sync_permissions(user)

                user.member.sync_user_permissions.assert_called_once_with()

            def it_does_not_match_subdomains(settings):
                from plfog.adapters import AdminRedirectAccountAdapter

                settings.ADMIN_DOMAINS = ["example.com"]
                adapter = AdminRedirectAccountAdapter()

                user = MagicMock()
                user.email = "user@sub.example.com"
                user.is_staff = False
                user.is_superuser = False
                user.member = None
                adapter._sync_permissions(user)

                user.save.assert_not_called()

            def it_skips_save_when_user_already_has_admin_privileges(settings):
                from plfog.adapters import AdminRedirectAccountAdapter

                settings.ADMIN_DOMAINS = ["example.com"]
                adapter = AdminRedirectAccountAdapter()

                user = MagicMock()
                user.email = "admin@example.com"
                user.is_staff = True
                user.is_superuser = True
                adapter._sync_permissions(user)

                user.save.assert_not_called()

            def it_upgrades_when_only_is_staff_is_true(settings):
                from plfog.adapters import AdminRedirectAccountAdapter

                settings.ADMIN_DOMAINS = ["example.com"]
                adapter = AdminRedirectAccountAdapter()

                user = MagicMock()
                user.email = "admin@example.com"
                user.is_staff = True
                user.is_superuser = False
                adapter._sync_permissions(user)

                assert user.is_staff is True
                assert user.is_superuser is True
                user.save.assert_called_once_with(update_fields=["is_staff", "is_superuser"])

            def it_upgrades_when_only_is_superuser_is_true(settings):
                from plfog.adapters import AdminRedirectAccountAdapter

                settings.ADMIN_DOMAINS = ["example.com"]
                adapter = AdminRedirectAccountAdapter()

                user = MagicMock()
                user.email = "admin@example.com"
                user.is_staff = False
                user.is_superuser = True
                adapter._sync_permissions(user)

                assert user.is_staff is True
                assert user.is_superuser is True
                user.save.assert_called_once_with(update_fields=["is_staff", "is_superuser"])

            def it_skips_user_with_empty_email(settings):
                from plfog.adapters import AdminRedirectAccountAdapter

                settings.ADMIN_DOMAINS = ["example.com"]
                adapter = AdminRedirectAccountAdapter()

                user = MagicMock()
                user.email = ""
                user.is_staff = False
                user.is_superuser = False
                user.member = None
                adapter._sync_permissions(user)

                user.save.assert_not_called()

            def it_skips_user_with_email_without_at_sign(settings):
                from plfog.adapters import AdminRedirectAccountAdapter

                settings.ADMIN_DOMAINS = ["example.com"]
                adapter = AdminRedirectAccountAdapter()

                user = MagicMock()
                user.email = "noemail"
                user.is_staff = False
                user.is_superuser = False
                user.member = None
                adapter._sync_permissions(user)

                user.save.assert_not_called()

            def it_skips_user_with_none_email(settings):
                from plfog.adapters import AdminRedirectAccountAdapter

                settings.ADMIN_DOMAINS = ["example.com"]
                adapter = AdminRedirectAccountAdapter()

                user = MagicMock()
                user.email = None
                user.is_staff = False
                user.is_superuser = False
                user.member = None
                adapter._sync_permissions(user)

                user.save.assert_not_called()

            def it_skips_when_admin_domains_not_configured(settings):
                from plfog.adapters import AdminRedirectAccountAdapter

                if hasattr(settings, "ADMIN_DOMAINS"):
                    delattr(settings, "ADMIN_DOMAINS")
                adapter = AdminRedirectAccountAdapter()

                user = MagicMock()
                user.email = "user@example.com"
                user.is_staff = False
                user.is_superuser = False
                user.member = None
                adapter._sync_permissions(user)

                user.save.assert_not_called()

        def describe_fog_role_mapping():
            def it_grants_full_admin_for_admin_fog_role(settings):
                from plfog.adapters import AdminRedirectAccountAdapter

                settings.ADMIN_DOMAINS = []
                adapter = AdminRedirectAccountAdapter()

                user = _create_user_with_fog_role("adm", "admin")
                assert user.is_staff is False
                assert user.is_superuser is False

                adapter._sync_permissions(user)
                user.refresh_from_db()

                assert user.is_staff is True
                assert user.is_superuser is True

            def it_grants_staff_only_for_guild_officer(settings):
                from plfog.adapters import AdminRedirectAccountAdapter

                settings.ADMIN_DOMAINS = []
                adapter = AdminRedirectAccountAdapter()

                user = _create_user_with_fog_role("go", "guild_officer")

                adapter._sync_permissions(user)
                user.refresh_from_db()

                assert user.is_staff is True
                assert user.is_superuser is False

            def it_removes_staff_for_member_fog_role(settings):
                from plfog.adapters import AdminRedirectAccountAdapter

                settings.ADMIN_DOMAINS = []
                adapter = AdminRedirectAccountAdapter()

                user = _create_user_with_fog_role("std", "member")
                user.is_staff = True
                user.is_superuser = True
                user.save()

                adapter._sync_permissions(user)
                user.refresh_from_db()

                assert user.is_staff is False
                assert user.is_superuser is False

            def it_does_not_save_when_permissions_already_match(settings):
                from plfog.adapters import AdminRedirectAccountAdapter

                settings.ADMIN_DOMAINS = []
                adapter = AdminRedirectAccountAdapter()

                user = _create_user_with_fog_role("nosave", "admin")
                user.is_staff = True
                user.is_superuser = True
                user.save()

                with patch.object(User, "save") as mock_save:
                    adapter._sync_permissions(user)
                    mock_save.assert_not_called()

            def it_handles_user_with_no_member(settings):
                from plfog.adapters import AdminRedirectAccountAdapter

                settings.ADMIN_DOMAINS = []
                adapter = AdminRedirectAccountAdapter()

                user = User.objects.create_user(username="nomember", email="nomember@other.com", password="pass")
                # Delete the auto-created member via signal
                Member.objects.filter(user=user).delete()
                # Clear cached property
                try:
                    del user.member
                except AttributeError:
                    pass

                adapter._sync_permissions(user)
                user.refresh_from_db()

                # No member, no domain match → no change
                assert user.is_staff is False
                assert user.is_superuser is False

            def it_admin_domain_takes_precedence_over_member_fog_role(settings):
                from plfog.adapters import AdminRedirectAccountAdapter

                settings.ADMIN_DOMAINS = ["example.com"]
                adapter = AdminRedirectAccountAdapter()

                user = User.objects.create_user(username="domwin", email="domwin@example.com", password="pass")
                # fog_role defaults to "member" — domain override should still grant admin

                adapter._sync_permissions(user)
                user.refresh_from_db()

                # Domain override wins — still gets admin despite member fog_role
                assert user.is_staff is True
                assert user.is_superuser is True

    def describe_sync_permissions_logging():
        def it_logs_when_admin_is_granted_via_domain(settings, caplog):
            from plfog.adapters import AdminRedirectAccountAdapter

            settings.ADMIN_DOMAINS = ["example.com"]
            adapter = AdminRedirectAccountAdapter()

            user = MagicMock()
            user.email = "admin@example.com"
            user.is_staff = False
            user.is_superuser = False

            with caplog.at_level(logging.INFO, logger="plfog.adapters"):
                adapter._sync_permissions(user)

            assert "Auto-admin granted to admin@example.com" in caplog.text
            assert "domain: example.com" in caplog.text

        def it_does_not_log_when_already_admin(settings, caplog):
            from plfog.adapters import AdminRedirectAccountAdapter

            settings.ADMIN_DOMAINS = ["example.com"]
            adapter = AdminRedirectAccountAdapter()

            user = MagicMock()
            user.email = "admin@example.com"
            user.is_staff = True
            user.is_superuser = True

            with caplog.at_level(logging.INFO, logger="plfog.adapters"):
                adapter._sync_permissions(user)

            assert "Auto-admin granted" not in caplog.text

        def it_logs_when_syncing_from_fog_role(settings, caplog):
            from plfog.adapters import AdminRedirectAccountAdapter

            settings.ADMIN_DOMAINS = []
            adapter = AdminRedirectAccountAdapter()

            user = _create_user_with_fog_role("logtest", "guild_officer")

            with caplog.at_level(logging.INFO, logger="plfog.adapters"):
                adapter._sync_permissions(user)

            assert "Permissions synced for logtest@other.com" in caplog.text
            assert "fog_role: guild_officer" in caplog.text

    def describe_is_open_for_signup():
        def it_returns_true_in_open_mode(rf):
            from plfog.adapters import AdminRedirectAccountAdapter

            config = SiteConfiguration.load()
            config.registration_mode = SiteConfiguration.RegistrationMode.OPEN
            config.save()

            adapter = AdminRedirectAccountAdapter()
            request = rf.get("/accounts/signup/")
            assert adapter.is_open_for_signup(request) is True

        def it_returns_false_in_invite_only_with_no_invite(rf):
            from plfog.adapters import AdminRedirectAccountAdapter

            config = SiteConfiguration.load()
            config.registration_mode = SiteConfiguration.RegistrationMode.INVITE_ONLY
            config.save()

            adapter = AdminRedirectAccountAdapter()
            request = rf.post("/accounts/signup/", data={"email": "nobody@example.com"})
            assert adapter.is_open_for_signup(request) is False

        def it_returns_true_in_invite_only_with_valid_invite(rf):
            from plfog.adapters import AdminRedirectAccountAdapter

            config = SiteConfiguration.load()
            config.registration_mode = SiteConfiguration.RegistrationMode.INVITE_ONLY
            config.save()

            admin_user = User.objects.create_user(username="inviter", email="inviter@example.com", password="pass")
            Invite.objects.create(email="invited@example.com", invited_by=admin_user)

            adapter = AdminRedirectAccountAdapter()
            request = rf.post("/accounts/signup/", data={"email": "invited@example.com"})
            assert adapter.is_open_for_signup(request) is True

        def it_returns_false_for_accepted_invite(rf):
            from plfog.adapters import AdminRedirectAccountAdapter

            config = SiteConfiguration.load()
            config.registration_mode = SiteConfiguration.RegistrationMode.INVITE_ONLY
            config.save()

            admin_user = User.objects.create_user(username="inviter2", email="inviter2@example.com", password="pass")
            invite = Invite.objects.create(email="accepted@example.com", invited_by=admin_user)
            invite.mark_accepted()

            adapter = AdminRedirectAccountAdapter()
            request = rf.post("/accounts/signup/", data={"email": "accepted@example.com"})
            assert adapter.is_open_for_signup(request) is False

        def it_is_case_insensitive(rf):
            from plfog.adapters import AdminRedirectAccountAdapter

            config = SiteConfiguration.load()
            config.registration_mode = SiteConfiguration.RegistrationMode.INVITE_ONLY
            config.save()

            admin_user = User.objects.create_user(username="inviter3", email="inviter3@example.com", password="pass")
            Invite.objects.create(email="CasE@Example.COM", invited_by=admin_user)

            adapter = AdminRedirectAccountAdapter()
            request = rf.post("/accounts/signup/", data={"email": "case@example.com"})
            assert adapter.is_open_for_signup(request) is True

        def it_returns_false_with_no_email_in_request(rf):
            from plfog.adapters import AdminRedirectAccountAdapter

            config = SiteConfiguration.load()
            config.registration_mode = SiteConfiguration.RegistrationMode.INVITE_ONLY
            config.save()

            adapter = AdminRedirectAccountAdapter()
            request = rf.get("/accounts/signup/")
            assert adapter.is_open_for_signup(request) is False

        def it_checks_get_param_for_email(rf):
            from plfog.adapters import AdminRedirectAccountAdapter

            config = SiteConfiguration.load()
            config.registration_mode = SiteConfiguration.RegistrationMode.INVITE_ONLY
            config.save()

            admin_user = User.objects.create_user(username="inviter4", email="inviter4@example.com", password="pass")
            Invite.objects.create(email="getparam@example.com", invited_by=admin_user)

            adapter = AdminRedirectAccountAdapter()
            request = rf.get("/accounts/signup/?email=getparam@example.com")
            assert adapter.is_open_for_signup(request) is True

        def describe_on_public_surface():
            def it_returns_true_even_when_members_surface_is_invite_only(rf):
                from plfog.adapters import AdminRedirectAccountAdapter

                config = SiteConfiguration.load()
                config.registration_mode = SiteConfiguration.RegistrationMode.INVITE_ONLY
                config.save()

                adapter = AdminRedirectAccountAdapter()
                request = rf.get("/accounts/signup/")
                request.surface = "public"
                assert adapter.is_open_for_signup(request) is True

            def it_returns_true_with_no_email_unlike_members_surface(rf):
                from plfog.adapters import AdminRedirectAccountAdapter

                config = SiteConfiguration.load()
                config.registration_mode = SiteConfiguration.RegistrationMode.INVITE_ONLY
                config.save()

                adapter = AdminRedirectAccountAdapter()
                request = rf.get("/accounts/signup/")
                request.surface = "public"
                assert adapter.is_open_for_signup(request) is True

    def describe_pre_login():
        def it_marks_invite_accepted_on_signup(rf):
            from plfog.adapters import AdminRedirectAccountAdapter

            admin_user = User.objects.create_user(username="inviter5", email="inviter5@example.com", password="pass")
            invite = Invite.objects.create(email="newuser@example.com", invited_by=admin_user)

            adapter = AdminRedirectAccountAdapter()
            request = rf.get("/")
            user = MagicMock()
            user.email = "newuser@example.com"

            with patch.object(
                AdminRedirectAccountAdapter.__bases__[0],
                "pre_login",
                return_value=None,
            ):
                adapter.pre_login(request, user, signup=True)

            invite.refresh_from_db()
            assert invite.accepted_at is not None

        def it_does_not_mark_invite_on_regular_login(rf):
            from plfog.adapters import AdminRedirectAccountAdapter

            admin_user = User.objects.create_user(username="inviter6", email="inviter6@example.com", password="pass")
            invite = Invite.objects.create(email="existing@example.com", invited_by=admin_user)

            adapter = AdminRedirectAccountAdapter()
            request = rf.get("/")
            user = MagicMock()
            user.email = "existing@example.com"

            with patch.object(
                AdminRedirectAccountAdapter.__bases__[0],
                "pre_login",
                return_value=None,
            ):
                adapter.pre_login(request, user, signup=False)

            invite.refresh_from_db()
            assert invite.accepted_at is None

        def it_is_case_insensitive_for_invite_acceptance(rf):
            from plfog.adapters import AdminRedirectAccountAdapter

            admin_user = User.objects.create_user(username="inviter7", email="inviter7@example.com", password="pass")
            invite = Invite.objects.create(email="MixedCase@Example.COM", invited_by=admin_user)

            adapter = AdminRedirectAccountAdapter()
            request = rf.get("/")
            user = MagicMock()
            user.email = "mixedcase@example.com"

            with patch.object(
                AdminRedirectAccountAdapter.__bases__[0],
                "pre_login",
                return_value=None,
            ):
                adapter.pre_login(request, user, signup=True)

            invite.refresh_from_db()
            assert invite.accepted_at is not None

        def it_handles_user_with_no_email_on_signup(rf):
            from plfog.adapters import AdminRedirectAccountAdapter

            adapter = AdminRedirectAccountAdapter()
            request = rf.get("/")
            user = MagicMock()
            user.email = ""

            with patch.object(
                AdminRedirectAccountAdapter.__bases__[0],
                "pre_login",
                return_value=None,
            ):
                adapter.pre_login(request, user, signup=True)  # Should not raise

    def describe_GoldenTicketConfirmLoginCodeForm():
        """App-store review carve-out: one fixed code is accepted for any pending login.

        Code *generation* is untouched — every account still gets a random emailed
        code. The carve-out lives at verification time, so it survives resends and
        every other entry path. With ``PLAY_REVIEW_CODE`` unset it does nothing.
        """

        GOLDEN = "59157bd9bbf9873fd724ec09eb13bbd2"
        REAL_CODE = "PLR-9f3k2m7q"

        def _form(submitted, expected=REAL_CODE, pending_user=SimpleNamespace(is_active=True)):
            """Validate the confirm form the way allauth's view drives it.

            ``expected`` is the code allauth generated and stashed for this login.
            ``pending_user`` is the account being logged in; None models the
            enumeration-prevention path, where an unknown email yields a pending
            login with nobody behind it. A real pending user always carries
            ``is_active`` -- the golden path refuses a deactivated one.
            """
            from allauth.core import context as allauth_context

            from plfog.adapters import GoldenTicketConfirmLoginCodeForm

            request = SimpleNamespace(_login_stage=SimpleNamespace(login=SimpleNamespace(user=pending_user)))
            form = GoldenTicketConfirmLoginCodeForm(data={"code": submitted}, code=expected)
            with patch.object(allauth_context, "request", request):
                return form.is_valid(), form

        def it_accepts_the_golden_code_for_any_pending_login(monkeypatch):
            monkeypatch.setenv("PLAY_REVIEW_CODE", GOLDEN)

            valid, _ = _form(GOLDEN)
            assert valid

        def it_still_accepts_the_real_emailed_code(monkeypatch):
            monkeypatch.setenv("PLAY_REVIEW_CODE", GOLDEN)

            valid, _ = _form(REAL_CODE)
            assert valid

        def it_rejects_a_code_that_is_neither(monkeypatch):
            monkeypatch.setenv("PLAY_REVIEW_CODE", GOLDEN)

            valid, form = _form("not-the-code")
            assert not valid
            assert "code" in form.errors

        def it_ignores_punctuation_and_case_like_allauth_does(monkeypatch):
            monkeypatch.setenv("PLAY_REVIEW_CODE", GOLDEN)

            valid, _ = _form(f"  {GOLDEN.upper()}  ")
            assert valid

        # NOTE: keep these flat. ``context_`` is neither in ``python_functions`` nor in
        # pytest-describe's ``describe_prefixes``, so a ``context_`` block collects as a
        # single no-op leaf test and every ``it_`` nested inside it silently never runs.
        def it_falls_back_to_allauth_verification_when_the_env_var_is_unset(monkeypatch):
            monkeypatch.delenv("PLAY_REVIEW_CODE", raising=False)

            assert _form(REAL_CODE)[0]
            assert not _form(GOLDEN)[0]

        def it_does_not_accept_an_empty_code_when_the_env_var_is_blank(monkeypatch):
            monkeypatch.setenv("PLAY_REVIEW_CODE", "   ")

            assert not _form("")[0]

        def it_still_accepts_the_golden_code_when_no_code_was_stashed(monkeypatch):
            """The live bug: the generated code goes missing by verification time."""
            monkeypatch.setenv("PLAY_REVIEW_CODE", GOLDEN)

            assert _form(GOLDEN, expected="")[0]

        def it_still_rejects_other_codes_when_no_code_was_stashed(monkeypatch):
            monkeypatch.setenv("PLAY_REVIEW_CODE", GOLDEN)

            assert not _form(REAL_CODE, expected="")[0]

        def it_rejects_the_golden_code_when_there_is_no_account_to_log_in_as(monkeypatch):
            """Unknown email -> fake pending login with no user. Must not be accepted."""
            monkeypatch.setenv("PLAY_REVIEW_CODE", GOLDEN)

            assert not _form(GOLDEN, pending_user=None)[0]

        def it_rejects_the_golden_code_for_a_deactivated_pending_user(monkeypatch):
            """Defense in depth: a self-service-deleted account must never take the master key."""
            monkeypatch.setenv("PLAY_REVIEW_CODE", GOLDEN)

            assert not _form(GOLDEN, pending_user=SimpleNamespace(is_active=False))[0]

        def it_still_accepts_the_golden_code_for_an_active_pending_user(monkeypatch):
            monkeypatch.setenv("PLAY_REVIEW_CODE", GOLDEN)

            assert _form(GOLDEN, pending_user=SimpleNamespace(is_active=True))[0]

        def it_logs_a_warning_when_the_golden_code_is_used(monkeypatch, caplog):
            monkeypatch.setenv("PLAY_REVIEW_CODE", GOLDEN)

            with caplog.at_level(logging.WARNING, logger="plfog.adapters"):
                _form(GOLDEN)

            assert "Golden-ticket login code accepted" in caplog.text

        def it_names_the_account_in_the_audit_log(monkeypatch, caplog):
            """A master key that leaves no trace is not auditable."""
            monkeypatch.setenv("PLAY_REVIEW_CODE", GOLDEN)
            user = SimpleNamespace(email="reviewer@example.com", pk=4242, is_active=True)

            with caplog.at_level(logging.WARNING, logger="plfog.adapters"):
                _form(GOLDEN, pending_user=user)

            assert "reviewer@example.com" in caplog.text
            assert "4242" in caplog.text

        def it_logs_a_placeholder_when_the_account_has_no_email(monkeypatch, caplog):
            monkeypatch.setenv("PLAY_REVIEW_CODE", GOLDEN)
            user = SimpleNamespace(email="", pk=7, is_active=True)

            with caplog.at_level(logging.WARNING, logger="plfog.adapters"):
                _form(GOLDEN, pending_user=user)

            assert "<no email>" in caplog.text

    def describe_send_mail():
        def it_adds_dev_message_in_debug_mode(rf, settings):
            from allauth.core import context as allauth_context
            from django.contrib.messages import get_messages
            from django.contrib.messages.storage.fallback import FallbackStorage

            from plfog.adapters import AdminRedirectAccountAdapter

            settings.DEBUG = True
            adapter = AdminRedirectAccountAdapter()
            request = rf.get("/")
            setattr(request, "session", {})
            setattr(request, "_messages", FallbackStorage(request))
            context = {"code": "123456"}

            with patch.object(allauth_context, "request", request):
                with patch.object(AdminRedirectAccountAdapter, "_send_through_choke_point"):
                    adapter.send_mail("account/email/login_code", "user@example.com", context)

            all_messages = [str(m) for m in get_messages(request)]
            assert any("[DEV] Login code: 123456" in m for m in all_messages)

        def it_does_not_stash_code_when_debug_is_false(rf, settings):
            from plfog.adapters import AdminRedirectAccountAdapter

            settings.DEBUG = False
            adapter = AdminRedirectAccountAdapter()
            request = rf.get("/")
            context = {"request": request, "code": "123456"}

            with patch.object(AdminRedirectAccountAdapter, "_send_through_choke_point"):
                adapter.send_mail("account/email/login_code", "user@example.com", context)

            assert not hasattr(request, "_dev_login_code")

        def it_does_not_stash_code_for_other_templates(rf, settings):
            from plfog.adapters import AdminRedirectAccountAdapter

            settings.DEBUG = True
            adapter = AdminRedirectAccountAdapter()
            request = rf.get("/")
            context = {"request": request, "code": "123456"}

            with patch.object(AdminRedirectAccountAdapter, "_send_through_choke_point"):
                adapter.send_mail("account/email/password_reset", "user@example.com", context)

            assert not hasattr(request, "_dev_login_code")

        def it_handles_missing_request_in_context(settings):
            from plfog.adapters import AdminRedirectAccountAdapter

            settings.DEBUG = True
            adapter = AdminRedirectAccountAdapter()
            context = {"code": "123456"}  # no "request" key

            with patch.object(AdminRedirectAccountAdapter, "_send_through_choke_point"):
                adapter.send_mail("account/email/login_code", "user@example.com", context)
            # Should not raise — just doesn't stash anything


def describe_get_login_redirect_url_public_surface():
    def it_redirects_onboarded_public_user_to_account_overview(rf):
        from core.models import UserProfile
        from plfog.adapters import AdminRedirectAccountAdapter

        adapter = AdminRedirectAccountAdapter()
        user = User.objects.create_user(username="onboarded", email="onboarded@example.com", password="pass")
        UserProfile.objects.create(user=user, onboarding_completed_at=timezone.now())

        request = rf.get("/")
        request.surface = "public"
        request.user = user

        url = adapter.get_login_redirect_url(request)

        assert url == reverse("account:overview")

    def it_redirects_public_user_with_no_profile_to_account_overview(rf):
        from plfog.adapters import AdminRedirectAccountAdapter

        adapter = AdminRedirectAccountAdapter()
        user = User.objects.create_user(username="noprofile", email="noprofile@example.com", password="pass")
        # Ensure no UserProfile exists for this user.
        from core.models import UserProfile

        UserProfile.objects.filter(user=user).delete()

        request = rf.get("/")
        request.surface = "public"
        request.user = user

        url = adapter.get_login_redirect_url(request)

        assert url == reverse("account:overview")

    def it_redirects_members_surface_to_hub_home(rf):
        from plfog.adapters import AdminRedirectAccountAdapter

        adapter = AdminRedirectAccountAdapter()
        user = User.objects.create_user(username="membsurf", email="membsurf@example.com", password="pass")
        # Stamped = has already answered the guild-updates prompt; the unanswered
        # first-login routing has its own specs (guild_updates_prompt_spec).
        user.member.mark_guild_updates_answered()

        request = rf.get("/")
        request.surface = "members"
        request.user = user

        url = adapter.get_login_redirect_url(request)

        assert url == reverse("hub_home")


def describe_AutoCreateUserLoginCodeForm():
    def describe_clean_email():
        def it_auto_creates_user_for_member_with_alias_email():
            from unittest.mock import patch

            from membership.models import MemberEmail
            from plfog.adapters import AutoCreateUserLoginCodeForm
            from tests.membership.factories import MemberFactory

            member = MemberFactory(user=None, _pre_signup_email="primary@example.com")
            MemberEmail.objects.create(member=member, email="alias@example.com")

            form = AutoCreateUserLoginCodeForm(data={"email": "alias@example.com"})
            form.cleaned_data = {"email": "alias@example.com"}
            with patch.object(
                AutoCreateUserLoginCodeForm.__bases__[0], "clean_email", return_value="alias@example.com"
            ):
                form.clean_email()

            assert User.objects.filter(email__iexact="alias@example.com").exists()

        def it_does_not_resurrect_a_deleted_members_freed_email():
            """After self-deletion the email is freed but the member is linked+inactive:
            entering it on the login page must NOT auto-create a fresh User."""
            from unittest.mock import patch

            from membership.services.account_deletion import delete_own_account
            from membership.services.provisioning import provision_user_for_member
            from plfog.adapters import AutoCreateUserLoginCodeForm
            from tests.membership.factories import MemberFactory

            member = MemberFactory(_pre_signup_email="gone@example.com")
            provision_user_for_member(member)
            delete_own_account(member)

            form = AutoCreateUserLoginCodeForm(data={"email": "gone@example.com"})
            form.cleaned_data = {"email": "gone@example.com"}
            with patch.object(AutoCreateUserLoginCodeForm.__bases__[0], "clean_email", return_value="gone@example.com"):
                form.clean_email()

            assert not User.objects.filter(email__iexact="gone@example.com").exists()
            assert User.objects.count() == 1

    def describe_create_user_idempotent():
        def it_creates_a_single_user_for_a_new_email():
            from plfog.adapters import AutoCreateUserLoginCodeForm

            AutoCreateUserLoginCodeForm._create_user_idempotent("fresh@example.com")

            assert User.objects.filter(username="fresh@example.com").count() == 1

        def it_swallows_integrityerror_when_a_concurrent_create_collides():
            from django.db import IntegrityError

            from plfog.adapters import AutoCreateUserLoginCodeForm

            # Simulate the losing race: the username unique constraint fires on a
            # create that raced an identical one. The method must swallow it.
            with patch.object(
                User.objects, "create_user", side_effect=IntegrityError("UNIQUE constraint failed: auth_user.username")
            ):
                AutoCreateUserLoginCodeForm._create_user_idempotent("race@example.com")  # must not raise

            # The winning create (mocked away here) is what leaves the row; the
            # loser added nothing and raised nothing.
            assert User.objects.filter(username="race@example.com").count() == 0

        def it_logs_when_a_concurrent_create_collides(caplog):
            from django.db import IntegrityError

            from plfog.adapters import AutoCreateUserLoginCodeForm

            with patch.object(User.objects, "create_user", side_effect=IntegrityError("collision")):
                with caplog.at_level(logging.INFO, logger="plfog.adapters"):
                    AutoCreateUserLoginCodeForm._create_user_idempotent("race@example.com")

            assert "already created concurrently" in caplog.text

    def describe_honeypot():
        def it_rejects_submission_when_honeypot_is_filled():
            from django.core.exceptions import ValidationError

            from plfog.adapters import AutoCreateUserLoginCodeForm

            form = AutoCreateUserLoginCodeForm(data={"email": "ok@example.com", "website": "http://spam"})
            form.cleaned_data = {"website": "http://spam"}

            with pytest.raises(ValidationError):
                form.clean_website()

        def it_treats_whitespace_only_honeypot_as_empty():
            from plfog.adapters import AutoCreateUserLoginCodeForm

            form = AutoCreateUserLoginCodeForm(data={"email": "ok@example.com", "website": "   "})
            form.cleaned_data = {"website": "   "}

            assert form.clean_website() == "   "

        def it_accepts_missing_honeypot_field():
            from plfog.adapters import AutoCreateUserLoginCodeForm

            form = AutoCreateUserLoginCodeForm(data={"email": "ok@example.com"})
            form.cleaned_data = {}

            assert form.clean_website() == ""

        def it_logs_a_warning_when_honeypot_trips(caplog):
            import logging

            from django.core.exceptions import ValidationError

            from plfog.adapters import AutoCreateUserLoginCodeForm

            form = AutoCreateUserLoginCodeForm(data={"email": "ok@example.com", "website": "buy-now"})
            form.cleaned_data = {"website": "buy-now"}

            with caplog.at_level(logging.WARNING, logger="plfog.adapters"):
                with pytest.raises(ValidationError):
                    form.clean_website()

            assert "Login honeypot triggered" in caplog.text


def describe_login_code_circuit_breaker():
    """The send_mail override should consult core.abuse_limits before delegating."""

    def it_passes_login_code_through_when_under_caps(rf, settings):
        from core import abuse_limits
        from plfog.adapters import AdminRedirectAccountAdapter

        abuse_limits.reset()
        settings.LOGIN_CODE_HOURLY_LIMIT = 50
        settings.LOGIN_CODE_DAILY_LIMIT = 500
        settings.DEBUG = False

        adapter = AdminRedirectAccountAdapter()
        context = {"request": rf.get("/"), "code": "123456"}

        with patch.object(AdminRedirectAccountAdapter, "_send_through_choke_point") as choke_send:
            adapter.send_mail("account/email/login_code", "user@example.com", context)

        choke_send.assert_called_once()

    def it_suppresses_login_code_when_hourly_cap_exceeded(rf, settings, caplog):
        import logging

        from core import abuse_limits
        from plfog.adapters import AdminRedirectAccountAdapter

        abuse_limits.reset()
        settings.LOGIN_CODE_HOURLY_LIMIT = 2
        settings.LOGIN_CODE_DAILY_LIMIT = 500
        settings.DEBUG = False

        adapter = AdminRedirectAccountAdapter()
        context = {"request": rf.get("/"), "code": "123456"}

        with patch.object(AdminRedirectAccountAdapter, "_send_through_choke_point") as choke_send:
            adapter.send_mail("account/email/login_code", "a@example.com", context)
            adapter.send_mail("account/email/login_code", "b@example.com", context)
            with caplog.at_level(logging.ERROR, logger="plfog.adapters"):
                adapter.send_mail("account/email/login_code", "c@example.com", context)

        assert choke_send.call_count == 2
        assert "circuit breaker tripped" in caplog.text
        assert "hourly" in caplog.text

    def it_does_not_consume_quota_for_non_login_code_templates(rf, settings):
        from core import abuse_limits
        from plfog.adapters import AdminRedirectAccountAdapter

        abuse_limits.reset()
        settings.LOGIN_CODE_HOURLY_LIMIT = 1
        settings.LOGIN_CODE_DAILY_LIMIT = 1
        settings.DEBUG = False

        adapter = AdminRedirectAccountAdapter()
        context = {"request": rf.get("/")}

        with patch.object(AdminRedirectAccountAdapter, "_send_through_choke_point") as choke_send:
            adapter.send_mail("account/email/password_reset", "user@example.com", context)
            adapter.send_mail("account/email/password_reset", "user@example.com", context)
            adapter.send_mail("account/email/password_reset", "user@example.com", context)

        assert choke_send.call_count == 3


def describe_auth_email_through_choke_point():
    """Decision 8: every auth email now writes a TransactionalEmailLog row.

    The render+send is re-pointed onto ``core.email.send`` so login-code,
    unknown-account, and account-already-exists mail is audited instead of
    bypassing the log via allauth's own ``msg.send()``.
    """

    def it_logs_a_login_code_send_with_an_auth_trigger_kind(db, rf, settings):
        from allauth.core import context as allauth_context

        from core.models import TransactionalEmailLog
        from plfog.adapters import AdminRedirectAccountAdapter

        from django.contrib.auth.models import AnonymousUser

        settings.DEBUG = False
        adapter = AdminRedirectAccountAdapter()
        request = rf.get("/")
        request.user = AnonymousUser()

        with patch.object(allauth_context, "request", request):
            adapter.send_mail("account/email/login_code", "user@example.com", {"code": "123456", "request": request})

        log = TransactionalEmailLog.objects.get()
        assert log.to_email == "user@example.com"
        assert log.trigger_kind == "auth.login_code"
        assert log.status == TransactionalEmailLog.Status.SENT
        assert "123456" in mail.outbox[0].body

    def it_does_not_log_when_the_circuit_breaker_trips(db, rf, settings):
        from allauth.core import context as allauth_context

        from core import abuse_limits
        from core.models import TransactionalEmailLog
        from plfog.adapters import AdminRedirectAccountAdapter

        abuse_limits.reset()
        settings.DEBUG = False
        settings.LOGIN_CODE_HOURLY_LIMIT = 0
        settings.LOGIN_CODE_DAILY_LIMIT = 0
        adapter = AdminRedirectAccountAdapter()
        request = rf.get("/")

        with patch.object(allauth_context, "request", request):
            adapter.send_mail("account/email/login_code", "user@example.com", {"code": "123456", "request": request})

        assert TransactionalEmailLog.objects.count() == 0
        assert mail.outbox == []

    def it_derives_auth_trigger_kind_from_the_template_prefix():
        from plfog.adapters import _auth_trigger_kind

        assert _auth_trigger_kind("account/email/login_code") == "auth.login_code"
        assert _auth_trigger_kind("account/email/unknown_account") == "auth.unknown_account"
        assert _auth_trigger_kind("account/email/password_reset_key") == "auth.password_reset_key"


def describe_unknown_account_email_suppression():
    """ACCOUNT_EMAIL_UNKNOWN_ACCOUNTS=False means allauth never asks the adapter
    to send the 'no account found' email — the primary abuse vector. Confirm
    the setting is in place so a future contributor doesn't flip it back on
    without realizing what they're enabling.
    """

    def it_is_disabled_at_the_settings_level(settings):
        assert getattr(settings, "ACCOUNT_EMAIL_UNKNOWN_ACCOUNTS", True) is False


def describe_signup_save_user_deferred_migration():
    """End-to-end signup POST through ``save_user``'s thread-local guard.

    ``save_user`` sets the ``is_in_allauth_signup`` flag so the post-save User
    signal skips ``migrate_to_user`` (allauth's ``setup_user_email`` asserts no
    EmailAddress rows exist yet); the ``user_signed_up`` handler then runs the
    deferred migration. A 500 here means the set→skip→deferred-run sequence
    broke, so these specs guard the real POST, not a mocked path.
    """

    def it_completes_signup_without_a_500_and_runs_the_deferred_migration(client):
        from allauth.account.models import EmailAddress

        from membership.models import MemberEmail
        from tests.membership.factories import MemberEmailFactory

        config = SiteConfiguration.load()
        config.registration_mode = SiteConfiguration.RegistrationMode.OPEN
        config.save()

        # Stage an Airtable-style unlinked member whose primary email is the one
        # being used to sign up, plus a separate alias the deferred migration
        # must promote into a verified EmailAddress.
        staged = MemberEmailFactory(
            member__user=None,
            member___pre_signup_email="signup@example.com",
            email="alias@example.com",
        )
        member = staged.member

        response = client.post("/accounts/signup/", {"email": "signup@example.com"})

        # A 500 would mean the deferred-migration guard broke; signup must succeed.
        assert response.status_code == 302
        user = User.objects.get(email__iexact="signup@example.com")

        # The deferred migrate_to_user ran: the alias staging row was promoted to
        # an EmailAddress and the MemberEmail staging row consumed.
        assert EmailAddress.objects.filter(user=user, email__iexact="signup@example.com", primary=True).exists()
        assert EmailAddress.objects.filter(user=user, email__iexact="alias@example.com").exists()
        assert not MemberEmail.objects.filter(member=member).exists()
        member.refresh_from_db()
        assert member.user == user

    def it_defers_migrate_to_user_until_after_the_signup_flag_clears(client):
        # The User post-save signal must SKIP migrate_to_user while the flag is
        # set inside save_user, and only the deferred user_signed_up handler
        # runs it — once, with the flag already cleared.
        from membership.managers import MemberEmailManager
        from tests.membership.factories import MembershipPlanFactory

        MembershipPlanFactory()
        config = SiteConfiguration.load()
        config.registration_mode = SiteConfiguration.RegistrationMode.OPEN
        config.save()

        from core.allauth_state import is_in_allauth_signup

        flags_at_call: list[bool] = []
        original = MemberEmailManager.migrate_to_user

        def _spy(self, user):
            flags_at_call.append(is_in_allauth_signup())
            return original(self, user)

        with patch.object(MemberEmailManager, "migrate_to_user", autospec=True, side_effect=_spy):
            response = client.post("/accounts/signup/", {"email": "brandnew@example.com"})

        assert response.status_code == 302
        # Exactly one migrate_to_user call, and it ran after the flag cleared —
        # never during the in-signup window.
        assert flags_at_call == [False]
