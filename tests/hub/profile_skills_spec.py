"""Profile settings: commissions fields + skills section on the profile tab."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import Member
from tests.membership.factories import MembershipPlanFactory


def _login(client: Client) -> Member:
    """Log in a regular member, auto-provisioned by the user-create signal."""
    MembershipPlanFactory()
    user = User.objects.create_user(username="ed", password="pw")
    member = user.member
    client.login(username="ed", password="pw")
    return member


@pytest.mark.django_db
def describe_profile_commissions():
    def it_saves_open_for_commissions_and_note(client: Client):
        member = _login(client)
        data = {
            "form_id": "profile",
            "preferred_name": "Jo",
            "open_for_commissions": "on",
            "commission_note": "Custom woodworking welcome!",
            "show_in_directory": "on",
        }
        client.post(reverse("hub_user_settings"), data)
        member.refresh_from_db()
        assert member.open_for_commissions is True
        assert member.commission_note == "Custom woodworking welcome!"

    def it_renders_skills_section_on_profile_tab(client: Client):
        _login(client)
        resp = client.get(reverse("hub_user_settings"))
        assert b"profile-skills" in resp.content
