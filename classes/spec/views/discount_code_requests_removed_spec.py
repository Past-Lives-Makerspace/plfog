"""Discount code requests and self approval are gone (#1185): instructors make codes directly (#1184).

The autouse ``_instructor_discount_codes_on`` fixture in ``classes/spec/conftest.py`` turns the
master flag on. Assertions anchor on URLs, never on button copy, because the changelog renders on
every page (STANDARDS.md section 8).
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.urls import reverse

from classes.factories import CategoryFactory, ClassOfferingFactory, DiscountCodeFactory
from classes.models import ClassOffering, DiscountCode
from core.models import SiteConfiguration
from membership.models import Member

DISCOUNT_CODES = "classes:teach_discount_codes"
REQUEST_PATH = "/classes/teach/discount-codes/request/"


def _own_offering(member_user) -> ClassOffering:
    return ClassOfferingFactory(category=CategoryFactory(), instructor=Member.objects.get(user=member_user))


def describe_the_old_request_urls():
    def it_404s_the_instructor_request_page(client, member_user):
        client.force_login(member_user)

        assert client.get(REQUEST_PATH).status_code == 404
        assert client.post(REQUEST_PATH, {"code": "EARLY"}).status_code == 404

    def it_404s_the_admin_review_page(client, admin_user):
        client.force_login(admin_user)

        assert client.get("/classes/admin/discount-codes/requests/1/").status_code == 404

    def it_404s_the_instructor_self_approve_route(client, member_user):
        code = DiscountCodeFactory(created_by=member_user, is_approved=False)
        client.force_login(member_user)

        resp = client.post(f"/classes/teach/discount-codes/{code.pk}/approve/")

        assert resp.status_code == 404
        code.refresh_from_db()
        assert code.is_approved is False


def describe_the_direct_flow():
    def it_lets_an_instructor_create_a_code(client, member_user):
        client.force_login(member_user)

        # Since #1184 a code needs a scope: here, every class the instructor teaches.
        resp = client.post(
            reverse("classes:teach_discount_code_create"),
            {"code": "DIRECT10", "discount_pct": 10, "applies_to": "mine"},
        )

        assert resp.status_code == 302
        assert DiscountCode.objects.filter(code="DIRECT10", created_by=member_user).exists()

    def it_shows_new_code_edit_and_delete_and_no_request_link(client, member_user):
        mine = DiscountCodeFactory(code="MINE10", created_by=member_user)
        client.force_login(member_user)

        resp = client.get(reverse(DISCOUNT_CODES))

        assert reverse("classes:teach_discount_code_edit", kwargs={"pk": mine.pk}).encode() in resp.content
        assert reverse("classes:teach_discount_code_delete", kwargs={"pk": mine.pk}).encode() in resp.content
        assert reverse("classes:teach_discount_code_create").encode() in resp.content
        assert REQUEST_PATH.encode() not in resp.content

    def it_keeps_the_new_code_button_and_the_controls_on_the_class_tab(client, member_user):
        mine = _own_offering(member_user)
        code = DiscountCodeFactory(code="MINE10", class_offering=mine, created_by=member_user)
        client.force_login(member_user)

        resp = client.get(reverse("classes:teach_class_discount_codes", kwargs={"pk": mine.pk}))

        assert resp.status_code == 200
        assert f"{reverse('classes:teach_discount_code_create')}?class={mine.pk}".encode() in resp.content
        assert reverse("classes:teach_discount_code_edit", kwargs={"pk": code.pk}).encode() in resp.content
        assert REQUEST_PATH.encode() not in resp.content

    def it_offers_the_new_code_link_on_the_composer_discounts_step(client, member_user):
        mine = _own_offering(member_user)
        client.force_login(member_user)

        resp = client.get(reverse("classes:teach_class_edit", kwargs={"pk": mine.pk}))

        assert b'data-composer-discounts="direct"' in resp.content
        assert f"{reverse('classes:teach_discount_code_create')}?class={mine.pk}".encode() in resp.content
        assert REQUEST_PATH.encode() not in resp.content


def describe_the_admin_list():
    def it_has_no_requests_queue(client, admin_user):
        client.force_login(admin_user)

        resp = client.get(reverse("classes:admin_discount_codes"))

        assert resp.status_code == 200
        assert b"/discount-codes/requests/" not in resp.content
        assert "pending_requests" not in resp.context


def describe_the_retired_settings():
    def it_drops_the_approval_setting_and_the_self_approve_permission():
        assert not hasattr(SiteConfiguration, "instructor_discount_codes_need_approval")
        assert not hasattr(Member, "can_self_approve_discounts")

    def it_leaves_request_a_code_out_of_every_template_and_the_help_content():
        root = Path(settings.BASE_DIR)
        files = [*(root / "templates").rglob("*.html"), root / "membership" / "help_content.py"]

        assert [str(path) for path in files if "request a code" in path.read_text().lower()] == []
