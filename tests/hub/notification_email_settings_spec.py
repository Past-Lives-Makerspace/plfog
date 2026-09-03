"""The Settings → Account notification-email picker (form, endpoint, and card states).

The picker renders only for a member with a real choice (2+ verified addresses);
one verified address shows a muted explainer, zero addresses and memberless users
see nothing new. The POST endpoint saves only a verified choice — a tampered
value writes nothing and redirects back with an error banner.
"""

from __future__ import annotations

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from hub.forms import NotificationEmailForm
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db

_PICKER_URL_NAME = "hub_notification_email_set"
_SAVE_BUTTON = "Save notification email"
_MUTED_LINE = "Add and verify another address to choose a different one."


def _member_user(username: str) -> User:
    """A logged-in-able User whose Member is auto-provisioned (a plan must exist first).

    Provisioning also mints a VERIFIED PRIMARY EmailAddress for ``user.email``
    (``MemberEmail.objects.migrate_to_user``), so every user built here already has
    exactly one verified address: ``{username}@example.com``.
    """
    MembershipPlanFactory()
    return User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")


def _secondary(user: User, email: str, *, verified: bool = True) -> EmailAddress:
    return EmailAddress.objects.create(user=user, email=email, verified=verified, primary=False)


def _account_tab(client: Client) -> str:
    return client.get("/settings/?tab=account").content.decode()


def describe_NotificationEmailForm():
    def it_offers_only_verified_addresses_plus_the_default_choice():
        user = _member_user("notif_form")
        _secondary(user, "two@example.com")
        _secondary(user, "pending@example.com", verified=False)

        choices = list(NotificationEmailForm(user).fields["notification_email"].choices)

        assert choices[0] == ("", "Primary email (default)")
        assert ("notif_form@example.com", "notif_form@example.com") in choices
        assert ("two@example.com", "two@example.com") in choices
        assert all("pending@example.com" not in choice for choice, _label in choices[1:])


def describe_notification_email_set():
    def it_saves_a_verified_secondary_address():
        user = _member_user("notif_save")
        _secondary(user, "workshop@example.com")
        client = Client()
        client.force_login(user)

        response = client.post(reverse(_PICKER_URL_NAME), {"notification_email": "workshop@example.com"}, follow=True)

        # Fresh query — ``user.member`` may hold a pre-POST cached instance (force_login).
        assert Member.objects.get(user=user).notification_email == "workshop@example.com"
        assert "Notification email updated." in response.content.decode()

    def it_saves_blank_to_follow_the_primary_again():
        user = _member_user("notif_blank")
        _secondary(user, "workshop@example.com")
        user.member.notification_email = "workshop@example.com"
        user.member.save(update_fields=["notification_email"])
        client = Client()
        client.force_login(user)

        client.post(reverse(_PICKER_URL_NAME), {"notification_email": ""})

        user.member.refresh_from_db()
        assert user.member.notification_email == ""

    def it_writes_nothing_for_an_unlisted_address_and_redirects_with_the_error():
        user = _member_user("notif_tamper")
        _secondary(user, "workshop@example.com")
        client = Client()
        client.force_login(user)

        response = client.post(reverse(_PICKER_URL_NAME), {"notification_email": "evil@example.com"}, follow=True)

        user.member.refresh_from_db()
        assert user.member.notification_email == ""
        assert "Choose one of your verified addresses." in response.content.decode()

    def it_writes_nothing_for_an_unverified_own_address():
        user = _member_user("notif_unver")
        _secondary(user, "pending@example.com", verified=False)
        client = Client()
        client.force_login(user)

        client.post(reverse(_PICKER_URL_NAME), {"notification_email": "pending@example.com"})

        user.member.refresh_from_db()
        assert user.member.notification_email == ""

    def it_redirects_a_memberless_user_with_the_not_linked_error():
        user = _member_user("notif_nomember")
        Member.objects.filter(user=user).delete()
        client = Client()
        client.force_login(user)

        response = client.post(reverse(_PICKER_URL_NAME), {"notification_email": "x@example.com"})

        assert response.status_code == 302
        assert response["Location"] == reverse("hub_user_settings")


def describe_account_tab_picker_states():
    def it_shows_the_picker_with_two_verified_addresses():
        user = _member_user("notif_two")
        _secondary(user, "workshop@example.com")
        client = Client()
        client.force_login(user)

        content = _account_tab(client)

        assert reverse(_PICKER_URL_NAME) in content
        assert _SAVE_BUTTON in content

    def it_shows_the_muted_line_with_exactly_one_verified_address():
        user = _member_user("notif_one")
        client = Client()
        client.force_login(user)

        content = _account_tab(client)

        assert reverse(_PICKER_URL_NAME) not in content
        assert _MUTED_LINE in content

    def it_shows_the_muted_line_when_the_second_address_is_unverified():
        user = _member_user("notif_pend")
        _secondary(user, "pending@example.com", verified=False)
        client = Client()
        client.force_login(user)

        content = _account_tab(client)

        assert reverse(_PICKER_URL_NAME) not in content
        assert _MUTED_LINE in content

    def it_shows_nothing_extra_with_zero_addresses_on_file():
        user = _member_user("notif_zero")
        EmailAddress.objects.filter(user=user).delete()
        client = Client()
        client.force_login(user)

        content = _account_tab(client)

        assert "No email addresses on file yet." in content
        assert reverse(_PICKER_URL_NAME) not in content
        assert _MUTED_LINE not in content

    def it_shows_nothing_for_a_memberless_user_even_with_verified_addresses():
        user = _member_user("notif_ghost")
        _secondary(user, "workshop@example.com")
        Member.objects.filter(user=user).delete()
        client = Client()
        client.force_login(user)

        content = _account_tab(client)

        assert reverse(_PICKER_URL_NAME) not in content
        assert _MUTED_LINE not in content

    def it_renders_a_dangling_stored_value_as_the_default_choice():
        user = _member_user("notif_dangling")
        _secondary(user, "workshop@example.com")
        user.member.notification_email = "gone@example.com"
        user.member.save(update_fields=["notification_email"])
        client = Client()
        client.force_login(user)

        content = _account_tab(client)

        assert '<option value="" selected>Primary email (default)</option>' in content
        assert 'value="gone@example.com"' not in content

    def it_renders_a_case_differing_stored_value_as_the_canonical_address():
        user = _member_user("notif_case")
        _secondary(user, "workshop@example.com")
        user.member.notification_email = "Workshop@Example.com"
        user.member.save(update_fields=["notification_email"])
        client = Client()
        client.force_login(user)

        content = _account_tab(client)

        assert '<option value="workshop@example.com" selected>workshop@example.com</option>' in content
        assert '<option value="" selected>Primary email (default)</option>' not in content
