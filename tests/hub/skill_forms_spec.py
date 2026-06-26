from __future__ import annotations

from hub.forms import MemberSkillForm, SkillSuggestionForm
from membership.models import Member, Skill
from tests.membership.factories import MemberFactory, MemberSkillFactory, SkillFactory


def describe_MemberSkillForm():
    def it_adds_a_skill(db):
        member = MemberFactory()
        skill = SkillFactory()
        form = MemberSkillForm(member=member, data={"skill": skill.pk, "years_experience": 5})
        assert form.is_valid(), form.errors
        ms = form.save()
        assert ms.member == member and ms.years_experience == 5

    def it_rejects_duplicate(db):
        member = MemberFactory()
        skill = SkillFactory()
        MemberSkillFactory(member=member, skill=skill)
        form = MemberSkillForm(member=member, data={"skill": skill.pk})
        assert not form.is_valid()

    def it_rejects_when_at_cap(db):
        member = MemberFactory()
        for _ in range(Member.MAX_SKILLS):
            MemberSkillFactory(member=member)
        form = MemberSkillForm(member=member, data={"skill": SkillFactory().pk})
        assert not form.is_valid()


def describe_SkillSuggestionForm():
    def it_creates_pending_skill_and_link(db):
        member = MemberFactory()
        form = SkillSuggestionForm(member=member, data={"name": "Underwater basket weaving"})
        assert form.is_valid(), form.errors
        ms = form.save()
        assert ms.skill.status == Skill.Status.PENDING
        assert ms.skill.suggested_by == member
        assert ms.member == member

    def it_rejects_existing_skill_name_case_insensitive(db):
        SkillFactory(name="Welding")
        member = MemberFactory()
        form = SkillSuggestionForm(member=member, data={"name": "welding"})
        assert not form.is_valid()
