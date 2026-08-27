"""BDD specs for the guild-updates fat-model layer on ``Member``.

Covers the one-time prompt eligibility (``needs_guild_updates_prompt``), the shared
answered-stamp recorder (``mark_guild_updates_answered``), the single subscribe and
unsubscribe paths, the prompt answer (``answer_guild_updates_prompt``), and the
welcome-email period dedupe across skip/subscribe/resubscribe sequences.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core import mail

from membership.models import GuildMembership
from tests.membership.factories import (
    GuildFactory,
    GuildMembershipFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
)

pytestmark = pytest.mark.django_db


def describe_needs_guild_updates_prompt():
    def it_is_true_with_no_stamp_and_no_subscriptions():
        assert MemberFactory().needs_guild_updates_prompt is True

    def it_is_false_once_stamped():
        member = MemberFactory()
        member.mark_guild_updates_answered()
        assert member.needs_guild_updates_prompt is False

    def it_is_false_when_any_subscription_exists():
        member = MemberFactory()
        GuildMembershipFactory(member=member)
        assert member.needs_guild_updates_prompt is False

    def it_is_false_for_a_discord_sourced_subscription():
        # A Discord reactor has effectively answered — never prompted.
        member = MemberFactory()
        GuildMembershipFactory(member=member, source=GuildMembership.Source.DISCORD)
        assert member.needs_guild_updates_prompt is False

    def it_stays_false_after_unsubscribing_everything_post_answer():
        member = MemberFactory()
        guild = GuildFactory()
        member.answer_guild_updates_prompt([guild])
        member.unsubscribe_from_guild(guild)
        assert member.guild_memberships.count() == 0
        assert member.needs_guild_updates_prompt is False


def describe_mark_guild_updates_answered():
    def it_stamps_when_null():
        member = MemberFactory()
        member.mark_guild_updates_answered()
        member.refresh_from_db()
        assert member.guild_updates_prompt_answered_at is not None

    def it_never_overwrites_an_existing_stamp():
        member = MemberFactory()
        member.mark_guild_updates_answered()
        member.refresh_from_db()
        first = member.guild_updates_prompt_answered_at
        member.mark_guild_updates_answered()
        member.refresh_from_db()
        assert member.guild_updates_prompt_answered_at == first


def describe_subscribe_to_guild():
    def it_creates_an_app_sourced_row_and_fires_the_side_effects():
        member = MemberFactory()
        guild = GuildFactory()
        with (
            patch("membership.orientations.member_joined_guild") as joined,
            patch("core.events.discord_roles.on_membership_changed") as roles,
        ):
            created = member.subscribe_to_guild(guild)
        assert created is True
        row = GuildMembership.objects.get(guild=guild, member=member)
        assert row.source == GuildMembership.Source.APP
        joined.assert_called_once_with(guild, member)
        roles.assert_called_once_with(guild, member, joined=True)

    def it_fires_the_side_effect_on_a_discord_to_app_upgrade():
        member = MemberFactory()
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=member, source=GuildMembership.Source.DISCORD)
        with (
            patch("membership.orientations.member_joined_guild") as joined,
            patch("core.events.discord_roles.on_membership_changed"),
        ):
            upgraded = member.subscribe_to_guild(guild)
        assert upgraded is True
        joined.assert_called_once_with(guild, member)
        assert GuildMembership.objects.get(guild=guild, member=member).source == GuildMembership.Source.APP

    def it_does_not_refire_when_already_app_sourced():
        member = MemberFactory()
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=member, source=GuildMembership.Source.APP)
        with (
            patch("membership.orientations.member_joined_guild") as joined,
            patch("core.events.discord_roles.on_membership_changed") as roles,
        ):
            changed = member.subscribe_to_guild(guild)
        assert changed is False
        joined.assert_not_called()
        # The role self-heal still runs on every path (idempotent).
        roles.assert_called_once_with(guild, member, joined=True)
        assert GuildMembership.objects.filter(guild=guild, member=member).count() == 1


def describe_unsubscribe_from_guild():
    def it_deletes_the_row_and_fires_the_role_removal():
        member = MemberFactory()
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=member)
        with patch("core.events.discord_roles.on_membership_changed") as roles:
            member.unsubscribe_from_guild(guild)
        assert not GuildMembership.objects.filter(guild=guild, member=member).exists()
        roles.assert_called_once_with(guild, member, joined=False)

    def it_is_idempotent_when_no_row_exists():
        member = MemberFactory()
        guild = GuildFactory()
        with patch("core.events.discord_roles.on_membership_changed") as roles:
            member.unsubscribe_from_guild(guild)
        assert GuildMembership.objects.count() == 0
        roles.assert_called_once_with(guild, member, joined=False)


def describe_answer_guild_updates_prompt():
    def it_subscribes_each_pick_stamps_and_returns_the_count():
        member = MemberFactory()
        picks = [GuildFactory(name="One"), GuildFactory(name="Two")]
        count = member.answer_guild_updates_prompt(picks)
        assert count == 2
        assert member.guild_memberships.count() == 2
        member.refresh_from_db()
        assert member.guild_updates_prompt_answered_at is not None

    def it_stamps_with_zero_rows_for_an_empty_pick():
        member = MemberFactory()
        count = member.answer_guild_updates_prompt([])
        assert count == 0
        assert member.guild_memberships.count() == 0
        member.refresh_from_db()
        assert member.guild_updates_prompt_answered_at is not None

    def it_does_not_duplicate_rows_when_re_answered():
        member = MemberFactory()
        guild = GuildFactory()
        member.answer_guild_updates_prompt([guild])
        count = member.answer_guild_updates_prompt([guild])
        assert count == 0
        assert GuildMembership.objects.filter(guild=guild, member=member).count() == 1


def describe_welcome_email_dedupe():
    @pytest.fixture
    def guild_with_welcome():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(
            guild=guild,
            is_enabled=True,
            join_email_enabled=True,
            join_email_subject="Welcome to the guild!",
            join_email_body="So glad you're here.",
        )
        return guild

    def it_sends_the_welcome_once_when_a_skipper_later_subscribes(guild_with_welcome):
        member = MemberFactory()
        member.answer_guild_updates_prompt([])  # Skip
        member.subscribe_to_guild(guild_with_welcome)  # later, from Settings
        assert sum(1 for m in mail.outbox if m.subject == "Welcome to the guild!") == 1

    def it_does_not_resend_the_welcome_on_unsubscribe_then_resubscribe(guild_with_welcome):
        member = MemberFactory()
        member.subscribe_to_guild(guild_with_welcome)
        member.unsubscribe_from_guild(guild_with_welcome)
        member.subscribe_to_guild(guild_with_welcome)
        assert sum(1 for m in mail.outbox if m.subject == "Welcome to the guild!") == 1
