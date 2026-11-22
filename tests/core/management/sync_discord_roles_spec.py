"""BDD specs for the daily ``sync_discord_roles`` command (#1270): roster roles, then the managed roles."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import CommandError, call_command

from membership.discord_sync import RoleGrants

pytestmark = pytest.mark.django_db

ROSTER = "membership.discord_sync.sync_roster_roles"


def _run() -> tuple[str, list[tuple[str, ...]]]:
    out = StringIO()
    calls: list[tuple[str, ...]] = []
    with patch("core.management.commands.sync_discord_roles.call_command", side_effect=lambda *a, **k: calls.append(a)):
        call_command("sync_discord_roles", stdout=out)
    return out.getvalue(), calls


def describe_sync_discord_roles():
    def it_gives_roster_roles_then_applies_the_managed_roles():
        with patch(ROSTER, return_value=RoleGrants(given=3)):
            out, calls = _run()
        assert "Guild roles: 3 given, 0 failed." in out
        assert calls == [("sync_leadership_roles", "--apply")]

    def it_says_so_and_still_syncs_the_managed_roles_when_discord_is_not_configured():
        with patch(ROSTER, return_value=None):
            out, calls = _run()
        assert "Guild roles skipped" in out
        assert calls == [("sync_leadership_roles", "--apply")]

    def it_fails_the_run_after_the_managed_roles_when_a_guild_role_grant_failed():
        with patch(ROSTER, return_value=RoleGrants(given=1, failed=2)):
            with pytest.raises(CommandError, match="2 guild role grants failed"):
                _run()

    def it_runs_the_real_managed_role_sync_with_discord_off(settings):
        settings.DISCORD_BOT_TOKEN = ""
        out = StringIO()
        call_command("sync_discord_roles", stdout=out)
        assert "Guild roles skipped" in out.getvalue()
        assert "sync is off" in out.getvalue()
