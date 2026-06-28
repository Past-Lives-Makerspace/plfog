"""BDD specs for the VotingSettings singleton."""

from __future__ import annotations

from decimal import Decimal

import pytest

from membership.models import VotingSettings

pytestmark = pytest.mark.django_db


def describe_VotingSettings():
    def describe_load():
        def it_creates_the_pk1_singleton_with_documented_defaults():
            settings = VotingSettings.load()
            assert settings.pk == 1
            assert settings.reminder_lead_days == 3
            assert settings.minimum_pool_floor == Decimal("1000.00")
            assert settings.reminders_enabled is True
            assert settings.send_vote_soon_enabled is True
            assert settings.auto_snapshot_enabled is True

        def it_returns_the_same_row_on_a_second_load():
            first = VotingSettings.load()
            first.reminder_lead_days = 5
            first.save()
            second = VotingSettings.load()
            assert second.pk == first.pk
            assert second.reminder_lead_days == 5
            assert VotingSettings.objects.count() == 1

    def describe_save():
        def it_cannot_create_a_second_row():
            VotingSettings.load()
            extra = VotingSettings(reminder_lead_days=7)
            extra.save()
            assert VotingSettings.objects.count() == 1
            assert VotingSettings.objects.get(pk=1).reminder_lead_days == 7

    def describe_str():
        def it_reads_voting_settings():
            assert str(VotingSettings.load()) == "Voting settings"
