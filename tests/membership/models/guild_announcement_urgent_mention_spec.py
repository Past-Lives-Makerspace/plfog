"""BDD specs for the urgent-announcement-does-not-force-@here fix
(GuildAnnouncement.notify_members).

``override_preferences`` (the "mark as urgent" flag) only affects whether the
EMAIL channel bypasses a member's opt-out preference — it must never conjure a
Discord ping on its own. The ping is exactly whatever ``discord_mention`` the
author explicitly passed, threaded straight through to ``emit``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from membership.models import GuildAnnouncement
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db


def describe_notify_members_discord_mention():
    def it_does_not_force_an_here_mention_when_marked_urgent():
        guild = GuildFactory()
        announcement = GuildAnnouncement.objects.create(guild=guild, title="Urgent", body="Read now.")

        with patch("core.events.emit.emit") as mock_emit:
            announcement.notify_members(discord_mention="", override_preferences=True)

        _args, kwargs = mock_emit.call_args
        assert kwargs["discord_mention"] == ""
        assert kwargs["override_preferences"] is True

    def it_still_threads_an_explicit_here_mention_through():
        guild = GuildFactory()
        announcement = GuildAnnouncement.objects.create(guild=guild, title="T", body="B")

        with patch("core.events.emit.emit") as mock_emit:
            announcement.notify_members(discord_mention="@here")

        _args, kwargs = mock_emit.call_args
        assert kwargs["discord_mention"] == "@here"
