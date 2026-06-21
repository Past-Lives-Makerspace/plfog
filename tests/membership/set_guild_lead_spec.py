"""BDD specs for the set_guild_lead management command."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from classes.factories import UserFactory
from membership.models import Member
from tests.membership.factories import GuildFactory, MemberFactory

pytestmark = pytest.mark.django_db


def _run(**kwargs) -> str:
    out = StringIO()
    call_command("set_guild_lead", stdout=out, **kwargs)
    return out.getvalue()


def describe_set_guild_lead():
    def it_assigns_a_lead_by_member_email():
        user = UserFactory(username="newlead@example.com")
        guild = GuildFactory(name="Forge")
        output = _run(guild="Forge", member="newlead@example.com")
        guild.refresh_from_db()
        assert guild.guild_lead_id == user.member.pk
        assert "Set lead of 'Forge'" in output

    def it_assigns_by_guild_id():
        user = UserFactory(username="byid@example.com")
        guild = GuildFactory()
        _run(guild=str(guild.pk), member="byid@example.com")
        guild.refresh_from_db()
        assert guild.guild_lead_id == user.member.pk

    def it_clears_a_lead():
        user = UserFactory(username="tobecleared@example.com")
        guild = GuildFactory(name="Clay", guild_lead=user.member)
        output = _run(guild="Clay", clear=True)
        guild.refresh_from_db()
        assert guild.guild_lead_id is None
        assert "Cleared lead" in output

    def it_warns_when_the_member_has_no_linked_user():
        MemberFactory(_pre_signup_email="nouser@example.com")  # no User linked
        GuildFactory(name="Wood")
        output = _run(guild="Wood", member="nouser@example.com")
        assert "no linked user account" in output

    def it_warns_when_the_member_is_not_active():
        user = UserFactory(username="former@example.com")
        member = user.member
        member.status = Member.Status.FORMER
        member.save(update_fields=["status"])
        GuildFactory(name="Metal")
        output = _run(guild="Metal", member="former@example.com")
        assert "not Active" in output

    def it_errors_on_an_unknown_guild():
        UserFactory(username="present@example.com")
        with pytest.raises(CommandError):
            _run(guild="Nope", member="present@example.com")

    def it_errors_on_an_unknown_guild_id():
        UserFactory(username="present2@example.com")
        with pytest.raises(CommandError):
            _run(guild="9999999", member="present2@example.com")

    def it_errors_on_an_unknown_member():
        GuildFactory(name="Glass")
        with pytest.raises(CommandError):
            _run(guild="Glass", member="ghost@example.com")
