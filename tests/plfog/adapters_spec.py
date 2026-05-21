"""BDD-style tests for plfog.adapters module — auto-admin, admin redirect, and signup gating."""

import logging
from unittest.mock import MagicMock, patch

import pytest
from django.contrib import messages
from django.contrib.auth.models import User
from django.test import RequestFactory, override_settings
from django.urls import reverse

from core.models import Invite, SiteConfiguration
from membership.models import Member

pytestmark = pytest.mark.django_db


def _make_request_with_user(rf: RequestFactory, *, is_staff: bool, is_superuser: bool) -> object:
    """Create a GET request with an attached user having the given flags."""
    request = rf.get("/accounts/login/")
    user = MagicMock()
    user.is_staff = is_staff
    user.is_superuser = is_superuser
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
        def it_lands_staff_on_community_calendar(rf):
            from plfog.adapters import AdminRedirectAccountAdapter

            adapter = AdminRedirectAccountAdapter()
            request = _make_request_with_user(rf, is_staff=True, is_superuser=False)

            url = adapter.get_login_redirect_url(request)

            assert url == reverse("hub_community_calendar")

        def it_lands_non_staff_on_community_calendar(rf):
            from plfog.adapters import AdminRedirectAccountAdapter

            adapter = AdminRedirectAccountAdapter()
            request = _make_request_with_user(rf, is_staff=False, is_superuser=False)

            url = adapter.get_login_redirect_url(request)

            assert url == reverse("hub_community_calendar")

        def it_lands_superusers_on_community_calendar(rf):
            from plfog.adapters import AdminRedirectAccountAdapter

            adapter = AdminRedirectAccountAdapter()
            request = _make_request_with_user(rf, is_staff=True, is_superuser=True)

            url = adapter.get_login_redirect_url(request)

            assert url == reverse("hub_community_calendar")

        @override_settings(LOGIN_REDIRECT_URL="/dashboard/")
        def it_ignores_custom_url_setting(rf):
            from plfog.adapters import AdminRedirectAccountAdapter

            adapter = AdminRedirectAccountAdapter()
            request = _make_request_with_user(rf, is_staff=True, is_superuser=False)

            url = adapter.get_login_redirect_url(request)

            assert url == reverse("hub_community_calendar")

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

    def describe_send_mail():
        def it_stashes_login_code_on_request_in_debug_mode(rf, settings):
            from plfog.adapters import AdminRedirectAccountAdapter

            settings.DEBUG = True
            adapter = AdminRedirectAccountAdapter()
            request = rf.get("/")
            context = {"request": request, "code": "123456"}

            with patch.object(AdminRedirectAccountAdapter.__bases__[0], "send_mail"):
                adapter.send_mail("account/email/login_code", "user@example.com", context)

            assert request._dev_login_code == "123456"

        def it_does_not_stash_code_when_debug_is_false(rf, settings):
            from plfog.adapters import AdminRedirectAccountAdapter

            settings.DEBUG = False
            adapter = AdminRedirectAccountAdapter()
            request = rf.get("/")
            context = {"request": request, "code": "123456"}

            with patch.object(AdminRedirectAccountAdapter.__bases__[0], "send_mail"):
                adapter.send_mail("account/email/login_code", "user@example.com", context)

            assert not hasattr(request, "_dev_login_code")

        def it_does_not_stash_code_for_other_templates(rf, settings):
            from plfog.adapters import AdminRedirectAccountAdapter

            settings.DEBUG = True
            adapter = AdminRedirectAccountAdapter()
            request = rf.get("/")
            context = {"request": request, "code": "123456"}

            with patch.object(AdminRedirectAccountAdapter.__bases__[0], "send_mail"):
                adapter.send_mail("account/email/password_reset", "user@example.com", context)

            assert not hasattr(request, "_dev_login_code")

        def it_handles_missing_request_in_context(settings):
            from plfog.adapters import AdminRedirectAccountAdapter

            settings.DEBUG = True
            adapter = AdminRedirectAccountAdapter()
            context = {"code": "123456"}  # no "request" key

            with patch.object(AdminRedirectAccountAdapter.__bases__[0], "send_mail"):
                adapter.send_mail("account/email/login_code", "user@example.com", context)
            # Should not raise — just doesn't stash anything

    def describe_add_message():
        def it_appends_dev_code_message_when_code_is_stashed(rf, settings):
            from django.contrib.messages import get_messages
            from django.contrib.messages.storage.fallback import FallbackStorage

            from plfog.adapters import AdminRedirectAccountAdapter

            settings.DEBUG = True
            adapter = AdminRedirectAccountAdapter()
            request = rf.get("/")
            setattr(request, "session", {})
            setattr(request, "_messages", FallbackStorage(request))
            request._dev_login_code = "654321"

            adapter.add_message(
                request,
                messages.SUCCESS,
                message_template="account/messages/login_code_sent.txt",
                message_context={"recipient": "user@example.com", "email": "user@example.com"},
            )

            all_messages = [str(m) for m in get_messages(request)]
            assert any("[DEV] Your login code is: 654321" in m for m in all_messages)

        def it_does_not_append_code_when_none_stashed(rf, settings):
            from django.contrib.messages import get_messages
            from django.contrib.messages.storage.fallback import FallbackStorage

            from plfog.adapters import AdminRedirectAccountAdapter

            settings.DEBUG = True
            adapter = AdminRedirectAccountAdapter()
            request = rf.get("/")
            setattr(request, "session", {})
            setattr(request, "_messages", FallbackStorage(request))

            adapter.add_message(
                request,
                messages.SUCCESS,
                message_template="account/messages/login_code_sent.txt",
                message_context={"recipient": "user@example.com", "email": "user@example.com"},
            )

            all_messages = [str(m) for m in get_messages(request)]
            assert not any("[DEV]" in m for m in all_messages)

        def it_cleans_up_stashed_code_after_use(rf, settings):
            from django.contrib.messages.storage.fallback import FallbackStorage

            from plfog.adapters import AdminRedirectAccountAdapter

            settings.DEBUG = True
            adapter = AdminRedirectAccountAdapter()
            request = rf.get("/")
            setattr(request, "session", {})
            setattr(request, "_messages", FallbackStorage(request))
            request._dev_login_code = "111222"

            adapter.add_message(
                request,
                messages.SUCCESS,
                message_template="account/messages/login_code_sent.txt",
                message_context={"recipient": "user@example.com", "email": "user@example.com"},
            )

            assert not hasattr(request, "_dev_login_code")


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

        with patch.object(AdminRedirectAccountAdapter.__bases__[0], "send_mail") as super_send:
            adapter.send_mail("account/email/login_code", "user@example.com", context)

        super_send.assert_called_once()

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

        with patch.object(AdminRedirectAccountAdapter.__bases__[0], "send_mail") as super_send:
            adapter.send_mail("account/email/login_code", "a@example.com", context)
            adapter.send_mail("account/email/login_code", "b@example.com", context)
            with caplog.at_level(logging.ERROR, logger="plfog.adapters"):
                adapter.send_mail("account/email/login_code", "c@example.com", context)

        assert super_send.call_count == 2
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

        with patch.object(AdminRedirectAccountAdapter.__bases__[0], "send_mail") as super_send:
            adapter.send_mail("account/email/password_reset", "user@example.com", context)
            adapter.send_mail("account/email/password_reset", "user@example.com", context)
            adapter.send_mail("account/email/password_reset", "user@example.com", context)

        assert super_send.call_count == 3


def describe_unknown_account_email_suppression():
    """ACCOUNT_EMAIL_UNKNOWN_ACCOUNTS=False means allauth never asks the adapter
    to send the 'no account found' email — the primary abuse vector. Confirm
    the setting is in place so a future contributor doesn't flip it back on
    without realizing what they're enabling.
    """

    def it_is_disabled_at_the_settings_level(settings):
        assert getattr(settings, "ACCOUNT_EMAIL_UNKNOWN_ACCOUNTS", True) is False
