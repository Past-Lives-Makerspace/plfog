"""Guild announcement delivery reaches the custom mailing list ADDITIVELY.

The highest-risk slice: a guild announcement must email the guild's members AND its custom
mailing-list addresses, deduped on the lower-cased ``user.email`` key, with the email toggle
suppressing both. Exercised through both send entry points — ``GuildAnnouncement.notify_members``
and ``AnnouncementDraft.send`` (guild branch) — plus the single Discord post.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from membership.models import AnnouncementDraft, GuildAnnouncement
from tests.membership.factories import (
    GuildFactory,
    GuildAnnouncementFactory,
    GuildMailingListEmailFactory,
    GuildMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

_seq = {"n": 0}


def _guild_member(guild, email: str):
    _seq["n"] += 1
    member = MemberFactory()
    with mute_signals(post_save):
        user = User.objects.create_user(
            username=f"snd_{_seq['n']}", email=email, password="pw", last_login=timezone.now()
        )
    member.user = user
    member.save(update_fields=["user"])
    GuildMembershipFactory(guild=guild, member=member)
    return member


def _recipients(mailoutbox) -> set[str]:
    return {addr for message in mailoutbox for addr in message.to}


def describe_notify_members_with_a_mailing_list():
    def it_emails_the_members_and_the_custom_addresses(mailoutbox):
        guild = GuildFactory()
        _guild_member(guild, "member@example.com")
        GuildMailingListEmailFactory(guild=guild, email="booster@example.com")
        GuildMailingListEmailFactory(guild=guild, email="partner@example.com")
        GuildAnnouncementFactory(guild=guild, send_email=True).notify_members()
        assert _recipients(mailoutbox) == {"member@example.com", "booster@example.com", "partner@example.com"}

    def it_emails_a_member_only_once_when_a_custom_address_matches_case_insensitively(mailoutbox):
        guild = GuildFactory()
        _guild_member(guild, "member@example.com")
        GuildMailingListEmailFactory(guild=guild, email="Member@Example.com")
        GuildAnnouncementFactory(guild=guild, send_email=True).notify_members()
        # The member's address collapses the colliding custom address — a single send.
        addresses = [addr for message in mailoutbox for addr in message.to]
        assert addresses.count("member@example.com") == 1
        assert "Member@Example.com" not in addresses

    def it_suppresses_both_members_and_custom_addresses_when_email_is_off(mailoutbox):
        guild = GuildFactory()
        _guild_member(guild, "member@example.com")
        GuildMailingListEmailFactory(guild=guild, email="booster@example.com")
        GuildAnnouncementFactory(guild=guild, send_email=False).notify_members()
        assert mailoutbox == []

    def it_still_emails_the_members_when_there_are_no_custom_addresses(mailoutbox):
        guild = GuildFactory()
        _guild_member(guild, "member@example.com")
        GuildAnnouncementFactory(guild=guild, send_email=True).notify_members()
        assert _recipients(mailoutbox) == {"member@example.com"}

    def it_posts_to_the_guild_discord_channel_exactly_once(mailoutbox):
        guild = GuildFactory(
            discord_post_enabled=True,
            discord_webhook_url="https://discord.com/api/webhooks/guild",
        )
        _guild_member(guild, "member@example.com")
        GuildMailingListEmailFactory(guild=guild, email="booster@example.com")
        announcement = GuildAnnouncementFactory(
            guild=guild, send_email=True, discord_channel=GuildAnnouncement.DiscordChannel.GUILD
        )
        with patch("core.events.discord.post_embed", return_value=True) as mock_post:
            announcement.notify_members()
        guild_posts = [c for c in mock_post.call_args_list if c.args[0] == "https://discord.com/api/webhooks/guild"]
        assert len(guild_posts) == 1
        # The custom address is email-only — Discord still gets one guild post, not one per address.
        assert "booster@example.com" in _recipients(mailoutbox)


def describe_announcement_draft_send_with_a_mailing_list():
    def it_emails_the_members_and_the_custom_addresses(mailoutbox):
        MembershipPlanFactory()
        author = User.objects.create_user(username="draft_author", email="author@example.com", password="pw")
        guild = GuildFactory()
        _guild_member(guild, "member@example.com")
        GuildMailingListEmailFactory(guild=guild, email="booster@example.com")
        draft = AnnouncementDraft.objects.create(
            author=author,
            audience=AnnouncementDraft.Audience.GUILD,
            guild=guild,
            title="News",
            body="<p>Big news</p>",
            send_email=True,
            discord_channel=GuildAnnouncement.DiscordChannel.NONE,
        )
        draft.send()
        assert {"member@example.com", "booster@example.com"} <= _recipients(mailoutbox)
