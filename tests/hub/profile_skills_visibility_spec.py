"""Settings > Profile: the My Skills directory switch, and what Save Profile does with it (#674)."""

from __future__ import annotations

import re

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import Member, Skill
from tests.membership.factories import MembershipPlanFactory, MemberSkillFactory, SkillFactory

SKILLS_INPUT = '<input type="hidden" name="show_skills" form="profile-form"'


def _login(client: Client) -> Member:
    """Log in a listed member with one approved skill, auto-provisioned by the user-create signal."""
    MembershipPlanFactory()
    user = User.objects.create_user(username="sam", password="pw")
    member = user.member
    member.show_in_directory = True
    member.save(update_fields=["show_in_directory"])
    MemberSkillFactory(member=member, skill=SkillFactory(name="Blacksmithing", status=Skill.Status.APPROVED))
    client.login(username="sam", password="pw")
    return member


def _save_profile(client: Client, **extra: str) -> None:
    data = {
        "contacts-TOTAL_FORMS": "0",
        "contacts-INITIAL_FORMS": "0",
        "form_id": "profile",
        "preferred_name": "Sam",
        "show_in_directory": "on",
        **extra,
    }
    client.post(reverse("hub_user_settings"), data)


def _skills_on_card(client: Client) -> bool:
    return b'<span class="pl-skill-chip">Blacksmithing' in client.get(reverse("hub_member_directory")).content


def _skills_switch_starts_on(html: str) -> bool:
    match = re.search(r"skills: (true|false),", html)
    assert match, "the profile scope has no skills key"
    return match.group(1) == "true"


@pytest.mark.django_db
def describe_skills_visibility_switch():
    def it_renders_in_my_skills_and_posts_with_the_profile_form(client: Client):
        _login(client)
        html = client.get(reverse("hub_user_settings")).content.decode()

        skills_section = html[html.index('<h2 class="pl-profile-section__title">My Skills</h2>') :]
        assert SKILLS_INPUT in skills_section
        assert 'id="profile-form"' in html

    def it_starts_on_for_a_member_who_never_touched_it(client: Client):
        member = _login(client)
        assert member.directory_visibility == {}

        html = client.get(reverse("hub_user_settings")).content.decode()

        assert _skills_switch_starts_on(html)

    def it_starts_off_for_a_member_who_hid_their_skills(client: Client):
        member = _login(client)
        member.directory_visibility = {"skills": False}
        member.save(update_fields=["directory_visibility"])

        html = client.get(reverse("hub_user_settings")).content.decode()

        assert not _skills_switch_starts_on(html)


@pytest.mark.django_db
def describe_save_profile():
    def it_keeps_skills_on_the_card_when_the_switch_is_on(client: Client):
        member = _login(client)

        _save_profile(client, show_skills="on")

        member.refresh_from_db()
        assert member.directory_visibility["skills"] is True
        assert _skills_on_card(client)

    def it_hides_skills_from_the_card_when_the_switch_is_off(client: Client):
        member = _login(client)

        _save_profile(client, show_skills="")

        member.refresh_from_db()
        assert member.directory_visibility["skills"] is False
        assert not _skills_on_card(client)

    def it_keeps_skills_shown_on_a_save_that_never_touches_the_switch(client: Client):
        member = _login(client)
        html = client.get(reverse("hub_user_settings")).content.decode()
        untouched = "on" if _skills_switch_starts_on(html) else ""

        _save_profile(client, show_skills=untouched)

        member.refresh_from_db()
        assert member.directory_visibility["skills"] is True
        assert _skills_on_card(client)
