from __future__ import annotations

from django.contrib.admin.sites import site

from membership.admin import SkillAdmin
from membership.models import Skill
from tests.membership.factories import SkillFactory


def describe_SkillAdmin():
    def it_approve_action_marks_pending_skills_approved(db, rf):
        pending = SkillFactory(name="Telekinesis", status=Skill.Status.PENDING)
        admin = SkillAdmin(Skill, site)
        admin.approve_skills(rf.post("/"), Skill.objects.filter(pk=pending.pk))
        pending.refresh_from_db()
        assert pending.status == Skill.Status.APPROVED
