"""BDD-style tests for plfog.adapters module — auto-admin and admin redirect."""

import logging
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.test import RequestFactory, override_settings

pytestmark = pytest.mark.django_db


def _make_social_login(email: str) -> MagicMock:
    """Create a mock sociallogin object with the given email."""
    mock_sociallogin = MagicMock()
    mock_sociallogin.user = User(email=email, username=email.split("@")[0])
    mock_sociallogin.account = MagicMock()
    mock_sociallogin.email_addresses = []
    mock_sociallogin.token = MagicMock()
    return mock_sociallogin


def _make_existing_social_login(user: User) -> MagicMock:
    """Create a mock sociallogin object for an existing (saved) user."""
    mock_sociallogin = MagicMock()
    mock_sociallogin.user = user
    mock_sociallogin.account = MagicMock()
    mock_sociallogin.email_addresses = []
    mock_sociallogin.token = MagicMock()
    return mock_sociallogin


def _patch_super_save_user(email: str, username: str):
    """Return a context manager that patches DefaultSocialAccountAdapter.save_user."""
    from plfog.adapters import AutoAdminSocialAccountAdapter

    mock_user = User(email=email, username=username)
    mock_user.pk = 1
    object.__setattr__(mock_user, "save", MagicMock())
    return patch.object(
        AutoAdminSocialAccountAdapter.__bases__[0],
        "save_user",
        return_value=mock_user,
    )


def describe_save_user_with_empty_admin_domains():
    def it_does_not_grant_admin(rf, settings):
        from plfog.adapters import AutoAdminSocialAccountAdapter

        settings.ADMIN_DOMAINS = []
        adapter = AutoAdminSocialAccountAdapter()
        request = rf.get("/")

        with _patch_super_save_user("user@example.com", "user"):
            user = adapter.save_user(request, _make_social_login("user@example.com"))

        assert user.is_staff is False
        assert user.is_superuser is False


def describe_save_user_with_matching_domain():
    def it_grants_admin_privileges(rf, settings):
        from plfog.adapters import AutoAdminSocialAccountAdapter

        settings.ADMIN_DOMAINS = ["example.com"]
        adapter = AutoAdminSocialAccountAdapter()
        request = rf.get("/")

        with _patch_super_save_user("user@example.com", "user"):
            user = adapter.save_user(request, _make_social_login("user@example.com"))

        assert user.is_staff is True
        assert user.is_superuser is True
        user.save.assert_called_once_with(update_fields=["is_staff", "is_superuser"])

    def it_does_not_grant_admin_when_domain_does_not_match(rf, settings):
        from plfog.adapters import AutoAdminSocialAccountAdapter

        settings.ADMIN_DOMAINS = ["example.com"]
        adapter = AutoAdminSocialAccountAdapter()
        request = rf.get("/")

        with _patch_super_save_user("user@other.com", "user"):
            user = adapter.save_user(request, _make_social_login("user@other.com"))

        assert user.is_staff is False
        assert user.is_superuser is False


def describe_save_user_with_multiple_domains():
    def it_grants_admin_for_any_matching_domain(rf, settings):
        from plfog.adapters import AutoAdminSocialAccountAdapter

        settings.ADMIN_DOMAINS = ["pastlives.space", "roaming-panda.com"]
        adapter = AutoAdminSocialAccountAdapter()
        request = rf.get("/")

        with _patch_super_save_user("mark@roaming-panda.com", "mark"):
            user = adapter.save_user(request, _make_social_login("mark@roaming-panda.com"))

        assert user.is_staff is True
        assert user.is_superuser is True


def describe_save_user_case_insensitivity():
    def it_matches_uppercase_email_domain(rf, settings):
        from plfog.adapters import AutoAdminSocialAccountAdapter

        settings.ADMIN_DOMAINS = ["pastlives.space"]
        adapter = AutoAdminSocialAccountAdapter()
        request = rf.get("/")

        with _patch_super_save_user("User@PASTLIVES.SPACE", "User"):
            user = adapter.save_user(request, _make_social_login("User@PASTLIVES.SPACE"))

        assert user.is_staff is True
        assert user.is_superuser is True


