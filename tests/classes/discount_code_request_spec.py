"""DiscountCodeRequest: an instructor asks, an admin approves or declines, and the spine tells each side."""

from __future__ import annotations

import datetime as dt

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory, DiscountCodeRequestFactory, InstructorFactory, UserFactory
from classes.forms import DiscountCodeForm
from classes.models import DiscountCode, DiscountCodeRequest, DiscountCodeRequestAlreadyDecided
from core.models import Notification
from membership.models import AdminCapability, Member
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _member(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", last_login=timezone.now())
    member = user.member
    member.status = Member.Status.ACTIVE
    member.fog_role = fog_role
    member.save(update_fields=["status", "fog_role"])
    return member


def _admin() -> User:
    return _member("req-admin", fog_role=Member.FogRole.ADMIN).user


def _approver(username: str) -> Member:
    approver = _member(username)
    approver.admin_capabilities.create(capability=AdminCapability.Capability.DISCOUNT_APPROVER)
    return approver


def _request(**traits: object) -> DiscountCodeRequest:
    MembershipPlanFactory()
    user = UserFactory(username="req-teacher@example.com")
    instructor = InstructorFactory(user=user, instructor_slug="req-teacher")
    offering = ClassOfferingFactory(instructor=instructor, title="Bowl Turning")
    return DiscountCodeRequestFactory(class_offering=offering, **traits)


def _valid_form(req: DiscountCodeRequest, **overrides: object) -> DiscountCodeForm:
    """A bound, valid ``DiscountCodeForm`` the way the review view hands one to ``approve``."""
    data: dict[str, object] = {"code": req.code, "discount_pct": req.discount_pct, "is_active": "on", **overrides}
    form = DiscountCodeForm(data, initial=req.prefill(), scoped_to=req.class_offering, created_by=req.requested_by.user)
    assert form.is_valid(), form.errors
    return form


def describe_approve():
    def it_creates_the_code_with_the_requested_values():
        req = _request(
            code="earlybird",
            discount_pct=15,
            discount_fixed_cents=500,
            valid_from=dt.date(2026, 10, 1),
            valid_until=dt.date(2026, 10, 31),
            max_uses=10,
        )
        admin = _admin()
        form = _valid_form(
            req, discount_fixed_cents="5.00", valid_from="2026-10-01", valid_until="2026-10-31", max_uses=10
        )

        code = req.approve(admin, form)

        req.refresh_from_db()
        assert code.code == "EARLYBIRD"
        assert code.discount_pct == 15
        assert code.discount_fixed_cents == 500
        assert code.valid_from == dt.date(2026, 10, 1)
        assert code.valid_until == dt.date(2026, 10, 31)
        assert code.max_uses == 10
        assert code.class_offering == req.class_offering
        assert code.created_by == req.requested_by.user
        assert code.is_approved is True
        assert req.discount_code == code
        assert req.status == DiscountCodeRequest.Status.APPROVED
        assert req.decided_by == admin
        assert req.decided_at is not None

    def it_lets_the_admin_adjust_the_values_before_approving():
        req = _request(code="SPRING20", discount_pct=20)
        form = _valid_form(req, code="SPRING25", discount_pct=25)

        code = req.approve(_admin(), form)

        assert code.code == "SPRING25"
        assert code.discount_pct == 25
        req.refresh_from_db()
        assert req.code == "SPRING20"  # the ask is kept as asked; the code is what the admin made
        assert req.discount_code == code

    def it_notifies_the_instructor_and_nobody_else():
        req = _request()
        bystander = _member("bystander")

        req.approve(_admin(), _valid_form(req))

        instructor_rows = Notification.objects.filter(
            user=req.requested_by.user, trigger="discount_code.request_approved"
        )
        assert instructor_rows.count() == 1
        assert req.code in instructor_rows.get().title
        assert instructor_rows.get().url.endswith(
            reverse("classes:teach_class_discount_codes", kwargs={"pk": req.class_offering_id})
        )
        assert not Notification.objects.filter(user=bystander.user, trigger="discount_code.request_approved").exists()

    def it_sends_no_needs_approval_ping_for_the_approved_code():
        req = _request()
        # Created after the request, so the only "needs approval" ping this approver could get
        # would come from the code approve() creates.
        approver = _approver("dapprover")

        req.approve(_admin(), _valid_form(req))

        assert not Notification.objects.filter(user=approver.user, trigger="discount_code.requested").exists()

    def it_raises_when_the_request_was_already_decided():
        req = _request()
        admin = _admin()
        req.approve(admin, _valid_form(req))

        with pytest.raises(DiscountCodeRequestAlreadyDecided):
            req.approve(admin, _valid_form(req, code="OTHER"))

        assert DiscountCode.objects.count() == 1

    def it_makes_one_decision_when_two_admins_approve_the_same_request():
        # Two page loads, two in-memory copies both still PENDING. The second approve re-reads
        # the row under the lock and sees the first decision, so one code and one exception.
        req = _request()
        twin = DiscountCodeRequest.objects.get(pk=req.pk)
        admin = _admin()

        req.approve(admin, _valid_form(req))
        with pytest.raises(DiscountCodeRequestAlreadyDecided):
            twin.approve(admin, _valid_form(twin, code="OTHER"))

        assert DiscountCode.objects.count() == 1
        twin.refresh_from_db()
        assert twin.status == DiscountCodeRequest.Status.APPROVED
        assert Notification.objects.filter(trigger="discount_code.request_approved").count() == 1


def describe_decline():
    def it_records_the_note_and_the_decider():
        req = _request()
        admin = _admin()

        req.decline(admin, "  Too steep for a first class. ")

        req.refresh_from_db()
        assert req.status == DiscountCodeRequest.Status.DECLINED
        assert req.decision_note == "Too steep for a first class."
        assert req.decided_by == admin
        assert req.decided_at is not None

    def it_notifies_the_instructor_with_the_note():
        req = _request()

        req.decline(_admin(), "Too steep for a first class.")

        row = Notification.objects.get(user=req.requested_by.user, trigger="discount_code.request_declined")
        assert "Too steep for a first class." in row.body
        assert req.code in row.title

    def it_requires_a_note():
        req = _request()

        with pytest.raises(ValueError):
            req.decline(_admin(), "   ")

        req.refresh_from_db()
        assert req.status == DiscountCodeRequest.Status.PENDING
        assert req.decided_by is None

    def it_raises_when_the_request_was_already_decided():
        req = _request()
        admin = _admin()
        req.decline(admin, "No.")

        with pytest.raises(DiscountCodeRequestAlreadyDecided):
            req.decline(admin, "Still no.")

        req.refresh_from_db()
        assert req.decision_note == "No."

    def it_creates_no_code():
        req = _request()

        req.decline(_admin(), "Not this time.")

        assert not DiscountCode.objects.exists()
        req.refresh_from_db()
        assert req.discount_code is None

    def it_keeps_the_first_decision_when_a_stale_copy_declines_an_approved_request():
        req = _request()
        twin = DiscountCodeRequest.objects.get(pk=req.pk)
        admin = _admin()

        req.approve(admin, _valid_form(req))
        with pytest.raises(DiscountCodeRequestAlreadyDecided):
            twin.decline(admin, "Too late.")

        twin.refresh_from_db()
        assert twin.status == DiscountCodeRequest.Status.APPROVED
        assert twin.decision_note == ""
        assert DiscountCode.objects.count() == 1


def describe_a_new_request():
    def it_uppercases_the_code():
        req = _request(code=" spring20 ")
        req.refresh_from_db()
        assert req.code == "SPRING20"

    def it_notifies_a_discount_code_administrator():
        approver = _approver("dapprover")

        req = _request(code="SPRING20")

        row = Notification.objects.get(user=approver.user, trigger="discount_code.requested")
        assert "SPRING20" in row.body
        assert req.class_offering.title in row.body
        # The review page itself, so a holder who cannot open the admin queue can still act.
        assert row.url.endswith(reverse("classes:admin_discount_code_request_review", kwargs={"pk": req.pk}))
        assert row.url.startswith("http")

    def it_does_not_notify_a_plain_member():
        bystander = _member("bystander")

        _request()

        assert not Notification.objects.filter(user=bystander.user, trigger="discount_code.requested").exists()

    def it_has_a_meaningful_str():
        req = _request(code="SPRING20")
        assert str(req) == "SPRING20 for Bowl Turning (Pending)"

    def it_prefills_the_review_form_from_its_values():
        req = _request(code="SPRING20", discount_pct=None, discount_fixed_cents=500, max_uses=5, reason="Spring push.")
        assert req.prefill() == {
            "code": "SPRING20",
            "discount_pct": None,
            "discount_fixed_cents": 500,
            "valid_from": None,
            "valid_until": None,
            "max_uses": 5,
            "is_active": True,
            "description": "Spring push.",
        }


def describe_pending():
    def it_lists_only_the_requests_still_waiting():
        waiting = _request(code="WAITING")
        decided = DiscountCodeRequestFactory(
            class_offering=waiting.class_offering, code="DECIDED", status=DiscountCodeRequest.Status.DECLINED
        )

        assert list(DiscountCodeRequest.objects.pending()) == [waiting]
        assert decided not in DiscountCodeRequest.objects.pending()
