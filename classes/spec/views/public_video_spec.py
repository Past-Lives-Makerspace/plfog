"""BDD specs for the video block on the public class page (#368 item 8).

YouTube keeps its no-cookie iframe. Instagram and Facebook get a linked card instead:
no iframe to their hosts, no third party script, and a line that says the video opens
on their site.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory, InstructorFactory
from classes.models import ClassOffering

INSTAGRAM_URL = "https://www.instagram.com/reel/CxYzAbCdEfG/"
FACEBOOK_URL = "https://www.facebook.com/watch/?v=1234567890"


@pytest.fixture
def class_with_video(db):
    """A published class whose video_url the test sets, ready to GET."""

    def _make(video_url: str) -> ClassOffering:
        offering = ClassOfferingFactory(
            title="Forge Night",
            slug="forge-night",
            category=CategoryFactory(name="Blacksmithing", slug="blacksmithing"),
            instructor=InstructorFactory(full_legal_name="Wren", instructor_slug="wren"),
            status=ClassOffering.Status.PUBLISHED,
            video_url=video_url,
        )
        start = timezone.now() + timedelta(days=7)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        return offering

    return _make


def _page(client, offering: ClassOffering) -> str:
    response = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
    assert response.status_code == 200
    return response.content.decode()


def describe_public_class_video():
    def describe_youtube():
        def it_embeds_the_no_cookie_player(class_with_video, client):
            body = _page(client, class_with_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
            iframe = re.search(r"<iframe[^>]*youtube-nocookie\.com/embed/dQw4w9WgXcQ[^>]*>", body)
            assert iframe, "the YouTube iframe did not render"
            assert 'referrerpolicy="strict-origin-when-cross-origin"' in iframe.group(0)
            assert "pl-video-card" not in body

        def it_keeps_the_watch_heading(class_with_video, client):
            assert ">Watch<" in _page(client, class_with_video("https://youtu.be/dQw4w9WgXcQ"))

    def describe_instagram():
        def it_renders_a_linked_card_and_no_iframe(class_with_video, client):
            body = _page(client, class_with_video(INSTAGRAM_URL))
            card = re.search(r'<a class="pl-video-card".*?</a>', body, re.S)
            assert card, "the Instagram card did not render"
            markup = card.group(0)
            assert f'href="{INSTAGRAM_URL}"' in markup
            assert 'target="_blank"' in markup
            assert 'rel="noopener noreferrer"' in markup
            assert "Watch this video on Instagram" in markup
            # The hint shows the real destination, so the card cannot borrow a name.
            assert "Opens www.instagram.com/reel/CxYzAbCdEfG in a new tab." in markup
            assert "instagram.com/embed" not in body
            assert "<iframe" not in body

        def it_loads_no_third_party_script(class_with_video, client):
            body = _page(client, class_with_video(INSTAGRAM_URL))
            assert "instagram.com/embed.js" not in body
            assert "connect.facebook.net" not in body

    def describe_facebook():
        def it_renders_a_linked_card_and_no_iframe(class_with_video, client):
            body = _page(client, class_with_video(FACEBOOK_URL))
            card = re.search(r'<a class="pl-video-card".*?</a>', body, re.S)
            assert card, "the Facebook card did not render"
            markup = card.group(0)
            assert f'href="{FACEBOOK_URL}"' in markup
            assert "Watch this video on Facebook" in markup
            assert "Opens www.facebook.com/watch in a new tab." in markup
            assert "<iframe" not in body

    def describe_when_there_is_no_video():
        def it_renders_no_watch_section(class_with_video, client):
            body = _page(client, class_with_video(""))
            assert ">Watch<" not in body
            assert "pl-video-card" not in body

        def it_renders_nothing_for_a_link_no_provider_owns(class_with_video, client):
            # Only reachable for a row saved before the validator, or by a direct DB write.
            body = _page(client, class_with_video("https://vimeo.com/12345"))
            assert ">Watch<" not in body
            assert "vimeo.com" not in body
