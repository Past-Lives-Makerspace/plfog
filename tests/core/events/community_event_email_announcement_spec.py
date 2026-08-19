"""Specs for :meth:`membership.models.CommunityEvent.email_announcement`.

The "also email everyone" escalation the ``/create-event`` command offers: it resolves a chosen
audience and emails the launch announcement to them via ``emit(email_to=...)``, bypassing the
per-member event-email preference. Event emails are ON by default now (owner call, copy-review
2026-08-18), so the spine's per-member fan-out already reaches everyone who hasn't opted out; this
escalation therefore adds the members it would otherwise miss (those who turned event emails off),
letting an admin force the notice to the whole roster. Lives here (under ``tests/core/events``) to
reuse the ``linked_member`` fixture, whose Users carry the email the resolvers require.
"""

from __future__ import annotations

import pytest

from core.models import NotificationPreference
from tests.membership.factories import CommunityEventFactory, GuildFactory, GuildMembershipFactory

pytestmark = pytest.mark.django_db


def describe_email_announcement():
    def it_emails_every_guild_member_the_spine_would_miss(linked_member, mailoutbox):
        # Event emails are ON by default, so the escalation force-reaches the members the
        # spine's per-member fan-out skips: those who explicitly turned guild-event emails off.
        guild = GuildFactory()
        for _ in range(2):
            member = linked_member()
            GuildMembershipFactory(guild=guild, member=member)
            NotificationPreference.objects.create(
                user=member.user, event_key="event.guild_published", channel="email", enabled=False
            )
        event = CommunityEventFactory(guild=guild)

        assert event.email_announcement("guild_members") == 2
        assert len(mailoutbox) == 2

    def it_emails_the_opted_out_across_the_whole_membership(linked_member, mailoutbox):
        for _ in range(2):
            member = linked_member()
            NotificationPreference.objects.create(
                user=member.user, event_key="event.community_published", channel="email", enabled=False
            )
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
        def it_skips_members_the_spine_already_emails(linked_member, mailoutbox):
            # event.community_published email is now ON by default, so a member with no
            # preference row already gets the launch announce() email through the normal
            # per-member fan-out. The escalation therefore adds only members who explicitly
            # turned it OFF (no spine email) — never a double-send.
            opted_out = linked_member()
            default = linked_member()
            NotificationPreference.objects.create(
                user=opted_out.user, event_key="event.community_published", channel="email", enabled=False
            )
            event = CommunityEventFactory(community=True)

            assert event.email_announcement("all_active") == 1
            recipients = {addr for message in mailoutbox for addr in message.to}
            assert recipients == {opted_out.user.email}
            assert default.user.email not in recipients

        def it_still_emails_a_guild_member_who_opted_out_of_the_spine(linked_member, mailoutbox):
            # A guild member on the default now gets the spine email, so the escalation skips
            # them; only a member who explicitly opted OUT still needs the "also email everyone" copy.
            guild = GuildFactory()
            opted_out = linked_member()
            default = linked_member()
            GuildMembershipFactory(guild=guild, member=opted_out)
            GuildMembershipFactory(guild=guild, member=default)
            NotificationPreference.objects.create(
                user=opted_out.user, event_key="event.guild_published", channel="email", enabled=False
            )
            event = CommunityEventFactory(guild=guild)

            assert event.email_announcement("guild_members") == 1
            recipients = {addr for message in mailoutbox for addr in message.to}
            assert recipients == {opted_out.user.email}
