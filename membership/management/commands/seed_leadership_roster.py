"""Seed the Leadership Directory roster from a JSON file or a base64 argument (#464).

The roster names real people, so it stays out of the repo and is handed in at run time:
``--file roster.json`` locally, or ``--roster-b64 <base64 of the JSON>`` as a Render job,
whose arguments cannot carry spaces. ``--dry-run`` reports the matches and writes nothing;
run that locally against production first, then the real thing as a job. Safe to re-run.
The roster shape is documented in ``membership/leadership_roster.py``.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction

from membership.leadership_roster import seed_roster


class Command(BaseCommand):
    help = "Seed the Leadership Directory team and guild channel names from a roster JSON kept outside the repo."

    def add_arguments(self, parser: CommandParser) -> None:
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--file", help="Path to the roster JSON.")
        source.add_argument("--roster-b64", help="The roster JSON, base64 encoded (for a Render job).")
        parser.add_argument("--dry-run", action="store_true", help="Report what would change and write nothing.")

    def handle(self, *args: Any, **options: Any) -> None:
        if options["file"]:
            raw = Path(options["file"]).read_text(encoding="utf-8")
        else:
            raw = base64.b64decode(options["roster_b64"]).decode("utf-8")
        with transaction.atomic():
            report = seed_roster(json.loads(raw), dry_run=options["dry_run"])
        for line in report.lines():
            self.stdout.write(line)
        if options["dry_run"]:
            self.stdout.write("Dry run: nothing written.")
