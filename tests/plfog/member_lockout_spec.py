"""Former and suspended members are turned away from the members site (#409).

Drives the real allauth request-code -> confirm-code flow, the biometric unlock and a live
session through the Django test client. The gate reads ``Member.status`` on the members
surface only; ``User.is_active`` is never touched, so the book site still lets them in.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connection
from django.http import Http404
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.member_lockout import lockout_message, lockout_reason
from core.models import BiometricCredential, SiteConfiguration
from membership.models import Member
from membership.services.provisioning import provision_user_for_member
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db

BOOK_HOST = "book.pastlives.space"
FORMER_COPY = "Your membership is no longer active, so this account can no longer sign in to the member site."
SUSPENDED_COPY = "Your membership is paused right now, so this account cannot sign in to the member site."


@pytest.fixture(autouse=True)
def _clear_cache():
    """The biometric limiter counts in the cache, which outlives a single test."""
    cache.clear()
    yield
    cache.clear()


def _user_with_status(username: str, status: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    """A login-ready User (verified primary email) whose Member has ``status``."""
    member = MemberFactory(_pre_signup_email=f"{username}@example.com", fog_role=fog_role)
    provision_user_for_member(member)
    member.status = status
    member.save(update_fields=["status"])
    member.sync_user_permissions()
    user = member.user
    user.refresh_from_db()
    return user


def _sign_in_by_code(client: Client, email: str) -> object:
    """Run the request-code then confirm-code steps with a known code; return the confirm response."""
    with patch("allauth.account.adapter.DefaultAccountAdapter.generate_login_code", return_value="ABCDEF"):
        client.post(reverse("account_request_login_code"), {"email": email})
    return client.post(reverse("account_confirm_login_code"), {"code": "ABCDEF"})


def _signed_in_user_id(client: Client) -> str | None:
    return client.session.get("_auth_user_id")


def _locked_url(reason: str) -> str:
    return f"{reverse('account_locked')}?reason={reason}"


def describe_signing_in_by_code():
    def it_turns_a_former_member_away_with_the_former_message(client):
        user = _user_with_status("gone", Member.Status.FORMER)

        response = _sign_in_by_code(client, user.email)

        assert response.status_code == 302
        assert response["Location"] == _locked_url("former")
        assert _signed_in_user_id(client) is None
        page = client.get(response["Location"])
        assert FORMER_COPY in page.content.decode()
        assert b"This account is inactive." not in page.content

    def it_shows_the_support_email_on_the_lockout_page(client):
        user = _user_with_status("gone_contact", Member.Status.FORMER)

        response = _sign_in_by_code(client, user.email)

        page = client.get(response["Location"])
        assert b'id="account-locked-contact"' in page.content
        assert b"mailto:info@pastlives.space" in page.content

    def it_leaves_the_user_active_and_starts_the_next_attempt_clean(client):
        user = _user_with_status("gone_clean", Member.Status.FORMER)

        _sign_in_by_code(client, user.email)

        user.refresh_from_db()
        assert user.is_active is True
        # The half-finished login-code stage is cleared, so the code form is not pending.
        assert client.get(reverse("account_confirm_login_code")).status_code == 302

    def it_lets_an_invited_member_in(client):
        user = _user_with_status("invited", Member.Status.INVITED)

        _sign_in_by_code(client, user.email)

        assert _signed_in_user_id(client) == str(user.pk)

    def it_lets_an_active_member_in(client):
        user = _user_with_status("active", Member.Status.ACTIVE)

        _sign_in_by_code(client, user.email)

        assert _signed_in_user_id(client) == str(user.pk)

    def it_lets_a_user_with_no_member_row_in(client):
        user = User.objects.create_user(username="bookonly", email="bookonly@example.com")
        Member.objects.filter(user=user).delete()
        from allauth.account.models import EmailAddress

        EmailAddress.objects.update_or_create(user=user, email=user.email, defaults={"verified": True, "primary": True})
        user = User.objects.get(pk=user.pk)
        assert getattr(user, "member", None) is None

        _sign_in_by_code(client, user.email)

        assert _signed_in_user_id(client) == str(user.pk)

    def it_turns_away_a_former_member_who_is_a_superuser(client):
        user = _user_with_status("oldadmin", Member.Status.FORMER, fog_role=Member.FogRole.ADMIN)
        assert user.is_superuser and user.is_staff

        response = _sign_in_by_code(client, user.email)

        assert response["Location"] == _locked_url("former")
        assert _signed_in_user_id(client) is None

    def it_lets_a_reactivated_member_back_in(client):
        user = _user_with_status("returning", Member.Status.FORMER)
        _sign_in_by_code(client, user.email)
        assert _signed_in_user_id(client) is None

        Member.objects.filter(user=user).update(status=Member.Status.ACTIVE)
        _sign_in_by_code(client, user.email)

        assert _signed_in_user_id(client) == str(user.pk)

    def describe_a_suspended_member():
        def it_is_turned_away_with_its_own_message_by_default(client):
            user = _user_with_status("paused", Member.Status.SUSPENDED)

            response = _sign_in_by_code(client, user.email)

            assert response["Location"] == _locked_url("suspended")
            assert _signed_in_user_id(client) is None
            page = client.get(response["Location"]).content.decode()
            assert SUSPENDED_COPY in page
            assert FORMER_COPY not in page

        def it_signs_in_when_the_switch_is_off(client):
            config = SiteConfiguration.load()
            config.suspended_members_locked_out = False
            config.save()
            user = _user_with_status("paused_ok", Member.Status.SUSPENDED)

            _sign_in_by_code(client, user.email)

            assert _signed_in_user_id(client) == str(user.pk)


@pytest.fixture()
def book_client():
    """A client on the book host, with the book host configured as the public surface."""
    with override_settings(ALLOWED_HOSTS=["testserver", BOOK_HOST], PUBLIC_HOSTS=[BOOK_HOST]):
        yield Client(HTTP_HOST=BOOK_HOST)


BLOCKED_BOOK_PATHS = [
    "/home/",
    "/leadership/",
    "/equipment/",
    "/spaces/",
    "/help/",
    "/calendar/",
    "/meetings/",
    "/api/v1/",
    "/o/authorize/",
]


def describe_the_book_surface():
    def it_still_lets_a_former_member_sign_in(book_client):
        user = _user_with_status("receipts", Member.Status.FORMER)

        _sign_in_by_code(book_client, user.email)

        assert _signed_in_user_id(book_client) == str(user.pk)

    def it_serves_a_former_member_their_bookings_and_the_catalog(book_client):
        user = _user_with_status("receipts_live", Member.Status.FORMER)
        book_client.force_login(user)

        assert book_client.get(reverse("account:overview")).status_code == 200
        assert book_client.get("/classes/").status_code == 200
        assert _signed_in_user_id(book_client) == str(user.pk)

    @pytest.mark.parametrize("path", BLOCKED_BOOK_PATHS)
    def it_sends_a_former_member_to_the_lockout_page_from_anything_else(book_client, path):
        user = _user_with_status("book_walk", Member.Status.FORMER)
        book_client.force_login(user)

        response = book_client.get(path)

        assert response.status_code == 302
        assert response["Location"] == _locked_url("former")
        page = book_client.get(response["Location"])
        assert page.status_code == 200
        assert FORMER_COPY in page.content.decode()
        assert _signed_in_user_id(book_client) == str(user.pk)

    @pytest.mark.parametrize("path", ["/home/", "/equipment/", "/o/authorize/"])
    def it_leaves_an_active_member_alone(book_client, path):
        user = _user_with_status("book_active", Member.Status.ACTIVE)
        book_client.force_login(user)

        response = book_client.get(path)

        assert response.get("Location") != _locked_url("former")
        assert "/accounts/locked/" not in response.get("Location", "")


def describe_a_live_session():
    def it_lands_on_the_lockout_page_on_the_next_request_after_the_status_flips(client, book_client):
        user = _user_with_status("flipped", Member.Status.ACTIVE)
        client.force_login(user)
        assert client.get(reverse("hub_home")).status_code == 200

        Member.objects.filter(user=user).update(status=Member.Status.FORMER)
        response = client.get(reverse("hub_home"))

        assert response.status_code == 302
        assert response["Location"] == _locked_url("former")
        # Still signed in, so the book site (same session cookie in production) still works.
        assert _signed_in_user_id(client) == str(user.pk)
        book_client.cookies = client.cookies
        assert book_client.get(reverse("account:overview")).status_code == 200

    def it_lets_a_reactivated_member_back_into_the_hub(client):
        user = _user_with_status("flip_back", Member.Status.FORMER)
        client.force_login(user)
        assert client.get(reverse("hub_home"))["Location"] == _locked_url("former")

        Member.objects.filter(user=user).update(status=Member.Status.ACTIVE)

        assert client.get(reverse("hub_home")).status_code == 200

    def it_redirects_an_htmx_request_by_header(client):
        user = _user_with_status("flipped_htmx", Member.Status.FORMER)
        client.force_login(user)

        response = client.get(reverse("hub_home"), HTTP_HX_REQUEST="true")

        assert response.status_code == 200
        assert response["HX-Redirect"] == _locked_url("former")

    def it_sends_a_suspended_member_with_the_suspended_reason(client):
        user = _user_with_status("flipped_paused", Member.Status.SUSPENDED)
        client.force_login(user)

        response = client.get(reverse("hub_home"))

        assert response["Location"] == _locked_url("suspended")

    @pytest.mark.parametrize(
        "path", ["/health/", "/static/css/missing.css", "/accounts/logout/", "/accounts/locked/?reason=former"]
    )
    def it_leaves_the_lockout_page_logout_health_and_static_open(client, path):
        user = _user_with_status("flipped_open", Member.Status.FORMER)
        client.force_login(user)

        response = client.get(path)

        assert response.status_code != 302

    def it_leaves_an_active_member_alone(client):
        user = _user_with_status("staying", Member.Status.ACTIVE)
        client.force_login(user)

        assert client.get(reverse("hub_home")).status_code == 200


def describe_the_lockout_page():
    def it_offers_a_signed_in_member_their_bookings_and_a_log_out(client, settings):
        settings.BOOK_BASE_URL = "https://book.example.test"
        user = _user_with_status("page_signed_in", Member.Status.FORMER)
        client.force_login(user)

        page = client.get(_locked_url("former")).content.decode()

        assert 'href="https://book.example.test/account/"' in page
        assert f'href="{reverse("account_logout")}"' in page
        assert 'id="account-locked-login"' not in page

    def it_renders_on_the_book_host_for_a_signed_in_member(book_client):
        book_client.force_login(_user_with_status("page_book", Member.Status.FORMER))

        page = book_client.get(_locked_url("former"))

        assert page.status_code == 200
        assert b'id="account-locked-bookings"' in page.content

    def it_offers_an_anonymous_viewer_the_way_back_to_login(client):
        page = client.get(_locked_url("former")).content.decode()

        assert f'href="{reverse("account_login")}"' in page
        assert 'id="account-locked-bookings"' not in page
        assert 'id="account-locked-logout"' not in page


def describe_the_query_cost():
    def it_adds_no_member_query_to_an_ordinary_page(client, settings):
        user = _user_with_status("cheap", Member.Status.ACTIVE)
        client.force_login(user)

        def member_queries() -> int:
            with CaptureQueriesContext(connection) as queries:
                assert client.get(reverse("hub_home")).status_code == 200
            return sum('FROM "membership_member"' in q["sql"] for q in queries.captured_queries)

        with_gate = member_queries()
        settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if m != "core.middleware.MemberLockoutMiddleware"]
        without_gate = member_queries()

        # The gate's lookup is the cached ``request.user.member`` the rest of the request reads.
        assert with_gate == without_gate


def describe_the_biometric_unlock():
    def _unlock(client: Client, secret: str) -> object:
        return client.post(
            "/accounts/biometric/unlock/", data=json.dumps({"secret": secret}), content_type="application/json"
        )

    def it_refuses_a_former_member_with_the_message_and_kills_their_credentials(client):
        user = _user_with_status("bio_gone", Member.Status.FORMER)
        _credential, secret = BiometricCredential.objects.issue(
            user, device_label="iPhone", platform=BiometricCredential.Platform.IOS
        )

        response = _unlock(client, secret)

        assert response.status_code == 401
        assert response.json() == {"error": FORMER_COPY}
        assert _signed_in_user_id(client) is None
        assert BiometricCredential.objects.active_for(user).count() == 0

    def it_signs_in_an_active_member(client):
        user = _user_with_status("bio_ok", Member.Status.ACTIVE)
        _credential, secret = BiometricCredential.objects.issue(
            user, device_label="iPhone", platform=BiometricCredential.Platform.IOS
        )

        response = _unlock(client, secret)

        assert response.status_code == 200
        assert _signed_in_user_id(client) == str(user.pk)


def describe_the_messages():
    def it_renders_the_edited_former_message(client):
        config = SiteConfiguration.load()
        config.former_member_signin_message = "Thanks for being a member. Come back any time."
        config.save()

        page = client.get(_locked_url("former")).content.decode()

        assert "Thanks for being a member. Come back any time." in page
        assert FORMER_COPY not in page

    def it_renders_the_edited_suspended_message(client):
        config = SiteConfiguration.load()
        config.suspended_member_signin_message = "Your account is on hold. Talk to the front desk."
        config.save()

        page = client.get(_locked_url("suspended")).content.decode()

        assert "Your account is on hold. Talk to the front desk." in page

    def it_falls_back_to_the_default_when_a_message_is_cleared(client):
        config = SiteConfiguration.load()
        config.former_member_signin_message = "   "
        config.save()

        assert lockout_message("former") == FORMER_COPY

    def it_leaves_the_contact_line_out_when_there_is_no_support_email(client):
        config = SiteConfiguration.load()
        config.org_support_email = ""
        config.save()

        page = client.get(_locked_url("former"))

        assert b'id="account-locked-contact"' not in page.content

    def it_404s_an_unknown_reason(client):
        assert client.get(_locked_url("active")).status_code == 404
        with pytest.raises(Http404):
            lockout_message("")

    def it_saves_the_messages_and_the_switch_from_site_settings(client):
        User.objects.create_superuser(username="settings_admin", email="settings_admin@x.com", password="p")
        client.login(username="settings_admin", password="p")
        page = client.get(reverse("hub_admin_site_settings"))
        assert b'id="id_former_member_signin_message"' in page.content
        assert b'id="id_suspended_members_locked_out"' in page.content
        assert b'id="id_suspended_member_signin_message"' in page.content

        response = client.post(
            reverse("hub_admin_site_settings"),
            data={
                "org_name": "Past Lives Makerspace",
                "registration_mode": SiteConfiguration.RegistrationMode.OPEN,
                "member_event_policy": SiteConfiguration.MemberEventPolicy.APPROVAL,
                "late_cancel_notice_hours": "24",
                "late_cancel_grace_hours": "2",
                "classes_calendar_color": "#abcdef",
                "former_member_signin_message": "Edited former copy.",
                "suspended_member_signin_message": "Edited paused copy.",
                "submitted_tab": "general",
                "feeds-TOTAL_FORMS": "0",
                "feeds-INITIAL_FORMS": "0",
                "feeds-MIN_NUM_FORMS": "0",
                "feeds-MAX_NUM_FORMS": "1000",
            },
        )

        assert response.status_code == 302
        config = SiteConfiguration.load()
        assert config.former_member_signin_message == "Edited former copy."
        assert config.suspended_member_signin_message == "Edited paused copy."
        assert config.suspended_members_locked_out is False
        assert "Edited former copy." in client.get(_locked_url("former")).content.decode()


def describe_lockout_reason():
    def it_is_none_for_an_anonymous_user():
        from django.contrib.auth.models import AnonymousUser

        assert lockout_reason(AnonymousUser()) is None

    def it_names_each_status():
        assert lockout_reason(_user_with_status("r_f", Member.Status.FORMER)) == "former"
        assert lockout_reason(_user_with_status("r_s", Member.Status.SUSPENDED)) == "suspended"
        assert lockout_reason(_user_with_status("r_i", Member.Status.INVITED)) is None
        assert lockout_reason(_user_with_status("r_a", Member.Status.ACTIVE)) is None

    def it_does_not_lock_out_a_suspended_member_when_the_switch_is_off():
        config = SiteConfiguration.load()
        config.suspended_members_locked_out = False
        config.save()

        assert lockout_reason(_user_with_status("r_s_off", Member.Status.SUSPENDED)) is None
