"""BDD specs for ClassOfferingQuerySet.editable_by — who may edit which classes.

Guild leads (purely from the ``Guild.guild_lead`` FK) may edit any class in
their guild's categories; instructors may edit their own; admins and guild
officers may edit everything.
"""

from __future__ import annotations

import pytest

from classes.factories import CategoryFactory, ClassOfferingFactory
from classes.models import ClassOffering
from membership.models import Member
from tests.membership.factories import GuildFactory, MemberFactory

pytestmark = pytest.mark.django_db


def describe_editable_by():
    def it_returns_everything_for_admins():
        admin = MemberFactory(fog_role=Member.FogRole.ADMIN)
        a = ClassOfferingFactory(slug="ed-admin-a")
        b = ClassOfferingFactory(slug="ed-admin-b")
        result = ClassOffering.objects.editable_by(admin)
        assert a in result
        assert b in result

    def it_returns_everything_for_guild_officers():
        officer = MemberFactory(fog_role=Member.FogRole.GUILD_OFFICER)
        offering = ClassOfferingFactory(slug="ed-officer")
        assert offering in ClassOffering.objects.editable_by(officer)

    def it_includes_classes_the_member_instructs():
        me = MemberFactory()
        mine = ClassOfferingFactory(slug="ed-mine", instructor=me)
        theirs = ClassOfferingFactory(slug="ed-theirs")
        result = ClassOffering.objects.editable_by(me)
        assert mine in result
        assert theirs not in result

    def it_includes_classes_in_a_guild_the_member_leads():
        lead = MemberFactory()
        guild = GuildFactory(guild_lead=lead)
        guild_class = ClassOfferingFactory(slug="ed-guild", category=CategoryFactory(guild=guild))
        assert guild_class in ClassOffering.objects.editable_by(lead)

    def it_excludes_classes_in_guilds_the_member_does_not_lead():
        lead = MemberFactory()
        GuildFactory(guild_lead=lead)
        foreign = ClassOfferingFactory(slug="ed-foreign", category=CategoryFactory(guild=GuildFactory()))
        assert foreign not in ClassOffering.objects.editable_by(lead)

    def it_returns_nothing_for_a_plain_member_with_no_classes():
        plain = MemberFactory()
        ClassOfferingFactory(slug="ed-none")
        assert list(ClassOffering.objects.editable_by(plain)) == []