def describe_maybe_grant_admin_edge_cases():
    def it_skips_user_with_empty_email(settings):
        from plfog.adapters import AutoAdminSocialAccountAdapter

        settings.ADMIN_DOMAINS = ["example.com"]
        adapter = AutoAdminSocialAccountAdapter()

        user = MagicMock()
        user.email = ""
        user.is_staff = False
        user.is_superuser = False
        adapter._maybe_grant_admin(user)

        user.save.assert_not_called()

    def it_skips_user_with_email_without_at_sign(settings):
        from plfog.adapters import AutoAdminSocialAccountAdapter

        settings.ADMIN_DOMAINS = ["example.com"]
        adapter = AutoAdminSocialAccountAdapter()

        user = MagicMock()
        user.email = "noemail"
        user.is_staff = False
        user.is_superuser = False
        adapter._maybe_grant_admin(user)

        user.save.assert_not_called()

    def it_skips_user_with_none_email(settings):
        from plfog.adapters import AutoAdminSocialAccountAdapter

        settings.ADMIN_DOMAINS = ["example.com"]
        adapter = AutoAdminSocialAccountAdapter()

        user = MagicMock()
        user.email = None
        user.is_staff = False
        user.is_superuser = False
        adapter._maybe_grant_admin(user)

        user.save.assert_not_called()

    def it_skips_when_admin_domains_not_configured(settings):
        from plfog.adapters import AutoAdminSocialAccountAdapter

        if hasattr(settings, "ADMIN_DOMAINS"):
            delattr(settings, "ADMIN_DOMAINS")
        adapter = AutoAdminSocialAccountAdapter()

        user = MagicMock()
        user.email = "user@example.com"
        user.is_staff = False
        user.is_superuser = False
        adapter._maybe_grant_admin(user)

        user.save.assert_not_called()


def describe_maybe_grant_admin_matching():
    def it_sets_is_staff_and_is_superuser(settings):
        from plfog.adapters import AutoAdminSocialAccountAdapter

        settings.ADMIN_DOMAINS = ["example.com"]
        adapter = AutoAdminSocialAccountAdapter()

        user = MagicMock()
        user.email = "admin@example.com"
        user.is_staff = False
        user.is_superuser = False
        adapter._maybe_grant_admin(user)

        assert user.is_staff is True
        assert user.is_superuser is True
        user.save.assert_called_once_with(update_fields=["is_staff", "is_superuser"])

    def it_does_not_match_subdomains(settings):
        from plfog.adapters import AutoAdminSocialAccountAdapter

        settings.ADMIN_DOMAINS = ["example.com"]
        adapter = AutoAdminSocialAccountAdapter()

        user = MagicMock()
        user.email = "user@sub.example.com"
        user.is_staff = False
        user.is_superuser = False
        adapter._maybe_grant_admin(user)

        user.save.assert_not_called()

    def it_skips_save_when_user_already_has_admin_privileges(settings):
        from plfog.adapters import AutoAdminSocialAccountAdapter

        settings.ADMIN_DOMAINS = ["example.com"]
        adapter = AutoAdminSocialAccountAdapter()

        user = MagicMock()
        user.email = "admin@example.com"
        user.is_staff = True
        user.is_superuser = True
        adapter._maybe_grant_admin(user)

        user.save.assert_not_called()

    def it_upgrades_when_only_is_staff_is_true(settings):
        from plfog.adapters import AutoAdminSocialAccountAdapter

        settings.ADMIN_DOMAINS = ["example.com"]
        adapter = AutoAdminSocialAccountAdapter()

        user = MagicMock()
        user.email = "admin@example.com"
        user.is_staff = True
        user.is_superuser = False
        adapter._maybe_grant_admin(user)

        assert user.is_staff is True
        assert user.is_superuser is True
        user.save.assert_called_once_with(update_fields=["is_staff", "is_superuser"])

    def it_upgrades_when_only_is_superuser_is_true(settings):
        from plfog.adapters import AutoAdminSocialAccountAdapter

        settings.ADMIN_DOMAINS = ["example.com"]
        adapter = AutoAdminSocialAccountAdapter()

        user = MagicMock()
        user.email = "admin@example.com"
        user.is_staff = False
        user.is_superuser = True
        adapter._maybe_grant_admin(user)

        assert user.is_staff is True
        assert user.is_superuser is True
        user.save.assert_called_once_with(update_fields=["is_staff", "is_superuser"])


def describe_maybe_grant_admin_logging():
    def it_logs_when_admin_is_granted(settings, caplog):
        from plfog.adapters import AutoAdminSocialAccountAdapter

        settings.ADMIN_DOMAINS = ["example.com"]
        adapter = AutoAdminSocialAccountAdapter()

        user = MagicMock()
        user.email = "admin@example.com"
        user.is_staff = False
        user.is_superuser = False

        with caplog.at_level(logging.INFO, logger="plfog.adapters"):
            adapter._maybe_grant_admin(user)

        assert "Auto-admin granted to admin@example.com" in caplog.text
        assert "domain: example.com" in caplog.text

    def it_does_not_log_when_already_admin(settings, caplog):
        from plfog.adapters import AutoAdminSocialAccountAdapter

        settings.ADMIN_DOMAINS = ["example.com"]
        adapter = AutoAdminSocialAccountAdapter()

        user = MagicMock()
        user.email = "admin@example.com"
        user.is_staff = True
        user.is_superuser = True

        with caplog.at_level(logging.INFO, logger="plfog.adapters"):
            adapter._maybe_grant_admin(user)

        assert "Auto-admin granted" not in caplog.text


