"""Specs for :meth:`membership.models.CommunityEvent.email_announcement`.

The "also email everyone" escalation the ``/create-event`` command offers: it resolves a chosen
audience and emails the launch announcement to them via ``emit(email_to=...)``, bypassing the
opt-in-off-by-default event email preference. Lives here (under ``tests/core/events``) to reuse
the ``linked_member`` fixture, whose Users carry the email the resolvers require.
"""

from __future__ import annotations

import pytest

from tests.membership.factories import CommunityEventFactory, GuildFactory, GuildMembershipFactory

pytestmark = pytest.mark.django_db


def describe_email_announcement():
    def it_emails_every_active_member_of_the_guild(linked_member, mailoutbox):
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=linked_member())
        GuildMembershipFactory(guild=guild, member=linked_member())
        event = CommunityEventFactory(guild=guild)

        assert event.email_announcement("guild_members") == 2
        assert len(mailoutbox) == 2

    def it_emails_the_whole_active_membership(linked_member, mailoutbox):
        linked_member()
        linked_member()
        event = CommunityEventFactory(community=True)

        assert event.email_announcement("all_active") == 2
        assert len(mailoutbox) == 2

    def it_emails_no_one_for_guild_members_on_a_site_wide_event(mailoutbox):
        event = CommunityEventFactory(community=True)
        assert event.email_announcement("guild_members") == 0
        assert mailoutbox == []

    def it_returns_zero_when_the_guild_has_no_members(mailoutbox):
        event = CommunityEventFactory(guild=GuildFactory())
        assert event.email_announcement("guild_members") == 0
        assert mailoutbox == []

    def it_never_emails_for_studio_hours(mailoutbox):
        event = CommunityEventFactory(studio_hours=True)
        assert event.email_announcement("all_active") == 0
        assert mailoutbox == []

    def it_fails_loudly_on_an_unknown_audience():
        event = CommunityEventFactory()
        with pytest.raises(ValueError, match="Unknown event email audience"):
            event.email_announcement("everybody")
