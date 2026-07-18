"""Per-announcement recipient selection — notify_members subset kwargs + AnnouncementDraft.send mapping.

Proves the send narrows the EMAIL to the chosen members + custom addresses while the bell reaches
every member and Discord posts once; that ``selected_*=None`` (every existing caller) never regresses
the mailing-list full-custom send; and that a saved selection is intersected with the *current* roster
at send so a stale member pk / removed custom row can't resurrect.
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
    def it_narrows_the_email_to_the_selected_members_and_custom_addresses(mailoutbox):
        guild = GuildFactory()
        a = _guild_member(guild, "a@example.com")
        _guild_member(guild, "b@example.com")  # NOT selected
        GuildMailingListEmailFactory(guild=guild, email="booster@example.com")
        GuildMailingListEmailFactory(guild=guild, email="partner@example.com")  # NOT selected
        GuildAnnouncementFactory(guild=guild, send_email=True).notify_members(
            selected_user_ids={a.user_id},
            selected_custom_emails=["booster@example.com"],
        )
        assert _recipients(mailoutbox) == {"a@example.com", "booster@example.com"}

    def it_bells_every_member_even_the_unselected_ones(mailoutbox):
        guild = GuildFactory()
        a = _guild_member(guild, "a@example.com")
        b = _guild_member(guild, "b@example.com")
        GuildAnnouncementFactory(guild=guild, send_email=True).notify_members(selected_user_ids={a.user_id})
        # b is not emailed but still gets the in-app bell.
        assert "b@example.com" not in _recipients(mailoutbox)
        assert Notification.objects.filter(user=a.user, trigger="guild_announcement").exists()
        assert Notification.objects.filter(user=b.user, trigger="guild_announcement").exists()

    def it_sends_the_full_custom_list_when_selected_custom_emails_is_none(mailoutbox):
        # No-regression guard: an existing caller passing None (or nothing) still emails EVERY custom.
        guild = GuildFactory()
        _guild_member(guild, "a@example.com")
        GuildMailingListEmailFactory(guild=guild, email="booster@example.com")
        GuildMailingListEmailFactory(guild=guild, email="partner@example.com")
        GuildAnnouncementFactory(guild=guild, send_email=True).notify_members(
            selected_user_ids=None, selected_custom_emails=None
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
            announcement.notify_members(selected_user_ids={a.user_id})
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
            email_recipient_selection=selection,
        )

    def it_emails_only_the_selected_subset_and_returns_the_counts(mailoutbox):
        author = User.objects.create_user(username="a_author", email="author@example.com", password="pw")
        guild = GuildFactory()
        a = _guild_member(guild, "a@example.com")
        _guild_member(guild, "b@example.com")  # not selected
        GuildMailingListEmailFactory(guild=guild, email="booster@example.com")
        GuildMailingListEmailFactory(guild=guild, email="partner@example.com")  # not selected
        draft = _guild_draft(author, guild, selection={"users": [a.user_id], "custom": ["booster@example.com"]})
        counts = draft.send()
        assert _recipients(mailoutbox) == {"a@example.com", "booster@example.com"}
        # emailed = 1 member + 1 custom; total = 2 members + 2 customs.
        assert counts == (2, 4)

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
        assert counts == (2, 2)  # 1 member + 1 custom emailed; total 1 member + 1 custom

    def it_emails_everyone_when_the_selection_is_empty(mailoutbox):
        author = User.objects.create_user(username="all_author", email="author3@example.com", password="pw")
        guild = GuildFactory()
        _guild_member(guild, "a@example.com")
        _guild_member(guild, "b@example.com")
        GuildMailingListEmailFactory(guild=guild, email="booster@example.com")
        draft = _guild_draft(author, guild, selection={})
        counts = draft.send()
        assert _recipients(mailoutbox) == {"a@example.com", "b@example.com", "booster@example.com"}
        assert counts == (3, 3)

    def it_ignores_the_selection_when_email_is_off(mailoutbox):
        author = User.objects.create_user(username="off_author", email="author4@example.com", password="pw")
        guild = GuildFactory()
        a = _guild_member(guild, "a@example.com")
        _guild_member(guild, "b@example.com")
        draft = _guild_draft(author, guild, selection={"users": [a.user_id], "custom": []}, send_email=False)
        counts = draft.send()
        assert mailoutbox == []
        assert counts == (0, 2)  # emailed 0 (email off); total 2 members
