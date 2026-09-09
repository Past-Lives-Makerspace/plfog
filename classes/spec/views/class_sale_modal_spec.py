"""BDD specs for the Put This Class On Sale modal and the sale pill (teach and admin manage class pages)."""

from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering
from membership.models import Member, MembershipPlan

Status = ClassOffering.Status


def _messages(response) -> list[str]:
    return [m.message for m in get_messages(response.wsgi_request)]


@pytest.fixture
def instructor_fixture(db):
    user = UserFactory(username="sale-teacher@example.com")
    return InstructorFactory(user=user, full_legal_name="Teacher S", instructor_slug="teacher-s")


@pytest.fixture
def other_instructor(db):
    user = UserFactory(username="sale-other@example.com")
    return InstructorFactory(user=user, full_legal_name="Other S", instructor_slug="other-s")


@pytest.fixture
def plain_member_user(db):
    """An active member with no teaching access at all."""
    plan, _ = MembershipPlan.objects.get_or_create(name="Standard", defaults={"monthly_price": "50.00"})
    user = UserFactory(username="sale-plain@example.com")
    member = Member.objects.get(user=user)
    member.status = Member.Status.ACTIVE
    member.membership_plan = plan
    member.instructor_oriented_at = None
    member.save()
    return user


def _on(**extra) -> dict:
    data = {
        "action": "on",
        "sale_kind": "percent",
        "sale_percent": "20",
        "sale_amount_cents": "",
        "sale_banner_text": "",
        "sale_allow_discount_codes": "",
    }
    data.update(extra)
    return data


def describe_teach_manage_class_page():
    def it_offers_to_set_up_a_sale_on_a_priced_class(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, price_cents=10000)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})).content.decode()
        assert "Set Up a Sale" in html
        assert "Put This Class On Sale" in html
        assert f'action="{reverse("classes:teach_class_sale", kwargs={"pk": offering.pk})}"' in html
        assert "Turn the Sale On" in html
        assert "Turn the Sale Off" not in html
        assert "pl-sale-pill" not in html
        assert "How Much Off?" in html
        assert "Allow discount codes on top" in html

    def it_offers_to_edit_a_live_sale_and_shows_the_pill(instructor_fixture, client):
        offering = ClassOfferingFactory(
            instructor=instructor_fixture, status=Status.DRAFT, price_cents=10000, sale_enabled=True, sale_percent=20
        )
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})).content.decode()
        assert "Edit the Sale" in html
        assert "Save Sale Changes" in html and "Turn the Sale Off" in html
        assert 'class="pl-lifecycle-badge pl-sale-pill">Sale: 20% off</span>' in html

    def it_shows_the_pill_on_every_sub_tab(instructor_fixture, client):
        offering = ClassOfferingFactory(
            instructor=instructor_fixture,
            status=Status.PUBLISHED,
            price_cents=10000,
            sale_enabled=True,
            sale_kind=ClassOffering.SaleKind.FIXED,
            sale_amount_cents=1500,
        )
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_registrations", kwargs={"pk": offering.pk})).content.decode()
        assert "Sale: $15 off" in html

    def it_says_a_free_class_cannot_go_on_sale(instructor_fixture, client):
        offering = ClassOfferingFactory(
            instructor=instructor_fixture, status=Status.DRAFT, price_cents=0, member_discount_pct=0
        )
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})).content.decode()
        assert "A free class cannot go on sale." in html
        assert "Set Up a Sale" not in html
        assert "Put This Class On Sale" not in html

    def it_offers_no_sale_on_a_cancelled_class(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.CANCELLED, price_cents=10000)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})).content.decode()
        assert "Set Up a Sale" not in html
        assert "Put This Class On Sale" not in html