def describe_pre_social_login():
    """Tests for the pre_social_login hook — promotes existing users on every login."""

    def it_upgrades_existing_user_with_matching_domain(rf, settings):
        from plfog.adapters import AutoAdminSocialAccountAdapter

        settings.ADMIN_DOMAINS = ["roaming-panda.com"]
        adapter = AutoAdminSocialAccountAdapter()
        request = rf.get("/")

        user = User.objects.create_user(
            username="mark",
            email="mark@roaming-panda.com",
            password="testpass",
        )
        assert user.is_staff is False
        assert user.is_superuser is False

        sociallogin = _make_existing_social_login(user)
        adapter.pre_social_login(request, sociallogin)

        user.refresh_from_db()
        assert user.is_staff is True
        assert user.is_superuser is True

    def it_does_not_upgrade_existing_user_with_non_matching_domain(rf, settings):
        from plfog.adapters import AutoAdminSocialAccountAdapter

        settings.ADMIN_DOMAINS = ["roaming-panda.com"]
        adapter = AutoAdminSocialAccountAdapter()
        request = rf.get("/")

        user = User.objects.create_user(
            username="outsider",
            email="outsider@gmail.com",
            password="testpass",
        )

        sociallogin = _make_existing_social_login(user)
        adapter.pre_social_login(request, sociallogin)

        user.refresh_from_db()
        assert user.is_staff is False
        assert user.is_superuser is False

    def it_skips_save_when_existing_user_already_has_admin(rf, settings):
        from plfog.adapters import AutoAdminSocialAccountAdapter

        settings.ADMIN_DOMAINS = ["roaming-panda.com"]
        adapter = AutoAdminSocialAccountAdapter()
        request = rf.get("/")

        user = User.objects.create_user(
            username="mark",
            email="mark@roaming-panda.com",
            password="testpass",
            is_staff=True,
            is_superuser=True,
        )

        # Spy on save to verify it is NOT called
        with patch.object(User, "save", wraps=user.save) as mock_save:
            sociallogin = _make_existing_social_login(user)
            adapter.pre_social_login(request, sociallogin)

            mock_save.assert_not_called()

    def it_does_not_promote_new_unsaved_users(rf, settings):
        """pre_social_login should skip users without a pk (new users).

        New users are handled by save_user() instead.
        """
        from plfog.adapters import AutoAdminSocialAccountAdapter

        settings.ADMIN_DOMAINS = ["example.com"]
        adapter = AutoAdminSocialAccountAdapter()
        request = rf.get("/")

        sociallogin = _make_social_login("newuser@example.com")
        # sociallogin.user has no pk (unsaved User instance)
        assert sociallogin.user.pk is None

        adapter.pre_social_login(request, sociallogin)

        # User should NOT have been promoted (no pk = new user, save_user handles it)
        assert sociallogin.user.is_staff is False
        assert sociallogin.user.is_superuser is False

    def it_upgrades_with_multiple_admin_domains(rf, settings):
        from plfog.adapters import AutoAdminSocialAccountAdapter

        settings.ADMIN_DOMAINS = ["pastlives.space", "roaming-panda.com"]
        adapter = AutoAdminSocialAccountAdapter()
        request = rf.get("/")

        user = User.objects.create_user(
            username="mark",
            email="mark@roaming-panda.com",
            password="testpass",
        )

        sociallogin = _make_existing_social_login(user)
        adapter.pre_social_login(request, sociallogin)

        user.refresh_from_db()
        assert user.is_staff is True
        assert user.is_superuser is True

    def it_handles_case_insensitive_email_domain(rf, settings):
        from plfog.adapters import AutoAdminSocialAccountAdapter

        settings.ADMIN_DOMAINS = ["roaming-panda.com"]
        adapter = AutoAdminSocialAccountAdapter()
        request = rf.get("/")

        user = User.objects.create_user(
            username="mark",
            email="Mark@ROAMING-PANDA.COM",
            password="testpass",
        )

        sociallogin = _make_existing_social_login(user)
        adapter.pre_social_login(request, sociallogin)

        user.refresh_from_db()
        assert user.is_staff is True
        assert user.is_superuser is True

    def it_skips_when_admin_domains_is_empty(rf, settings):
        from plfog.adapters import AutoAdminSocialAccountAdapter

        settings.ADMIN_DOMAINS = []
        adapter = AutoAdminSocialAccountAdapter()
        request = rf.get("/")

        user = User.objects.create_user(
            username="mark",
            email="mark@roaming-panda.com",
            password="testpass",
        )

        sociallogin = _make_existing_social_login(user)
        adapter.pre_social_login(request, sociallogin)

        user.refresh_from_db()
        assert user.is_staff is False
        assert user.is_superuser is False

    def it_logs_promotion_for_existing_user(rf, settings, caplog):
        from plfog.adapters import AutoAdminSocialAccountAdapter

        settings.ADMIN_DOMAINS = ["roaming-panda.com"]
        adapter = AutoAdminSocialAccountAdapter()
        request = rf.get("/")

        user = User.objects.create_user(
            username="mark",
            email="mark@roaming-panda.com",
            password="testpass",
        )

        sociallogin = _make_existing_social_login(user)
        with caplog.at_level(logging.INFO, logger="plfog.adapters"):
            adapter.pre_social_login(request, sociallogin)

        assert "Auto-admin granted to mark@roaming-panda.com" in caplog.text

    def it_upgrades_user_with_only_is_staff(rf, settings):
        """User with is_staff=True but is_superuser=False should be upgraded."""
        from plfog.adapters import AutoAdminSocialAccountAdapter

        settings.ADMIN_DOMAINS = ["roaming-panda.com"]
        adapter = AutoAdminSocialAccountAdapter()
        request = rf.get("/")

        user = User.objects.create_user(
            username="mark",
            email="mark@roaming-panda.com",
            password="testpass",
            is_staff=True,
            is_superuser=False,
        )

        sociallogin = _make_existing_social_login(user)
        adapter.pre_social_login(request, sociallogin)

        user.refresh_from_db()
        assert user.is_staff is True
        assert user.is_superuser is True


