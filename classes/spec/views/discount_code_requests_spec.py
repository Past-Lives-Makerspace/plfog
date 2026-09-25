"""The instructor's discount code surfaces under approval mode (#428, part 1).

The autouse ``_instructor_discount_codes_on`` fixture in ``classes/spec/conftest.py`` turns the
master flag on and the approval flag OFF; the approval-mode describes here flip the approval flag
on explicitly in each ``it_``.
"""

from __future__ import annotations

from django.contrib.messages import get_messages
from django.urls import reverse

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    DiscountCodeFactory,
    DiscountCodeRequestFactory,
    InstructorFactory,
)
from classes.models import ClassOffering, DiscountCode, DiscountCodeRequest
from core.models import SiteConfiguration
from membership.models import Member

DISCOUNT_CODES = "classes:teach_discount_codes"
REQUEST = "classes:teach_discount_code_request"


def _approval_mode(on: bool) -> None:
    config = SiteConfiguration.load()
    config.instructor_discount_codes_need_approval = on
    config.save(update_fields=["instructor_discount_codes_need_approval"])


def _master(on: bool) -> None:
    config = SiteConfiguration.load()
    config.instructor_discount_codes_enabled = on
    config.save(update_fields=["instructor_discount_codes_enabled"])


def _own_offering(member_user, **traits) -> ClassOffering:
    return ClassOfferingFactory(category=CategoryFactory(), instructor=Member.objects.get(user=member_user), **traits)


def _foreign_offering(**traits) -> ClassOffering:
    return ClassOfferingFactory(category=CategoryFactory(), instructor=InstructorFactory(), **traits)


def _messages(response) -> list[str]:
    return [m.message for m in get_messages(response.wsgi_request)]


def _request_post(offering: ClassOffering, **overrides) -> dict[str, object]:
    return {
        "class_offering": offering.pk,
        "code": "earlybird",
        "discount_pct": 15,
        "reason": "Returning students get a head start.",
        **overrides,
    }


