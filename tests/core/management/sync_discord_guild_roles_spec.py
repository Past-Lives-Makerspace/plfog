"""BDD specs for the sync_discord_guild_roles cron command."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command

from membership.discord_sync import ReconcileStats, RoleGrants

pytestmark = pytest.mark.django_db


def describe_sync_discord_guild_roles():
    def it_reports_the_reconcile_counts_when_it_runs():
        out = StringIO()
        stats = ReconcileStats(added=2, left=2, taken=3, roles=RoleGrants(given=4, failed=1), nudged=3, ran=True)
        with patch("membership.discord_sync.reconcile_reactions", return_value=stats):
            call_command("sync_discord_guild_roles", stdout=out)
        assert (
            "+2 added, 2 left, 3 roles taken, 4 roles given, 1 role grants failed, 3 unlinked reactor(s) nudged"
            in out.getvalue()
        )

    def it_reports_a_skip_when_unconfigured():
        out = StringIO()
        with patch("membership.discord_sync.reconcile_reactions", return_value=ReconcileStats(ran=False)):
            call_command("sync_discord_guild_roles", stdout=out)
        assert "skipped (not configured)" in out.getvalue()
