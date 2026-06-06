"""BDD specs for ClassApproval + the dual approval workflow on ClassOffering."""

from __future__ import annotations

import pytest

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
)
from classes.models import ClassApproval, ClassOffering
from tests.membership.factories import GuildFactory, MemberFactory


def describe_ClassApproval():
    def describe_required_review_roles():
        def it_requires_only_admin_when_category_has_no_guild(db):
            offering = ClassOfferingFactory(category=CategoryFactory(guild=None))
            assert offering.required_review_roles == [ClassApproval.Role.ADMIN]

        def it_requires_only_admin_when_guild_has_no_lead(db):
            guild = GuildFactory(guild_lead=None)
            cat = CategoryFactory(guild=guild)
            offering = ClassOfferingFactory(category=cat)
            assert offering.required_review_roles == [ClassApproval.Role.ADMIN]

        def it_requires_admin_plus_guild_lead_when_both_are_set(db):
            lead_member = MemberFactory()
            guild = GuildFactory(guild_lead=lead_member)
            offering = ClassOfferingFactory(category=CategoryFactory(guild=guild))
            assert offering.required_review_roles == [
                ClassApproval.Role.ADMIN,
                ClassApproval.Role.GUILD_LEAD,
            ]

    def describe_submit_for_review():
        def it_creates_one_pending_row_per_required_role(db):
            lead = MemberFactory()
            guild = GuildFactory(guild_lead=lead)
            offering = ClassOfferingFactory(
                category=CategoryFactory(guild=guild),
                status=ClassOffering.Status.DRAFT,
            )
            rows = offering.submit_for_review()
            assert len(rows) == 2
            assert {r.role for r in rows} == {
                ClassApproval.Role.ADMIN,
                ClassApproval.Role.GUILD_LEAD,
            }
            assert all(r.decision == "" for r in rows)
            assert all(r.token for r in rows)

        def it_clears_stale_rows_on_resubmit(db):
            offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
            first = offering.submit_for_review()
            # Bounce back to draft and resubmit
            offering.status = ClassOffering.Status.DRAFT
            offering.save(update_fields=["status"])
            second = offering.submit_for_review()
            assert offering.approvals.count() == len(second)
            assert {r.pk for r in first}.isdisjoint({r.pk for r in second})

        def it_refuses_to_submit_non_draft_classes(db):
            offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
            with pytest.raises(ValueError):
                offering.submit_for_review()

    def describe_decide():
        def it_publishes_when_every_required_gate_is_approved(db, admin_user):
            offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
            (admin_row,) = offering.submit_for_review()
            admin_row.decide(ClassApproval.Decision.APPROVED, user=admin_user)
            offering.refresh_from_db()
            assert offering.status == ClassOffering.Status.PUBLISHED
            assert offering.approved_by == admin_user
            assert offering.published_at is not None

        def it_holds_off_publishing_while_guild_lead_pending(db, admin_user):
            lead = MemberFactory()
            guild = GuildFactory(guild_lead=lead)
            offering = ClassOfferingFactory(
                category=CategoryFactory(guild=guild),
                status=ClassOffering.Status.DRAFT,
            )
            rows = offering.submit_for_review()
            admin_row = next(r for r in rows if r.role == ClassApproval.Role.ADMIN)
            admin_row.decide(ClassApproval.Decision.APPROVED, user=admin_user)
            offering.refresh_from_db()
            assert offering.status == ClassOffering.Status.PENDING

        def it_returns_to_draft_on_changes_requested(db, admin_user):
            offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
            (row,) = offering.submit_for_review()
            row.decide(
                ClassApproval.Decision.CHANGES_REQUESTED,
                user=admin_user,
                notes="Please add prerequisites.",
            )
            offering.refresh_from_db()
            assert offering.status == ClassOffering.Status.DRAFT

        def it_returns_to_draft_on_decline(db, admin_user):
            offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
            (row,) = offering.submit_for_review()
            row.decide(ClassApproval.Decision.DENIED, user=admin_user, notes="No.")
            offering.refresh_from_db()
            assert offering.status == ClassOffering.Status.DRAFT
