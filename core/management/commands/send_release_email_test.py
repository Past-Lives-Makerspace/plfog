"""Send the current release email as a one-off test to a single address.

Renders the release email exactly as the admin composer would (using the same
``build_release_cards`` / ``render_release_email`` pipeline, with screenshots
from R2), then delivers it directly via ``core.email.send`` — NOT through the
notification spine — so the real send's dedup period is never consumed.

Usage (Render one-off job):
    python manage.py send_release_email_test --to plazajosue2@gmail.com
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Send the current release email as a test to one address (skips the notification spine)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--to", type=str, required=True, help="Recipient email address.")

    def handle(self, *args: Any, **options: Any) -> None:
        from core.email import send
        from core.release_email import build_release_cards, render_release_email
        from plfog.version import VERSION

        to_addr: str = options["to"].strip()
        if not to_addr or "@" not in to_addr:
            raise CommandError(f"--to must be a valid email address, got: {to_addr!r}")

        cards = build_release_cards(VERSION)
        subject = f"What's new at Past Lives: {cards[0].title}" if cards else "What's new at Past Lives"
        html, text = render_release_email(
            VERSION,
            subject=subject,
            preheader=cards[0].title if cards else "See what's new.",
            intro="<p>We've just shipped a big update — here's what's new.</p>",
            cards=cards,
        )

        send(
            to=to_addr,
            subject=subject,
            trigger_kind="release_email.test",
            text_body=text,
            html_body=html,
            best_effort=True,
        )
        self.stdout.write(self.style.SUCCESS(f"Release test email sent to {to_addr}."))
