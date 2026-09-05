"""BDD specs for ClassApproval + the dual approval workflow on ClassOffering."""

from __future__ import annotations

import pytest

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
)
from classes.models import ClassApproval, ClassOffering
from tests.membership.factories import GuildFactory, MemberFactory


def _lead_user_and_member(db):
    """Create a User and return (user, its auto-created Member) for guild-lead tests."""
    from django.contrib.auth import get_user_model

    from membership.models import Member, MembershipPlan

    MembershipPlan.objects.get_or_create(name="Standard", defaults={"monthly_price": "50.00"})
    User = get_user_model()
    user = User.objects.create_user(username="ca-lead@example.com", email="ca-lead@example.com")
    return user, Member.objects.get(user=user)


def describe_ClassApproval():
    def describe_required_review_roles():
        def it_requires_only_admin_when_category_has_no_guild(db):
            offering = ClassOfferingFactory(ready=True, category=CategoryFactory(guild=None))
            assert offering.required_review_roles == [ClassApproval.Role.ADMIN]

        def it_requires_only_admin_when_guild_has_no_lead(db):
            guild = GuildFactory(guild_lead=None)
            cat = CategoryFactory(guild=guild)
            offering = ClassOfferingFactory(ready=True, category=cat)
            assert offering.required_review_roles == [ClassApproval.Role.ADMIN]

        def it_requires_admin_plus_guild_lead_when_both_are_set(db):
            lead_member = MemberFactory()
            guild = GuildFactory(guild_lead=lead_member)
            offering = ClassOfferingFactory(ready=True, category=CategoryFactory(guild=guild))
            assert offering.required_review_roles == [
                ClassApproval.Role.ADMIN,
                ClassApproval.Role.GUILD_LEAD,
            ]

    def describe_submit_for_review():
        def it_opens_only_the_guild_lead_gate_when_a_lead_exists(db):
            lead = MemberFactory()
            guild = GuildFactory(guild_lead=lead)
            offering = ClassOfferingFactory(
                ready=True,
                category=CategoryFactory(guild=guild),
                status=ClassOffering.Status.DRAFT,
            )
            rows = offering.submit_for_review()
            assert len(rows) == 1
            assert rows[0].role == ClassApproval.Role.GUILD_LEAD
            assert rows[0].decision == ""
            assert rows[0].token

        def it_clears_stale_rows_on_resubmit(db):
            offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
            first = offering.submit_for_review()
            # Bounce back to draft and resubmit
            offering.status = ClassOffering.Status.DRAFT
            offering.save(update_fields=["status"])
            second = offering.submit_for_review()
            assert offering.approvals.count() == len(second)
            assert {r.pk for r in first}.isdisjoint({r.pk for r in second})

        def it_refuses_to_submit_non_draft_classes(db):
            offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.PUBLISHED)
            with pytest.raises(ValueError):
                offering.submit_for_review()

    def describe_decide():
        def it_publishes_when_every_required_gate_is_approved(db, admin_user):
            offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
            (admin_row,) = offering.submit_for_review()
            admin_row.decide(ClassApproval.Decision.APPROVED, user=admin_user)
            offering.refresh_from_db()
            assert offering.status == ClassOffering.Status.PUBLISHED
            assert offering.approved_by == admin_user
            assert offering.published_at is not None

        def it_holds_off_publishing_until_admin_signs_off_after_lead(db, admin_user):
            lead_user, lead = _lead_user_and_member(db)
            guild = GuildFactory(guild_lead=lead)
            offering = ClassOfferingFactory(
                ready=True,
                category=CategoryFactory(guild=guild),
                status=ClassOffering.Status.DRAFT,
            )
            (gl_row,) = offering.submit_for_review()
            gl_row.decide(ClassApproval.Decision.APPROVED, user=lead_user)
            offering.refresh_from_db()
            # Guild lead approved → admin gate opens but the class is not yet live.
            assert offering.status == ClassOffering.Status.PENDING
            assert offering.approvals.filter(role=ClassApproval.Role.ADMIN, decision="").exists()

        def it_returns_to_draft_on_changes_requested(db, admin_user):
            offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
            (row,) = offering.submit_for_review()
            row.decide(
                ClassApproval.Decision.CHANGES_REQUESTED,
                user=admin_user,
                notes="Please add prerequisites.",
            )
            offering.refresh_from_db()
            assert offering.status == ClassOffering.Status.DRAFT

        def it_returns_to_draft_on_decline(db, admin_user):
            offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
            (row,) = offering.submit_for_review()
            row.decide(ClassApproval.Decision.DENIED, user=admin_user, notes="No.")
            offering.refresh_from_db()
            assert offering.status == ClassOffering.Status.DRAFT
