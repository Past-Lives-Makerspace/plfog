"""The settings matrix only surfaces channels an event actually offers."""

import pytest
from django.contrib.auth.models import User

from core.events import settings_matrix
from core.events.registry import Channel

pytestmark = pytest.mark.django_db


def describe_visible_channels():
    def it_includes_channels_events_offer():
        user = User.objects.create_user(username="vc", email="vc@example.com")
        channels = settings_matrix.visible_channels(user)
        assert Channel.IN_APP in channels
        assert Channel.EMAIL in channels
        assert Channel.PUSH in channels

    def it_drops_channels_no_event_offers():
        # SCHEDULED_EMAIL and DIGEST are declared shells no event uses yet — they
        # must not render as dead, all-"—" columns on the settings page.
        user = User.objects.create_user(username="vc2", email="vc2@example.com")
        channels = settings_matrix.visible_channels(user)
        assert Channel.SCHEDULED_EMAIL not in channels
        assert Channel.DIGEST not in channels

    def it_keeps_user_channel_display_order():
        user = User.objects.create_user(username="vc3", email="vc3@example.com")
        channels = settings_matrix.visible_channels(user)
        assert channels == [c for c in settings_matrix.USER_CHANNELS if c in channels]


def describe_build_matrix():
    def it_emits_one_cell_per_visible_channel():
        user = User.objects.create_user(username="vc4", email="vc4@example.com")
        expected = len(settings_matrix.visible_channels(user))
        matrix = settings_matrix.build_matrix(user)
        assert matrix  # categories exist
        for _category, rows in matrix:
            for row in rows:
                assert len(row.cells) == expected
