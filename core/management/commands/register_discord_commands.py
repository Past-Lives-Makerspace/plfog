"""Register the slash-command set with Discord (a go-live / command-set-change step).

Reads the single declarative registry (:func:`core.events.discord_commands.all_commands`)
and PUTs it to Discord's bulk-overwrite endpoint — one call per scope. Guild-scoped
commands go to the Past Lives server (``SiteConfiguration.discord_server_id``) and appear
instantly; global commands go to the application and take up to ~1h to propagate.

Idempotent (Discord's bulk PUT replaces the whole set). Fails loudly — registration is an
explicit operator action, not a runtime no-op — so blank credentials raise rather than
silently doing nothing. Run via a Render one-off job at go-live and whenever the command
set changes.
"""

from __future__ import annotations

from typing import Any

import httpx
from django.core.management.base import BaseCommand, CommandError

from core.events import discord_dm
from core.events.discord_commands import SlashCommand, all_commands
from core.events.discord_dm import API_BASE
from core.events.discord_oauth import client_id
from core.models import SiteConfiguration

_TIMEOUT_SECONDS = 10.0


class Command(BaseCommand):
    help = "Register the app's slash commands with Discord (guild-scoped by default)."

    def add_arguments(self, parser: Any) -> None:
        scope_group = parser.add_mutually_exclusive_group()
        scope_group.add_argument(
            "--guild-only",
            action="store_true",
            help="Register only guild-scoped commands (skip global).",
        )
        scope_group.add_argument(
            "--global-only",
            action="store_true",
            help="Register only global commands (skip guild-scoped).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be registered without calling Discord.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        application_id = client_id()
        token = discord_dm.bot_token()
        if not application_id or not token:
            raise CommandError("DISCORD_CLIENT_ID and DISCORD_BOT_TOKEN must both be set to register commands.")

        commands = all_commands()
        guild_commands = [c for c in commands if c.scope == "guild"]
        global_commands = [c for c in commands if c.scope == "global"]
        dry_run: bool = options["dry_run"]

        if not options["global_only"]:
            server_id = SiteConfiguration.load().discord_server_id
            if not server_id:
                raise CommandError(
                    "SiteConfiguration.discord_server_id is blank — set the Past Lives server id "
                    "before registering guild-scoped commands."
                )
            self._register(
                f"{API_BASE}/applications/{application_id}/guilds/{server_id}/commands",
                guild_commands,
                token,
                dry_run=dry_run,
                scope="guild",
            )

        if not options["guild_only"]:
            self._register(
                f"{API_BASE}/applications/{application_id}/commands",
                global_commands,
                token,
                dry_run=dry_run,
                scope="global",
            )

    def _register(self, url: str, commands: list[SlashCommand], token: str, *, dry_run: bool, scope: str) -> None:
        """PUT the serialized ``commands`` to ``url`` (or print them on ``--dry-run``)."""
        payload = [c.to_api_dict() for c in commands]
        names = ", ".join(c.name for c in commands) or "(none)"

        if dry_run:
            self.stdout.write(f"[dry-run] would register {len(payload)} {scope} command(s): {names}")
            return

        response = httpx.put(
            url,
            json=payload,
            headers={"Authorization": f"Bot {token}"},
            timeout=_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        self.stdout.write(self.style.SUCCESS(f"Registered {len(payload)} {scope} command(s): {names}"))
