"""Specs for ``MemberQuerySet.directory_visible()`` — the one directory-privacy filter.

The hub directory page, ``Guild.roster_members``, and the Discord ``/members`` command all
build on this queryset method; these specs pin the filter itself (the call-site suites
prove the refactor changed no behavior).
"""

from __future__ import annotations

import pytest

from membership.models import Member
from tests.membership.factories import GuildFactory, MemberFactory

pytestmark = pytest.mark.django_db


def describe_directory_visible():
    def it_includes_an_active_opted_in_member():
        member = MemberFactory(show_in_directory=True)
        assert member in Member.objects.directory_visible()

    def it_excludes_an_opted_out_standard_member():
        member = MemberFactory(show_in_directory=False)
        assert member not in Member.objects.directory_visible()

    def it_includes_an_opted_out_admin():
        member = MemberFactory(show_in_directory=False, fog_role=Member.FogRole.ADMIN)
        assert member in Member.objects.directory_visible()

    def it_includes_an_opted_out_guild_officer():
        member = MemberFactory(show_in_directory=False, fog_role=Member.FogRole.GUILD_OFFICER)
        assert member in Member.objects.directory_visible()

    def it_includes_an_opted_out_guild_lead():
        member = MemberFactory(show_in_directory=False)
        GuildFactory(name="Led Guild", guild_lead=member)
        assert member in Member.objects.directory_visible()

    def it_includes_an_opted_out_instructor():
        member = MemberFactory(show_in_directory=False, instructor_slug="teaches-things")
        assert member not in Member.objects.filter(show_in_directory=True)
        assert member in Member.objects.directory_visible()

    def it_excludes_a_non_active_member_regardless_of_role():
        member = MemberFactory(status=Member.Status.FORMER, show_in_directory=True, fog_role=Member.FogRole.ADMIN)
        assert member not in Member.objects.directory_visible()

    def it_returns_a_multi_guild_lead_exactly_once():
        member = MemberFactory(show_in_directory=False)
        GuildFactory(name="First Led", guild_lead=member)
        GuildFactory(name="Second Led", guild_lead=member)
        assert list(Member.objects.directory_visible().filter(pk=member.pk)) == [member]

    def it_excludes_a_hidden_opted_in_plain_member():
        # hide_from_directory is the ops-only override — it beats the member's own opt-in too.
        member = MemberFactory(show_in_directory=True, hide_from_directory=True)
        assert member not in Member.objects.directory_visible()

    def it_excludes_a_hidden_admin():
        member = MemberFactory(show_in_directory=False, fog_role=Member.FogRole.ADMIN, hide_from_directory=True)
        assert member not in Member.objects.directory_visible()

    def it_excludes_a_hidden_guild_officer():
        member = MemberFactory(show_in_directory=False, fog_role=Member.FogRole.GUILD_OFFICER, hide_from_directory=True)
        assert member not in Member.objects.directory_visible()

    def it_excludes_a_hidden_guild_lead():
        member = MemberFactory(show_in_directory=False, hide_from_directory=True)
        GuildFactory(name="Hidden Lead Guild", guild_lead=member)
        assert member not in Member.objects.directory_visible()

    def it_excludes_a_hidden_instructor():
        member = MemberFactory(show_in_directory=False, instructor_slug="hidden-teacher", hide_from_directory=True)
        assert member not in Member.objects.directory_visible()
