"""Member directory: labeled contacts render only when flagged for the directory."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import Member
from tests.membership.factories import MemberContactFactory, MemberFactory, MembershipPlanFactory


def _login(client: Client) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username="viewer", password="pw")
    member = user.member
    member.show_in_directory = True
    member.save(update_fields=["show_in_directory"])
    client.login(username="viewer", password="pw")
    return member


@pytest.mark.django_db
def describe_member_directory_contacts():
    def it_shows_a_contact_flagged_for_the_directory(client: Client):
        _login(client)
        member = MemberFactory(show_in_directory=True, full_legal_name="Connie Contact")
        MemberContactFactory(member=member, label="Instagram", value="@connie_makes", show_in_directory=True)

        resp = client.get(reverse("hub_member_directory"))
        content = resp.content.decode()

        assert "Instagram" in content
        assert "@connie_makes" in content

    def it_links_a_url_contact(client: Client):
        _login(client)
        member = MemberFactory(show_in_directory=True, full_legal_name="Wanda Web")
        MemberContactFactory(member=member, label="Website", value="https://wanda.example", show_in_directory=True)

        resp = client.get(reverse("hub_member_directory"))

        assert b'href="https://wanda.example"' in resp.content

    def it_renders_a_social_contact_with_its_platform_icon(client: Client):
        _login(client)
        member = MemberFactory(show_in_directory=True, full_legal_name="Sonia Social")
        MemberContactFactory(member=member, label="Instagram", value="https://instagram.example/sonia", kind="social")

        resp = client.get(reverse("hub_member_directory"))
        content = resp.content.decode()

        assert "pl-social-icon" in content
        assert 'href="https://instagram.example/sonia"' in content

    def it_renders_a_website_contact_in_the_website_group_with_the_link_icon(client: Client):
        _login(client)
        member = MemberFactory(show_in_directory=True, full_legal_name="Webb Site")
        MemberContactFactory(member=member, label="Portfolio", value="https://webb.example", kind="website")

        resp = client.get(reverse("hub_member_directory"))
        content = resp.content.decode()

        assert "pl-social-icon" in content
        assert 'href="https://webb.example"' in content

    def it_renders_an_other_contact_in_the_legacy_plain_style(client: Client):
        _login(client)
        member = MemberFactory(show_in_directory=True, full_legal_name="Otto Other")
        MemberContactFactory(member=member, label="Signal", value="@otto_signal", kind="other")

        resp = client.get(reverse("hub_member_directory"))
        content = resp.content.decode()

        assert "Signal" in content
        assert "@otto_signal" in content
        assert "pl-social-icon" not in content

    def it_hides_a_contact_not_flagged_for_the_directory(client: Client):
        _login(client)
        member = MemberFactory(show_in_directory=True, full_legal_name="Hank Hidden")
        MemberContactFactory(member=member, label="PrivateSignal", value="hidden-value", show_in_directory=False)

        resp = client.get(reverse("hub_member_directory"))
        content = resp.content.decode()

        assert "PrivateSignal" not in content
        assert "hidden-value" not in content
