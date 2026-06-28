"""BDD specs for the ``send_site_announcement`` management command.

Headless twin of the Site Settings → Announcements composer: sends the rich-HTML
``site_announcement`` email to activated members, subject/body base64-encoded.
"""

from __future__ import annotations

import base64
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from membership.models import Member
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def _activated(email, n):
    """An active member linked to a User who has logged in — the ALL_ACTIVE_MEMBERS audience."""
    member = MemberFactory(status=Member.Status.ACTIVE)
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"u{member.pk}", email=email, last_login=timezone.now())
    member.user = user
    member.save(update_fields=["user"])
    return member


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _html_part(message) -> str:
    return next((content for content, mime in message.alternatives if mime == "text/html"), "")


def describe_send_site_announcement_command():
    def it_emails_activated_members_with_rich_html_not_escaped():
        _activated("a@x.com", 1)
        _activated("b@x.com", 2)
        mail.outbox.clear()

        call_command(
            "send_site_announcement",
            subject_b64=_b64("Big news"),
            body_b64=_b64("<h3>Hello</h3><ul><li>one</li></ul>"),
        )

        assert len(mail.outbox) == 2
        html = _html_part(mail.outbox[0])
        assert "one" in html
        assert "&lt;li&gt;" not in html  # the list tag is real markup, not escaped text

    def it_suppresses_discord_by_default():
        _activated("a@x.com", 1)
        with patch("core.events.emit.emit") as mock_emit:
            call_command("send_site_announcement", subject_b64=_b64("T"), body_b64=_b64("<p>hi</p>"))
        assert mock_emit.call_args.kwargs["suppress_broadcast"] is True

    def it_posts_to_discord_when_flagged():
        _activated("a@x.com", 1)
        with patch("core.events.emit.emit") as mock_emit:
            call_command("send_site_announcement", subject_b64=_b64("T"), body_b64=_b64("<p>hi</p>"), discord=True)
        assert mock_emit.call_args.kwargs["suppress_broadcast"] is False

    def it_errors_on_invalid_base64():
        with pytest.raises(CommandError):
            call_command("send_site_announcement", subject_b64="@@bad@@", body_b64=_b64("<p>hi</p>"))

    def it_errors_when_the_body_sanitizes_to_empty():
        with pytest.raises(CommandError):
            call_command("send_site_announcement", subject_b64=_b64("Title"), body_b64=_b64("   "))
