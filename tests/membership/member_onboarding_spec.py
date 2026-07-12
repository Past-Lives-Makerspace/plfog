"""BDD specs for the member home-onboarding state (``Member.is_onboarded`` + checklist)."""

from __future__ import annotations

import pytest
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse

from membership.models import Member
from tests.membership.factories import (
    GuildFactory,
    GuildMembershipFactory,
    MemberFactory,
    VotePreferenceFactory,
)

pytestmark = pytest.mark.django_db


def _profile_ready_member() -> Member:
    """A member whose profile content essentials (photo/bio/pronouns/Discord) are filled."""
    return MemberFactory(
        profile_photo="members/profile/avatar.png",
        about_me="Potter and welder.",
        pronouns=Member.Pronouns.SHE_HER,
        discord_user_id="123456789012345678",
    )


def _profile_ready_member_unlinked_discord() -> Member:
    """Profile essentials filled via a TYPED discord_handle (no linked Discord account)."""
    return MemberFactory(
        profile_photo="members/profile/avatar.png",
        about_me="Potter and welder.",
        pronouns=Member.Pronouns.SHE_HER,
        discord_handle="@maker",
        discord_user_id="",
    )


def describe_member_is_onboarded():
    def it_is_false_for_a_brand_new_member():
        assert MemberFactory().is_onboarded is False

    def it_is_false_with_a_ready_profile_but_no_guild():
        assert _profile_ready_member().is_onboarded is False

    def it_is_false_with_a_guild_but_an_incomplete_profile():
        member = MemberFactory()
        GuildMembershipFactory(member=member)
        assert member.is_onboarded is False

    def it_is_true_with_a_ready_profile_and_a_joined_guild():
        member = _profile_ready_member()
        GuildMembershipFactory(member=member)
        assert member.is_onboarded is True

    def it_counts_a_directory_opt_out_member_as_onboarded():
        # The one subtle rule: hiding from the directory must NOT block onboarding.
        member = _profile_ready_member()
        member.show_in_directory = False
        member.save(update_fields=["show_in_directory"])
        GuildMembershipFactory(member=member)
        assert member.profile_completeness.complete is False
        assert member.is_onboarded is True

    def describe_voting_does_not_affect_it():
        def it_is_true_without_a_voting_preference():
            member = _profile_ready_member()
            GuildMembershipFactory(member=member)
            assert member._has_voting_preference is False
            assert member.is_onboarded is True

        def it_stays_false_when_only_voting_is_set():
            member = MemberFactory()
            VotePreferenceFactory(member=member)
            assert member.is_onboarded is False


