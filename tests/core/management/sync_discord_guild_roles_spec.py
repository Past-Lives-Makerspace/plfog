"""BDD specs for the sync_discord_guild_roles cron command."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command

from membership.discord_sync import ReconcileStats

pytestmark = pytest.mark.django_db


def describe_sync_discord_guild_roles():
    def it_reports_the_reconcile_counts_when_it_runs():
        out = StringIO()
        with patch(
            "membership.discord_sync.reconcile_reactions",
            return_value=ReconcileStats(added=2, removed=1, skipped_guilds=0, nudged=3, ran=True),
        ):
            call_command("sync_discord_guild_roles", stdout=out)
        assert "+2 added" in out.getvalue()
        assert "-1 removed" in out.getvalue()
        assert "3 unlinked reactor(s) nudged" in out.getvalue()

    def it_reports_a_skip_when_unconfigured():
        out = StringIO()
        with patch("membership.discord_sync.reconcile_reactions", return_value=ReconcileStats(ran=False)):
            call_command("sync_discord_guild_roles", stdout=out)
        assert "skipped (not configured)" in out.getvalue()
