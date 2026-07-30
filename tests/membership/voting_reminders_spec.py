"""BDD specs for the per-member voting reminder sources (closing_soon + vote_soon).

Timing tests freeze ``now`` so the month-end close + configurable lead window are
deterministic.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from decimal import Decimal

from core.events.scheduler import run_sources
from core.models import EventDelivery, Notification
from membership.models import Member, VotingSettings
from membership.voting import (
    close_period,
    closing_soon_occurrences,
    cycle_start,
    cycle_turnout_stats,
    month_end_close,
    officers_closing_soon_occurrences,
    previous_cycle_label,
    vote_soon_occurrences,
)
from tests.membership.factories import GuildFactory, MemberFactory, VotePreferenceFactory

pytestmark = pytest.mark.django_db


def _linked(member, email, *, last_login):
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"u{member.pk}", email=email)
    if last_login is not None:
        user.last_login = last_login
        user.save(update_fields=["last_login"])
    member.user = user
    member.save(update_fields=["user"])
    return user


def _voted(email, *, picks=None):
    member = MemberFactory()  # ACTIVE + STANDARD (paying)
    _linked(member, email, last_login=timezone.now())
    g1, g2, g3 = picks or (GuildFactory(), GuildFactory(), GuildFactory())
    VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3, signed_up=False)
    return member


def _logged_in_no_vote(email, *, member_type=Member.MemberType.STANDARD):
    member = MemberFactory(member_type=member_type)
    _linked(member, email, last_login=timezone.now())
    return member


def _aware(y, m, d, h=0):
    return timezone.make_aware(datetime(y, m, d, h, 0))


def describe_cycle_timing_helpers():
    def it_closes_a_mid_year_cycle_on_the_first_of_next_month():
        assert month_end_close(_aware(2026, 6, 15)) == _aware(2026, 7, 1)

    def it_rolls_a_december_cycle_into_next_january():
        assert month_end_close(_aware(2026, 12, 15)) == _aware(2027, 1, 1)

    def it_derives_the_just_closed_cycle_for_a_mid_year_tick():
        assert cycle_start(_aware(2026, 7, 5)) == _aware(2026, 6, 1)
        assert previous_cycle_label(_aware(2026, 7, 5)) == "June 2026"
        assert close_period(_aware(2026, 7, 5)) == "voting_close:2026-06"

    def it_handles_the_january_rollover_for_the_just_closed_cycle():
        assert cycle_start(_aware(2027, 1, 3)) == _aware(2026, 12, 1)
        assert previous_cycle_label(_aware(2027, 1, 3)) == "December 2026"
        assert close_period(_aware(2027, 1, 3)) == "voting_close:2026-12"


def describe_closing_soon_occurrences():
    def it_yields_one_per_voted_member_with_their_vote_context():
        now = _aware(2026, 6, 1)
        picks = (GuildFactory(name="Metal"), GuildFactory(name="Fiber"), GuildFactory(name="Wood"))
        member = _voted("v@x.com", picks=picks)

        occ = list(closing_soon_occurrences(now))

        assert len(occ) == 1
        o = occ[0]
        assert o.event_key == "voting.closing_soon"
        assert o.context["member"].pk == member.pk
        assert o.context["vote_1st"] == "Metal"
        assert o.context["cycle_label"] == "June 2026"
        assert o.anchor == month_end_close(now)
        assert o.offset.days == -3  # the default lead

    def it_fires_lead_days_before_close_and_dedupes_per_cycle():
        _voted("v@x.com")
        fire = _aware(2026, 6, 28)  # July-1 close − 3 days

        first = run_sources([closing_soon_occurrences], now=fire)
        second = run_sources([closing_soon_occurrences], now=fire)

        assert first == 1
        assert second == 0  # EventDelivery period voting:2026-06 dedupes
        assert EventDelivery.objects.filter(event_key="voting.closing_soon", period="voting:2026-06").exists()

    def it_honors_a_changed_lead_time():
        settings = VotingSettings.load()
        settings.reminder_lead_days = 5
        settings.save()
        _voted("v@x.com")

        # With a 5-day lead the default 3-day instant is NOT due...
        assert run_sources([closing_soon_occurrences], now=_aware(2026, 6, 28)) == 0
        # ...but the 5-day instant is.
        assert run_sources([closing_soon_occurrences], now=_aware(2026, 6, 26)) == 1

    def it_yields_nothing_when_reminders_are_disabled():
        settings = VotingSettings.load()
        settings.reminders_enabled = False
        settings.save()
        _voted("v@x.com")

        assert list(closing_soon_occurrences(_aware(2026, 6, 1))) == []


def describe_vote_soon_occurrences():
    def it_targets_only_paying_active_logged_in_non_voters():
        paying = _logged_in_no_vote("p@x.com")
        nonpaying = _logged_in_no_vote("np@x.com", member_type=Member.MemberType.WORK_TRADE)
        never = MemberFactory()
        _linked(never, "never@x.com", last_login=None)
        voted = _voted("voted@x.com")

        members = {o.context["member"].pk for o in vote_soon_occurrences(_aware(2026, 6, 1))}

        assert paying.pk in members
        assert nonpaying.pk not in members
        assert never.pk not in members
        assert voted.pk not in members

    def it_emits_the_vote_soon_event_key():
        _logged_in_no_vote("p@x.com")
        occ = list(vote_soon_occurrences(_aware(2026, 6, 1)))
        assert occ and all(o.event_key == "voting.vote_soon" for o in occ)

    def it_yields_nothing_when_vote_soon_is_disabled():
        settings = VotingSettings.load()
        settings.send_vote_soon_enabled = False
        settings.save()
        _logged_in_no_vote("p@x.com")

        assert list(vote_soon_occurrences(_aware(2026, 6, 1))) == []

    def it_yields_nothing_when_the_reminders_master_switch_is_off():
        settings = VotingSettings.load()
        settings.reminders_enabled = False  # send_vote_soon_enabled still True
        settings.save()
        _logged_in_no_vote("p@x.com")

        assert list(vote_soon_occurrences(_aware(2026, 6, 1))) == []


def _lead(email):
    """An active guild lead with a linked, signed-in user — an ALL_GUILD_LEADS recipient."""
    member = MemberFactory()
    _linked(member, email, last_login=timezone.now())
    GuildFactory(name=f"Guild for {email}", guild_lead=member)
    return member


def describe_cycle_turnout_stats():
    def it_counts_voters_nonvoters_and_applies_the_pool_floor():
        _voted("v@x.com")  # one paying voter → $10 contributed
        _logged_in_no_vote("nv@x.com")  # one eligible non-voter

        stats = cycle_turnout_stats()

        assert stats["turnout_count"] == "1"
        assert stats["not_voted_count"] == "1"
        # $10 contributed is below the default $1,000 floor, so the floor wins.
        assert stats["pool_display"] == "$1,000"

    def it_uses_the_contributed_pool_when_it_exceeds_the_floor():
        settings = VotingSettings.load()
        settings.minimum_pool_floor = Decimal("5.00")
        settings.save()
        _voted("a@x.com")
        _voted("b@x.com")  # two paying voters → $20 contributed, above the $5 floor

        assert cycle_turnout_stats()["pool_display"] == "$20"

    def it_formats_a_fractional_pool_with_cents():
        settings = VotingSettings.load()
        settings.minimum_pool_floor = Decimal("1000.50")
        settings.save()
        _voted("v@x.com")

        assert cycle_turnout_stats()["pool_display"] == "$1,000.50"


def describe_officers_closing_soon_occurrences():
    def it_yields_a_single_heads_up_carrying_turnout_context():
        now = _aware(2026, 6, 1)
        _voted("v@x.com")
        _logged_in_no_vote("nv@x.com")

        occ = list(officers_closing_soon_occurrences(now))

        assert len(occ) == 1
        o = occ[0]
        assert o.event_key == "voting.officers_closing_soon"
        assert "member" not in o.context  # broadcast-style: one shared occurrence
        assert o.context["cycle_label"] == "June 2026"
        assert o.context["turnout_count"] == "1"
        assert o.context["not_voted_count"] == "1"
        assert o.anchor == month_end_close(now)
        assert o.offset.days == -3

    def it_fires_to_guild_leadership_and_dedupes_per_cycle():
        lead = _lead("lead@x.com")
        fire = _aware(2026, 6, 28)  # default 3-day lead

        first = run_sources([officers_closing_soon_occurrences], now=fire)
        second = run_sources([officers_closing_soon_occurrences], now=fire)

        assert first == 1
        assert second == 0  # EventDelivery period voting:2026-06 dedupes per officer
        assert Notification.objects.filter(trigger="voting.officers_closing_soon", user=lead.user).exists()
        assert EventDelivery.objects.filter(event_key="voting.officers_closing_soon", period="voting:2026-06").exists()

    def it_yields_nothing_when_the_officer_switch_is_off():
        settings = VotingSettings.load()
        settings.send_officer_reminder_enabled = False
        settings.save()

        assert list(officers_closing_soon_occurrences(_aware(2026, 6, 1))) == []

    def it_yields_nothing_when_the_reminders_master_switch_is_off():
        settings = VotingSettings.load()
        settings.reminders_enabled = False  # send_officer_reminder_enabled still True
        settings.save()

        assert list(officers_closing_soon_occurrences(_aware(2026, 6, 1))) == []


def describe_both_sources_together():
    def it_reminds_a_voter_and_a_nonvoter_distinctly_and_skips_never_logged_in():
        voted = _voted("voted@x.com")
        nonvoter = _logged_in_no_vote("nv@x.com")
        never = MemberFactory()
        never_user = _linked(never, "never@x.com", last_login=None)
        fire = _aware(2026, 6, 28)  # default 3-day lead

        run_sources([closing_soon_occurrences, vote_soon_occurrences], now=fire)

        assert Notification.objects.filter(trigger="voting.closing_soon", user=voted.user).exists()
        assert Notification.objects.filter(trigger="voting.vote_soon", user=nonvoter.user).exists()
        assert not Notification.objects.filter(user=never_user).exists()
