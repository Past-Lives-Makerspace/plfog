"""HTMX skill editor endpoints: add, remove, suggest."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import Member, MemberSkill, Skill
from tests.membership.factories import MembershipPlanFactory, MemberSkillFactory, SkillFactory


def _login(client: Client) -> Member:
    """Log in a regular member, auto-provisioned by the user-create signal."""
    MembershipPlanFactory()
    user = User.objects.create_user(username="editor", password="pw")
    member = user.member
    client.login(username="editor", password="pw")
    return member


@pytest.mark.django_db
def describe_skill_endpoints():
    def it_adds_a_skill(client: Client):
        member = _login(client)
        skill = SkillFactory(name="Coding")
        resp = client.post(reverse("hub_skill_add"), {"skill": skill.pk, "years_experience": 8})
        assert member.skills.filter(skill=skill, years_experience=8).exists()
        assert b"Coding" in resp.content

    def it_removes_a_skill(client: Client):
        member = _login(client)
        ms = MemberSkillFactory(member=member, skill=SkillFactory(name="Sewing"))
        resp = client.post(reverse("hub_skill_remove", args=[ms.pk]))
        assert not MemberSkill.objects.filter(pk=ms.pk).exists()
        # The skill name still appears in the add dropdown, so assert the empty state instead.
        assert b"No skills yet" in resp.content

    def it_does_not_remove_another_members_skill(client: Client):
        _login(client)
        other = MemberSkillFactory()
        client.post(reverse("hub_skill_remove", args=[other.pk]))
        assert MemberSkill.objects.filter(pk=other.pk).exists()

    def it_suggests_a_skill(client: Client):
        member = _login(client)
        resp = client.post(reverse("hub_skill_suggest"), {"name": "Kintsugi"})
        assert member.skills.filter(skill__name="Kintsugi", skill__status=Skill.Status.PENDING).exists()
        assert resp.status_code == 200

    def it_reports_invalid_add(client: Client):
        _login(client)
        resp = client.post(reverse("hub_skill_add"), {"skill": ""})
        assert resp.status_code == 200
        assert b"No skills yet" in resp.content

    def it_reports_invalid_suggest(client: Client):
        _login(client)
        SkillFactory(name="Whittling")
        resp = client.post(reverse("hub_skill_suggest"), {"name": "whittling"})
        assert resp.status_code == 200
        assert b"No skills yet" in resp.content

    def it_rejects_an_unlinked_account(client: Client):
        user = User.objects.create_user(username="nomember", password="pw")
        Member.objects.filter(user=user).delete()
        client.login(username="nomember", password="pw")
        assert client.post(reverse("hub_skill_add")).status_code == 403
        assert client.post(reverse("hub_skill_remove", args=[1])).status_code == 403
        assert client.post(reverse("hub_skill_suggest")).status_code == 403
