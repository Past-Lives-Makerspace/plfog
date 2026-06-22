"""BDD specs for GuildAnnouncement expiry — is_active + .objects.active()."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from membership.models import GuildAnnouncement
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db


def _ann(guild, **kwargs):
    kwargs.setdefault("title", "T")
    kwargs.setdefault("body", "B")
    return GuildAnnouncement.objects.create(guild=guild, **kwargs)


def describe_guild_announcement_expiry():
    def it_is_active_with_no_expiry():
        assert _ann(GuildFactory()).is_active is True

    def it_is_active_when_expiry_is_today():
        assert _ann(GuildFactory(), expires_at=timezone.localdate()).is_active is True

    def it_is_inactive_after_the_expiry_date():
        assert _ann(GuildFactory(), expires_at=timezone.localdate() - timedelta(days=1)).is_active is False

    def it_active_queryset_keeps_unexpired_and_drops_expired():
        guild = GuildFactory()
        live = _ann(guild, title="Live")
        future = _ann(guild, title="Future", expires_at=timezone.localdate() + timedelta(days=5))
        expired = _ann(guild, title="Expired", expires_at=timezone.localdate() - timedelta(days=1))
        active = list(guild.announcements.active())
        assert live in active
        assert future in active
        assert expired not in active
