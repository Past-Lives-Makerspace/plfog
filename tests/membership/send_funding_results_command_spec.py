"""BDD specs for the ``send_funding_results`` management command.

Headless send path for a funding snapshot's member results email, with an optional
base64-encoded intro note (so a whitespace-splitting job runner can't mangle it).
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
from factory.django import mute_signals

from membership.models import FundingSnapshot, Member
from tests.membership.factories import GuildFactory, MemberFactory, VotePreferenceFactory

pytestmark = pytest.mark.django_db


def _voter(email):
    """An active paying member with a linked, email-bearing user and a vote."""
    member = MemberFactory(member_type=Member.MemberType.STANDARD, status=Member.Status.ACTIVE)
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"u{member.pk}", email=email)
    member.user = user
    member.save(update_fields=["user"])
    VotePreferenceFactory(
        member=member, guild_1st=GuildFactory(), guild_2nd=GuildFactory(), guild_3rd=GuildFactory(), signed_up=False
    )
    return member


def describe_send_funding_results_command():
    def it_sends_the_snapshot_results_and_stamps_it():
        _voter("a@x.com")
        snap = FundingSnapshot.take()
        assert snap is not None
        mail.outbox.clear()

        call_command("send_funding_results", snapshot_id=snap.pk)

        snap.refresh_from_db()
        assert snap.results_sent_at is not None
        assert len(mail.outbox) == 1

    def it_decodes_a_base64_note_into_the_email():
        _voter("a@x.com")
        snap = FundingSnapshot.take()
        assert snap is not None
        note = "Heads-up: this one is late — future results are automated."
        encoded = base64.b64encode(note.encode("utf-8")).decode("ascii")
        mail.outbox.clear()

        call_command("send_funding_results", snapshot_id=snap.pk, note_b64=encoded)

        assert note in mail.outbox[0].body

    def it_errors_on_an_unknown_snapshot():
        with pytest.raises(CommandError):
            call_command("send_funding_results", snapshot_id=999999)

    def it_errors_on_invalid_base64():
        _voter("a@x.com")
        snap = FundingSnapshot.take()
        assert snap is not None
        with pytest.raises(CommandError):
            call_command("send_funding_results", snapshot_id=snap.pk, note_b64="@@not base64@@")

    def it_refuses_a_second_send_without_resend_then_allows_it_with_the_flag():
        _voter("a@x.com")
        snap = FundingSnapshot.take()
        assert snap is not None
        call_command("send_funding_results", snapshot_id=snap.pk)

        with pytest.raises(CommandError):
            call_command("send_funding_results", snapshot_id=snap.pk)

        call_command("send_funding_results", snapshot_id=snap.pk, resend=True)
        snap.refresh_from_db()
        assert snap.results_send_count == 2


def describe_re_running_after_a_run_was_killed():
    """A killed run must be resumable without re-emailing the people it already reached.

    The delivery period embeds a generation number. The command bumps that number for a
    fresh send but must NOT bump it for a re-run of a send that never finished, or the
    re-run gets new periods and emails everyone a second time. This is what actually
    saved the September 2026 results: the worker died before the counter was persisted,
    so the manual re-run landed on the same generation and reached only the members the
    dead run had missed.
    """

    def it_keeps_the_generation_and_emails_only_the_members_the_dead_run_missed():
        _voter("reached@x.com")
        _voter("missed@x.com")
        snap = FundingSnapshot.take()
        assert snap is not None
        mail.outbox.clear()

        from core.email import _deliver as real_send

        def die_on_the_second_member(*args, **kwargs):
            if "missed@x.com" in kwargs.get("recipients", []):
                raise KeyboardInterrupt("worker killed")
            return real_send(*args, **kwargs)

        with patch("core.email._deliver", side_effect=die_on_the_second_member):
            with pytest.raises(KeyboardInterrupt):
                call_command("send_funding_results", snapshot_id=snap.pk)

        snap.refresh_from_db()
        assert snap.results_sent_at is None
        assert snap.results_send_count == 1

        call_command("send_funding_results", snapshot_id=snap.pk)

        snap.refresh_from_db()
        assert snap.results_send_count == 1, "a resumed run must not open a new generation"
        recipients = sorted(m.to[0] for m in mail.outbox)
        assert recipients == ["missed@x.com", "reached@x.com"], "nobody emailed twice"