def describe_teach_class_sale():
    def it_turns_the_sale_on_and_the_pill_appears(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, price_cents=10000)
        client.force_login(instructor_fixture.user)
        url = reverse("classes:teach_class_sale", kwargs={"pk": offering.pk})
        resp = client.post(url, _on())
        assert resp.status_code == 302
        assert resp["Location"] == reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})
        assert "Sale is on. Members see it now." in _messages(resp)
        offering.refresh_from_db()
        assert offering.sale_is_active is True
        assert offering.sale_percent == 20
        html = client.get(resp["Location"]).content.decode()
        assert "Sale: 20% off" in html

    def it_updates_a_live_sale(instructor_fixture, client):
        offering = ClassOfferingFactory(
            instructor=instructor_fixture, status=Status.DRAFT, price_cents=10000, sale_enabled=True, sale_percent=20
        )
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_sale", kwargs={"pk": offering.pk}), _on(sale_percent="30"))
        assert "Sale updated." in _messages(resp)
        offering.refresh_from_db()
        assert offering.sale_percent == 30 and offering.sale_enabled is True

    def it_turns_the_sale_off_and_the_pill_disappears(instructor_fixture, client):
        offering = ClassOfferingFactory(
            instructor=instructor_fixture, status=Status.DRAFT, price_cents=10000, sale_enabled=True, sale_percent=20
        )
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_sale", kwargs={"pk": offering.pk}), {"action": "off"})
        assert resp.status_code == 302
        assert "Sale is off." in _messages(resp)
        offering.refresh_from_db()
        assert offering.sale_enabled is False
        assert offering.sale_percent == 20
        html = client.get(resp["Location"]).content.decode()
        assert "pl-sale-pill" not in html
        assert "Set Up a Sale" in html

    def it_turns_off_a_sale_with_stale_amounts_without_validating(instructor_fixture, client):
        offering = ClassOfferingFactory(
            instructor=instructor_fixture,
            status=Status.DRAFT,
            price_cents=1000,
            sale_enabled=True,
            sale_kind=ClassOffering.SaleKind.FIXED,
            sale_amount_cents=5000,
        )
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_sale", kwargs={"pk": offering.pk}),
            {"action": "off", "sale_kind": "fixed", "sale_amount_cents": "50.00"},
        )
        assert resp.status_code == 302
        offering.refresh_from_db()
        assert offering.sale_enabled is False

    def it_reopens_the_modal_with_the_error_on_an_invalid_sale(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, price_cents=10000)
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_sale", kwargs={"pk": offering.pk}), _on(sale_percent="0"))
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "$dispatch('open-modal', 'class-sale')" in html
        assert "Enter the percent off (1–99)." in html
        offering.refresh_from_db()
        assert offering.sale_enabled is False

    def it_refuses_a_crafted_sale_on_a_free_class(instructor_fixture, client):
        offering = ClassOfferingFactory(
            instructor=instructor_fixture, status=Status.DRAFT, price_cents=0, member_discount_pct=0
        )
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_sale", kwargs={"pk": offering.pk}), _on())
        assert resp.status_code == 200
        assert "A free class can" in resp.content.decode()
        offering.refresh_from_db()
        assert offering.sale_enabled is False

    def it_404s_another_instructors_class(instructor_fixture, other_instructor, client):
        theirs = ClassOfferingFactory(instructor=other_instructor, status=Status.DRAFT, price_cents=10000)
        client.force_login(instructor_fixture.user)
        resp = client.post(reverse("classes:teach_class_sale", kwargs={"pk": theirs.pk}), _on())
        assert resp.status_code == 404
        theirs.refresh_from_db()
        assert theirs.sale_enabled is False

    def it_sends_a_plain_member_to_the_marketing_page(plain_member_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT, price_cents=10000)
        client.force_login(plain_member_user)
        resp = client.post(reverse("classes:teach_class_sale", kwargs={"pk": offering.pk}), _on())
        assert resp.status_code == 302
        assert resp["Location"] == reverse("classes:teach_overview")
        offering.refresh_from_db()
        assert offering.sale_enabled is False

    def it_redirects_anonymous_to_login(db, client):
        offering = ClassOfferingFactory(status=Status.DRAFT, price_cents=10000)
        resp = client.post(reverse("classes:teach_class_sale", kwargs={"pk": offering.pk}), _on())
        assert resp.status_code == 302
        assert "login" in resp["Location"]

    def it_is_post_only(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, price_cents=10000)
        client.force_login(instructor_fixture.user)
        assert client.get(reverse("classes:teach_class_sale", kwargs={"pk": offering.pk})).status_code == 405


def describe_admin_class_sale():
    def it_shows_the_trigger_and_the_pill(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT, price_cents=10000, sale_enabled=True, sale_percent=25)
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_detail", kwargs={"pk": offering.pk})).content.decode()
        assert "Edit the Sale" in html
        assert "Sale: 25% off" in html
        assert f'action="{reverse("classes:admin_class_sale", kwargs={"pk": offering.pk})}"' in html

    def it_turns_the_sale_on(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT, price_cents=10000)
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:admin_class_sale", kwargs={"pk": offering.pk}),
            _on(sale_kind="fixed", sale_amount_cents="15.00"),
        )
        assert resp["Location"] == reverse("classes:admin_class_detail", kwargs={"pk": offering.pk})
        offering.refresh_from_db()
        assert offering.sale_is_active is True
        assert offering.sale_amount_cents == 1500

    def it_turns_the_sale_off(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT, price_cents=10000, sale_enabled=True, sale_percent=25)
        client.force_login(admin_user)
        resp = client.post(reverse("classes:admin_class_sale", kwargs={"pk": offering.pk}), {"action": "off"})
        assert "Sale is off." in _messages(resp)
        offering.refresh_from_db()
        assert offering.sale_enabled is False

    def it_reopens_the_modal_on_an_invalid_sale(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT, price_cents=10000)
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:admin_class_sale", kwargs={"pk": offering.pk}),
            _on(sale_kind="fixed", sale_amount_cents="100.00"),
        )
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "$dispatch('open-modal', 'class-sale')" in html
        assert "The amount off must be less than the price." in html

    def it_403s_a_member(member_user, client, db):
        offering = ClassOfferingFactory(status=Status.DRAFT, price_cents=10000)
        client.force_login(member_user)
        resp = client.post(reverse("classes:admin_class_sale", kwargs={"pk": offering.pk}), _on())
        assert resp.status_code == 403
        offering.refresh_from_db()
        assert offering.sale_enabled is False

    def it_offers_no_sale_on_a_completed_class(admin_user, client, db):
        offering = ClassOfferingFactory(status=Status.PUBLISHED, price_cents=10000)
        from datetime import timedelta

        from classes.factories import ClassSessionFactory

        past = timezone.now() - timedelta(days=3)
        ClassSessionFactory(class_offering=offering, starts_at=past, ends_at=past + timedelta(hours=2))
        client.force_login(admin_user)
        html = client.get(reverse("classes:admin_class_detail", kwargs={"pk": offering.pk})).content.decode()
        assert "Set Up a Sale" not in html