def describe_instructor_surfaces_in_approval_mode():
    def it_redirects_create_edit_delete_and_approve_to_the_discount_codes_page_with_an_info_message(
        client, member_user
    ):
        _approval_mode(True)
        client.force_login(member_user)
        attempts = [
            ("get", reverse("classes:teach_discount_code_create")),
            ("post", reverse("classes:teach_discount_code_create")),
            ("get", reverse("classes:teach_discount_code_edit", kwargs={"pk": 9999})),
            ("post", reverse("classes:teach_discount_code_edit", kwargs={"pk": 9999})),
            ("post", reverse("classes:teach_discount_code_delete", kwargs={"pk": 9999})),
            ("post", reverse("classes:teach_discount_code_approve", kwargs={"pk": 9999})),
        ]
        for method, url in attempts:
            resp = getattr(client, method)(url)
            assert resp.status_code == 302, (method, url)
            assert resp["Location"] == reverse(DISCOUNT_CODES), (method, url)
        assert "Discount codes are requested here and approved by an admin. Use Request a Code." in _messages(resp)

    def it_renders_the_request_form_with_only_my_classes(client, member_user):
        _approval_mode(True)
        mine = _own_offering(member_user)
        cancelled = _own_offering(member_user, status=ClassOffering.Status.CANCELLED)
        other = _foreign_offering()
        client.force_login(member_user)

        resp = client.get(reverse(REQUEST) + f"?class={mine.pk}")

        assert resp.status_code == 200
        assert f'<option value="{mine.pk}" selected'.encode() in resp.content
        assert f'<option value="{cancelled.pk}"'.encode() not in resp.content
        assert f'<option value="{other.pk}"'.encode() not in resp.content

    def it_ignores_a_class_in_the_query_string_that_is_not_mine(client, member_user):
        _approval_mode(True)
        mine = _own_offering(member_user)
        other = _foreign_offering()
        client.force_login(member_user)

        for raw in (str(other.pk), "abc"):
            resp = client.get(reverse(REQUEST) + f"?class={raw}")
            assert resp.status_code == 200
            assert f'<option value="{mine.pk}">'.encode() in resp.content, raw  # offered, not preselected
            assert f'<option value="{other.pk}"'.encode() not in resp.content, raw

    def it_creates_a_pending_request_and_returns_to_the_class_tab(client, member_user):
        _approval_mode(True)
        mine = _own_offering(member_user)
        client.force_login(member_user)

        resp = client.post(reverse(REQUEST), _request_post(mine))

        assert resp.status_code == 302
        assert resp["Location"] == reverse("classes:teach_class_discount_codes", kwargs={"pk": mine.pk})
        req = DiscountCodeRequest.objects.get()
        assert req.code == "EARLYBIRD"
        assert req.class_offering == mine
        assert req.requested_by == Member.objects.get(user=member_user)
        assert req.status == DiscountCodeRequest.Status.PENDING
        assert not DiscountCode.objects.exists()
        assert "Your request for EARLYBIRD is in. An admin will review it." in _messages(resp)

    def it_refuses_a_foreign_class(client, member_user):
        _approval_mode(True)
        other = _foreign_offering()
        client.force_login(member_user)

        resp = client.post(reverse(REQUEST), _request_post(other))

        assert resp.status_code == 200
        assert not DiscountCodeRequest.objects.exists()

    def it_refuses_a_request_with_no_discount_value(client, member_user):
        _approval_mode(True)
        mine = _own_offering(member_user)
        client.force_login(member_user)

        resp = client.post(reverse(REQUEST), _request_post(mine, discount_pct=""))

        assert resp.status_code == 200
        assert resp.content.count(b"Set a percent off or a fixed amount off.") == 1
        # The CheckConstraint's own wording is the one error; Django's raw "is violated" never shows.
        assert b"is violated" not in resp.content
        assert not DiscountCodeRequest.objects.exists()

    def it_refuses_a_code_that_already_exists(client, member_user):
        _approval_mode(True)
        mine = _own_offering(member_user)
        DiscountCodeFactory(code="TAKEN")
        client.force_login(member_user)

        resp = client.post(reverse(REQUEST), _request_post(mine, code="taken"))

        assert resp.status_code == 200
        assert b"That code is already taken. Pick another." in resp.content
        assert not DiscountCodeRequest.objects.exists()

    def it_refuses_a_code_another_request_is_waiting_on(client, member_user):
        _approval_mode(True)
        mine = _own_offering(member_user)
        DiscountCodeRequestFactory(class_offering=mine, code="ASKED")
        client.force_login(member_user)

        resp = client.post(reverse(REQUEST), _request_post(mine, code="asked"))

        assert resp.status_code == 200
        assert b"That code is already taken. Pick another." in resp.content
        assert DiscountCodeRequest.objects.count() == 1

    def it_shows_codes_on_my_classes_read_only_with_the_request_button(client, member_user):
        _approval_mode(True)
        # A self-approver on an unapproved code would see Approve in the direct flow; not here.
        Member.objects.filter(user=member_user).update(can_self_approve_discounts=True)
        mine = _own_offering(member_user)
        code = DiscountCodeFactory(code="MINE10", class_offering=mine, is_approved=False)
        client.force_login(member_user)

        resp = client.get(reverse(DISCOUNT_CODES))

        assert resp.status_code == 200
        assert b"MINE10" in resp.content
        for name in ("teach_discount_code_edit", "teach_discount_code_delete", "teach_discount_code_approve"):
            assert reverse(f"classes:{name}", kwargs={"pk": code.pk}).encode() not in resp.content, name
        assert reverse("classes:teach_discount_code_create").encode() not in resp.content
        assert reverse(REQUEST).encode() in resp.content

    def it_shows_the_request_link_in_the_empty_state(client, member_user):
        _approval_mode(True)
        client.force_login(member_user)

        resp = client.get(reverse(DISCOUNT_CODES))

        assert resp.status_code == 200
        assert reverse(REQUEST).encode() in resp.content
        assert reverse("classes:teach_discount_code_create").encode() not in resp.content
        assert b'class="pl-table-empty"' in resp.content

    def it_lists_my_requests_with_status_and_note(client, member_user):
        _approval_mode(True)
        mine = _own_offering(member_user)
        DiscountCodeRequestFactory(class_offering=mine, code="WAITING")
        DiscountCodeRequestFactory(class_offering=mine, code="GRANTED", status=DiscountCodeRequest.Status.APPROVED)
        DiscountCodeRequestFactory(
            class_offering=mine,
            code="REFUSED",
            discount_pct=None,
            discount_fixed_cents=500,
            status=DiscountCodeRequest.Status.DECLINED,
            decision_note="Too steep for a first class.",
        )
        client.force_login(member_user)

        resp = client.get(reverse(DISCOUNT_CODES))

        assert b'<span class="hub-pill hub-pill--warn">Pending</span>' in resp.content
        assert b'<span class="hub-pill hub-pill--ok">Approved</span>' in resp.content
        assert b'<span class="hub-pill hub-pill--danger">Declined</span>' in resp.content
        assert b"Too steep for a first class." in resp.content
        assert b"$5.00 off" in resp.content
        assert b"15% off" in resp.content

    def it_marks_an_approved_request_whose_code_was_deleted(client, member_user):
        _approval_mode(True)
        mine = _own_offering(member_user)
        DiscountCodeRequestFactory(
            class_offering=mine,
            code="KEPT",
            status=DiscountCodeRequest.Status.APPROVED,
            discount_code=DiscountCodeFactory(code="KEPT", class_offering=mine),
        )
        DiscountCodeRequestFactory(class_offering=mine, code="GONE", status=DiscountCodeRequest.Status.APPROVED)
        client.force_login(member_user)

        resp = client.get(reverse(DISCOUNT_CODES))

        assert resp.content.count(b'<span class="hub-pill hub-pill--ok">Approved</span>') == 2
        assert resp.content.count(b'<span class="hub-pill hub-pill--neutral">Code removed</span>') == 1

    def it_shows_the_request_button_and_hides_the_controls_on_the_class_tab(client, member_user):
        _approval_mode(True)
        Member.objects.filter(user=member_user).update(can_self_approve_discounts=True)
        mine = _own_offering(member_user)
        code = DiscountCodeFactory(code="MINE10", class_offering=mine, is_approved=False)
        DiscountCodeRequestFactory(class_offering=mine, code="WAITING")
        client.force_login(member_user)

        resp = client.get(reverse("classes:teach_class_discount_codes", kwargs={"pk": mine.pk}))

        assert resp.status_code == 200
        assert b"MINE10" in resp.content
        assert f"{reverse(REQUEST)}?class={mine.pk}".encode() in resp.content
        assert reverse("classes:teach_discount_code_create").encode() not in resp.content
        for name in ("teach_discount_code_edit", "teach_discount_code_delete", "teach_discount_code_approve"):
            assert reverse(f"classes:{name}", kwargs={"pk": code.pk}).encode() not in resp.content, name
        assert b'<span class="hub-pill hub-pill--warn">Pending</span>' in resp.content


