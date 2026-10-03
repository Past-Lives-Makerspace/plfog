"""Send the site-wide announcements the composer queued, oldest first.

A site-wide send reaches every active member, which takes longer than a web request is
allowed to live (an inline fan-out to 279 members once took about a minute and the worker
was killed partway). So the composer only queues it (``AnnouncementDraft.queue_send``) and
this job, on the 15-minute scheduler, performs it.

Re-running is safe, and is the recovery path. A site send's delivery period is the draft's
own pk, so a run that died partway skips every member and the Discord post it already
delivered and reaches only the rest. A draft whose send raises stays queued for the next
tick; the run still sends the others and then fails loudly, naming every draft it could not
send, so the run record is red. The one exception is a results announcement whose results
already went out (another admin's draft got there first): it can never send, so it is taken
off the queue rather than failing every 15 minutes forever.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from membership.models import AnnouncementDraft, ResultsAlreadySentError


class Command(BaseCommand):
    help = "Send site-wide announcements queued from the composer. Safe to run every 15 minutes."

    def handle(self, *args: Any, **options: Any) -> None:
        queued = list(AnnouncementDraft.objects.queued())
        if not queued:
            self.stdout.write("No queued announcements.")
            return
        failed: list[str] = []
        for draft in queued:
            label = f"announcement #{draft.pk} ({draft.title})"
            try:
                _emailed, total = draft.send()
            except ResultsAlreadySentError as exc:
                draft.unqueue()
                failed.append(f"{label}: {exc} It was taken off the queue.")
                continue
            except Exception as exc:  # noqa: BLE001 — one bad draft must not block the rest; reported below
                failed.append(f"{label}: {exc}")
                continue
            self.stdout.write(self.style.SUCCESS(f"Sent {label} to {total} recipient(s)."))
        if failed:
            raise CommandError("Could not send " + "; ".join(failed))
