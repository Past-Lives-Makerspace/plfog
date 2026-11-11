"""BDD specs for the set_fog_role management command."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from membership.models import Member
from tests.membership.factories import MemberFactory


@pytest.mark.django_db
def describe_set_fog_role_command():
    def it_sets_fog_role_for_existing_member():
        member = MemberFactory(_pre_signup_email="test@example.com", fog_role=Member.FogRole.MEMBER)

        out = StringIO()
        call_command("set_fog_role", "test@example.com", "admin", stdout=out)

        member.refresh_from_db()
        assert member.fog_role == Member.FogRole.ADMIN
        assert "Admin" in out.getvalue()

    def it_raises_error_for_unknown_email():
        with pytest.raises(CommandError, match="No member found"):
            call_command("set_fog_role", "nobody@example.com", "admin")

    def it_shows_old_and_new_role_in_output():
        member = MemberFactory(_pre_signup_email="lead@example.com", fog_role=Member.FogRole.ADMIN)

        out = StringIO()
        call_command("set_fog_role", "lead@example.com", "member", stdout=out)

        member.refresh_from_db()
        assert member.fog_role == Member.FogRole.MEMBER
        output = out.getvalue()
        assert "from 'Admin' to 'Member'" in output

    def it_refuses_the_retired_guild_officer_role():
        MemberFactory(_pre_signup_email="officer@example.com")
        with pytest.raises(CommandError, match="invalid choice"):
            call_command("set_fog_role", "officer@example.com", "guild_officer")
