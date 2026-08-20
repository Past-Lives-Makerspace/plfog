"""Specs for the new-member welcome path.

``Member.objects.awaiting_welcome_email`` is the candidate set for the ``welcome_new_members``
automation, and ``Member.send_welcome_email`` is the idempotent, send-once wrapper over
``send_login_invite`` that the automation calls. Together they email a new paying, active member
their first sign-in link exactly once, and never touch anyone else.
"""

from __future__ import annotations

import pytest
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