def describe_the_direct_flow_when_the_new_flag_is_off():
    def it_still_lets_an_instructor_create_a_code(client, member_user):
        client.force_login(member_user)

        resp = client.post(
            reverse("classes:teach_discount_code_create"), {"code": "DIRECT10", "discount_pct": 10, "is_active": "on"}
        )

        assert resp.status_code == 302
        assert DiscountCode.objects.filter(code="DIRECT10", created_by=member_user).exists()

    def it_shows_edit_and_delete_controls(client, member_user):
        mine = DiscountCodeFactory(code="MINE10", created_by=member_user)
        client.force_login(member_user)

        resp = client.get(reverse(DISCOUNT_CODES))

        assert reverse("classes:teach_discount_code_edit", kwargs={"pk": mine.pk}).encode() in resp.content
        assert reverse("classes:teach_discount_code_delete", kwargs={"pk": mine.pk}).encode() in resp.content
        assert reverse("classes:teach_discount_code_create").encode() in resp.content
        assert reverse(REQUEST).encode() not in resp.content

    def it_keeps_the_new_code_button_and_the_controls_on_the_class_tab(client, member_user):
        mine = _own_offering(member_user)
        code = DiscountCodeFactory(code="MINE10", class_offering=mine, created_by=member_user)
        client.force_login(member_user)

        resp = client.get(reverse("classes:teach_class_discount_codes", kwargs={"pk": mine.pk}))

        assert resp.status_code == 200
        assert f"{reverse('classes:teach_discount_code_create')}?class={mine.pk}".encode() in resp.content
        assert reverse("classes:teach_discount_code_edit", kwargs={"pk": code.pk}).encode() in resp.content
        assert reverse(REQUEST).encode() not in resp.content

    def it_redirects_the_request_route_with_an_info_message(client, member_user):
        client.force_login(member_user)

        resp = client.get(reverse(REQUEST))

        assert resp.status_code == 302
        assert resp["Location"] == reverse(DISCOUNT_CODES)
        assert "Discount codes do not need approval right now. Use New Code instead." in _messages(resp)


def describe_the_master_switch():
    def it_redirects_the_request_route_to_the_dashboard_when_instructor_discount_codes_are_off(client, member_user):
        _master(False)
        _approval_mode(True)
        client.force_login(member_user)

        resp = client.get(reverse(REQUEST))

        assert resp.status_code == 302
        assert resp["Location"] == reverse("classes:teach_dashboard")
