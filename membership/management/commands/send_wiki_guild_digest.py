"""Email each guild's leadership their monthly wiki digest, and prune old search misses.

Registered in ``core/scheduled_jobs.py`` as a DAILY job, so the existing dispatcher fires
it once a day at ~6 AM Portland — no new cron service and no Render change. The command
gates the digest itself on the 1st of the month and runs the retention prune on **every**
invocation, which is what keeps the failed-search table bounded without a second job row
somebody has to remember exists. A mid-month manual "Run now" is therefore always safe and
never sends anything.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.events.senders import emit_with_email_shell
from membership.models import Guild
from membership.wiki_guild import (
    digest_in_app_body,
    digest_subject,
    guild_digest_payload,
    previous_month_window,
    purge_old_search_misses,
)


class Command(BaseCommand):
    help = "Send each guild's monthly wiki digest (on the 1st) and prune old search misses (daily)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Send the digest even when today is not the 1st. For a shell run; the dashboard never passes it.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Prune first, then send — the prune must run even on a day the digest does not."""
        pruned = purge_old_search_misses()
        self.stdout.write(f"Pruned {pruned} old wiki search miss row(s).")

        today = timezone.localdate()
        if today.day != 1 and not options["force"]:
            self.stdout.write("Not the 1st — no digests today.")
            return

        month_start, month_end = previous_month_window(today)
        sent = 0
        skipped = 0
        for guild in Guild.objects.order_by("name"):
            payload = guild_digest_payload(guild, month_start=month_start, month_end=month_end)
            if payload is None:
                # A quiet month sends nothing at all. An email of three empty sections is
                # how a digest becomes a filter rule.
                skipped += 1
                continue
            emit_with_email_shell(
                "wiki.guild_digest_monthly",
                context={"guild": guild},
                subject=digest_subject(guild, payload),
                text_template="membership/emails/wiki_guild_digest.txt",
                html_template="membership/emails/wiki_guild_digest.html",
                template_context=payload,
                in_app_title=f"{guild.name} wiki digest",
                in_app_body=digest_in_app_body(payload),
                url=payload["tab_url"],
                # One delivery per guild per month, so a re-run, a manual "Run now" and a
                # retry all deliver nothing the second time.
                period=f"wiki-digest:{guild.pk}:{month_start:%Y-%m}",
            )
            sent += 1
        self.stdout.write(self.style.SUCCESS(f"Sent {sent} guild wiki digest(s); {skipped} guild(s) had nothing."))
