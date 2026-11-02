"""Seed the "Guild Staff Rules and Tools" wiki article that the guild staff welcome links (#1076).

The welcome email and the rules popup link it (``membership.staff_onboarding.rules_page_path``,
which falls back to the wiki home until it exists). It states the rule Felix set for every guild,
that orientations, reservations, equipment and email lists run through the Member Portal and
nowhere else, and where each Guild Staff Tool lives.

Like the other wiki seeders it never touches a page that exists: a live space-wide page with this
title, in any state and by any author, is left as it is. It is NOT part of any deploy; run it by
hand once after the release.

Usage::

    manage.py seed_wiki_staff_rules [--dry-run]
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from membership.staff_onboarding import RULES_PAGE_TITLE

CREATION_NOTE = "Created as the guild staff rules"

BODY = (
    "<h2>The Rules</h2>"
    '<p class="pl-panel-warning">Your guild\'s orientations, reservations, equipment and email lists all run '
    "through the Member Portal. Managing them anywhere else is no longer allowed.</p>"
    "<ul>"
    "<li>Orientations: offer them, set your hours and confirm every booking in the Member Portal.</li>"
    "<li>Reservations: the rooms, spaces and tools members reserve from your guild are booked in the Member Portal.</li>"
    "<li>Equipment: your guild's equipment, its agreements and who is signed off live in the Member Portal.</li>"
    "<li>Email lists: write to your members and your guild's email lists from Email Members in the Member Portal.</li>"
    "</ul>"
    "<h2>Where Your Tools Live</h2>"
    "<ul>"
    "<li><strong>Guild Settings</strong>: on your guild page, press Guild Settings. Every tab is one job and saves on its own.</li>"
    "<li><strong>Orientations</strong>: the Orientations tab in Guild Settings.</li>"
    "<li><strong>Reservations</strong>: the Reservations tab in Guild Settings.</li>"
    "<li><strong>Equipment</strong>: Reservations in the sidebar lists every item; open one and press Manage.</li>"
    "<li><strong>Manage Bookings</strong>: Manage Bookings in the sidebar, every orientation and reservation request waiting on you.</li>"
    "<li><strong>Email Members</strong>: the Email Members tab in Guild Settings.</li>"
    "<li><strong>Staff</strong>: the Staff tab in Guild Settings.</li>"
    "<li><strong>Guild Tools</strong>: Guild Tools in the sidebar, everything for every guild you run.</li>"
    "</ul>"
    "<h2>Tours</h2>"
    "<p>Take the Guild Staff Tools tour any time from the Help page.</p>"
)


class Command(BaseCommand):
    help = (
        f"Seed the '{RULES_PAGE_TITLE}' wiki article the guild staff welcome links. "
        "Never touches a page that already exists. Use --dry-run to preview."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--dry-run", action="store_true", help="Report what would be added without writing.")

    def handle(self, *args: Any, **options: Any) -> None:
        from membership.models import WikiPage

        if WikiPage.objects.filter(title__iexact=RULES_PAGE_TITLE, guild__isnull=True).not_archived().exists():
            self.stdout.write(f"'{RULES_PAGE_TITLE}' already exists and was left alone.")
            return
        if options["dry_run"]:
            self.stdout.write(f"Would add '{RULES_PAGE_TITLE}'.")
            return
        page = WikiPage.objects.create_page(
            title=RULES_PAGE_TITLE, kind=WikiPage.Kind.REFERENCE, author=None, body=BODY, note=CREATION_NOTE
        )
        self.stdout.write(f"Added '{RULES_PAGE_TITLE}' at /wiki/p/{page.slug}/.")
