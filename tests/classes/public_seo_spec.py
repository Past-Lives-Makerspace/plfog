"""What search engines read off the class pages (#1259): canonical, noindex, titles, descriptions, Event data."""

from __future__ import annotations

import json
import re
from datetime import date, timedelta

import pytest
from django.test import Client
from django.utils import timezone

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    RegistrationFactory,
)
from classes.models import ClassOffering

pytestmark = pytest.mark.django_db

CLASSES = "classes.pastlives.space"
BASE = f"https://{CLASSES}"


@pytest.fixture(autouse=True)
def _hosts(settings) -> None:
    settings.ALLOWED_HOSTS = [CLASSES, "testserver"]
    settings.PUBLIC_HOSTS = [CLASSES]
    settings.BOOK_BASE_URL = BASE


def _class(slug: str, *, days: int, **kwargs) -> ClassOffering:
    kwargs.setdefault("status", ClassOffering.Status.PUBLISHED)
    # Titled after the slug so the class's address is the slug the spec requests (#1260).
    kwargs.setdefault("title", slug.replace("-", " ").title())
    offering = ClassOfferingFactory(slug=slug, **kwargs)
    start = timezone.now() + timedelta(days=days)
    ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def _get(client: Client, path: str) -> str:
    response = client.get(path, HTTP_HOST=CLASSES, secure=True)
    assert response.status_code == 200
    return response.content.decode()


def _title(html: str) -> str:
    return re.search(r"<title>(.*?)</title>", html, re.S)[1]  # type: ignore[index]


def _canonicals(html: str) -> list[str]:
    return re.findall(r'<link rel="canonical" href="([^"]*)">', html)


def _descriptions(html: str) -> list[str]:
    return re.findall(r'<meta name="description" content="([^"]*)">', html)


def _json_ld(html: str) -> dict:
    return json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)[1])  # type: ignore[index]


NOINDEX = '<meta name="robots" content="noindex">'


def describe_the_catalog():
    def it_has_one_canonical_without_filters(client: Client):
        html = _get(client, "/classes/?guild=wood&within=7")

        assert _canonicals(html) == [f"{BASE}/classes/"]
        assert NOINDEX in html

    def it_keeps_the_page_number_past_the_first_and_says_so(client: Client):
        for n in range(26):
            _class(f"class-{n}", days=n + 1)

        html = _get(client, "/classes/?page=2")

        assert _canonicals(html) == [f"{BASE}/classes/?page=2"]
        assert NOINDEX not in html
        assert "Page 2" in _title(html)
        assert _descriptions(html)[0].endswith("Page 2.")

    def it_is_indexable_unfiltered_with_the_brand_once(client: Client):
        html = _get(client, "/classes/")

        assert NOINDEX not in html
        assert _title(html) == "Classes — Past Lives Makerspace"


def describe_a_class_type_page():
    def it_has_its_own_canonical_title_and_description(client: Client):
        CategoryFactory(name="Blacksmithing", slug="blacksmithing")

        html = _get(client, "/classes/category/blacksmithing/")

        assert _canonicals(html) == [f"{BASE}/classes/category/blacksmithing/"]
        assert _title(html) == "Blacksmithing Classes — Past Lives Makerspace"
        assert _descriptions(html)[0].startswith("Blacksmithing classes at Past Lives Makerspace")
        assert NOINDEX not in html

    def it_is_noindex_once_filtered(client: Client):
        CategoryFactory(name="Blacksmithing", slug="blacksmithing")

        assert NOINDEX in _get(client, "/classes/category/blacksmithing/?upcoming=1")


