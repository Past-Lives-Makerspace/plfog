"""Send a site-wide announcement email headlessly — the CLI twin of the Site Settings
→ Announcements composer (``hub.views._send_site_announcement``).

The web composer needs an admin session; this command sends the same
``site_announcement`` event from a one-off job (e.g. a Render job) with no login, from
the same ``noreply@`` Resend sender. It builds the branded rich-HTML email exactly like
the composer (sanitize → inline-style → branded shell) and hands it to ``emit`` as a
per-channel EMAIL override, so the rich HTML is NOT autoescaped (the spine escapes
copy-merge values, but a pre-rendered ``Message.html_body`` is trusted).

Subject and body are base64-encoded (``--subject-b64`` / ``--body-b64``) so a
whitespace-splitting job runner can't mangle them. The body is simple HTML (the same
tags the editor allows). Discord is OFF unless ``--discord`` is passed. Recipients are
the activated-member audience the ``site_announcement`` event already resolves to.

    python manage.py send_site_announcement --subject-b64 <b64> --body-b64 <b64>
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.html import escape


class Command(BaseCommand):
    help = "Send a site-wide announcement email to activated members (subject/body base64-encoded)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--subject-b64", type=str, required=True, help="Subject, base64-encoded UTF-8.")
        parser.add_argument("--body-b64", type=str, required=True, help="Body (simple HTML), base64-encoded UTF-8.")
        parser.add_argument(
            "--discord",
            action="store_true",
            help="Also post the announcement to Discord (off by default).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from core.events.channels import Channel, Message
        from core.events.emit import emit
        from core.events.templates import wrap_email_html
        from core.html_sanitize import render_rich_email_body, rich_html_to_text, sanitize_rich_html

        title = self._decode(options["subject_b64"], "--subject-b64").strip()
        body_html = sanitize_rich_html(self._decode(options["body_b64"], "--body-b64"))
        if not title or not body_html:
            raise CommandError("Subject and body must both be non-empty (body must survive sanitization).")

        body_text = rich_html_to_text(body_html)
        site_url = settings.MEMBER_BASE_URL
        email = Message(
            title=title,
            body=f"{title}\n\n{body_text}\n\n{site_url}",
            url=site_url,
            html_body=wrap_email_html(f"<h2>{escape(title)}</h2>{render_rich_email_body(body_html)}"),
            trigger_kind="site_announcement",
        )
        result = emit(
            "site_announcement",
            context={
                "member_name": "there",
                "announcement_title": title,
                "announcement_body": body_text,
                "site_url": site_url,
            },
            url=site_url,
            period=f"site:{timezone.now():%Y%m%d%H%M%S%f}",
            messages={Channel.EMAIL: email},
            suppress_broadcast=not options["discord"],
        )
        self.stdout.write(self.style.SUCCESS(f"Announcement sent to {result.recipient_count} member(s)."))

    def _decode(self, raw: str, flag: str) -> str:
        try:
            return base64.b64decode(raw, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise CommandError(f"{flag} is not valid base64-encoded UTF-8: {exc}")
