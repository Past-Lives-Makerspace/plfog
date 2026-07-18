"""emit(extra_emails=…) — ADDITIVE explicit emails that never suppress the member fan-out.

Unlike ``email_to`` (which replaces the member email), ``extra_emails`` rides *alongside*
the resolved per-recipient members: members still get their own email while the extra
addresses are emailed too, deduped against the members on the lower-cased ``user.email`` key.
Guards the ``emit.py`` suppression trap (``suppress_user_email`` must stay False here).
"""

from __future__ import annotations

import pytest

from core.events.emit import emit
from core.events.registry import Channel
from core.models import TransactionalEmailLog
from tests.membership.factories import GuildFactory, GuildMembershipFactory

pytestmark = pytest.mark.django_db


def _emailed(trigger: str = "guild_announcement") -> set[str]:
    return set(TransactionalEmailLog.objects.filter(trigger_kind=trigger).values_list("to_email", flat=True))


def describe_extra_emails():
    def it_emails_extra_addresses_in_addition_to_the_members(linked_member):
        guild = GuildFactory()
        member = linked_member(email="member@example.com")
        GuildMembershipFactory(guild=guild, member=member)
        emit(
            "guild_announcement",
            context={"guild": guild},
            title="t",
            body="b",
            extra_emails=["booster@example.com"],
        )
        assert _emailed() == {"member@example.com", "booster@example.com"}

    def it_does_not_suppress_the_member_email_when_extra_emails_given(linked_member):
        # The emit.py:161 trap: an explicit email must not flip suppress_user_email here.
        guild = GuildFactory()
        member = linked_member(email="member@example.com")
        GuildMembershipFactory(guild=guild, member=member)
        result = emit(
            "guild_announcement",
            context={"guild": guild},
            title="t",
            body="b",
            extra_emails=["booster@example.com"],
        )
        assert (member.user_id, Channel.EMAIL) in result.delivered
        assert (0, Channel.EMAIL) in result.delivered  # the extra address, via the explicit path

    def it_dedupes_an_extra_email_that_matches_a_member_case_insensitively(linked_member):
        guild = GuildFactory()
        member = linked_member(email="member@example.com")
        GuildMembershipFactory(guild=guild, member=member)
        emit(
            "guild_announcement",
            context={"guild": guild},
            title="t",
            body="b",
            extra_emails=["Member@Example.com"],  # same address, different case
        )
        # One email to the member, none duplicated to the "extra" that collapses onto it.
        assert _emailed() == {"member@example.com"}
        assert TransactionalEmailLog.objects.filter(trigger_kind="guild_announcement").count() == 1

    def it_is_a_noop_when_extra_emails_is_none(linked_member):
        guild = GuildFactory()
        member = linked_member(email="member@example.com")
        GuildMembershipFactory(guild=guild, member=member)
        emit("guild_announcement", context={"guild": guild}, title="t", body="b", extra_emails=None)
        assert _emailed() == {"member@example.com"}

    def it_still_emails_the_extra_address_when_no_member_resolves(linked_member):
        guild = GuildFactory()  # no members joined
        emit(
            "guild_announcement",
            context={"guild": guild},
            title="t",
            body="b",
            extra_emails=["booster@example.com"],
        )
        assert _emailed() == {"booster@example.com"}
