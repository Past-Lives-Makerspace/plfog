"""What search engines read off the tour booking page (#1259): one address, the root, and a share card."""

from __future__ import annotations

import re
from typing import Any

import pytest
from django.test import Client

from membership.models import Member
from tests.tours.factories import TourHoursFactory

pytestmark = pytest.mark.django_db

HOST = "tours.pastlives.space"
NOINDEX = '<meta name="robots" content="noindex">'


@pytest.fixture(autouse=True)
def hours(guide: Member) -> Any:
    return TourHoursFactory(guide=guide)


def _page(path: str) -> str:
    response = Client(HTTP_HOST=HOST).get(path, secure=True)
    assert response.status_code == 200
    return response.content.decode()


def describe_the_booking_page():
    def it_is_indexable_at_the_root_with_a_share_card():
        html = _page("/")

        assert re.findall(r'<link rel="canonical" href="([^"]*)">', html) == [f"https://{HOST}/"]
        assert NOINDEX not in html
        assert "<title>Book a Tour | Past Lives Makerspace</title>" in html
        assert 'property="og:title" content="Book a Tour | Past Lives Makerspace"' in html
        assert f'property="og:url" content="https://{HOST}/"' in html
        assert 'property="og:image" content="https://' in html

    @pytest.mark.parametrize("path", ["/?date=2020-01-01", "/tour/?tour=abc"])
    def it_points_a_shared_variant_at_the_root_and_keeps_it_out_of_search(path: str):
        html = _page(path)

        assert re.findall(r'<link rel="canonical" href="([^"]*)">', html) == [f"https://{HOST}/"]
        assert NOINDEX in html
