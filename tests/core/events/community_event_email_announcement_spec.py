"""Specs for :meth:`membership.models.CommunityEvent.email_announcement`.

The "also email everyone" escalation the ``/create-event`` command offers: it resolves a chosen
audience and emails the launch announcement to them via ``emit(email_to=...)``, bypassing the
opt-in-off-by-default event email preference. Lives here (under ``tests/core/events``) to reuse
the ``linked_member`` fixture, whose Users carry the email the resolvers require.
"""

from __future__ import annotations

import pytest

from core.models import NotificationPreference
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

    def describe_dedupe_against_the_launch_email():
        def it_skips_a_recipient_who_already_gets_the_launch_email(linked_member, mailoutbox):
            opted_in = linked_member()
            default = linked_member()
            NotificationPreference.objects.create(
                user=opted_in.user, event_key="event.community_published", channel="email", enabled=True
            )
            event = CommunityEventFactory(community=True)

            # The opted-in member already receives the launch announce() email, so the escalation
            # adds only the default-preference member — no double-send, and a lower count.
            assert event.email_announcement("all_active") == 1
            recipients = {addr for message in mailoutbox for addr in message.to}
            assert recipients == {default.user.email}
            assert opted_in.user.email not in recipients

        def it_still_emails_a_guild_member_with_the_launch_email_off(linked_member, mailoutbox):
            guild = GuildFactory()
            opted_in = linked_member()
            default = linked_member()
            GuildMembershipFactory(guild=guild, member=opted_in)
            GuildMembershipFactory(guild=guild, member=default)
            NotificationPreference.objects.create(
                user=opted_in.user, event_key="event.guild_published", channel="email", enabled=True
            )
            event = CommunityEventFactory(guild=guild)

            assert event.email_announcement("guild_members") == 1
            recipients = {addr for message in mailoutbox for addr in message.to}
            assert recipients == {default.user.email}
