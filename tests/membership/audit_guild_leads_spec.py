"""BDD specs for the audit_guild_leads management command."""

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
    call_command("audit_guild_leads", stdout=out, **kwargs)
    return out.getvalue()


def describe_audit_guild_leads():
    def it_reports_a_healthy_lead():
        user = UserFactory(username="healthy-lead@example.com")
        GuildFactory(name="Healthy", guild_lead=user.member)
        output = _run()
        assert "1 healthy" in output
        assert "No guild-lead problems found" in output

    def it_flags_a_lead_with_no_linked_user():
        GuildFactory(name="Forge", guild_lead=MemberFactory())  # MemberFactory has no User
        output = _run()
        assert "NO linked user account" in output
        assert "Forge" in output

    def it_flags_an_inactive_lead():
        user = UserFactory(username="inactive-lead@example.com")
        member = user.member
        member.status = Member.Status.FORMER
        member.save(update_fields=["status"])
        GuildFactory(name="Clay", guild_lead=member)
        output = _run()
        assert "not Active" in output
        assert "Clay" in output

    def it_lists_guilds_with_no_lead():
        GuildFactory(name="Leaderless", guild_lead=None)
        output = _run()
        assert "no lead" in output
        assert "Leaderless" in output

    def it_raises_in_strict_mode_when_problems_exist():
        GuildFactory(name="Broken", guild_lead=MemberFactory())
        with pytest.raises(CommandError):
            _run(strict=True)
