"""Guild announcements now emit an ABSOLUTE member-hub URL (was relative).

``GuildAnnouncement.notify_members`` used to pass a relative ``/guilds/<id>/`` link in
both the embed/click target and the email's ``guild_url`` placeholder — dead links once
they leave the site. It now builds the URL through MEMBER_BASE_URL, while still carrying
the ``guild`` handle the dual-route fan-out reads.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.conf import settings

from membership.models import GuildAnnouncement
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db


def describe_notify_members_url():
    def it_emits_absolute_member_hub_urls():
        guild = GuildFactory()
        announcement = GuildAnnouncement.objects.create(guild=guild, title="Anvil day", body="Come help.")

        with patch("core.events.emit.emit") as mock_emit:
            announcement.notify_members()

        _args, kwargs = mock_emit.call_args
        assert kwargs["url"].startswith(settings.MEMBER_BASE_URL)
        assert not kwargs["url"].startswith("/")
        assert kwargs["context"]["guild_url"].startswith(settings.MEMBER_BASE_URL)

    def it_still_carries_the_guild_handle_for_the_dual_route():
        guild = GuildFactory()
        announcement = GuildAnnouncement.objects.create(guild=guild, title="T", body="B")

        with patch("core.events.emit.emit") as mock_emit:
            announcement.notify_members()

        _args, kwargs = mock_emit.call_args
        assert kwargs["context"]["guild"] == guild


def describe_notify_members_broadcast_choices():
    """The author's two opt-out switches ride into ``emit`` as suppression flags."""

    def it_defaults_to_sending_both_channels():
        guild = GuildFactory()
        announcement = GuildAnnouncement.objects.create(guild=guild, title="T", body="B")

        with patch("core.events.emit.emit") as mock_emit:
            announcement.notify_members()

        _args, kwargs = mock_emit.call_args
        assert kwargs["suppress_email"] is False
        assert kwargs["suppress_guild_broadcast"] is False

    def it_passes_through_the_authors_off_choices():
        guild = GuildFactory()
        announcement = GuildAnnouncement.objects.create(
            guild=guild, title="T", body="B", send_email=False, post_to_discord=False
        )

        with patch("core.events.emit.emit") as mock_emit:
            announcement.notify_members()

        _args, kwargs = mock_emit.call_args
        assert kwargs["suppress_email"] is True
        assert kwargs["suppress_guild_broadcast"] is True
