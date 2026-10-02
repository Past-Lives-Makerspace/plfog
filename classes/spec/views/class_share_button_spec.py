"""BDD specs for the Share button on the public class page.

The button carries the facts ``static/js/class_share.js`` reads as data attributes the
server renders (the class's readable public page, a title with any CMS date suffix
stripped, and the share text), and the Text and Email menu items point at the same link.
A preview renders no button, because the page is not public yet. Assertions anchor on
``data-share-url`` and the hrefs, never on copy: the changelog renders into every page.
"""

from __future__ import annotations

import re
from urllib.parse import quote

import pytest
from django.urls import reverse

from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering

pytestmark = pytest.mark.django_db

SHARE_URL_RE = re.compile(r'data-share-url="([^"]*)"')
TITLE = "Forge a Leaf Dish"
TEXT = "Forge a Leaf Dish at Past Lives Makerspace"


def _published() -> ClassOffering:
    # The CMS date suffix is what strip_date_suffix drops; the slug is explicit so the
    # factory never derives one from the slashes in the title.
    return ClassOfferingFactory(
        status=ClassOffering.Status.PUBLISHED, title="Forge a Leaf Dish - 10/5/26", slug="forge-a-leaf-dish"
    )


def _public_page(client, offering: ClassOffering) -> str:
    return client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug})).content.decode()


def describe_the_share_button():
    def it_carries_the_public_url_title_and_text(client):
        offering = _published()
        html = _public_page(client, offering)
        assert SHARE_URL_RE.findall(html) == [offering.public_url]
        assert f'data-share-title="{TITLE}"' in html
        assert f'data-share-text="{TEXT}"' in html

    def it_renders_for_a_guest_and_for_a_signed_in_member_alike(admin_user, client):
        offering = _published()
        assert len(SHARE_URL_RE.findall(_public_page(client, offering))) == 1
        client.force_login(admin_user)
        assert len(SHARE_URL_RE.findall(_public_page(client, offering))) == 1

    def it_points_text_and_email_at_the_same_link(client):
        offering = _published()
        html = _public_page(client, offering)
        body = quote(f"{TEXT} {offering.public_url}", safe="")
        assert f'href="sms:?&body={body}"' in html
        assert f'href="mailto:?subject={quote(TITLE, safe="")}&body={body}"' in html
        assert f'data-share-copy="{offering.public_url}"' in html

    def it_loads_the_share_script_from_the_head(client):
        html = _public_page(client, _published())
        head = html.partition("<body")[0]
        assert '<script defer src="/static/js/class_share.js"></script>' in head
        assert html.count("js/class_share.js") == 1


def describe_the_share_button_on_a_preview():
    @pytest.fixture
    def instructor(db):
        return InstructorFactory(user=UserFactory(username="sharer@example.com"))

    def it_renders_no_button_on_the_owner_preview_of_a_draft(instructor, client):
        draft = ClassOfferingFactory(instructor=instructor, status=ClassOffering.Status.DRAFT, slug="draft-share")
        client.force_login(instructor.user)
        html = client.get(reverse("classes:class_preview", kwargs={"pk": draft.pk})).content.decode()
        assert "data-share-url" not in html
        assert 'class="cp-share"' not in html

    def it_renders_no_button_on_a_preview_of_a_published_class(admin_user, client):
        # It is the preview, not the class's status, that hides the button.
        offering = _published()
        client.force_login(admin_user)
        html = client.get(reverse("classes:class_preview", kwargs={"pk": offering.pk})).content.decode()
        assert "data-share-url" not in html
        assert len(SHARE_URL_RE.findall(_public_page(client, offering))) == 1
