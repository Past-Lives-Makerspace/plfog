"""The admin's queue of discount code requests and the review page that decides them (#428, part 1)."""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.messages import get_messages
from django.urls import reverse

from classes.factories import ClassOfferingFactory, DiscountCodeFactory, DiscountCodeRequestFactory
from classes.models import DiscountCode, DiscountCodeRequest, DiscountCodeRequestAlreadyDecided
from core.models import SiteConfiguration
from membership.models import AdminCapability, Member

QUEUE = "classes:admin_discount_codes"
REVIEW = "classes:admin_discount_code_request_review"


def _master(on: bool) -> None:
    config = SiteConfiguration.load()
    config.instructor_discount_codes_enabled = on
    config.save(update_fields=["instructor_discount_codes_enabled"])


def _review_url(req: DiscountCodeRequest) -> str:
    return reverse(REVIEW, kwargs={"pk": req.pk})


def _messages(response) -> list[str]:
    return [m.message for m in get_messages(response.wsgi_request)]


def _approve_post(**overrides) -> dict[str, object]:
    return {"decision": "approve", "code": "SPRING20", "discount_pct": 20, "is_active": "on", **overrides}


def _grant_discount_approver(member_user) -> None:
    """The capability the "needs approval" ping is addressed to, on a member who is not an admin."""
    Member.objects.get(user=member_user).admin_capabilities.create(
        capability=AdminCapability.Capability.DISCOUNT_APPROVER
    )


def describe_the_queue():
    def it_lists_pending_requests_with_a_review_link(admin_user, client, db):
        waiting = DiscountCodeRequestFactory(code="WAITING", reason="Spring push.")
        decided = DiscountCodeRequestFactory(code="DECIDED", status=DiscountCodeRequest.Status.APPROVED)
        client.force_login(admin_user)

        resp = client.get(reverse(QUEUE))

        assert resp.status_code == 200
        assert _review_url(waiting).encode() in resp.content
        assert _review_url(decided).encode() not in resp.content
        assert waiting.requested_by.display_name.encode() in resp.content
        assert b"15% off" in resp.content

    def it_hides_the_queue_when_instructor_discount_codes_are_off(admin_user, client, db):
        _master(False)
        waiting = DiscountCodeRequestFactory(code="WAITING")
        client.force_login(admin_user)

        resp = client.get(reverse(QUEUE))

        assert resp.status_code == 200
        assert _review_url(waiting).encode() not in resp.content

    def it_gates_behind_the_admin_role(member_user, client, db):
        client.force_login(member_user)
        assert client.get(reverse(QUEUE)).status_code == 403

    def it_stays_admin_only_for_a_discount_code_administrator(member_user, client, db):
        # The holder decides from the review page the ping links to; the queue is the admin's.
        _grant_discount_approver(member_user)
        client.force_login(member_user)
        assert client.get(reverse(QUEUE)).status_code == 403


