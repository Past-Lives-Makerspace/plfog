"""BDD specs for the provision_member_users backfill command.

Scope is ACTIVE + userless only (Review fix #1). Duplicate ``_pre_signup_email`` rows
are pre-flighted and skipped, never crashing the batch (Review fix #2). ``--dry-run``
writes nothing, the run is silent, and re-running is a no-op.
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import IntegrityError

from membership.models import Member
from tests.membership.factories import MemberFactory

_PROVISION = "membership.management.commands.provision_member_users.provision_user_for_member"

pytestmark = pytest.mark.django_db


def _run(*args: str) -> str:
    out = StringIO()
    call_command("provision_member_users", *args, stdout=out)
    return out.getvalue()


def describe_provision_member_users():
    def it_provisions_every_userless_active_member(mailoutbox):
        a = MemberFactory(_pre_signup_email="a@example.com")
        b = MemberFactory(_pre_signup_email="b@example.com")

        output = _run()

        a.refresh_from_db()
        b.refresh_from_db()
        assert a.user_id is not None
        assert b.user_id is not None
        assert "Provisioned 2 users" in output
        assert mailoutbox == []

    def it_skips_members_with_a_blank_email():
        MemberFactory(_pre_signup_email="has@example.com")
        blank = MemberFactory(_pre_signup_email="")

        output = _run()

        blank.refresh_from_db()
        assert blank.user_id is None
        assert "Provisioned 1 users, skipped 1 (no email)" in output

    def describe_scope():
        @pytest.mark.parametrize(
            "status",
            [Member.Status.INVITED, Member.Status.FORMER, Member.Status.SUSPENDED],
        )
        def it_skips_non_active_members(status):
            member = MemberFactory(_pre_signup_email="off@example.com", status=status)

            _run()

            member.refresh_from_db()
            assert member.user_id is None
            assert User.objects.count() == 0

    def describe_duplicate_emails():
        def it_reports_duplicates_as_skips_without_crashing():
            first = MemberFactory(_pre_signup_email="dup@example.com")
            second = MemberFactory(_pre_signup_email="DUP@example.com")  # same email, different case

            output = _run()

            first.refresh_from_db()
            second.refresh_from_db()
            assert first.user_id is None
            assert second.user_id is None
            assert "2 (duplicate email)" in output
            assert User.objects.count() == 0

        def it_lists_duplicates_in_dry_run():
            MemberFactory(_pre_signup_email="dup@example.com")
            MemberFactory(_pre_signup_email="dup@example.com")

            output = _run("--dry-run")

            assert "SKIP (duplicate email dup@example.com)" in output

    def describe_dry_run():
        def it_writes_nothing(mailoutbox):
            member = MemberFactory(_pre_signup_email="dry@example.com")

            output = _run("--dry-run")

            member.refresh_from_db()
            assert member.user_id is None
            assert User.objects.count() == 0
            assert "Would provision 1 users" in output
            assert "PROVISION: " in output
            assert mailoutbox == []

    def it_is_idempotent_on_re_run(mailoutbox):
        MemberFactory(_pre_signup_email="once@example.com")

        first = _run()
        second = _run()

        assert "Provisioned 1 users" in first
        assert "Provisioned 0 users" in second
        assert User.objects.count() == 1
        assert mailoutbox == []

    def describe_error_resilience():
        def it_skips_and_counts_a_member_whose_provision_errors():
            MemberFactory(_pre_signup_email="err@example.com")

            with patch(_PROVISION, side_effect=IntegrityError("boom")):
                output = _run()

            assert "SKIP (error)" in output
            assert "1 (errors)" in output

        def it_skips_and_counts_a_member_provisioning_declines():
            MemberFactory(_pre_signup_email="declined@example.com")

            with patch(_PROVISION, return_value=None):
                output = _run()

            assert "SKIP (could not provision)" in output
            assert "Provisioned 0 users" in output
            assert "1 (errors)" in output
