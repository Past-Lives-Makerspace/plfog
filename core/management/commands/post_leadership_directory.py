"""Show, and with ``--apply`` send, the #leadership-directory Discord post (#1149).

Dry run by default: prints the rendered messages as text and, with ``--wipe``, how many
messages in the channel it would delete; nothing is sent or stored. ``--apply`` runs the sync
now, forced: the stored hash is cleared first, so every stored message is edited (or, when one
is gone, a fresh set posted) even if nothing changed. ``--wipe`` first prints the channel's name (and refuses when it cannot be read); ``--wipe --apply`` then deletes every
message in the channel (bulk delete for those under 14 days old, one by one for older ones),
clears the stored ids, then posts fresh. ``--adopt <id>[,<id>...] --apply`` stores existing
message ids as FOG's post, so a post made by hand is edited in place rather than reposted.

A blank channel id or bot token sends nothing.
"""

from __future__ import annotations

import re

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from core.events.discord_dm import bot_disabled
from core.events.discord_leadership_directory import (
    LeadershipDirectorySyncError,
    channel_id,
    channel_message_ids,
    payload_text,
    render_directory_payloads,
    sync_leadership_directory,
    wipe_channel,
)
from core.integrations.discord_channel import DiscordChannelError, channel_info
from core.models import SiteConfiguration


def _adopted_ids(raw: str) -> list[str]:
    """The message ids ``--adopt`` names, in order; each must be digits."""
    ids = [part for part in re.split(r"[,\s]+", raw.strip()) if part]
    if not ids or not all(part.isdigit() for part in ids):
        raise CommandError("--adopt takes Discord message ids, numbers only, separated by commas.")
    return ids


def _force(message_ids: list[str] | None) -> None:
    """Clear the stored hash so the next sync sends; with ``message_ids``, store them as FOG's post too."""
    config = SiteConfiguration.load()
    if message_ids is not None:
        config.discord_leadership_directory_message_ids = message_ids
    config.discord_leadership_directory_hash = ""
    config.save(update_fields=["discord_leadership_directory_message_ids", "discord_leadership_directory_hash"])


class Command(BaseCommand):
    help = "Print the #leadership-directory Discord post; --apply sends it, --wipe clears the channel first."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--apply", action="store_true", help="Send instead of printing.")
        parser.add_argument("--wipe", action="store_true", help="Delete every message in the channel first.")
        parser.add_argument("--adopt", default="", help="Existing message ids to edit in place, comma separated.")

    def handle(self, *args: object, **options: object) -> None:
        apply, wipe, adopt = bool(options["apply"]), bool(options["wipe"]), str(options["adopt"] or "")
        if wipe and adopt:
            raise CommandError("Use --wipe or --adopt, not both.")
        adopted = _adopted_ids(adopt) if adopt else []
        try:
            payloads = render_directory_payloads()
        except DiscordChannelError as exc:
            raise CommandError(f"Could not read the Discord server's members for usernames. {exc}") from exc
        self.stdout.write(payload_text(payloads))
        channel = channel_id()
        if not channel or bot_disabled("leadership directory post"):
            self.stdout.write("Nothing sent: the leadership directory channel id or the bot token is blank.")
            return
        try:
            existing = self._wipe_targets(channel) if wipe else []
            if not apply:
                self._report_dry_run(wipe, existing, adopted)
                return
            if wipe:
                deleted = wipe_channel(channel, existing, now=timezone.now())
                self.stdout.write(f"Deleted {deleted} messages.")
            _force([] if wipe else adopted or None)
            sync_leadership_directory.call()
        except (DiscordChannelError, LeadershipDirectorySyncError) as exc:
            raise CommandError(str(exc)) from exc
        stored = SiteConfiguration.load().discord_leadership_directory_message_ids
        self.stdout.write(self.style.SUCCESS(f"Posted. Message ids: {', '.join(stored)}"))

    def _wipe_targets(self, channel: str) -> list[str]:
        """Name the channel, then list its messages; refuse when the channel cannot be read, so nothing is deleted blind."""
        try:
            name = str(channel_info(channel).get("name") or "")
        except DiscordChannelError as exc:
            raise CommandError(f"Could not read channel {channel}; nothing was deleted. {exc}") from exc
        if not name:
            raise CommandError(f"Channel {channel} has no name; check the id. Nothing was deleted.")
        self.stdout.write(f"Channel: #{name} ({channel})")
        return channel_message_ids(channel)

    def _report_dry_run(self, wipe: bool, existing: list[str], adopted: list[str]) -> None:
        if wipe:
            self.stdout.write(f"Would delete {len(existing)} messages in the channel.")
        if adopted:
            self.stdout.write(f"Would adopt messages {', '.join(adopted)} and edit them in place.")
        self.stdout.write("Dry run: nothing sent. Add --apply to send.")
