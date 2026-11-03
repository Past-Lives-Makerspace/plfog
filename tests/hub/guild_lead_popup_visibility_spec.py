"""The Guild Lead popup, Email Guild Lead button and guild flyer follow the lead's directory visibility (#1099)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import Member
from tests.membership.factories import GuildFactory, MemberFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

FIELDS = {
    "phone": "503-555-0199",
    "discord_handle": "lena#4242",
    "pronouns": "she/her",
    "about_me": "I run the letterpress.",
}


def _login(client: Client, username: str = "viewer") -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pw", email=f"{username}@example.com")
    client.force_login(user)
    return user.member


def _lead(**visibility: bool) -> Member:
    return MemberFactory(
        full_legal_name="Lena Lead",
        _pre_signup_email="lena.lead@example.com",
        directory_visibility=visibility,
        **FIELDS,
    )


def _guild_page(client: Client, lead: Member) -> str:
    guild = GuildFactory(name="Writers Guild", guild_lead=lead)
    resp = client.get(f"/guilds/{guild.slug}/")
    assert resp.status_code == 200
    return resp.content.decode()


def describe_guild_lead_popup():
    @pytest.mark.parametrize("field", list(FIELDS))
    def it_hides_a_field_the_lead_set_to_hidden(client: Client, field: str):
        _login(client)
        body = _guild_page(client, _lead(**{field: False}))

        assert "guild-lead-profile" in body
        assert FIELDS[field] not in body

    @pytest.mark.parametrize("field", list(FIELDS))
    def it_shows_a_field_the_lead_left_public(client: Client, field: str):
        _login(client)
        body = _guild_page(client, _lead(**{field: True}))

        assert FIELDS[field] in body

    def it_hides_the_email_the_lead_set_to_hidden(client: Client):
        _login(client)
        body = _guild_page(client, _lead(email=False))

        assert "lena.lead@example.com" not in body
        assert "Email Guild Lead" not in body

    def it_shows_the_email_and_button_when_public(client: Client):
        _login(client)
        body = _guild_page(client, _lead())

        assert 'href="mailto:lena.lead@example.com"' in body
        assert "Email Guild Lead" in body

    def it_links_a_public_phone_for_dialing(client: Client):
        _login(client)
        body = _guild_page(client, _lead())

        assert 'href="tel:5035550199"' in body

    def it_renders_no_contact_rows_when_every_contact_is_hidden(client: Client):
        _login(client)
        body = _guild_page(client, _lead(email=False, phone=False, discord_handle=False))

        assert 'min-width:60px;">Phone' not in body
        assert 'min-width:60px;">Email' not in body
        assert 'min-width:60px;">Discord' not in body

    def it_gives_the_lead_the_same_view_as_other_members(client: Client):
        lead = _login(client, username="lena")
        for field, value in FIELDS.items():
            setattr(lead, field, value)
        lead.directory_visibility = {"phone": False}
        lead.save()

        body = _guild_page(client, lead)

        assert FIELDS["phone"] not in body
        assert FIELDS["discord_handle"] in body


def describe_guild_flyer_lead_email():
    def it_leaves_a_hidden_lead_email_off_the_flyer(client: Client):
        lead = _login(client, username="lena")
        lead.directory_visibility = {"email": False}
        lead.save(update_fields=["directory_visibility"])
        guild = GuildFactory(name="Writers Guild", guild_lead=lead, contact_email="")

        body = client.get(reverse("hub_guild_flyer", args=[guild.pk])).content.decode()

        assert "lena@example.com" not in body
        assert "Led by" in body

    def it_prints_a_public_lead_email_on_the_flyer(client: Client):
        lead = _login(client, username="lena")
        guild = GuildFactory(name="Writers Guild", guild_lead=lead, contact_email="")

        body = client.get(reverse("hub_guild_flyer", args=[guild.pk])).content.decode()

        assert "lena@example.com" in body
