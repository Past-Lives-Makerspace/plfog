"""BDD specs for #1042 part 2: the class card a past students email can carry.

Covers which classes may be featured (the instructor's published classes with an upcoming session
or flexible scheduling) and what the card reads off a class: photo, title, short description, next
date or "Flexible scheduling", price (the sale price during a sale) and the Register link.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering
from classes.past_students import ClassCard, featurable_classes
from core.urls_util import book_absolute_url
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db

_PUBLISHED = ClassOffering.Status.PUBLISHED


def _teacher(username: str = "teacher") -> Member:
    MembershipPlanFactory()
    return User.objects.create_user(username=username, email=f"{username}@example.com", password="x").member


def _class(instructor: Member, *, days: int | None = 3, **fields: object) -> ClassOffering:
    values = {"image": "", "gallery": 0, "status": _PUBLISHED, **fields}
    offering = ClassOfferingFactory(instructor=instructor, **values)
    if days is not None:
        start = timezone.now() + timedelta(days=days)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def describe_featurable_classes():
    def it_offers_published_classes_with_an_upcoming_session_or_flexible_scheduling():
        teacher = _teacher()
        dated = _class(teacher, title="B Dated")
        flexible = _class(
            teacher, days=None, title="A Flexible", scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE
        )
        assert list(featurable_classes(teacher)) == [flexible, dated]

    def it_leaves_out_past_undated_unpublished_and_other_instructors_classes():
        teacher = _teacher()
        _class(teacher, days=-3)
        _class(teacher, days=None)
        _class(teacher, status=ClassOffering.Status.DRAFT)
        _class(_teacher("other"))
        assert list(featurable_classes(teacher)) == []

    def it_lists_a_class_with_several_upcoming_sessions_once():
        teacher = _teacher()
        offering = _class(teacher)
        ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=9))
        assert list(featurable_classes(teacher)) == [offering]


def describe_ClassCard():
    def it_reads_the_title_description_next_date_price_and_page():
        offering = _class(
            _teacher(),
            title="Lost Wax Casting",
            description="Carve a ring in wax and cast it in silver.",
            price_cents=8500,
        )
        card = ClassCard.for_offering(offering)
        session = offering.sessions.get()
        assert card.title == "Lost Wax Casting"
        assert card.blurb == "Carve a ring in wax and cast it in silver."
        assert card.when == timezone.localtime(session.starts_at).strftime("%A, %B %-d at %-I:%M %p")
        assert card.price == "$85"
        assert card.url == book_absolute_url(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))

    def it_says_flexible_scheduling_for_a_flexible_class():
        offering = _class(_teacher(), days=None, scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
        assert ClassCard.for_offering(offering).when == "Flexible scheduling"

    def it_says_dates_tba_once_every_session_has_passed():
        offering = _class(_teacher(), days=-1)
        assert ClassCard.for_offering(offering).when == "Upcoming dates TBA"

    def it_shows_the_sale_price_during_a_sale():
        offering = _class(
            _teacher(), price_cents=10000, sale_enabled=True, sale_kind=ClassOffering.SaleKind.PERCENT, sale_percent=20
        )
        assert ClassCard.for_offering(offering).price == "$80"

    def it_makes_a_stored_photo_absolute():
        offering = _class(_teacher(), image="classes/ring.jpg")
        card = ClassCard.for_offering(offering)
        assert card.image_url.startswith("http")
        assert card.image_url.endswith("ring.jpg")

    def it_has_no_photo_when_neither_the_class_nor_its_category_has_one():
        offering = _class(_teacher())
        assert ClassCard.for_offering(offering).image_url == ""

    def it_renders_the_email_card_and_its_text():
        offering = _class(_teacher(), title="Ring Making", price_cents=0)
        card = ClassCard.for_offering(offering)
        html = card.html()
        assert "data-class-card" in html
        assert "Ring Making" in html
        assert f'href="{card.url}"' in html
        assert ">Register</a>" in html
        assert card.text() == f"Ring Making\n{card.when} · Free\n{card.blurb}\nRegister: {card.url}"
