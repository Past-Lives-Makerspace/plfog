"""Specs for the Member email-integrity report.

Covers ``MemberQuerySet.with_email_status`` / ``.missing_email``,
``Member.email_gap_label``, and the loud blank-email warning in the
``ensure_user_has_member`` signal.

See docs/superpowers/plans/2026-06-25-member-email-integrity.md.
The annotation ``has_email`` must mirror :attr:`Member.primary_email` exactly —
that 1:1 correspondence is the load-bearing correctness property here.
"""

from __future__ import annotations

import logging

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.db.models import Count

from classes.factories import ClassOfferingFactory
from membership.models import Member
from tests.membership.factories import MemberFactory, MembershipPlanFactory

User = get_user_model()


def _linked_with_primary(username: str = "linked", *, verified: bool = True) -> Member:
    """A linked member whose signal-created primary EmailAddress exists."""
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com")
    if not verified:
        EmailAddress.objects.filter(user=user).update(verified=False)
    return user.member


def _linked_no_emailaddress(username: str = "blankacct") -> Member:
    """A linked member with no EmailAddress and a blank mirror (the signal-warning case)."""
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email="")
    return user.member


def _linked_stale_mirror(username: str = "stale") -> Member:
    """A linked member with no EmailAddress but a non-blank ``user.email`` mirror."""
    member = _linked_no_emailaddress(username)
    user = member.user
    user.email = "stale@example.com"
    user.save(update_fields=["email"])
    return member


def _annotated(member: Member) -> Member:
    return Member.objects.with_email_status().get(pk=member.pk)


def describe_MemberQuerySet():
    def describe_with_email_status():
        def it_marks_linked_member_with_primary_emailaddress_as_having_email(db):
            member = _annotated(_linked_with_primary())
            assert member.has_email is True
            assert member.email_gap == ""
            assert not Member.objects.missing_email().filter(pk=member.pk).exists()

        def it_marks_linked_member_with_unverified_primary_as_having_email(db):
            # primary_email keys on the primary flag, not verified — the annotation must agree.
            member = _annotated(_linked_with_primary("unverified", verified=False))
            assert member.has_email is True
            assert member.email_gap == ""

        def it_flags_linked_member_with_no_emailaddress_and_blank_mirror(db):
            member = _annotated(_linked_no_emailaddress())
            assert member.has_email is False
            assert member.email_gap == Member.EmailGap.NO_ACCOUNT_EMAIL
            assert Member.objects.missing_email().filter(pk=member.pk).exists()

        def it_treats_a_stale_non_blank_mirror_as_having_email(db):
            member = _linked_stale_mirror()
            assert not EmailAddress.objects.filter(user=member.user).exists()
            annotated = _annotated(member)
            assert annotated.has_email is True
            assert annotated.email_gap == ""
            assert not Member.objects.missing_email().filter(pk=member.pk).exists()

        def it_marks_unlinked_member_with_pre_signup_email_as_having_email(db):
            member = _annotated(MemberFactory(user=None, _pre_signup_email="airtable@example.com"))
            assert member.has_email is True
            assert member.email_gap == ""
            assert not Member.objects.missing_email().filter(pk=member.pk).exists()

        def it_flags_unlinked_member_with_blank_pre_signup_email(db):
            member = _annotated(MemberFactory(user=None, _pre_signup_email=""))
            assert member.has_email is False
            assert member.email_gap == Member.EmailGap.NO_AIRTABLE_EMAIL
            assert Member.objects.missing_email().filter(pk=member.pk).exists()

        def it_matches_the_primary_email_property_in_every_case(db):
            builders = [
                lambda: _linked_with_primary("eq_primary"),
                lambda: _linked_with_primary("eq_unverified", verified=False),
                lambda: _linked_no_emailaddress("eq_blankacct"),
                lambda: _linked_stale_mirror("eq_stale"),
                lambda: MemberFactory(user=None, _pre_signup_email="eq_airtable@example.com"),
                lambda: MemberFactory(user=None, _pre_signup_email=""),
            ]
            for build in builders:
                member = _annotated(build())
                assert member.has_email == bool(member.primary_email)

    def describe_missing_email():
        def it_counts_only_emailless_members(db):
            MemberFactory(user=None, _pre_signup_email="")  # emailless
            MemberFactory(user=None, _pre_signup_email="")  # emailless
            MemberFactory(user=None, _pre_signup_email="has@example.com")  # not emailless
            assert Member.objects.missing_email().count() == 2

        def it_does_not_inflate_count_for_members_with_multiple_classes(db):
            emailless = MemberFactory(user=None, _pre_signup_email="")
            ClassOfferingFactory(instructor=emailless)
            ClassOfferingFactory(instructor=emailless)
            qs = Member.objects.annotate(class_count=Count("classes", distinct=True)).missing_email()
            assert qs.count() == 1
            assert qs.get(pk=emailless.pk).class_count == 2


def describe_email_gap_label():
    def it_returns_the_airtable_label_for_unlinked_members(db):
        member = _annotated(MemberFactory(user=None, _pre_signup_email=""))
        assert member.email_gap_label == "Never signed up — no email on file from Airtable"

    def it_returns_the_account_label_for_linked_members(db):
        member = _annotated(_linked_no_emailaddress())
        assert member.email_gap_label == "Signed up, but has no email on their account"

    def it_returns_blank_when_the_member_has_an_email(db):
        member = _annotated(_linked_with_primary())
        assert member.email_gap_label == ""


def describe_ensure_user_has_member_blank_email():
    def it_creates_an_active_member_and_warns_for_a_blank_email(db, caplog):
        MembershipPlanFactory()
        with caplog.at_level(logging.WARNING, logger="membership.signals"):
            user = User.objects.create_user(username="noemail", email="")
        member = user.member
        assert member.status == Member.Status.ACTIVE
        assert "with NO email" in caplog.text
        assert Member.objects.missing_email().filter(pk=member.pk).exists()

    def it_does_not_warn_when_the_user_has_an_email(db, caplog):
        MembershipPlanFactory()
        with caplog.at_level(logging.WARNING, logger="membership.signals"):
            user = User.objects.create_user(username="hasemail", email="hasemail@example.com")
        assert "with NO email" not in caplog.text
        assert not Member.objects.missing_email().filter(pk=user.member.pk).exists()
