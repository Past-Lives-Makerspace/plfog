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
