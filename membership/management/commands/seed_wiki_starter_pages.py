"""Seed the space-wide wiki pages every makerspace member goes looking for in their first month.

The machine seeder gives every tool a page; this gives the space itself one: first week,
cleanup, dust collection, waste, protective gear, scrap, who to ask, where to buy. Each is
a stub of headings and empty prompt lines (a panel or a checklist line the editor hints
inside, and the reading page drops while empty), so it lands on the wiki home's Pages
Waiting For You card, and a member's first contribution to it is an edit rather than a
blank page.

The stubs state no facts. A seeded sentence about the space would be wrong somewhere, and
it would read as official. Every word on these pages comes from a member.

Two rules make it safe to re-run against production:

1. **It never touches a page that exists.** A live space-wide page with the same title, in
   any state and by any author, is left exactly as it is and counted as skipped. There is
   no refresh: once a stub exists it belongs to the members.
2. **It writes through ``create_page``**, authorless like the machine seeder, so each stub
   has a first revision and ``body_edited_at`` stays blank until a person writes there.

Like ``seed_wiki_machine_pages``, it is NOT part of any deploy; run it by hand once.

Usage::

    manage.py seed_wiki_starter_pages [--dry-run]
"""

from __future__ import annotations

from typing import Any, NamedTuple

from django.core.management.base import BaseCommand
from django.db import IntegrityError, transaction

CREATION_NOTE = "Created as a starter page"

_EMPTY = "<p></p>"
_CHECK = '<ul class="pl-checklist"><li data-list="unchecked"><br></li></ul>'


def _panel(kind: str) -> str:
    return f'<p class="pl-panel-{kind}"><br></p>'


class StarterPage(NamedTuple):
    title: str
    kind: str
    body: str


PAGES: tuple[StarterPage, ...] = (
    StarterPage(
        "Start Here: Your First Week",
        "reference",
        f"<h2>Your First Visit</h2>{_CHECK}<h2>Orientations Worth Booking</h2>{_EMPTY}"
        f"<h2>Who To Ask</h2>{_panel('info')}",
    ),
    StarterPage(
        "Hours, Guests and Getting In",
        "reference",
        f"<h2>Hours</h2>{_EMPTY}<h2>Bringing A Guest</h2>{_EMPTY}{_panel('warning')}<h2>Getting In</h2>{_EMPTY}",
    ),
    StarterPage(
        "Where Things Are",
        "reference",
        f"<h2>Rooms</h2>{_EMPTY}<h2>Storage And Cubbies</h2>{_EMPTY}<h2>Lost And Found</h2>{_EMPTY}",
    ),
    StarterPage(
        "End Of Session Cleanup",
        "howto",
        f"<h2>Before You Leave</h2>{_CHECK}<h2>Where The Cleaning Supplies Are</h2>{_EMPTY}"
        f"<h2>What Gets Missed</h2>{_panel('tip')}",
    ),
    StarterPage(
        "Dust Collection",
        "howto",
        f"<h2>Which Gate To Open</h2>{_EMPTY}<h2>Before You Cut</h2>{_CHECK}"
        f"<h2>Emptying The Bags</h2>{_EMPTY}{_panel('warning')}",
    ),
    StarterPage(
        "Oily Rags, Solvents and Resin Waste",
        "material",
        f"<h2>What Goes Where</h2>{_EMPTY}{_panel('danger')}<h2>Who Empties It</h2>{_EMPTY}",
    ),
    StarterPage(
        "Protective Gear: What Lives Where",
        "reference",
        f"<h2>Eyes And Ears</h2>{_EMPTY}<h2>Respirators And Masks</h2>{_EMPTY}"
        f"<h2>Gloves And Aprons</h2>{_EMPTY}{_panel('warning')}",
    ),
    StarterPage(
        "Scrap Bins and the Free Shelf",
        "material",
        f"<h2>What Can Go In</h2>{_EMPTY}<h2>What Cannot</h2>{_panel('warning')}<h2>Taking From It</h2>{_EMPTY}",
    ),
    StarterPage(
        "Who To Ask For What",
        "reference",
        f"<h2>Machines And Tools</h2>{_EMPTY}<h2>Space And Storage</h2>{_EMPTY}"
        f"<h2>Membership And Billing</h2>{_panel('info')}",
    ),
    StarterPage(
        "Where To Buy Materials In Portland",
        "reference",
        f"<h2>Wood</h2>{_EMPTY}<h2>Metal</h2>{_EMPTY}<h2>Filament And Resin</h2>{_EMPTY}"
        f"<h2>Clay And Glaze</h2>{_EMPTY}{_panel('tip')}",
    ),
)


class Command(BaseCommand):
    help = (
        "Seed the space-wide starter pages (first week, cleanup, dust collection, waste and "
        "more) as stubs for members to fill in. Never touches a page that already exists. "
        "Use --dry-run to preview."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be added without writing any rows.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from membership.models import WikiError, WikiPage

        dry_run = bool(options["dry_run"])
        added: list[str] = []
        skipped: list[str] = []
        for entry in PAGES:
            if WikiPage.objects.filter(title__iexact=entry.title, guild__isnull=True).not_archived().exists():
                skipped.append(entry.title)
                continue
            if dry_run:
                added.append(entry.title)
                continue
            try:
                # A savepoint per page, so losing a race to a member creating the same title
                # skips that one page instead of ending the run.
                with transaction.atomic():
                    WikiPage.objects.create_page(
                        title=entry.title, kind=entry.kind, author=None, body=entry.body, note=CREATION_NOTE
                    )
            except (WikiError, IntegrityError):
                skipped.append(entry.title)
                continue
            added.append(entry.title)

        prefix = "Would add" if dry_run else "Added"
        self.stdout.write(f"{prefix} {len(added)} starter pages; {len(skipped)} already existed and were left alone.")
        for title in added:
            self.stdout.write(f"  + {title}")
