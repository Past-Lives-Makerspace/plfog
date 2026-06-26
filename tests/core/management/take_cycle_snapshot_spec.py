"""BDD specs for the take_cycle_snapshot auto-snapshot command.

Time is frozen so the "just-closed cycle" is deterministic across the year boundary:
with ``now`` in July 2026 the closed cycle is June 2026 (period voting_close:2026-06).
"""

from __future__ import annotations

from datetime import datetime

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from core.models import EventDelivery, Notification
from membership.models import FundingSnapshot, Member, VotingSettings
from tests.membership.factories import GuildFactory, MemberFactory, VotePreferenceFactory

pytestmark = pytest.mark.django_db


def _freeze(monkeypatch, moment):
    monkeypatch.setattr("core.management.commands.take_cycle_snapshot.timezone.now", lambda: moment)


def _linked(member, email):
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"u{member.pk}", email=email)
    member.user = user
    member.save(update_fields=["user"])
    return user


def _voter(email):
    member = MemberFactory()
    _linked(member, email)
    g1, g2, g3 = GuildFactory(), GuildFactory(), GuildFactory()
    VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3, signed_up=False)
    return member


def _admin(email):
    member = MemberFactory(fog_role=Member.FogRole.ADMIN)
    return _linked(member, email)


def _july():
    return timezone.make_aware(datetime(2026, 7, 5, 9, 0))


def describe_take_cycle_snapshot():
    def it_auto_takes_once_per_cycle_flagging_is_auto_and_pinging_admins(monkeypatch):
        _freeze(monkeypatch, _july())
        _voter("voter@x.com")
        admin_user = _admin("admin@x.com")

        call_command("take_cycle_snapshot")
        call_command("take_cycle_snapshot")  # second tick same cycle → no-op

        autos = FundingSnapshot.objects.filter(is_auto=True)
        assert autos.count() == 1
        snap = autos.first()
        assert snap.cycle_label == "June 2026"
        assert EventDelivery.objects.filter(event_key="voting.auto_snapshot", period="voting_close:2026-06").exists()
        # Admins pinged; members not emailed.
        assert Notification.objects.filter(user=admin_user, trigger="voting.results_ready").exists()
        assert not Notification.objects.filter(trigger="voting.results_published").exists()

    def it_is_a_noop_when_auto_snapshot_is_disabled(monkeypatch):
        settings = VotingSettings.load()
        settings.auto_snapshot_enabled = False
        settings.save()
        _freeze(monkeypatch, _july())
        _voter("voter@x.com")

        call_command("take_cycle_snapshot")

        assert FundingSnapshot.objects.count() == 0
        assert not EventDelivery.objects.filter(event_key="voting.auto_snapshot").exists()

    def it_is_a_noop_when_there_are_no_votes(monkeypatch):
        _freeze(monkeypatch, _july())

        call_command("take_cycle_snapshot")

        assert FundingSnapshot.objects.count() == 0

    def it_skips_when_a_manual_snapshot_already_covers_the_cycle_even_with_a_custom_title(monkeypatch):
        _freeze(monkeypatch, _july())
        _voter("voter@x.com")
        # An admin already took the month's snapshot under a non-default title.
        FundingSnapshot.take(title="Q2 wrap-up", is_auto=False)

        call_command("take_cycle_snapshot")

        # The window guard (snapshot_at >= cycle_start) suppresses the auto-take.
        assert not FundingSnapshot.objects.filter(is_auto=True).exists()
        assert FundingSnapshot.objects.count() == 1
