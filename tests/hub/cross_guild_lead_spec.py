"""A guild's lead edits their own guild and nobody else's (ADR 0004).

The retired Guild Officer role edited every guild and class, and it had drifted onto nearly every
lead and Co-Lead, so a lead could change another guild's settings. With the role gone, the only
cross-guild tier is admin: own guild = leadership, every guild = admin. These are crafted POSTs
from a real login, because a gate that only hides a button is decoration.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from classes.factories import CategoryFactory, ClassOfferingFactory
from classes.models import ClassOffering
from membership.models import Guild, Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

AUTOSAVE = {"HTTP_X_AUTOSAVE": "1"}


def _lead_of(name: str, client: Client) -> tuple[Member, Guild]:
    """A plain member (no admin role) who leads a guild called ``name``, signed in on ``client``."""
    MembershipPlanFactory()
    user = User.objects.create_user(username=f"{name.lower()}-lead", password="pass")
    member = Member.objects.get(user=user)
    assert member.fog_role == Member.FogRole.MEMBER
    guild = GuildFactory(name=name, guild_lead=member)
    client.login(username=user.username, password="pass")
    return member, guild


def describe_a_guild_lead_posting_to_another_guild():
    def it_cannot_save_another_guilds_settings(client: Client):
        _lead_of("Ceramics", client)
        other = GuildFactory(name="Woodshop", about="Original about")

        response = client.post(reverse("hub_guild_edit", args=[other.pk]), {"name": "Hijacked", "about": "Taken over"})

        assert response.status_code == 403
        other.refresh_from_db()
        assert (other.name, other.about) == ("Woodshop", "Original about")

    def it_cannot_save_another_guilds_settings_through_autosave(client: Client):
        _lead_of("Ceramics", client)
        other = GuildFactory(name="Woodshop")

        response = client.post(reverse("hub_guild_edit", args=[other.pk]), {"name": "Hijacked"}, **AUTOSAVE)

        assert response.status_code == 403
        other.refresh_from_db()
        assert other.name == "Woodshop"

    def it_cannot_edit_a_class_in_another_guild(client: Client):
        _lead_of("Ceramics", client)
        offering = ClassOfferingFactory(
            title="Intro to Joinery", slug="cross-guild-joinery", category=CategoryFactory(guild=GuildFactory())
        )

        response = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}), {"title": "Hijacked class"}
        )

        assert response.status_code == 404
        offering.refresh_from_db()
        assert offering.title == "Intro to Joinery"

    def it_still_saves_their_own_guilds_settings(client: Client):
        _lead, own = _lead_of("Ceramics", client)

        response = client.post(reverse("hub_guild_edit", args=[own.pk]), {"name": "Ceramics Studio"}, **AUTOSAVE)

        assert response.status_code == 200
        own.refresh_from_db()
        assert own.name == "Ceramics Studio"


def describe_editable_by_for_a_lead():
    def it_lists_their_own_guilds_class_and_not_another_guilds(client: Client):
        lead, own = _lead_of("Ceramics", client)
        mine = ClassOfferingFactory(slug="cross-own", category=CategoryFactory(guild=own))
        theirs = ClassOfferingFactory(slug="cross-theirs", category=CategoryFactory(guild=GuildFactory()))

        editable = ClassOffering.objects.editable_by(lead)

        assert mine in editable
        assert theirs not in editable
