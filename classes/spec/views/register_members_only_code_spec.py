"""BDD specs for Members only codes on the register page and the admin Discount Codes pages (#1072).

The register page is the price refresh too: the code box's ``hx-get`` re-renders this page with
``?discount_code=``, so a GET with the code is the refresh, and a POST is the checkout.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    DiscountCodeFactory,
    InstructorFactory,
)
from classes.forms import MEMBERS_ONLY_REFUSAL
from classes.models import ClassOffering, DiscountCode, Registration
from membership.models import Member

pytestmark = pytest.mark.django_db

#: Signed in buyers who are not Active but still reach the register page: Invited, and Suspended
#: once an admin switches the suspended lockout off. The form spec covers every status directly.
REACHES_PAGE = [Member.Status.INVITED, Member.Status.SUSPENDED]
#: Statuses the lockout middleware sends to the locked page before checkout is ever shown.
LOCKED_OUT = [Member.Status.FORMER, Member.Status.GUEST, Member.Status.SUSPENDED]


@pytest.fixture
def suspended_not_locked_out(db):
    from core.models import SiteConfiguration

    config = SiteConfiguration.load()
    config.suspended_members_locked_out = False
    config.save(update_fields=["suspended_members_locked_out"])


@pytest.fixture
def offering(db):
    offering = ClassOfferingFactory(
        title="Lathe Night",
        slug="lathe-night",
        category=CategoryFactory(),
        instructor=InstructorFactory(),
        status=ClassOffering.Status.PUBLISHED,
        price_cents=10000,
        capacity=4,
    )
    starts = timezone.now() + timedelta(days=7)
    ClassSessionFactory(class_offering=offering, starts_at=starts, ends_at=starts + timedelta(hours=2))
    return offering


@pytest.fixture(autouse=True)
def member_code(db):
    return DiscountCodeFactory(code="MEMBER10", discount_pct=10, members_only=True)


def _sign_in(client, status: str) -> None:
    user = get_user_model().objects.create(username="buyer@example.com", email="buyer@example.com")
    Member.objects.filter(user=user).update(status=status)
    client.force_login(user)


def _url(offering) -> str:
    return reverse("classes:register", kwargs={"slug": offering.slug})


def _total(body: str) -> str:
    """The #reg-price-summary block alone, so the changelog modal can't leak in."""
    return body[body.index('id="reg-price-summary"') : body.index('id="reg-form"')]


def _refresh(client, offering) -> str:
    return client.get(_url(offering), {"email": "buyer@example.com", "discount_code": "MEMBER10"}).content.decode()


def _checkout(client, offering):
    data = {
        "first_name": "Sam",
        "last_name": "Smith",
        "pronouns": "",
        "email": "buyer@example.com",
        "phone": "",
        "prior_experience": "",
        "looking_for": "",
        "discount_code": "MEMBER10",
        "liability_signature": "Sam Smith",
        "accepts_liability": "on",
    }
    return client.post(_url(offering), data)


def describe_the_price_refresh():
    def it_quotes_full_price_to_a_signed_out_buyer(client, offering):
        body = _refresh(client, offering)
        assert '<div class="total">$100</div>' in _total(body)
        assert 'Next: <span id="reg-submit-label">$100</span>' in body

    @pytest.mark.parametrize("status", REACHES_PAGE)
    def it_quotes_full_price_to_a_signed_in_buyer_who_is_not_active(client, offering, status, suspended_not_locked_out):
        _sign_in(client, status)
        assert '<div class="total">$100</div>' in _total(_refresh(client, offering))

    def it_quotes_ten_percent_off_to_an_active_member(client, offering):
        _sign_in(client, Member.Status.ACTIVE)
        body = _refresh(client, offering)
        assert '<div class="total">$90</div>' in _total(body)
        assert 'Next: <span id="reg-submit-label">$90</span>' in body


def describe_checkout():
    def it_refuses_a_signed_out_buyer_under_the_code_box(client, offering):
        response = _checkout(client, offering)
        assert response.status_code == 200
        body = response.content.decode()
        assert f'<ul class="reg-errors"><li>{MEMBERS_ONLY_REFUSAL}</li></ul>' in body
        assert '<div class="total">$100</div>' in _total(body)
        assert not Registration.objects.filter(class_offering=offering).exists()

    @pytest.mark.parametrize("status", REACHES_PAGE)
    def it_refuses_a_signed_in_buyer_who_is_not_active(client, offering, status, suspended_not_locked_out):
        _sign_in(client, status)
        response = _checkout(client, offering)
        assert response.status_code == 200
        assert f"<li>{MEMBERS_ONLY_REFUSAL}</li>" in response.content.decode()
        assert not Registration.objects.filter(class_offering=offering).exists()

    @pytest.mark.parametrize("status", LOCKED_OUT)
    def it_never_shows_checkout_to_a_locked_out_buyer(client, offering, status):
        _sign_in(client, status)
        response = _checkout(client, offering)
        assert response.status_code == 302
        assert response.url.startswith(reverse("account_locked"))
        assert not Registration.objects.filter(class_offering=offering).exists()

    def it_charges_an_active_member_the_discounted_price(client, offering, member_code):
        _sign_in(client, Member.Status.ACTIVE)
        with patch("billing.stripe_utils.create_class_checkout_session") as stripe_checkout:
            stripe_checkout.return_value = {
                "id": "cs_test_1072",
                "url": "https://checkout.stripe.com/c/pay/cs_test_1072",
            }
            response = _checkout(client, offering)
        assert response.status_code == 302
        assert stripe_checkout.call_args.kwargs["amount_cents"] == 9000
        assert Registration.objects.get(class_offering=offering).discount_code == member_code


def describe_the_admin_pages():
    def it_offers_the_switch_on_create_and_edit_with_the_guidance_hook(admin_user, client, member_code):
        client.force_login(admin_user)
        for url in (
            reverse("classes:admin_discount_code_create"),
            reverse("classes:admin_discount_code_edit", kwargs={"pk": member_code.pk}),
        ):
            body = client.get(url).content.decode()
            assert 'data-guidance-target="classes.members-only"' in body
            assert 'name="members_only"' in body
            assert "Only signed in members with an active membership can use it." in body

    def it_saves_the_switch_on_create_and_clears_it_on_edit(admin_user, client):
        client.force_login(admin_user)
        create = reverse("classes:admin_discount_code_create")
        client.post(create, {"code": "GUILD15", "discount_pct": 15, "is_active": "on", "members_only": "on"})
        code = DiscountCode.objects.get(code="GUILD15")
        assert code.members_only is True
        client.post(
            reverse("classes:admin_discount_code_edit", kwargs={"pk": code.pk}),
            {"code": "GUILD15", "discount_pct": 15, "is_active": "on"},
        )
        code.refresh_from_db()
        assert code.members_only is False

    def it_tags_members_only_codes_in_the_list(admin_user, client, member_code):
        DiscountCodeFactory(code="ANYONE5")
        client.force_login(admin_user)
        body = client.get(reverse("classes:admin_discount_codes")).content.decode()
        assert 'MEMBER10 <span class="hub-pill hub-pill--neutral" data-members-only>Members only</span>' in body
        assert body.count("data-members-only") == 1
