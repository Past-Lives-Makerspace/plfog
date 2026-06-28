from __future__ import annotations

import pytest
from django.db import IntegrityError

from membership.models import Member, Skill
from tests.membership.factories import MemberFactory, MemberSkillFactory, SkillCategoryFactory, SkillFactory


def describe_SkillCategory():
    def it_str_is_the_name(db):
        assert str(SkillCategoryFactory(name="Woodworking")) == "Woodworking"


def describe_Skill():
    def it_str_is_the_name(db):
        assert str(SkillFactory(name="Welding")) == "Welding"

    def it_defaults_to_approved(db):
        assert SkillFactory().status == Skill.Status.APPROVED

    def it_can_be_pending(db):
        member = MemberFactory()
        skill = SkillFactory(name="Telekinesis", status=Skill.Status.PENDING, suggested_by=member)
        assert skill.status == Skill.Status.PENDING
        assert skill.suggested_by == member


def describe_MemberSkill():
    def it_str_shows_member_and_skill(db):
        ms = MemberSkillFactory(skill__name="Coding")
        assert "Coding" in str(ms)

    def it_str_includes_years_when_set(db):
        ms = MemberSkillFactory(skill__name="Coding", years_experience=10)
        assert "(10y)" in str(ms)

    def it_str_omits_years_when_unset(db):
        ms = MemberSkillFactory(skill__name="Coding", years_experience=None)
        assert "y)" not in str(ms)

    def it_rejects_duplicate_member_skill(db):
        member = MemberFactory()
        skill = SkillFactory()
        MemberSkillFactory(member=member, skill=skill)
        with pytest.raises(IntegrityError):
            MemberSkillFactory(member=member, skill=skill)


def describe_Member_commissions():
    def it_defaults_open_for_commissions_false(db):
        assert MemberFactory().open_for_commissions is False

    def it_includes_skills_in_toggleable_fields(db):
        assert "skills" in Member.DIRECTORY_TOGGLEABLE_FIELDS

    def it_skills_default_public(db):
        # is_public defaults missing keys to True
        assert MemberFactory().is_public("skills") is True


def describe_member_skill_queries():
    def it_approved_skills_excludes_pending(db):
        member = MemberFactory()
        approved = SkillFactory(name="Coding", status=Skill.Status.APPROVED)
        pending = SkillFactory(name="Mind reading", status=Skill.Status.PENDING)
        MemberSkillFactory(member=member, skill=approved)
        MemberSkillFactory(member=member, skill=pending)
        names = [ms.skill.name for ms in member.approved_skills]
        assert names == ["Coding"]

    def it_with_skill_filters_by_slug(db):
        wanted = SkillFactory(name="Welding", slug="welding")
        other = SkillFactory(name="Sewing", slug="sewing")
        welder = MemberFactory()
        sewer = MemberFactory()
        MemberSkillFactory(member=welder, skill=wanted)
        MemberSkillFactory(member=sewer, skill=other)
        result = Member.objects.with_skill("welding")
        assert list(result) == [welder]

    def it_with_skill_ignores_pending(db):
        pending = SkillFactory(name="Alchemy", slug="alchemy", status=Skill.Status.PENDING)
        member = MemberFactory()
        MemberSkillFactory(member=member, skill=pending)
        assert list(Member.objects.with_skill("alchemy")) == []

    def it_open_for_commissions_filters(db):
        open_member = MemberFactory(open_for_commissions=True)
        MemberFactory(open_for_commissions=False)
        assert list(Member.objects.open_for_commissions()) == [open_member]

    def it_search_skills_matches_skill_name(db):
        member = MemberFactory()
        MemberSkillFactory(member=member, skill=SkillFactory(name="Music production"))
        assert member in Member.objects.search_skills("music")

    def it_search_skills_matches_display_name(db):
        member = MemberFactory(full_legal_name="Ada Lovelace")
        assert member in Member.objects.search_skills("lovelace")