def _make_request_with_user(rf: RequestFactory, *, is_staff: bool, is_superuser: bool) -> object:
    """Create a GET request with an attached user having the given flags."""
    request = rf.get("/accounts/google/login/callback/")
    user = MagicMock()
    user.is_staff = is_staff
    user.is_superuser = is_superuser
    request.user = user
    return request


def describe_AdminRedirectAccountAdapter():
    def describe_get_login_redirect_url():
        def it_redirects_staff_to_admin(rf):
            from plfog.adapters import AdminRedirectAccountAdapter

            adapter = AdminRedirectAccountAdapter()
            request = _make_request_with_user(rf, is_staff=True, is_superuser=False)

            url = adapter.get_login_redirect_url(request)

            assert url == "/admin/"

        def it_redirects_non_staff_to_default(rf):
            from plfog.adapters import AdminRedirectAccountAdapter

            adapter = AdminRedirectAccountAdapter()
            request = _make_request_with_user(rf, is_staff=False, is_superuser=False)

            url = adapter.get_login_redirect_url(request)

            assert url == "/"

        def it_redirects_staff_superuser_to_admin(rf):
            from plfog.adapters import AdminRedirectAccountAdapter

            adapter = AdminRedirectAccountAdapter()
            request = _make_request_with_user(rf, is_staff=True, is_superuser=True)

            url = adapter.get_login_redirect_url(request)

            assert url == "/admin/"

        def it_redirects_superuser_without_staff_to_default(rf):
            from plfog.adapters import AdminRedirectAccountAdapter

            adapter = AdminRedirectAccountAdapter()
            request = _make_request_with_user(rf, is_staff=False, is_superuser=True)

            url = adapter.get_login_redirect_url(request)

            assert url == "/"

        def describe_custom_login_redirect_url():
            @override_settings(LOGIN_REDIRECT_URL="/dashboard/")
            def it_respects_custom_url_for_non_staff(rf):
                from plfog.adapters import AdminRedirectAccountAdapter

                adapter = AdminRedirectAccountAdapter()
                request = _make_request_with_user(rf, is_staff=False, is_superuser=False)

                url = adapter.get_login_redirect_url(request)

                assert url == "/dashboard/"

            @override_settings(LOGIN_REDIRECT_URL="/dashboard/")
            def it_ignores_custom_url_for_staff(rf):
                from plfog.adapters import AdminRedirectAccountAdapter

                adapter = AdminRedirectAccountAdapter()
                request = _make_request_with_user(rf, is_staff=True, is_superuser=False)

                url = adapter.get_login_redirect_url(request)

                assert url == "/admin/"
