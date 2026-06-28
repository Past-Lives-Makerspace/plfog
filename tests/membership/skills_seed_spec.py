from __future__ import annotations

from membership.models import Skill, SkillCategory


def describe_skill_seed():
    def it_loads_categories_and_skills(db):
        # Migrations run for the test DB, so seeded rows exist.
        assert SkillCategory.objects.filter(slug="software-tech").exists()
        assert Skill.objects.filter(slug="ai-development-consulting").exists()
        assert Skill.objects.filter(slug="small-woodworking-projects").exists()

    def it_seeds_only_approved_skills(db):
        assert not Skill.objects.exclude(status=Skill.Status.APPROVED).exists()
