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
from tests.membership.factories import MemberFactory

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
        actor = UserFactory(username="withdrawer@example.com")
        offering.withdraw_submission(actor=actor)
        offering.refresh_from_db()
        assert offering.status == Status.DRAFT
        assert offering.approvals.count() == 0
        assert offering.lifecycle == ClassOffering.Lifecycle.DRAFT
        row = CmsActivity.objects.get(kind=CmsActivity.Kind.CLASS_WITHDRAWN, class_offering=offering)
        assert row.actor == actor

    def it_refuses_a_class_that_is_not_in_review(db):
        for status in (Status.DRAFT, Status.PUBLISHED, Status.CANCELLED, Status.ARCHIVED):
            with pytest.raises(ValueError):
                ClassOfferingFactory(status=status).withdraw_submission()

    def it_makes_the_reviewers_token_page_read_not_awaiting_review(db, client):
        offering = ClassOfferingFactory(ready=True, status=Status.DRAFT)
        (row,) = offering.submit_for_review()
        offering.withdraw_submission()
        # The token row is gone: the emailed link renders the not-awaiting state and names
        # nothing about the class. A stale row left on the draft reads the same.
        resp = client.get(reverse("classes:class_review", kwargs={"token": row.token}))
        assert resp.status_code == 200
        assert b"not awaiting review" in resp.content
        assert offering.title.encode() not in resp.content
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
        # Criterion 39: the LEGACY name, deliberately. This URL is persisted on the
        # Notification row and mailed out, so it has to keep resolving after a revert — one
        # 302 hop while the merge is live is the cheaper half of that trade.
        assert bell.url == reverse("classes:admin_class_edit", kwargs={"pk": offering.pk})
        sent = [m for m in mail.outbox if m.to == ["cms-change@example.com"]]
        assert len(sent) == 1 and "Move it to Friday." in sent[0].body
        # A second request is its own period, so it delivers again.
        offering.request_change(instructor, "Also add a seat.")
        assert Notification.objects.filter(trigger="class_change_requested", user=holder_user).count() == 2

    def it_sends_an_identical_note_once_within_the_hour(instructor, db):
        holder_user = UserFactory(username="cms-repeat@example.com", email="cms-repeat@example.com")
        Member.objects.get(user=holder_user).admin_capabilities.create(
            capability=AdminCapability.Capability.CLASS_APPROVER
        )
        offering = _live(instructor)
        # Picture Framing, 2026-10-12: one note tapped 33 times in 49 seconds reached every
        # CMS Administrator 33 times. A repeat inside the window is the same request.
        note = "Please add Jenelle Giordano as an instructor. Thanks!"
        rows = CmsActivity.objects.filter(kind=CmsActivity.Kind.CLASS_CHANGE_REQUESTED)
        assert offering.request_change(instructor, note) is True
        assert offering.request_change(instructor, note) is False
        assert offering.request_change(instructor, note) is False
        assert rows.count() == 1
        assert Notification.objects.filter(trigger="class_change_requested", user=holder_user).count() == 1
        assert len([m for m in mail.outbox if m.to == ["cms-repeat@example.com"]]) == 1
        # Only the latest request counts: a different note in between makes the same note new again.
        assert offering.request_change(instructor, "Also add a seat.") is True
        assert offering.request_change(instructor, note) is True
        assert rows.count() == 3
        # The same note from a co-instructor is that person's own first request.
        partner_user = UserFactory(username="partner@example.com", email="partner@example.com")
        partner = Member.objects.get(user=partner_user)
        assert offering.request_change(partner, note) is True
        assert rows.count() == 4

    def it_treats_a_long_note_edited_past_two_hundred_characters_as_new(instructor, db):
        offering = _live(instructor)
        head = "Please move the class to the following Saturday and raise the cap by two, " * 3
        assert len(head) > 200
        assert offering.request_change(instructor, head + "and keep the price.") is True
        assert offering.request_change(instructor, head + "and drop the price to $60.") is True
        rows = CmsActivity.objects.filter(kind=CmsActivity.Kind.CLASS_CHANGE_REQUESTED).order_by("pk")
        assert [row.payload["note"] for row in rows] == [
            " ".join((head + "and keep the price.").split()),
            " ".join((head + "and drop the price to $60.").split()),
        ]

    def it_never_matches_a_member_with_no_login_against_anyone(db):
        # Unreachable from the page (it needs a login), kept honest at the model: with no user
        # there is nothing to repeat, so the request goes out each time.
        nobody = MemberFactory(full_legal_name="No Login")
        offering = _live(nobody)
        assert offering.request_change(nobody, "Move it.") is True
        assert offering.request_change(nobody, "Move it.") is True
        assert CmsActivity.objects.filter(kind=CmsActivity.Kind.CLASS_CHANGE_REQUESTED).count() == 2

    def it_sends_the_same_note_again_once_the_hour_has_passed(instructor, db):
        holder_user = UserFactory(username="cms-later@example.com", email="cms-later@example.com")
        Member.objects.get(user=holder_user).admin_capabilities.create(
            capability=AdminCapability.Capability.CLASS_APPROVER
        )
        offering = _live(instructor)
        offering.request_change(instructor, "Move it to Friday.")
        CmsActivity.objects.filter(kind=CmsActivity.Kind.CLASS_CHANGE_REQUESTED).update(
            created_at=timezone.now() - ClassOffering.CHANGE_REQUEST_REPEAT_WINDOW - timedelta(minutes=1)
        )
        offering.request_change(instructor, "Move it to Friday.")
        assert CmsActivity.objects.filter(kind=CmsActivity.Kind.CLASS_CHANGE_REQUESTED).count() == 2
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
        # Criterion 39: the legacy name, persisted and mailed — see the note above.
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
