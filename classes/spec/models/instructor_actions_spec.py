"""BDD specs for the instructor's own actions on a class: withdraw, request a change, and the
refund notice an instructor's cancel raises."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory, RegistrationFactory, UserFactory
from classes.models import ClassApproval, ClassOffering, CmsActivity, Registration
from core.models import Notification
from membership.models import AdminCapability, Member

Status = ClassOffering.Status


@pytest.fixture(autouse=True)
def _outbox():
    mail.outbox = []


@pytest.fixture
def instructor(db):
    user = UserFactory(username="own-teacher@example.com", email="own-teacher@example.com")
    member = Member.objects.get(user=user)
    member.full_legal_name = "Own Teacher"
    member.instructor_oriented_at = timezone.now()
    member.save(update_fields=["full_legal_name", "instructor_oriented_at"])
    return member


@pytest.fixture
def refund_admin(db):
    user = UserFactory(username="refunder@example.com", email="refunder@example.com", last_login=timezone.now())
    member = Member.objects.get(user=user)
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    return user


def _live(instructor, **kwargs) -> ClassOffering:
    offering = ClassOfferingFactory(
        instructor=instructor, status=Status.PUBLISHED, published_at=timezone.now(), **kwargs
    )
    start = timezone.now() + timedelta(days=3)
    ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def describe_withdraw_submission():
    def it_returns_to_draft_and_deletes_every_row_including_an_approved_guild_lead_row(db):
        offering = ClassOfferingFactory(status=Status.PENDING)
        ClassApproval.objects.create(
            class_offering=offering, role=ClassApproval.Role.GUILD_LEAD, decision=ClassApproval.Decision.APPROVED
        )
        ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        offering.withdraw_submission()
        offering.refresh_from_db()
        assert offering.status == Status.DRAFT
        assert offering.approvals.count() == 0
        assert offering.lifecycle == ClassOffering.Lifecycle.DRAFT
        assert CmsActivity.objects.filter(kind=CmsActivity.Kind.CLASS_WITHDRAWN, class_offering=offering).exists()

    def it_refuses_a_class_that_is_not_in_review(db):
        for status in (Status.DRAFT, Status.PUBLISHED, Status.CANCELLED, Status.ARCHIVED):
            with pytest.raises(ValueError):
                ClassOfferingFactory(status=status).withdraw_submission()

    def it_makes_the_reviewers_token_page_read_not_awaiting_review(db, client):
        offering = ClassOfferingFactory(ready=True, status=Status.DRAFT)
        (row,) = offering.submit_for_review()
        offering.withdraw_submission()
        # The token row is gone; a leftover bookmark 404s. A reviewer who kept a fresh
        # row (legacy) sees the not-awaiting state: model the second with a stale row.
        assert client.get(reverse("classes:class_review", kwargs={"token": row.token})).status_code == 404
        stale = ClassApproval.objects.create(class_offering=offering, role=ClassApproval.Role.ADMIN)
        resp = client.get(reverse("classes:class_review", kwargs={"token": stale.token}))
        assert b"not awaiting review" in resp.content


def describe_request_change():
    def it_logs_and_notifies_the_cms_administrators_with_the_note(instructor, db):
        holder_user = UserFactory(username="cms-change@example.com", email="cms-change@example.com")
        Member.objects.get(user=holder_user).admin_capabilities.create(
            capability=AdminCapability.Capability.CLASS_APPROVER
        )
        offering = _live(instructor, title="Live Lathe")
        offering.request_change(instructor, "  Move it   to Friday.  ")
        row = CmsActivity.objects.get(kind=CmsActivity.Kind.CLASS_CHANGE_REQUESTED, class_offering=offering)
        assert row.actor == instructor.user
        assert row.payload == {"note": "Move it to Friday."}
        bell = Notification.objects.get(trigger="class_change_requested", user=holder_user)
        assert bell.title == "Own Teacher asked for a change to Live Lathe"
        assert bell.body == "Move it to Friday."
        assert bell.url == reverse("classes:admin_class_edit", kwargs={"pk": offering.pk})
        sent = [m for m in mail.outbox if m.to == ["cms-change@example.com"]]
        assert len(sent) == 1 and "Move it to Friday." in sent[0].body
        # A second request is its own period, so it delivers again.
        offering.request_change(instructor, "Also add a seat.")
        assert Notification.objects.filter(trigger="class_change_requested", user=holder_user).count() == 2

    def it_refuses_a_blank_note_and_a_class_that_is_not_live(instructor, db):
        live = _live(instructor)
        with pytest.raises(ValueError):
            live.request_change(instructor, "   ")
        draft = ClassOfferingFactory(instructor=instructor, status=Status.DRAFT)
        with pytest.raises(ValueError):
            draft.request_change(instructor, "Change it")
        assert not CmsActivity.objects.filter(kind=CmsActivity.Kind.CLASS_CHANGE_REQUESTED).exists()


def describe_instructor_cancel_refund_notice():
    def it_notifies_the_refund_authority_with_the_paid_count(instructor, refund_admin, db):
        offering = _live(instructor, title="Paid Pots")
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED, amount_paid_cents=5000)
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED, amount_paid_cents=5000)
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED, amount_paid_cents=0)
        offering.cancel(instructor.user, "Kiln broke")
        bell = Notification.objects.get(trigger="class_cancelled_admin_notice", user=refund_admin)
        assert bell.title == "Own Teacher cancelled Paid Pots"
        assert bell.body == "2 paid registrations need refunds."
        assert bell.url == reverse("classes:admin_class_registrations", kwargs={"pk": offering.pk})
        sent = [m for m in mail.outbox if m.to == ["refunder@example.com"] and "Refunds needed" in m.subject]
        assert len(sent) == 1
        assert "[missing:" not in sent[0].body

    def it_sends_no_notice_for_a_free_class(instructor, refund_admin, db):
        offering = _live(instructor)
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED, amount_paid_cents=0)
        offering.cancel(instructor.user, "Kiln broke")
        assert not Notification.objects.filter(trigger="class_cancelled_admin_notice").exists()

    def it_sends_no_notice_when_an_admin_cancels(instructor, refund_admin, db):
        offering = _live(instructor)
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED, amount_paid_cents=5000)
        offering.cancel(refund_admin, "Kiln broke")
        assert not Notification.objects.filter(trigger="class_cancelled_admin_notice").exists()

    def it_sends_no_notice_without_an_actor(instructor, refund_admin, db):
        offering = _live(instructor)
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED, amount_paid_cents=5000)
        offering.cancel(None, "Kiln broke")
        assert not Notification.objects.filter(trigger="class_cancelled_admin_notice").exists()
