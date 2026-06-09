"""Opt-in guild membership + privacy-respecting roster."""

import pytest
from django.db import IntegrityError

from membership.models import GuildMembership
from tests.membership.factories import GuildFactory, MemberFactory

pytestmark = pytest.mark.django_db


def describe_GuildMembership():
    def it_is_unique_per_guild_and_member():
        guild = GuildFactory()
        member = MemberFactory()
        GuildMembership.objects.create(guild=guild, member=member)
        with pytest.raises(IntegrityError):
            GuildMembership.objects.create(guild=guild, member=member)


def describe_roster_members():
    def it_includes_listed_members_only():
        guild = GuildFactory()
        shown = MemberFactory(show_in_directory=True)
        hidden = MemberFactory(show_in_directory=False)
        GuildMembership.objects.create(guild=guild, member=shown)
        GuildMembership.objects.create(guild=guild, member=hidden)
        roster = list(guild.roster_members())
        assert shown in roster
        assert hidden not in roster

    def it_includes_public_role_members_even_if_hidden():
        guild = GuildFactory()
        lead_member = MemberFactory(show_in_directory=False)
        guild.guild_lead = lead_member
        guild.save()
        GuildMembership.objects.create(guild=guild, member=lead_member)
        assert lead_member in list(guild.roster_members())


def describe_str():
    def it_describes_the_membership():
        guild = GuildFactory(name="Painters")
        member = MemberFactory(full_legal_name="Dana")
        gm = GuildMembership.objects.create(guild=guild, member=member)
        assert str(gm) == f"{member} in Painters"