def describe_an_upcoming_class():
    def it_is_indexable_with_its_canonical_and_one_title(client: Client):
        _class("forge-basics", days=5, title="Forge Basics")

        html = _get(client, "/classes/forge-basics/?utm_source=discord")

        assert _canonicals(html) == [f"{BASE}/classes/forge-basics/"]
        assert NOINDEX not in html
        assert _title(html).count("Past Lives") <= 1
        assert len(_title(html)) <= 60

    def it_carries_event_json_ld_and_open_graph(client: Client):
        offering = _class("forge-basics", days=5, title="Forge Basics", price_cents=7500, capacity=4)

        html = _get(client, "/classes/forge-basics/")
        event = _json_ld(html)

        assert event["@type"] == "Event"
        assert event["name"] == "Forge Basics"
        assert event["startDate"] == offering.sessions.get().starts_at.isoformat()
        assert event["endDate"] == offering.sessions.get().ends_at.isoformat()
        assert event["location"]["address"]["streetAddress"] == "2808 SE 9th Ave"
        assert event["offers"] == {
            "@type": "Offer",
            "price": "75.00",
            "priceCurrency": "USD",
            "availability": "https://schema.org/InStock",
            "url": f"{BASE}/classes/forge-basics/",
        }
        assert event["image"][0].startswith(BASE)
        assert 'property="og:title" content="Forge Basics' in html
        assert f'property="og:url" content="{BASE}/classes/forge-basics/"' in html
        assert 'property="og:image" content="https://' in html


def describe_a_past_class():
    def it_is_noindex_and_carries_no_event(client: Client):
        _class("open-forge-sundays", days=-30)

        html = _get(client, "/classes/open-forge-sundays/")

        assert NOINDEX in html
        assert "application/ld+json" not in html


def describe_the_transactional_pages():
    @pytest.mark.parametrize("suffix", ["register/", "register/success/", "register/cancelled/"])
    def it_keeps_registration_out_of_search(client: Client, suffix: str):
        _class("forge-basics", days=5)

        assert NOINDEX in _get(client, f"/classes/forge-basics/{suffix}")

    def it_keeps_a_registration_page_out_of_search(client: Client):
        registration = RegistrationFactory(class_offering=_class("forge-basics", days=5))

        assert NOINDEX in _get(client, f"/classes/my/{registration.self_serve_token}/")


def describe_an_instructor_page():
    def it_describes_the_instructor_from_their_bio(client: Client):
        InstructorFactory(instructor_slug="ada", instructor_bio="Ada forges <b>knives</b> and hooks.")

        html = _get(client, "/classes/instructors/ada/")

        assert _descriptions(html) == ["Ada forges knives and hooks."]
        assert _title(html).count("Past Lives") == 1
        assert _canonicals(html) == [f"{BASE}/classes/instructors/ada/"]

    def it_falls_back_to_a_sentence_with_their_name(client: Client):
        instructor = InstructorFactory(instructor_slug="ada", instructor_bio="")

        html = _get(client, "/classes/instructors/ada/")

        assert _descriptions(html) == [
            f"Classes taught by {instructor.public_name} at Past Lives Makerspace in Portland, OR."
        ]


def describe_the_class_title():
    def it_drops_an_instructor_the_title_already_names():
        instructor = InstructorFactory(full_legal_name="Anjali Figueira")
        offering = _class("cup", days=5, title="Make a Cup w/ Anjali", instructor=instructor)

        assert "Figueira" not in offering.seo_title
        assert offering.seo_title.startswith("Make a Cup w/ Anjali — ")


def describe_is_indexable():
    def it_leaves_out_a_private_class():
        assert _class("by-invite", days=5, is_private=True).is_indexable is False


def describe_event_json_ld():
    def it_marks_a_full_class_sold_out():
        offering = _class("full", days=5, capacity=1)
        RegistrationFactory(class_offering=offering)

        assert offering.event_json_ld()["offers"]["availability"] == "https://schema.org/SoldOut"  # type: ignore[index]

    def it_spans_a_flexible_window():
        offering = ClassOfferingFactory(
            scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
            flexible_starts_on=date(2027, 1, 4),
            flexible_ends_on=None,
            image="",
        )

        event = offering.event_json_ld()

        assert event is not None
        assert event["startDate"] == "2027-01-04"
        assert "endDate" not in event
        assert "image" not in event

    def it_has_no_event_without_a_start():
        offering = ClassOfferingFactory(scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)

        assert offering.event_json_ld() is None

    def it_keeps_a_storage_image_url(monkeypatch):
        offering = _class("forge", days=5)
        monkeypatch.setattr(ClassOffering, "hero_image_url", property(lambda self: "https://cdn.example/hero.jpg"))

        assert offering.event_json_ld()["image"] == ["https://cdn.example/hero.jpg"]  # type: ignore[index]
