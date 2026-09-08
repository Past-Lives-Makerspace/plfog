"""Specs for the new-member welcome path.

``Member.objects.awaiting_welcome_email`` is the candidate set for the ``welcome_new_members``
automation, and ``Member.send_welcome_email`` is the idempotent, send-once wrapper over
``send_login_invite`` that the automation calls. Together they email a new paying, active member
their first sign-in link exactly once, and never touch anyone else.
"""

from __future__ import annotations

import pytest
from datetime import timedelta

from django.utils import timezone

from membership.models import Member
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def _candidate(**overrides):
    """A member who qualifies for the welcome email: paying (Standard, the factory default),
    active, imported from Airtable, with an email and no prior welcome."""
    defaults = {"airtable_record_id": "recNEW1", "_pre_signup_email": "newbie@example.com"}
    defaults.update(overrides)
    return MemberFactory(**defaults)


def describe_awaiting_welcome_email():
    def _qs_has(member: Member) -> bool:
        return Member.objects.awaiting_welcome_email().filter(pk=member.pk).exists()

    def it_includes_a_new_paying_active_airtable_member():
        assert _qs_has(_candidate()) is True

    def it_excludes_a_member_not_imported_from_airtable():
        assert _qs_has(MemberFactory(airtable_record_id=None)) is False

    def it_excludes_a_non_paying_member():
        assert _qs_has(_candidate(member_type=Member.MemberType.WORK_TRADE)) is False

    def it_excludes_a_member_who_is_not_active():
        assert _qs_has(_candidate(status=Member.Status.FORMER)) is False

    def it_excludes_a_member_already_welcomed():
        assert _qs_has(_candidate(welcome_email_sent_at=timezone.now())) is False

    def it_excludes_a_member_with_no_email():
        assert _qs_has(_candidate(_pre_signup_email="")) is False


def describe_send_welcome_email():
    def it_sends_the_login_invite_provisions_and_stamps(mailoutbox):
        member = _candidate(_pre_signup_email="fresh@example.com")
        assert member.user_id is None

        result = member.send_welcome_email()

        assert result is True
        assert len(mailoutbox) == 1
        assert mailoutbox[0].to == ["fresh@example.com"]
        member.refresh_from_db()
        assert member.welcome_email_sent_at is not None
        assert member.user_id is not None  # provisioned by send_login_invite

    def it_is_a_noop_when_already_welcomed(mailoutbox):
        stamped_at = timezone.now()
        member = _candidate(welcome_email_sent_at=stamped_at)

        result = member.send_welcome_email()

        assert result is False
        assert mailoutbox == []
        member.refresh_from_db()
        assert member.welcome_email_sent_at == stamped_at

    def it_propagates_the_error_and_does_not_stamp_when_the_send_fails(mailoutbox):
        # A blank-email member can't be provisioned, so send_login_invite raises. The wrapper must
        # let that surface and leave welcome_email_sent_at blank so the member is retried, never
        # recorded as a phantom send.
        member = MemberFactory(_pre_signup_email="", airtable_record_id="recNOEMAIL")

        with pytest.raises(ValueError, match="no email on file"):
            member.send_welcome_email()

        assert mailoutbox == []
        member.refresh_from_db()
        assert member.welcome_email_sent_at is None


def describe_the_candidate_email_guard():
    def it_includes_a_linked_member_whose_email_lives_only_in_allauth():
        # The old filter read _pre_signup_email directly, which is only the source of
        # truth for UNLINKED members. A linked member with a verified allauth address and
        # a blank legacy column was silently never welcomed and never reported.
        from allauth.account.models import EmailAddress
        from django.contrib.auth.models import User

        user = User.objects.create_user(username="linked@example.com", email="linked@example.com")
        # The auto-provision signal may already have made the row.
        EmailAddress.objects.get_or_create(
            user=user, email="linked@example.com", defaults={"verified": True, "primary": True}
        )
        member = Member.objects.filter(user=user).first() or MemberFactory(user=user)
        Member.objects.filter(pk=member.pk).update(
            _pre_signup_email="",
            airtable_record_id="recLINKED",
            status=Member.Status.ACTIVE,
            member_type=Member.MemberType.STANDARD,
            welcome_email_sent_at=None,
        )

        assert Member.objects.awaiting_welcome_email().filter(pk=member.pk).exists()

    def it_still_excludes_a_member_with_no_address_anywhere():
        member = MemberFactory(_pre_signup_email="", airtable_record_id="recNONE")
        assert Member.objects.awaiting_welcome_email().filter(pk=member.pk).exists() is False


def describe_a_send_that_does_not_land():
    """The blocker this feature shipped with: ``core.email.send`` runs ``best_effort=True``,
    so a provider rejection is logged FAILED and swallowed rather than raised. Stamping on
    that path marks the member welcomed forever, because ``awaiting_welcome_email`` filters
    on the stamp. ``emit`` releases its ledger slot precisely so a retry can happen."""

    def it_does_not_stamp_when_the_provider_rejects_the_email(monkeypatch, mailoutbox):
        from core import email as core_email

        member = _candidate(_pre_signup_email="bounces@example.com")
        monkeypatch.setattr(core_email, "_deliver", _raise_provider_error)

        sent = member.send_welcome_email()

        assert sent is False
        member.refresh_from_db()
        assert member.welcome_email_sent_at is None, "a swallowed failure must leave the member retryable"
        assert Member.objects.awaiting_welcome_email().filter(pk=member.pk).exists()

    def it_stamps_and_reports_true_when_the_email_actually_goes_out(mailoutbox):
        member = _candidate(_pre_signup_email="lands@example.com")

        assert member.send_welcome_email() is True
        member.refresh_from_db()
        assert member.welcome_email_sent_at is not None
        assert Member.objects.awaiting_welcome_email().filter(pk=member.pk).exists() is False

    def it_claims_the_send_before_attempting_it(mailoutbox):
        # Two callers racing (the 13:xx cron tick and an admin's Run now) must produce one
        # email, not two. The claim is an UPDATE, so the loser sees zero rows changed.
        member = _candidate(_pre_signup_email="raced@example.com")
        racer = Member.objects.get(pk=member.pk)

        first = member.send_welcome_email()
        second = racer.send_welcome_email()

        assert (first, second) == (True, False)
        assert len(mailoutbox) == 1


def _raise_provider_error(*args, **kwargs):
    raise RuntimeError("provider said no")


def describe_record_welcome_sent():
    def it_fills_a_blank_stamp(mailoutbox):
        member = _candidate()
        member.record_welcome_sent()
        member.refresh_from_db()
        assert member.welcome_email_sent_at is not None

    def it_never_rewrites_an_existing_stamp():
        original = timezone.now() - timedelta(days=5)
        member = _candidate(welcome_email_sent_at=original)
        member.record_welcome_sent()
        member.refresh_from_db()
        assert member.welcome_email_sent_at == original

    def it_takes_the_member_out_of_the_candidate_set():
        member = _candidate()
        member.record_welcome_sent()
        assert Member.objects.awaiting_welcome_email().filter(pk=member.pk).exists() is False
