"""BDD specs for the ``send_funding_results`` management command.

Headless send path for a funding snapshot's member results email, with an optional
base64-encoded intro note (so a whitespace-splitting job runner can't mangle it).
"""

from __future__ import annotations

import base64

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
