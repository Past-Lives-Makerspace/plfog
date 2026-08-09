"""Send a funding snapshot's member results email headlessly (with an optional note).

The admin UI (``voting_send_results``) is the normal path; this command exists so a
results send can run as a one-off job (e.g. a Render job) without an admin session —
and so a late/overdue send can carry an organizer note at the top of the email.

The note is passed **base64-encoded** (``--note-b64``) on purpose: a one-off job runner
may split the command on whitespace, which would mangle a plain ``--note "long text"``.
base64 is a single whitespace-free token, so it survives intact.

    python manage.py send_funding_results --snapshot-id 4
    python manage.py send_funding_results --snapshot-id 4 --note-b64 "$(printf '%s' 'Sorry this is late!' | base64 -w0)"
    python manage.py send_funding_results --snapshot-id 4 --resend   # re-send an already-sent snapshot
    python manage.py send_funding_results --snapshot-id 4 --no-discord  # suppress the Discord @everyone post
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from membership.models import FundingSnapshot, ResultsAlreadySentError


class Command(BaseCommand):
    help = "Send a funding snapshot's personalized results email to its voters (optional intro note)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--snapshot-id", type=int, required=True, help="FundingSnapshot primary key to send.")
        parser.add_argument(
            "--note-b64",
            type=str,
            default="",
            help="Optional intro note, base64-encoded (UTF-8), shown at the top of the email.",
        )
        parser.add_argument(
            "--resend",
            action="store_true",
            help="Allow re-sending a snapshot whose results were already sent.",
        )
        parser.add_argument(
            "--no-discord",
            action="store_true",
            help="Suppress the @everyone Discord broadcast — email members only.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        note = self._decode_note(options["note_b64"])

        try:
            snapshot = FundingSnapshot.objects.get(pk=options["snapshot_id"])
        except FundingSnapshot.DoesNotExist:
            raise CommandError(f"No FundingSnapshot with id {options['snapshot_id']}.")

        try:
            sent = snapshot.send_results(
                resend=options["resend"],
                intro_note=note,
                discord=not options["no_discord"],
            )
        except ResultsAlreadySentError as exc:
            raise CommandError(f"{exc} Pass --resend to send it again.")

        note_label = " (with intro note)" if note else ""
        self.stdout.write(self.style.SUCCESS(f"Sent {sent} results email(s) for '{snapshot.cycle_label}'{note_label}."))

    def _decode_note(self, raw: str) -> str:
        """Decode the base64 ``--note-b64`` argument to text; raise on malformed input."""
        if not raw:
            return ""
        try:
            return base64.b64decode(raw, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise CommandError(f"--note-b64 is not valid base64-encoded UTF-8: {exc}")
