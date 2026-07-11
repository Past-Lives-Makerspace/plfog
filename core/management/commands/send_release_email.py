"""Send the release "what's new" email to every activated member, headlessly.

The all-members twin of ``send_release_email_test`` (which sends only to one inbox).
It mirrors ``send_site_announcement``'s emit-to-all fan-out but carries the RELEASE-email
content: a multi-line span of the CHANGELOG (``--lines 0.20,0.21`` covers both feature
batches in one email), each feature's screenshot from R2, and the branded hybrid layout.
The pre-rendered HTML rides as a per-channel EMAIL override so the spine does not
autoescape it (same trust boundary as ``send_site_announcement``).

SAFETY GUARD — this is a send-to-EVERY-activated-member capability, so it never fires by
accident. Without ``--confirm`` it sends NOTHING: it renders the email, resolves the
audience READ-ONLY (the same resolver the real send uses, so the count matches), and
prints what it WOULD do. Only ``--confirm`` actually emits. The ``period`` is derived
from the VERSION + selected lines (not a timestamp), so a confirmed retry with the same
scope is idempotent — the delivery ledger dedupes it and no member is emailed twice.

Usage (Render one-off job):
    # Dry run — prints the subject, card count, and recipient count; sends nothing:
    python manage.py send_release_email --lines 0.20,0.21
    # Real send (spans the 0.20 + 0.21 feature batches in one email):
    python manage.py send_release_email --lines 0.20,0.21 --confirm
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

# Shown on the light body above the feature cards when ``--intro`` is not supplied.
DEFAULT_INTRO_HTML = "<p>We've shipped a big batch of updates — here's what's new at Past Lives.</p>"


class Command(BaseCommand):
    help = "Send the release 'what's new' email to activated members. Requires --confirm to actually send."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--lines",
            type=str,
            default="",
            help="Comma-separated MAJOR.MINOR lines to span, e.g. 0.20,0.21. Default: the current line of VERSION.",
        )
        parser.add_argument(
            "--subject",
            type=str,
            default="",
            help='Email subject. Default: "What\'s new at Past Lives: <first feature>".',
        )
        parser.add_argument(
            "--intro",
            type=str,
            default="",
            help="Intro line above the cards — base64-encoded UTF-8 or plain text. Default: a short update line.",
        )
        parser.add_argument(
            "--discord",
            action="store_true",
            help="Also post the announcement to Discord (off by default).",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="REQUIRED to actually send. Without it, prints the would-send count and sends nothing.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from core.events import resolvers
        from core.events.channels import Channel, Message
        from core.events.emit import emit
        from core.events.registry import get_event
        from core.release_email import build_release_cards, parse_lines, render_release_email
        from plfog.version import VERSION

        lines = None
        if options["lines"]:
            try:
                lines = parse_lines(options["lines"])
            except ValueError as exc:
                raise CommandError(str(exc))

        cards = build_release_cards(VERSION, lines=lines)
        if not cards:
            scope = ", ".join(lines) if lines else _current_minor(VERSION)
            raise CommandError(f"No changelog entries for line(s) {scope} — nothing to send.")

        subject = (options["subject"] or "").strip() or f"What's new at Past Lives: {cards[0].title}"
        intro = self._resolve_intro(options["intro"])
        html, text = render_release_email(
            VERSION,
            subject=subject,
            preheader=cards[0].title,
            intro=intro,
            cards=cards,
            lines=lines,
        )

        # Read-only audience resolution — the SAME resolver the real emit uses, so the
        # dry-run count equals the send count. Resolving recipients does not send anything.
        event = get_event("site_announcement")
        recipient_count = len(resolvers.resolve(event.recipient, {}))
        scope_label = ", ".join(lines) if lines else f"current ({_current_minor(VERSION)})"

        if not options["confirm"]:
            self.stdout.write("DRY RUN — no email sent. Re-run with --confirm to send.")
            self.stdout.write(f"  Subject:    {subject}")
            self.stdout.write(f"  Lines:      {scope_label}")
            self.stdout.write(f"  Cards:      {len(cards)}")
            self.stdout.write(f"  Would send: {recipient_count} activated member(s)")
            return

        site_url = settings.MEMBER_BASE_URL
        email = Message(
            title=subject,
            body=text,
            url=site_url,
            html_body=html,
            trigger_kind="site_announcement",
        )
        # Deterministic period keyed to the release + scope: a retry with the same args
        # reuses these delivery slots, so the ledger dedupes it — no member is double-sent.
        period = f"release:{VERSION}:{'+'.join(lines) if lines else _current_minor(VERSION)}"
        result = emit(
            "site_announcement",
            context={
                "member_name": "there",
                "announcement_title": subject,
                "announcement_body": self._summary(cards),
                "site_url": site_url,
            },
            url=site_url,
            period=period,
            messages={Channel.EMAIL: email},
            suppress_broadcast=not options["discord"],
        )
        self.stdout.write(self.style.SUCCESS(f"Release email sent to {result.recipient_count} member(s)."))

    def _resolve_intro(self, raw: str) -> str:
        """The intro HTML: decode ``--intro`` (base64 or plain), else the default line."""
        if not raw:
            return DEFAULT_INTRO_HTML
        try:
            return base64.b64decode(raw, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            # Not base64 (plain multi-word text has spaces/punctuation) — take it verbatim.
            return raw

    def _summary(self, cards: list[Any]) -> str:
        """A one-line in-app/Discord body listing the features (the full copy rides the email)."""
        titles = "; ".join(card.title for card in cards[:5])
        return f"Here's what's new: {titles}."


def _current_minor(version: str) -> str:
    """The ``MAJOR.MINOR`` line of a full version string (``"0.21.4"`` → ``"0.21"``)."""
    return ".".join(version.split(".")[:2])
