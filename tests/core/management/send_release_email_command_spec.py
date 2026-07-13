"""BDD specs for the ``send_release_email`` (+ ``send_release_email_test``) commands.

``send_release_email`` is the all-members release blast — guarded behind ``--confirm``
so it can never fire by accident. ``send_release_email_test`` is its one-inbox draft.
Both render the same multi-line release email, so a ``--lines`` draft previews exactly
what the real send will deliver. Tests use the locmem outbox and factory members — never
a real send.
"""

from __future__ import annotations

import io

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

# Stable feature titles from the real CHANGELOG, used to prove which lines an email spans.
V20_TITLE = "A home base when you sign in"  # a 0.20 feature
V21_TITLE = "Your notifications, cleaned up"  # a 0.21 feature


def _activated(email: str) -> Member:
    """An active member linked to a User who has logged in — the ALL_ACTIVE_MEMBERS audience."""
    member = MemberFactory(status=Member.Status.ACTIVE)
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"u{member.pk}", email=email, last_login=timezone.now())
    member.user = user
    member.save(update_fields=["user"])
    return member


def _never_logged_in(email: str) -> Member:
    """An active member whose account has never signed in — excluded from the broadcast audience."""
    member = MemberFactory(status=Member.Status.ACTIVE)
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"u{member.pk}", email=email)  # last_login stays None
    member.user = user
    member.save(update_fields=["user"])
    return member


def _html_part(message) -> str:
    return next((content for content, mime in message.alternatives if mime == "text/html"), "")


def describe_send_release_email_command():
    def describe_without_confirm():
        def it_sends_nothing_and_prints_the_would_send_count():
            _activated("a@x.com")
            _activated("b@x.com")
            _never_logged_in("c@x.com")
            mail.outbox.clear()

            out = io.StringIO()
            call_command("send_release_email", lines="0.20,0.21", stdout=out)

            # The safety guard: no --confirm → not one email leaves, even with members present.
            assert len(mail.outbox) == 0
            output = out.getvalue()
            assert "DRY RUN" in output
            assert "Would send: 2" in output  # the two activated members, not the never-logged-in one

    def describe_with_confirm():
        def it_emails_only_activated_members_with_both_release_lines():
            _activated("a@x.com")
            _activated("b@x.com")
            _never_logged_in("c@x.com")
            mail.outbox.clear()

            call_command("send_release_email", lines="0.20,0.21", confirm=True)

            assert len(mail.outbox) == 2  # activated only; the never-logged-in account is excluded
            html = _html_part(mail.outbox[0])
            assert V20_TITLE in html  # spans the 0.20 batch
            assert V21_TITLE in html  # and the 0.21 batch, in one email

        def it_scopes_the_email_to_the_named_lines():
            _activated("a@x.com")
            mail.outbox.clear()

            call_command("send_release_email", lines="0.21", confirm=True)

            html = _html_part(mail.outbox[0])
            assert V21_TITLE in html  # a 0.21 feature is present
            assert V20_TITLE not in html  # a 0.20 feature is out of scope

        def it_does_not_double_send_on_a_retry_with_the_same_scope():
            _activated("a@x.com")
            _activated("b@x.com")
            mail.outbox.clear()

            call_command("send_release_email", lines="0.20,0.21", confirm=True)
            first = len(mail.outbox)
            call_command("send_release_email", lines="0.20,0.21", confirm=True)

            # The deterministic per-release period makes the retry idempotent — the
            # delivery ledger dedupes it and no member is emailed a second time.
            assert first == 2
            assert len(mail.outbox) == 2

    def it_errors_on_a_malformed_lines_value():
        with pytest.raises(CommandError):
            call_command("send_release_email", lines="banana", confirm=True)


def describe_send_release_email_test_command():
    def it_renders_the_spanned_email_to_one_address():
        mail.outbox.clear()

        call_command("send_release_email_test", to="me@x.com", lines="0.20,0.21")

        assert len(mail.outbox) == 1  # single-recipient draft, spine bypassed
        assert mail.outbox[0].to == ["me@x.com"]
        html = _html_part(mail.outbox[0])
        assert V20_TITLE in html  # same 0.20 + 0.21 span the real --confirm send delivers
        assert V21_TITLE in html

    def it_errors_on_a_malformed_lines_value():
        with pytest.raises(CommandError):
            call_command("send_release_email_test", to="me@x.com", lines="0.20.5")
