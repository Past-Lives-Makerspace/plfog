"""Per-announcement recipient selection — notify_members recipient kwargs + AnnouncementDraft.send mapping.

Proves the send scopes ALL personal channels (in-app bell + push + email) to the chosen recipient
set while Discord posts once; that ``recipient_user_ids=None`` (every existing caller) never regresses
the whole-roster send or the full-custom mailing-list send; and that a saved selection maps into the
explicit recipient set at send (a stale member pk is dropped by emit, a removed custom row is narrowed).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from core.models import Notification
from membership.models import AnnouncementDraft, GuildAnnouncement
from tests.membership.factories import (
    GuildAnnouncementFactory,
    GuildFactory,
    GuildMailingListEmailFactory,
    GuildMembershipFactory,
    MemberFactory,
)

pytestmark = pytest.mark.django_db

_seq = {"n": 0}


def _guild_member(guild, email: str):
    _seq["n"] += 1
    member = MemberFactory()
    with mute_signals(post_save):
        user = User.objects.create_user(
            username=f"rsel_{_seq['n']}", email=email, password="pw", last_login=timezone.now()
        )
    member.user = user
    member.save(update_fields=["user"])
    GuildMembershipFactory(guild=guild, member=member)
    return member


def _recipients(mailoutbox) -> set[str]:
    return {addr for message in mailoutbox for addr in message.to}


def describe_notify_members_selection():
    def it_scopes_the_email_to_the_selected_members_and_custom_addresses(mailoutbox):
        guild = GuildFactory()
        a = _guild_member(guild, "a@example.com")
        _guild_member(guild, "b@example.com")  # NOT selected
        GuildMailingListEmailFactory(guild=guild, email="booster@example.com")
        GuildMailingListEmailFactory(guild=guild, email="partner@example.com")  # NOT selected
        GuildAnnouncementFactory(guild=guild, send_email=True).notify_members(
            recipient_user_ids={a.user_id},
            selected_custom_emails=["booster@example.com"],
        )
        assert _recipients(mailoutbox) == {"a@example.com", "booster@example.com"}

    def it_reaches_only_the_selected_members_across_every_channel(mailoutbox):
        guild = GuildFactory()
        a = _guild_member(guild, "a@example.com")
        b = _guild_member(guild, "b@example.com")
        GuildAnnouncementFactory(guild=guild, send_email=True).notify_members(recipient_user_ids={a.user_id})
        # The selection governs ALL personal channels now: b is neither emailed nor belled.
        assert "b@example.com" not in _recipients(mailoutbox)
        assert Notification.objects.filter(user=a.user, trigger="guild_announcement").exists()
        assert not Notification.objects.filter(user=b.user, trigger="guild_announcement").exists()

    def it_suppresses_push_when_the_toggle_is_off(mailoutbox):
        guild = GuildFactory()
        a = _guild_member(guild, "a@example.com")
        with patch("core.events.channels.PushAdapter.deliver") as mock_push:
            GuildAnnouncementFactory(guild=guild, send_email=True).notify_members(
                recipient_user_ids={a.user_id}, suppress_push=True
            )
        assert mock_push.call_count == 0
        # The bell still fires even with push off.
        assert Notification.objects.filter(user=a.user, trigger="guild_announcement").exists()

    def it_sends_the_full_custom_list_when_selected_custom_emails_is_none(mailoutbox):
        # No-regression guard: an existing caller passing None (or nothing) still emails EVERY custom.
        guild = GuildFactory()
        _guild_member(guild, "a@example.com")
        GuildMailingListEmailFactory(guild=guild, email="booster@example.com")
        GuildMailingListEmailFactory(guild=guild, email="partner@example.com")
        GuildAnnouncementFactory(guild=guild, send_email=True).notify_members(
            recipient_user_ids=None, selected_custom_emails=None
        )
        assert _recipients(mailoutbox) == {"a@example.com", "booster@example.com", "partner@example.com"}

    def it_posts_the_guild_discord_once_even_with_a_subset(mailoutbox):
        guild = GuildFactory(
            discord_post_enabled=True,
            discord_webhook_url="https://discord.com/api/webhooks/guild",
        )
        a = _guild_member(guild, "a@example.com")
        _guild_member(guild, "b@example.com")
        announcement = GuildAnnouncementFactory(
            guild=guild, send_email=True, discord_channel=GuildAnnouncement.DiscordChannel.GUILD
        )
        with patch("core.events.discord.post_embed", return_value=True) as mock_post:
            announcement.notify_members(recipient_user_ids={a.user_id})
        guild_posts = [c for c in mock_post.call_args_list if c.args[0] == "https://discord.com/api/webhooks/guild"]
        assert len(guild_posts) == 1


def describe_announcement_draft_send_selection():
    def _guild_draft(author, guild, *, selection, send_email=True):
        return AnnouncementDraft.objects.create(
            author=author,
            audience=AnnouncementDraft.Audience.GUILD,
            guild=guild,
            title="News",
            body="<p>Big news</p>",
            send_email=send_email,
            discord_channel=GuildAnnouncement.DiscordChannel.NONE,
            recipient_selection=selection,
        )

    def it_reaches_only_the_selected_subset_and_returns_the_counts(mailoutbox):
        author = User.objects.create_user(username="a_author", email="author@example.com", password="pw")
        guild = GuildFactory()
        a = _guild_member(guild, "a@example.com")
        _guild_member(guild, "b@example.com")  # not selected
        GuildMailingListEmailFactory(guild=guild, email="booster@example.com")
        GuildMailingListEmailFactory(guild=guild, email="partner@example.com")  # not selected
        draft = _guild_draft(author, guild, selection={"users": [a.user_id], "custom": ["booster@example.com"]})
        counts = draft.send()
        assert _recipients(mailoutbox) == {"a@example.com", "booster@example.com"}
        # total = the one selected member; emailed = that member (email on). Custom addresses ride
        # the email additively but are not counted as members.
        assert counts == (1, 1)

    def it_drops_a_stale_pk_and_a_removed_custom_row_at_send(mailoutbox):
        author = User.objects.create_user(username="stale_author", email="author2@example.com", password="pw")
        guild = GuildFactory()
        a = _guild_member(guild, "a@example.com")
        GuildMailingListEmailFactory(guild=guild, email="booster@example.com")
        # 999999 is not a member; ghost@ is not a custom row — both must be dropped, not resurrected.
        draft = _guild_draft(
            author,
            guild,
            selection={"users": [a.user_id, 999999], "custom": ["booster@example.com", "ghost@example.com"]},
        )
        counts = draft.send()
        assert _recipients(mailoutbox) == {"a@example.com", "booster@example.com"}
        assert counts == (2, 2)  # both selected ids are counted; the non-existent one simply delivers nothing

    def it_reaches_everyone_when_the_selection_is_empty(mailoutbox):
        author = User.objects.create_user(username="all_author", email="author3@example.com", password="pw")
        guild = GuildFactory()
        _guild_member(guild, "a@example.com")
        _guild_member(guild, "b@example.com")
        GuildMailingListEmailFactory(guild=guild, email="booster@example.com")
        draft = _guild_draft(author, guild, selection={})
        counts = draft.send()
        assert _recipients(mailoutbox) == {"a@example.com", "b@example.com", "booster@example.com"}
        assert counts == (2, 2)  # both members; custom addresses ride the email but are not counted

    def it_scopes_the_bell_to_the_selection_even_when_email_is_off(mailoutbox):
        author = User.objects.create_user(username="off_author", email="author4@example.com", password="pw")
        guild = GuildFactory()
        a = _guild_member(guild, "a@example.com")
        b = _guild_member(guild, "b@example.com")
        draft = _guild_draft(author, guild, selection={"users": [a.user_id], "custom": []}, send_email=False)
        counts = draft.send()
        assert mailoutbox == []
        assert Notification.objects.filter(user=a.user, trigger="guild_announcement").exists()
        assert not Notification.objects.filter(user=b.user, trigger="guild_announcement").exists()
        assert counts == (0, 1)  # emailed 0 (email off); total 1 (the selected member)
