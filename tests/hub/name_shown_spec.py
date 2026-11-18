"""Name other members see: a member chooses whether their legal name shows beside their preferred name."""

from __future__ import annotations

from html import unescape

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.test import Client
from django.urls import reverse

from hub.forms import MemberAdminEditForm, ProfileSettingsForm, name_shown_examples
from hub.templatetags.hub_tags import instructors_for, name_for
from membership.models import Member
from classes.factories import ClassOfferingFactory
from tests.membership.factories import GuildFactory, MemberFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

PASSWORD = "pw"


def _login(client: Client, username: str = "viewer", **fields: object) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password=PASSWORD)
    member = user.member
    for name, value in {"full_legal_name": "Quinn Ashdown", "preferred_name": "Wren", **fields}.items():
        setattr(member, name, value)
    member.save()
    client.login(username=username, password=PASSWORD)
    return member


def describe_the_settings_choice():
    def it_offers_each_choice_as_the_members_own_names_would_read(client: Client):
        _login(client)

        html = unescape(client.get(reverse("hub_user_settings")).content.decode())

        assert 'name="name_shown_to_members"' in html
        assert "data-name-shown" in html
        for example in ('Shows as "Wren"', 'Shows as "Wren (Quinn A.)"', 'Shows as "Wren (Quinn Ashdown)"'):
            assert example in html

    def it_shows_the_legal_name_in_every_example_without_a_preferred_name():
        member = MemberFactory(full_legal_name="Quinn Ashdown", preferred_name="")
        assert set(name_shown_examples(member).values()) == {'Shows as "Quinn Ashdown"'}

    def it_saves_the_choice():
        member = MemberFactory(full_legal_name="Quinn Ashdown", preferred_name="Wren")
        form = ProfileSettingsForm({"preferred_name": "Wren", "name_shown_to_members": "initial"}, instance=member)
        assert form.is_valid(), form.errors
        assert form.save().display_name == "Wren (Quinn A.)"

    def it_keeps_the_choice_when_a_post_leaves_it_out():
        member = MemberFactory(full_legal_name="Quinn Ashdown", preferred_name="Wren", name_shown_to_members="full")
        form = ProfileSettingsForm({"preferred_name": "Wren"}, instance=member)
        assert form.is_valid(), form.errors
        assert form.save().name_shown_to_members == Member.NameShown.FULL

    def it_refuses_a_choice_that_is_not_offered():
        member = MemberFactory()
        form = ProfileSettingsForm({"preferred_name": "Wren", "name_shown_to_members": "nickname"}, instance=member)
        assert "name_shown_to_members" in form.errors


def describe_the_admin_edit_page():
    def it_lets_an_admin_set_the_choice():
        member = MemberFactory(full_legal_name="Quinn Ashdown", preferred_name="Wren")
        data = {
            "full_legal_name": "Quinn Ashdown",
            "preferred_name": "Wren",
            "status": Member.Status.ACTIVE,
            "member_type": Member.MemberType.STANDARD,
            "role": Member.FogRole.MEMBER,
            "name_shown_to_members": "full",
        }
        form = MemberAdminEditForm(data, instance=member)
        assert form.is_valid(), form.errors
        form.save()
        member.refresh_from_db()
        assert member.name_shown_to_members == Member.NameShown.FULL


def describe_the_member_directory_card():
    def it_names_a_member_once_by_the_preferred_name_by_default(client: Client):
        _login(client, show_in_directory=True)

        html = client.get(reverse("hub_member_directory")).content.decode()

        assert '<div class="directory-card__name">Wren</div>' in html
        assert "Quinn Ashdown" not in html

    def it_adds_the_legal_name_once_the_member_chooses_to(client: Client):
        _login(client, show_in_directory=True, name_shown_to_members="full")

        html = client.get(reverse("hub_member_directory")).content.decode()

        assert '<div class="directory-card__name">Wren (Quinn Ashdown)</div>' in html


def describe_a_page_signed_out_visitors_reach():
    def it_never_shows_them_a_legal_name_a_member_chose_to_show_members(client: Client):
        lead = MemberFactory(full_legal_name="Quinn Ashdown", preferred_name="Wren", name_shown_to_members="full")
        guild = GuildFactory(guild_lead=lead, is_active=True)

        html = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()

        assert "Led by Wren<" in html
        assert "Quinn Ashdown" not in html

    def it_names_by_the_members_choice_for_a_signed_in_viewer():
        lead = MemberFactory(full_legal_name="Quinn Ashdown", preferred_name="Wren", name_shown_to_members="initial")
        viewer = User.objects.create_user(username="signed-in")
        assert name_for(lead, viewer) == "Wren (Quinn A.)"
        assert name_for(lead, AnonymousUser()) == "Wren"

    def it_lists_instructors_the_same_way():
        teacher = MemberFactory(full_legal_name="Quinn Ashdown", preferred_name="Wren", name_shown_to_members="full")
        offering = ClassOfferingFactory(instructor=teacher)
        viewer = User.objects.create_user(username="signed-in-2")
        assert instructors_for(offering, viewer) == "Wren (Quinn Ashdown)"
        assert instructors_for(offering, AnonymousUser()) == "Wren"
        assert offering.staff_instructor_names == "Wren (Quinn Ashdown)"
