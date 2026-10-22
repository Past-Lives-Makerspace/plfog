"""End-to-end: a Community Event's flyer (hub_event_flyer) fits its sheet and prints on one Letter page.

The worst case the composer allows: a photo, a 200 character title, a 200 character place, a 200 character pitch, a
repeating series and an RSVP goal. Every line of the sheet must fit its body with nothing cut: the title steps down by
its length rather than clamping, the place stops at two lines, the goal sits above the pitch, and the pitch, the one
line that may give way, is not squeezed. The title carries no clamp, so a body that holds every line holds it
whole. Then the page prints as one Letter page. A short, ordinary event is checked
the same way. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import io
import re
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image, ImageOps
from playwright.sync_api import Page, expect

from membership.models import CommunityEvent
from tests.membership.factories import CommunityEventFactory

PDF_PAGE = re.compile(rb"/Type\s*/Page(?!s)")
TITLE = ("Holiday Bizarre Winter Makers Market, Open Studios, Guild Demos and Wine Wall Night " * 3)[:200]
PLACE = ("Main Hall and the back courtyard behind the ceramics studio, enter through the big roll up door " * 3)[:200]
PITCH = ("Bring friends and family for a night of handmade gifts from forty local makers, live music, hot cider " * 3)[
    :200
]

#: Where each part of the sheet sits, and whether the body holds every line at its own height (the pitch's
#: shrink turned off for the measure, so a squeezed pitch shows as an overflow).
MEASURE = """() => {
    const body = document.querySelector('.pl-evfl__body');
    const goal = document.querySelector('[data-evfl-goal]');
    const pitch = document.querySelector('[data-evfl-pitch]');
    pitch.style.flexShrink = '0';
    const fits = body.scrollHeight <= body.clientHeight + 1;
    const inside = goal.getBoundingClientRect().bottom <= body.getBoundingClientRect().bottom;
    const over = body.scrollHeight - body.clientHeight;
    pitch.style.flexShrink = '';
    return {fits, inside, over: fits ? 0 : over};
}"""


def _photo() -> SimpleUploadedFile:
    buffer = io.BytesIO()
    shade = Image.linear_gradient("L").rotate(90).resize((1600, 900))
    ImageOps.colorize(shade, black=(9, 46, 76), white=(238, 180, 75)).save(buffer, format="JPEG")
    return SimpleUploadedFile("hero.jpg", buffer.getvalue(), content_type="image/jpeg")


def _worst_case() -> CommunityEvent:
    start = timezone.now() + timedelta(days=14)
    return CommunityEventFactory(
        community=True,
        title=TITLE,
        location=PLACE,
        pitch=PITCH,
        rsvp_goal=150,
        recurrence=CommunityEvent.Recurrence.EVERY_2_MONTHS,
        starts_at=start,
        ends_at=start + timedelta(hours=2),
        photo=_photo(),
    )


def _assert_fits_and_prints_on_one_page(page: Page, url: str) -> None:
    page.set_viewport_size({"width": 1280, "height": 1400})
    page.goto(url)
    expect(page.locator(".pl-evfl__qr svg")).to_be_visible()
    page.wait_for_function("() => [...document.images].every((img) => img.complete && img.naturalWidth > 0)")
    assert page.evaluate(MEASURE) == {"fits": True, "inside": True, "over": 0}

    page.emulate_media(media="print")
    expect(page.locator(".pl-evfl-page__toolbar")).to_be_hidden()
    assert page.evaluate(MEASURE) == {"fits": True, "inside": True, "over": 0}
    pdf = page.pdf(format="Letter", print_background=True, prefer_css_page_size=True)
    assert len(PDF_PAGE.findall(pdf)) == 1, f"{url} printed {len(PDF_PAGE.findall(pdf))} pages"


def describe_the_event_flyer_in_print():
    def it_fits_the_longest_event_the_composer_allows_on_one_page(live_server, page: Page, serve_media):
        event = _worst_case()

        _assert_fits_and_prints_on_one_page(page, f"{live_server.url}{reverse('hub_event_flyer', args=[event.pk])}")

    @pytest.mark.parametrize("photo", [True, False])
    def it_fits_an_ordinary_event_on_one_page(live_server, page: Page, serve_media, photo):
        event = CommunityEventFactory(
            community=True,
            title="Holiday Bizarre",
            location="2808 SE 9th Ave",
            pitch="Handmade gifts, wine and music.",
            rsvp_goal=300,
            **({"photo": _photo()} if photo else {}),
        )

        _assert_fits_and_prints_on_one_page(page, f"{live_server.url}{reverse('hub_event_flyer', args=[event.pk])}")
