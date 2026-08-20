"""Specs for the ``welcome_new_members`` management command.

The command drives the ``welcome_new_members`` automation: it emails each candidate
(``Member.objects.awaiting_welcome_email``) their first sign-in link via
``Member.send_welcome_email``, is idempotent across runs (the send-once stamp), and survives one
member's failure without aborting the rest.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from membership.services.provisioning import provision_user_for_member
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def describe_welcome_new_members():
    def it_welcomes_new_paying_active_airtable_members_only(mailoutbox):
        candidate = MemberFactory(airtable_record_id="recNEW", _pre_signup_email="new@example.com")
        # Not from Airtable -> not a candidate.
        bystander = MemberFactory(airtable_record_id=None, _pre_signup_email="old@example.com")

        call_command("welcome_new_members")

        assert [m.to for m in mailoutbox] == [["new@example.com"]]
        candidate.refresh_from_db()
        bystander.refresh_from_db()
        assert candidate.welcome_email_sent_at is not None
        assert bystander.welcome_email_sent_at is None

    def it_does_not_email_the_same_member_twice(mailoutbox):
        MemberFactory(airtable_record_id="recNEW", _pre_signup_email="new@example.com")

        call_command("welcome_new_members")
        call_command("welcome_new_members")

        assert len(mailoutbox) == 1  # second run finds no candidates

    def it_skips_a_member_whose_send_fails_without_aborting_the_batch(mailoutbox):
        # Give a candidate an email that already belongs to a different account: provisioning
        # refuses (the OneToOne would break), so send_login_invite raises. The command must skip
        # it, keep going, and still welcome the healthy candidate.
        owner = MemberFactory(airtable_record_id=None, _pre_signup_email="taken@example.com")
        provision_user_for_member(owner)  # silent; now a User owns taken@example.com

        broken = MemberFactory(airtable_record_id="recBROKEN", _pre_signup_email="taken@example.com")
        healthy = MemberFactory(airtable_record_id="recOK", _pre_signup_email="ok@example.com")

        call_command("welcome_new_members")

        assert [m.to for m in mailoutbox] == [["ok@example.com"]]
        broken.refresh_from_db()
        healthy.refresh_from_db()
        assert broken.welcome_email_sent_at is None  # retried next run, not marked done
        assert healthy.welcome_email_sent_at is not None
