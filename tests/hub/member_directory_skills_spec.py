"""Member directory: skill chips, commission badge, and skill/commission/search filters."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import Member
from tests.membership.factories import MemberFactory, MembershipPlanFactory, MemberSkillFactory, SkillFactory


def _login(client: Client) -> Member:
    """Log in a regular member, auto-provisioned by the user-create signal."""
    MembershipPlanFactory()
    user = User.objects.create_user(username="viewer", password="pw")
    member = user.member
    member.show_in_directory = True
    member.save(update_fields=["show_in_directory"])
    client.login(username="viewer", password="pw")
    return member


@pytest.mark.django_db
def describe_member_directory_skills():
    def it_filters_by_skill_slug(client: Client):
        _login(client)
        welder = MemberFactory(show_in_directory=True, full_legal_name="Wendy Welder")
        MemberFactory(show_in_directory=True, full_legal_name="Sandy Sewer")
        MemberSkillFactory(member=welder, skill=SkillFactory(name="Welding", slug="welding"))
        resp = client.get(reverse("hub_member_directory"), {"skill": "welding"})
        assert b"Wendy Welder" in resp.content
        assert b"Sandy Sewer" not in resp.content

    def it_filters_open_for_commissions(client: Client):
        _login(client)
        MemberFactory(show_in_directory=True, full_legal_name="Carla Commission", open_for_commissions=True)
        MemberFactory(show_in_directory=True, full_legal_name="Nina None", open_for_commissions=False)
        resp = client.get(reverse("hub_member_directory"), {"commissions": "1"})
        assert b"Carla Commission" in resp.content
        assert b"Nina None" not in resp.content

    def it_searches_skill_names(client: Client):
        _login(client)
        producer = MemberFactory(show_in_directory=True, full_legal_name="Polly Producer")
        MemberSkillFactory(member=producer, skill=SkillFactory(name="Music production", slug="music-production"))
        resp = client.get(reverse("hub_member_directory"), {"q": "music"})
        assert b"Polly Producer" in resp.content

    def it_never_surfaces_hidden_members_via_skill(client: Client):
        _login(client)
        hidden = MemberFactory(show_in_directory=False, full_legal_name="Henry Hidden")
        MemberSkillFactory(member=hidden, skill=SkillFactory(name="Welding", slug="welding"))
        resp = client.get(reverse("hub_member_directory"), {"skill": "welding"})
        assert b"Henry Hidden" not in resp.content


@pytest.mark.django_db
def describe_member_directory_skill_display():
    def it_shows_skill_chips_with_years(client: Client):
        _login(client)
        m = MemberFactory(show_in_directory=True, full_legal_name="Carl Coder")
        MemberSkillFactory(member=m, skill=SkillFactory(name="Coding", slug="coding"), years_experience=10)
        resp = client.get(reverse("hub_member_directory"))
        assert b"Coding" in resp.content
        assert b"10y" in resp.content

    def it_hides_skills_when_section_private(client: Client):
        _login(client)
        m = MemberFactory(
            show_in_directory=True,
            full_legal_name="Pat Private",
            directory_visibility={"skills": False},
        )
        MemberSkillFactory(member=m, skill=SkillFactory(name="Welding", slug="welding"))
        resp = client.get(reverse("hub_member_directory"))
        # The only member with a skill has it hidden, so no skill chips render on any card.
        # (The skill name still appears in the filter dropdown, so assert on the chip markup.)
        assert b"pl-skill-chip" not in resp.content

    def it_shows_open_for_commissions_badge(client: Client):
        _login(client)
        MemberFactory(
            show_in_directory=True,
            full_legal_name="Carla Commission",
            open_for_commissions=True,
            commission_note="Custom woodworking welcome!",
        )
        resp = client.get(reverse("hub_member_directory"))
        assert b"Open for commissions" in resp.content
        assert b"Custom woodworking welcome!" in resp.content
