"""emit(email_only_user_ids=…) — a per-announcement EMAIL subset that leaves bell/push/Discord whole.

The shared-spine slice for "send the email to some members, not all". Unlike ``email_to`` (which
replaces the member email) and ``suppress_email`` (which drops it for everyone), this narrows ONLY
the EMAIL channel to the chosen ``pk``\\ s while the in-app bell still reaches every resolved member,
the per-user preference gate still runs (an opted-out selected member is still skipped), and the one
Discord broadcast still fires. ``None`` (every existing caller) is byte-unchanged.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.events.emit import emit
from core.events.registry import Channel
from core.models import NotificationPreference, TransactionalEmailLog
from tests.membership.factories import GuildFactory, GuildMembershipFactory

pytestmark = pytest.mark.django_db


def _emailed(trigger: str = "guild_announcement") -> set[str]:
    return set(TransactionalEmailLog.objects.filter(trigger_kind=trigger).values_list("to_email", flat=True))


def describe_email_only_user_ids():
    def it_emails_only_the_subset_but_bells_every_member(linked_member):
        guild = GuildFactory()
        a = linked_member(email="a@example.com")
        b = linked_member(email="b@example.com")
        GuildMembershipFactory(guild=guild, member=a)
        GuildMembershipFactory(guild=guild, member=b)
        result = emit(
            "guild_announcement",
            context={"guild": guild},
            title="t",
            body="b",
            email_only_user_ids={a.user_id},
        )
        # Email: only the selected member.
        assert _emailed() == {"a@example.com"}
        assert (a.user_id, Channel.EMAIL) in result.delivered
        assert (b.user_id, Channel.EMAIL) not in result.delivered
        # In-app bell: BOTH members (the subset governs email alone).
        assert (a.user_id, Channel.IN_APP) in result.delivered
        assert (b.user_id, Channel.IN_APP) in result.delivered

    def it_still_suppresses_the_email_of_a_selected_member_who_opted_out(linked_member):
        guild = GuildFactory()
        a = linked_member(email="a@example.com")
        GuildMembershipFactory(guild=guild, member=a)
        # a is selected, but opted out of the announcement email — the preference gate still wins.
        NotificationPreference.objects.create(
            user=a.user, event_key="guild_announcement", channel=Channel.EMAIL.value, enabled=False
        )
        result = emit(
            "guild_announcement",
            context={"guild": guild},
            title="t",
            body="b",
            email_only_user_ids={a.user_id},
        )
        assert _emailed() == set()
        assert (a.user_id, Channel.EMAIL) not in result.delivered
        assert (a.user_id, Channel.IN_APP) in result.delivered  # bell still fires

    def it_emails_every_member_when_none(linked_member):
        guild = GuildFactory()
        a = linked_member(email="a@example.com")
        b = linked_member(email="b@example.com")
        GuildMembershipFactory(guild=guild, member=a)
        GuildMembershipFactory(guild=guild, member=b)
        emit("guild_announcement", context={"guild": guild}, title="t", body="b", email_only_user_ids=None)
        assert _emailed() == {"a@example.com", "b@example.com"}

    def it_emails_nobody_when_the_subset_is_empty_but_still_bells_everyone(linked_member):
        guild = GuildFactory()
        a = linked_member(email="a@example.com")
        GuildMembershipFactory(guild=guild, member=a)
        result = emit(
            "guild_announcement",
            context={"guild": guild},
            title="t",
            body="b",
            email_only_user_ids=set(),  # an explicit empty set is "email no member" (not None = everyone)
        )
        assert _emailed() == set()
        assert (a.user_id, Channel.IN_APP) in result.delivered

    def it_posts_the_discord_broadcast_once_regardless_of_the_subset(linked_member):
        webhook = "https://discord.com/api/webhooks/guild"
        guild = GuildFactory(discord_post_enabled=True, discord_webhook_url=webhook)
        a = linked_member(email="a@example.com")
        b = linked_member(email="b@example.com")
        GuildMembershipFactory(guild=guild, member=a)
        GuildMembershipFactory(guild=guild, member=b)
        with patch("core.events.discord.post_embed", return_value=True) as mock_post:
            emit(
                "guild_announcement",
                context={"guild": guild, "discord_broadcast_webhook": webhook},
                title="t",
                body="b",
                email_only_user_ids={a.user_id},
            )
        guild_posts = [call for call in mock_post.call_args_list if call.args[0] == webhook]
        assert len(guild_posts) == 1

    def it_coexists_with_extra_emails(linked_member):
        # The custom-address additive path is untouched by the member subset filter.
        guild = GuildFactory()
        a = linked_member(email="a@example.com")
        b = linked_member(email="b@example.com")
        GuildMembershipFactory(guild=guild, member=a)
        GuildMembershipFactory(guild=guild, member=b)
        emit(
            "guild_announcement",
            context={"guild": guild},
            title="t",
            body="b",
            email_only_user_ids={a.user_id},
            extra_emails=["booster@example.com"],
        )
        assert _emailed() == {"a@example.com", "booster@example.com"}