def describe_member_onboarding():
    def it_builds_four_steps_in_order():
        checklist = MemberFactory().onboarding
        assert [step.key for step in checklist.steps] == ["profile", "guilds", "discord", "voting"]

    def it_marks_discord_and_voting_optional():
        checklist = MemberFactory().onboarding
        optional = {step.key: step.optional for step in checklist.steps}
        assert optional == {"profile": False, "guilds": False, "discord": True, "voting": True}

    def it_links_each_step_to_its_page():
        checklist = MemberFactory().onboarding
        urls = {step.key: step.url for step in checklist.steps}
        assert urls["profile"] == f"{reverse('hub_user_settings')}?tab=profile"
        assert urls["guilds"] == f"{reverse('hub_user_settings')}?tab=guilds"
        assert urls["discord"] == reverse("hub_discord_connect")
        assert urls["voting"] == reverse("hub_guild_voting")

    def describe_discord_step():
        def it_is_not_done_for_a_brand_new_member():
            step = next(s for s in MemberFactory().onboarding.steps if s.key == "discord")
            assert step.done is False
            assert step.hint == "We'll set up your guilds instantly"

        def it_is_done_only_when_discord_is_linked():
            member = MemberFactory(discord_user_id="123456789012345678")
            step = next(s for s in member.onboarding.steps if s.key == "discord")
            assert step.done is True
            assert step.hint == ""

        def it_is_not_satisfied_by_a_typed_handle_alone():
            # A free-text discord_handle does NOT satisfy the linked-account step.
            member = MemberFactory(discord_handle="@maker", discord_user_id="")
            step = next(s for s in member.onboarding.steps if s.key == "discord")
            assert step.done is False

        def it_does_not_change_is_onboarded():
            # Optional: an unlinked Discord never blocks onboarding.
            member = _profile_ready_member_unlinked_discord()
            GuildMembershipFactory(member=member)
            assert member.discord_is_linked is False
            assert member.is_onboarded is True

    def it_shows_the_profile_percent_hint_while_undone():
        member = MemberFactory()  # brand-new → 20% complete
        profile_step = next(s for s in member.onboarding.steps if s.key == "profile")
        assert profile_step.done is False
        assert profile_step.hint == f"{member.profile_completeness.percent}% complete"

    def it_clears_the_profile_hint_once_done():
        member = _profile_ready_member()
        profile_step = next(s for s in member.onboarding.steps if s.key == "profile")
        assert profile_step.done is True
        assert profile_step.hint == ""

    def it_reflects_completed_steps_in_done_flags():
        member = _profile_ready_member()  # _profile_ready_member links Discord
        GuildMembershipFactory(member=member)
        VotePreferenceFactory(member=member)
        done = {step.key: step.done for step in member.onboarding.steps}
        assert done == {"profile": True, "guilds": True, "discord": True, "voting": True}

    def describe_required_progress():
        def it_counts_only_profile_and_guilds():
            checklist = MemberFactory().onboarding
            assert checklist.required_total == 2

        def it_counts_completed_required_steps():
            member = _profile_ready_member()  # profile done, no guild
            assert member.onboarding.required_done == 1

        def it_does_not_let_the_optional_step_inflate_the_count():
            member = MemberFactory()
            VotePreferenceFactory(member=member)  # only the optional step is done
            assert member.onboarding.required_done == 0

        def it_mirrors_is_onboarded_in_complete():
            done = _profile_ready_member()
            GuildMembershipFactory(member=done)
            assert done.onboarding.complete is True
            assert MemberFactory().onboarding.complete is False


def describe_member_show_onboarding():
    def it_is_true_when_not_onboarded_and_not_dismissed():
        assert MemberFactory().show_onboarding is True

    def it_is_false_when_onboarded_even_if_not_dismissed():
        member = _profile_ready_member()
        GuildMembershipFactory(member=member)
        assert member.onboarding_dismissed_at is None
        assert member.show_onboarding is False

    def it_is_false_when_dismissed_even_if_not_onboarded():
        member = MemberFactory()
        member.dismiss_onboarding()
        assert member.is_onboarded is False
        assert member.show_onboarding is False


def describe_member_dismiss_onboarding():
    def it_stamps_onboarding_dismissed_at_only():
        member = MemberFactory()
        assert member.onboarding_dismissed_at is None

        member.dismiss_onboarding()

        member.refresh_from_db()
        assert member.onboarding_dismissed_at is not None
        # Does not touch the independent welcome timestamp.
        assert member.welcome_dismissed_at is None

    def it_re_stamps_on_a_second_dismiss():
        member = MemberFactory()
        member.dismiss_onboarding()
        first = member.onboarding_dismissed_at

        member.dismiss_onboarding()

        assert member.onboarding_dismissed_at >= first


def describe_no_n_plus_one():
    def it_resolves_the_checklist_without_per_guild_queries():
        member = _profile_ready_member()
        # Several joined guilds — the join check must not scale with guild count.
        for _ in range(3):
            GuildMembershipFactory(guild=GuildFactory(), member=member)

        fresh = Member.objects.get(pk=member.pk)
        with CaptureQueriesContext(connection) as ctx:
            show = fresh.show_onboarding
            checklist = fresh.onboarding

        # One guild-exists query + one voting-exists query; profile signals are local fields.
        assert show is False
        assert checklist.complete is True
        assert len(ctx.captured_queries) == 2
