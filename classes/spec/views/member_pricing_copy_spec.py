"""Member-pricing copy must read 'Past Lives Members' — never the 'PL' abbreviation.

The assertions are scoped to the member-rate label markup (``cls-price-member`` on the
catalog card, ``cp-detail__price-member`` on the detail rail). A blanket
``"PL members" not in body`` check is unsafe here: the global changelog modal renders
the historical v2.2.0 release note (which quotes the old "for PL members" copy) into the
DOM of every page, so the guard must target the live pricing label, not the whole body.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
)
from classes.models import ClassOffering

CATALOG_LABEL = re.compile(r'<span class="cls-price-member">(.*?)</span>')
DETAIL_LABEL = re.compile(r'<div class="cp-detail__price-member">(.*?)</div>')


@pytest.fixture
def published_class(db):
    category = CategoryFactory(name="Ceramics", slug="ceramics")
    instructor = InstructorFactory(full_legal_name="Deenie", instructor_slug="deenie")
    offering = ClassOfferingFactory(
        title="Intro to Wheel Throwing",
        slug="intro-to-wheel-throwing",
        category=category,
        instructor=instructor,
        status=ClassOffering.Status.PUBLISHED,
        price_cents=5000,
        member_discount_pct=10,
    )
    ClassSessionFactory(
        class_offering=offering,
        starts_at=timezone.now() + timedelta(days=7),
        ends_at=timezone.now() + timedelta(days=7, hours=2),
    )
    return offering


def describe_member_pricing_copy():
    def it_uses_full_name_on_the_catalog_card(published_class, client):
        body = client.get(reverse("classes:public_list")).content.decode()
        label = CATALOG_LABEL.search(body)
        assert label is not None
        assert "Past Lives Members" in label.group(1)
        assert "PL members" not in label.group(1)

    def it_uses_full_name_on_the_detail_rail(published_class, client):
        url = reverse("classes:public_class_detail", kwargs={"slug": published_class.slug})
        body = client.get(url).content.decode()
        label = DETAIL_LABEL.search(body)
        assert label is not None
        assert "Past Lives Members" in label.group(1)
        assert "PL members" not in label.group(1)

    def it_still_shows_the_discounted_member_price(published_class, client):
        body = client.get(reverse("classes:public_list")).content.decode()
        label = CATALOG_LABEL.search(body)
        assert published_class.member_price_cents == 4500
        assert label is not None
        assert "$45" in label.group(1)


def describe_sale_markup():
    @pytest.fixture
    def sale_class(published_class):
        published_class.sale_enabled = True
        published_class.sale_kind = ClassOffering.SaleKind.PERCENT
        published_class.sale_percent = 20  # $50 -> $40
        published_class.sale_banner_text = "Summer blowout!"
        published_class.save()
        return published_class

    def it_renders_the_sale_badge_and_struck_price_on_the_catalog_card(sale_class, client):
        body = client.get(reverse("classes:public_list")).content.decode()
        assert '<span class="badge sale">Sale</span>' in body
        assert '<span class="cls-price--was">$50</span>' in body
        assert '<span class="cls-price">$40</span>' in body

    def it_bases_the_catalog_member_price_on_the_sale_price(sale_class, client):
        body = client.get(reverse("classes:public_list")).content.decode()
        label = CATALOG_LABEL.search(body)
        assert label is not None
        assert "$36" in label.group(1)  # 10% off the $40 sale price

    def it_renders_the_sale_banner_and_struck_rail_price_on_the_detail_page(sale_class, client):
        url = reverse("classes:public_class_detail", kwargs={"slug": sale_class.slug})
        body = client.get(url).content.decode()
        assert 'class="cp-detail__sale-banner"' in body
        assert "Summer blowout!" in body
        assert "20% off" in body
        assert '<div class="cp-detail__price--was">$50</div>' in body
        assert '<div class="cp-detail__price">$40</div>' in body

    def it_bases_the_detail_member_price_on_the_sale_price(sale_class, client):
        url = reverse("classes:public_class_detail", kwargs={"slug": sale_class.slug})
        body = client.get(url).content.decode()
        label = DETAIL_LABEL.search(body)
        assert label is not None
        assert "$36" in label.group(1)

    def it_renders_the_struck_original_in_the_register_summary(sale_class, client):
        body = client.get(reverse("classes:register", kwargs={"slug": sale_class.slug})).content.decode()
        assert '<span class="reg-was">$50</span> $40' in body

    def it_hides_all_sale_markup_when_no_sale_is_active(published_class, client):
        body = client.get(reverse("classes:public_list")).content.decode()
        assert "badge sale" not in body
        assert "cls-price--was" not in body
        url = reverse("classes:public_class_detail", kwargs={"slug": published_class.slug})
        detail = client.get(url).content.decode()
        assert "cp-detail__sale-banner" not in detail
