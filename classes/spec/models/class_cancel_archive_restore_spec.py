"""BDD specs for cancel (member-facing), archive (quiet, guarded), restore, publish, and the clone paths."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory, RegistrationFactory, UserFactory
from classes.models import ClassApproval, ClassOffering, CmsActivity, Registration
from core.models import Notification, SiteActivity

Status = ClassOffering.Status


def _future(offering: ClassOffering, days: int = 3) -> None:
    start = timezone.now() + timedelta(days=days)
    ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))


@pytest.fixture(autouse=True)
def _outbox():
    mail.outbox = []


def describe_cancel():
    def it_refuses_anything_but_a_published_class(db):
        for status in (Status.DRAFT, Status.PENDING, Status.CANCELLED, Status.ARCHIVED):
            with pytest.raises(ValueError):
                ClassOfferingFactory(status=status).cancel(None, "Reason")

    def it_refuses_a_blank_reason(db):
        offering = ClassOfferingFactory(status=Status.PUBLISHED)
        with pytest.raises(ValueError):
            offering.cancel(None, "   ")
        offering.refresh_from_db()
        assert offering.status == Status.PUBLISHED

    def it_sets_the_three_fields_logs_and_emits_once(db):
        actor = UserFactory(last_login=timezone.now(), email="admin-actor@example.com")
        offering = ClassOfferingFactory(status=Status.PUBLISHED, title="Wheel Throwing")
        _future(offering)
        RegistrationFactory(class_offering=offering, email="booked@example.com", status=Registration.Status.CONFIRMED)
        RegistrationFactory(class_offering=offering, email="gone@example.com", status=Registration.Status.CANCELLED)

        offering.cancel(actor, "  The kiln broke.  ")

        offering.refresh_from_db()
        assert offering.status == Status.CANCELLED
        assert offering.cancelled_at is not None
        assert offering.cancelled_by == actor.member
        assert offering.cancellation_reason == "The kiln broke."
        assert offering.lifecycle == ClassOffering.Lifecycle.CANCELLED
        row = CmsActivity.objects.get(kind=CmsActivity.Kind.CLASS_CANCELLED, class_offering=offering)
        assert row.actor == actor
        assert row.payload == {"reason": "The kiln broke."}
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.CLASS_CANCELLED).count() == 1
        # One email, to the registrant only, carrying the reason; the actor's own bell row carries none.
        sent = [m for m in mail.outbox if m.to == ["booked@example.com"]]
        assert len(sent) == 1
        assert "Reason: The kiln broke." in sent[0].body
        assert [m for m in mail.outbox if m.to == ["gone@example.com"]] == []
        bell = Notification.objects.get(trigger="class_cancelled", user=actor)
        assert "kiln" not in bell.body
        assert "Wheel Throwing" in bell.title

    def it_records_no_canceller_without_an_actor(db):
        offering = ClassOfferingFactory(status=Status.PUBLISHED)
        offering.cancel(None, "Weather")
        offering.refresh_from_db()
        assert offering.cancelled_by is None

    def it_drops_out_of_the_catalog_and_the_public_session_feed(db):
        offering = ClassOfferingFactory(status=Status.PUBLISHED)
        _future(offering)
        from classes.models import ClassSession

        assert offering in ClassOffering.objects.bookable()
        assert ClassSession.objects.upcoming_public_count() == 1
        offering.cancel(None, "Weather")
        assert offering not in ClassOffering.objects.bookable()
        assert offering not in ClassOffering.objects.public()
        assert ClassSession.objects.upcoming_public_count() == 0


def describe_archive():
    def it_refuses_an_upcoming_class_with_active_registrations(db):
        offering = ClassOfferingFactory(status=Status.PUBLISHED)
        _future(offering)
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        RegistrationFactory(class_offering=offering, status=Registration.Status.WAITLISTED)
        assert (
            offering.archive_blocker == "Cancel this class instead. It has upcoming dates and 2 active registrations."
        )
        with pytest.raises(ValueError, match="Cancel this class instead"):
            offering.archive()
        offering.refresh_from_db()
        assert offering.status == Status.PUBLISHED

    def it_allows_an_upcoming_class_whose_registrations_all_left(db):
        offering = ClassOfferingFactory(status=Status.PUBLISHED)
        _future(offering)
        RegistrationFactory(class_offering=offering, status=Registration.Status.CANCELLED)
        assert offering.archive_blocker == ""
        offering.archive()
        offering.refresh_from_db()
        assert offering.status == Status.ARCHIVED

    def it_archives_a_completed_class_with_registrations_quietly(db):
        active_member = UserFactory(last_login=timezone.now(), email="bystander@example.com")
        offering = ClassOfferingFactory(status=Status.PUBLISHED)
        _future(offering, days=-3)
        RegistrationFactory(class_offering=offering, email="past@example.com", status=Registration.Status.CONFIRMED)
        offering.archive()
        offering.refresh_from_db()
        assert offering.status == Status.ARCHIVED
        assert mail.outbox == []
        assert not Notification.objects.filter(trigger="class_cancelled", user=active_member).exists()
        assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.CLASS_CANCELLED).exists()
        assert CmsActivity.objects.filter(kind=CmsActivity.Kind.CLASS_ARCHIVED, class_offering=offering).exists()

    def it_archives_a_draft_and_a_cancelled_class(db):
        for status in (Status.DRAFT, Status.CANCELLED, Status.PENDING):
            offering = ClassOfferingFactory(status=status)
            offering.archive()
            offering.refresh_from_db()
            assert offering.status == Status.ARCHIVED
        assert mail.outbox == []


def describe_restore():
    def it_returns_an_archived_class_to_draft_with_approvals_cleared(db):
        offering = ClassOfferingFactory(
            status=Status.ARCHIVED,
            cancelled_at=timezone.now(),
            cancellation_reason="Old reason",
        )
        ClassApproval.objects.create(
            class_offering=offering, role=ClassApproval.Role.ADMIN, decision=ClassApproval.Decision.APPROVED
        )
        offering.restore()
        offering.refresh_from_db()
        assert offering.status == Status.DRAFT
        assert offering.approvals.count() == 0
        assert offering.cancelled_at is None
        assert offering.cancellation_reason == ""
        assert offering.lifecycle == ClassOffering.Lifecycle.DRAFT
        assert CmsActivity.objects.filter(kind=CmsActivity.Kind.CLASS_RESTORED, class_offering=offering).exists()

    def it_refuses_a_class_that_is_not_archived(db):
        with pytest.raises(ValueError):
            ClassOfferingFactory(status=Status.DRAFT).restore()


def describe_unpublish():
    """Taking a live class back to draft: the exact reverse of publish, and completely quiet."""

    def it_returns_a_published_class_to_draft_with_approvals_cleared(db):
        offering = ClassOfferingFactory(status=Status.PUBLISHED, published_at=timezone.now())
        _future(offering)
        actor = UserFactory()
        offering.approved_by = actor
        offering.save(update_fields=["approved_by"])
        ClassApproval.objects.create(
            class_offering=offering, role=ClassApproval.Role.ADMIN, decision=ClassApproval.Decision.APPROVED
        )
        offering.unpublish(actor=actor)
        offering.refresh_from_db()
        assert offering.status == Status.DRAFT
        assert offering.approvals.count() == 0
        assert offering.approved_by is None
        assert offering.published_at is None
        assert offering.lifecycle == ClassOffering.Lifecycle.DRAFT
        assert CmsActivity.objects.filter(kind=CmsActivity.Kind.CLASS_UNPUBLISHED, class_offering=offering).exists()

    def it_refuses_any_status_but_published(db):
        for status in (Status.DRAFT, Status.PENDING, Status.CANCELLED, Status.ARCHIVED):
            with pytest.raises(ValueError):
                ClassOfferingFactory(status=status).unpublish()

    def it_tells_nobody(db):
        """Unlike cancel, this is housekeeping: no email, no bell, no site activity mirror."""
        offering = ClassOfferingFactory(status=Status.PUBLISHED, published_at=timezone.now())
        _future(offering)
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        mail.outbox = []
        before = SiteActivity.objects.count()
        offering.unpublish()
        assert mail.outbox == []
        assert Notification.objects.count() == 0
        assert SiteActivity.objects.count() == before

    def it_leaves_registrations_exactly_as_they_were(db):
        offering = ClassOfferingFactory(status=Status.PUBLISHED, published_at=timezone.now())
        _future(offering)
        reg = RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        offering.unpublish()
        reg.refresh_from_db()
        assert reg.status == Registration.Status.CONFIRMED
        assert reg.class_offering_id == offering.pk

    def it_drops_the_class_out_of_the_catalog_at_once(db):
        offering = ClassOfferingFactory(status=Status.PUBLISHED, published_at=timezone.now())
        _future(offering)
        assert offering in ClassOffering.objects.public()
        offering.unpublish()
        assert offering not in ClassOffering.objects.public()
        assert offering not in ClassOffering.objects.bookable()

    def it_sends_the_class_back_to_the_first_gate_of_the_pipeline(db):
        offering = ClassOfferingFactory(ready=True, status=Status.PUBLISHED, published_at=timezone.now())
        _future(offering)
        ClassApproval.objects.create(
            class_offering=offering, role=ClassApproval.Role.ADMIN, decision=ClassApproval.Decision.APPROVED
        )
        offering.unpublish()
        offering.refresh_from_db()
        pipeline = offering.review_pipeline()
        assert not pipeline.is_live
        assert pipeline.steps[0].state == "current"


def describe_publish():
    def it_refuses_an_unready_class_naming_the_items(db, admin_user):
        offering = ClassOfferingFactory(status=Status.PENDING, description="Short")
        with pytest.raises(ValidationError) as excinfo:
            offering.publish(admin_user)
        assert excinfo.value.messages == ["Not ready to publish: Write a short description. Add at least one date."]
        offering.refresh_from_db()
        assert offering.status == Status.PENDING
        assert not CmsActivity.objects.filter(kind=CmsActivity.Kind.CLASS_PUBLISHED).exists()

    def it_publishes_a_ready_class_once(db, admin_user):
        member = UserFactory(last_login=timezone.now())
        offering = ClassOfferingFactory(ready=True, status=Status.PENDING)
        offering.publish(admin_user)
        offering.refresh_from_db()
        assert offering.status == Status.PUBLISHED
        assert offering.approved_by == admin_user
        assert offering.published_at is not None
        assert CmsActivity.objects.filter(kind=CmsActivity.Kind.CLASS_PUBLISHED, class_offering=offering).count() == 1
        # Publishing notifies nobody site-wide — the #classes announcer does that.
        assert not Notification.objects.filter(user=member).exists()

    def it_logs_the_publish_again_after_an_unpublish(db, admin_user):
        # A class taken back to draft and published again is a second publish, so the
        # CmsActivity trail records both rather than collapsing them.
        offering = ClassOfferingFactory(ready=True, status=Status.PENDING)
        offering.publish(admin_user)
        offering.unpublish(admin_user)
        offering.publish(admin_user)
        assert CmsActivity.objects.filter(kind=CmsActivity.Kind.CLASS_PUBLISHED, class_offering=offering).count() == 2

    def it_refuses_the_admin_decision_on_an_unready_class_before_saving_the_row(db, admin_user):
        offering = ClassOfferingFactory(status=Status.PENDING, description="Short")
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        with pytest.raises(ValidationError):
            row.decide(ClassApproval.Decision.APPROVED, user=admin_user)
        row.refresh_from_db()
        offering.refresh_from_db()
        assert row.decision == ""
        assert offering.status == Status.PENDING

    def it_approve_refuses_an_unready_class_without_minting_an_admin_row(db, admin_user):
        offering = ClassOfferingFactory(status=Status.PENDING, description="Short")
        with pytest.raises(ValidationError) as excinfo:
            offering.approve(admin_user)
        assert excinfo.value.messages[0].startswith("Not ready to publish:")
        assert not offering.approvals.filter(role=ClassApproval.Role.ADMIN).exists()

    def it_still_lets_a_guild_lead_approve_an_unready_class(db, admin_user):
        # The lead's approval only escalates; the readiness gate sits on the publishing decision.
        offering = ClassOfferingFactory(status=Status.PENDING, description="Short")
        row = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.GUILD_LEAD)
        row.decide(ClassApproval.Decision.APPROVED, user=admin_user)
        row.refresh_from_db()
        assert row.decision == ClassApproval.Decision.APPROVED

    def it_publishes_through_the_approval_pathway_as_before(db, admin_user):
        offering = ClassOfferingFactory(ready=True, status=Status.DRAFT)
        (row,) = offering.submit_for_review()
        row.decide(ClassApproval.Decision.APPROVED, user=admin_user)
        offering.refresh_from_db()
        assert offering.status == Status.PUBLISHED
        assert offering.approved_by == admin_user


def describe_clone_paths():
    def it_duplicate_clears_the_cancel_record(db):
        original = ClassOfferingFactory(
            status=Status.CANCELLED,
            cancelled_at=timezone.now(),
            cancellation_reason="Flooded",
            cancelled_by=UserFactory().member,
        )
        copy = original.duplicate()
        assert copy.status == Status.DRAFT
        assert copy.cancelled_at is None
        assert copy.cancelled_by is None
        assert copy.cancellation_reason == ""

    def it_duplicate_as_new_run_clears_the_cancel_record(db):
        original = ClassOfferingFactory(
            status=Status.CANCELLED,
            cancelled_at=timezone.now(),
            cancellation_reason="Flooded",
            cancelled_by=UserFactory().member,
        )
        run = original.duplicate_as_new_run()
        assert run.status == Status.DRAFT
        assert run.cancelled_at is None
        assert run.cancelled_by is None
        assert run.cancellation_reason == ""