def describe_the_review_page():
    def it_prefills_the_discount_code_form_from_the_request(admin_user, client, db):
        offering = ClassOfferingFactory(title="Bowl Turning")
        req = DiscountCodeRequestFactory(
            class_offering=offering,
            code="SPRING20",
            discount_pct=20,
            discount_fixed_cents=500,
            max_uses=5,
            reason="Spring push.",
        )
        client.force_login(admin_user)

        resp = client.get(_review_url(req))

        assert resp.status_code == 200
        assert b'name="code" value="SPRING20"' in resp.content
        assert b'name="description" value="Spring push."' in resp.content
        assert b'name="discount_pct" value="20"' in resp.content
        assert b'name="discount_fixed_cents" value="5"' in resp.content
        assert b'name="max_uses" value="5"' in resp.content
        assert b'name="is_active"' in resp.content
        assert reverse("classes:teach_class_discount_codes", kwargs={"pk": offering.pk}).encode() in resp.content
        assert b"confirm_id" not in resp.content  # the include resolved; no raw params leaked
        assert b'id="decline-request-note"' in resp.content

    def it_approves_and_returns_to_the_discount_codes_page(admin_user, client, db):
        req = DiscountCodeRequestFactory(code="SPRING20", discount_pct=20)
        client.force_login(admin_user)

        resp = client.post(_review_url(req), _approve_post())

        assert resp.status_code == 302
        assert resp["Location"] == reverse(QUEUE)
        code = DiscountCode.objects.get(code="SPRING20")
        assert code.discount_pct == 20
        assert code.is_approved is True
        assert code.class_offering == req.class_offering
        assert code.created_by == req.requested_by.user
        req.refresh_from_db()
        assert req.status == DiscountCodeRequest.Status.APPROVED
        assert req.discount_code == code
        assert req.decided_by == admin_user
        assert "Discount code SPRING20 approved and ready to use." in _messages(resp)

    def it_lets_the_admin_change_the_values_before_approving(admin_user, client, db):
        req = DiscountCodeRequestFactory(code="SPRING20", discount_pct=20)
        client.force_login(admin_user)

        resp = client.post(_review_url(req), _approve_post(code="SPRING25", discount_pct=25, max_uses=3))

        assert resp.status_code == 302
        code = DiscountCode.objects.get(code="SPRING25")
        assert code.discount_pct == 25
        assert code.max_uses == 3
        req.refresh_from_db()
        assert req.discount_code == code

    def it_refuses_to_approve_a_code_that_already_exists_and_keeps_the_request_pending(admin_user, client, db):
        DiscountCodeFactory(code="SPRING20")
        req = DiscountCodeRequestFactory(code="SPRING20")
        client.force_login(admin_user)

        resp = client.post(_review_url(req), _approve_post())

        assert resp.status_code == 200
        assert DiscountCode.objects.count() == 1
        req.refresh_from_db()
        assert req.status == DiscountCodeRequest.Status.PENDING

    def it_declines_with_a_note(admin_user, client, db):
        req = DiscountCodeRequestFactory(code="SPRING20")
        client.force_login(admin_user)

        resp = client.post(_review_url(req), {"decision": "decline", "note": "Too steep for a first class."})

        assert resp.status_code == 302
        assert resp["Location"] == reverse(QUEUE)
        req.refresh_from_db()
        assert req.status == DiscountCodeRequest.Status.DECLINED
        assert req.decision_note == "Too steep for a first class."
        assert req.decided_by == admin_user
        assert not DiscountCode.objects.exists()
        assert "Request declined. The instructor has been told." in _messages(resp)

    def it_refuses_a_decline_without_a_note(admin_user, client, db):
        req = DiscountCodeRequestFactory(code="SPRING20")
        client.force_login(admin_user)

        resp = client.post(_review_url(req), {"decision": "decline", "note": "   "})

        assert resp.status_code == 200
        assert b"This field is required." in resp.content
        req.refresh_from_db()
        assert req.status == DiscountCodeRequest.Status.PENDING
        assert not DiscountCode.objects.exists()

    def it_rejects_an_unknown_decision(admin_user, client, db):
        req = DiscountCodeRequestFactory(code="SPRING20")
        client.force_login(admin_user)

        assert client.post(_review_url(req), {"decision": "maybe"}).status_code == 400
        assert client.post(_review_url(req), {}).status_code == 400
        req.refresh_from_db()
        assert req.status == DiscountCodeRequest.Status.PENDING

    def it_redirects_a_decided_request_with_an_info_message(admin_user, client, db):
        req = DiscountCodeRequestFactory(code="SPRING20", status=DiscountCodeRequest.Status.DECLINED)
        client.force_login(admin_user)

        resp = client.get(_review_url(req))

        assert resp.status_code == 302
        assert resp["Location"] == reverse(QUEUE)
        assert "This request has already been decided." in _messages(resp)

    def it_redirects_with_the_info_message_when_another_reviewer_decided_first(admin_user, client, db):
        # The page was fetched while pending; the model's row lock then found it decided. The
        # race itself is the model spec's; here only the view's answer to it is exercised.
        req = DiscountCodeRequestFactory(code="SPRING20")
        client.force_login(admin_user)

        with patch.object(DiscountCodeRequest, "approve", side_effect=DiscountCodeRequestAlreadyDecided("raced")):
            resp = client.post(_review_url(req), _approve_post())

        assert resp.status_code == 302
        assert resp["Location"] == reverse(QUEUE)
        assert "This request has already been decided." in _messages(resp)
        assert not DiscountCode.objects.exists()

    def it_404s_a_request_that_does_not_exist(admin_user, client, db):
        client.force_login(admin_user)
        assert client.get(reverse(REVIEW, kwargs={"pk": 9999})).status_code == 404

    def it_lets_a_discount_code_administrator_who_is_not_an_admin_decide(member_user, client, db):
        _grant_discount_approver(member_user)
        req = DiscountCodeRequestFactory(code="SPRING20", discount_pct=20)
        client.force_login(member_user)

        assert client.get(_review_url(req)).status_code == 200
        resp = client.post(_review_url(req), _approve_post())

        # Not the queue: that page is admin only, so a holder lands on the hub instead.
        assert resp.status_code == 302
        assert resp["Location"] == reverse("hub_home")
        code = DiscountCode.objects.get(code="SPRING20")
        assert code.is_approved is True
        req.refresh_from_db()
        assert req.status == DiscountCodeRequest.Status.APPROVED
        assert req.decided_by == member_user
        assert "Discount code SPRING20 approved and ready to use." in _messages(resp)

    def it_sends_a_holder_who_is_not_an_admin_home_from_a_decided_request(member_user, client, db):
        _grant_discount_approver(member_user)
        req = DiscountCodeRequestFactory(code="SPRING20", status=DiscountCodeRequest.Status.APPROVED)
        client.force_login(member_user)

        resp = client.get(_review_url(req))

        assert resp.status_code == 302
        assert resp["Location"] == reverse("hub_home")

    def it_refuses_a_plain_member(member_user, client, db):
        req = DiscountCodeRequestFactory(code="SPRING20")
        client.force_login(member_user)

        assert client.get(_review_url(req)).status_code == 403
        assert client.post(_review_url(req), _approve_post()).status_code == 403
        req.refresh_from_db()
        assert req.status == DiscountCodeRequest.Status.PENDING
        assert not DiscountCode.objects.exists()

    def it_refuses_a_self_approver_without_the_capability(member_user, client, db):
        # Self-approval covers a member's own codes; a request is decided by admins or holders only.
        Member.objects.filter(user=member_user).update(can_self_approve_discounts=True)
        req = DiscountCodeRequestFactory(code="SPRING20")
        client.force_login(member_user)

        assert client.get(_review_url(req)).status_code == 403
        assert client.post(_review_url(req), _approve_post()).status_code == 403
        assert not DiscountCode.objects.exists()

    def it_redirects_anonymous_users_to_login(client, db):
        req = DiscountCodeRequestFactory(code="SPRING20")
        resp = client.get(_review_url(req))
        assert resp.status_code == 302
        assert "login" in resp["Location"].lower()
