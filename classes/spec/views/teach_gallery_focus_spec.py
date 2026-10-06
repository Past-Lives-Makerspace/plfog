"""BDD specs for the gallery cover and Set focus tools: the focus route, and what the composer and the class page render."""

from __future__ import annotations

import json
import re
from datetime import timedelta

import pytest
from django.urls import resolve, reverse
from django.utils import timezone

from classes.factories import (
    ClassImageFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    UserFactory,
)
from classes.forms import GALLERY_FOCUS_INVALID
from classes.models import ClassImage, ClassOffering
from classes.views import teach_class_image_focus, teach_image_url_base

Status = ClassOffering.Status


@pytest.fixture
def instructor_fixture(db):
    user = UserFactory(username="focus-teacher@example.com")
    return InstructorFactory(user=user, full_legal_name="Focus Teacher", instructor_slug="focus-teacher")


@pytest.fixture
def stranger(db):
    user = UserFactory(username="focus-stranger@example.com")
    return InstructorFactory(user=user, full_legal_name="Focus Stranger", instructor_slug="focus-stranger")


def _published(instructor, **kwargs) -> ClassOffering:
    offering = ClassOfferingFactory(
        instructor=instructor, status=Status.PUBLISHED, published_at=timezone.now(), **kwargs
    )
    start = timezone.now() + timedelta(days=3)
    ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def _post_focus(client, image: ClassImage, payload: object, *, url_name: str = "classes:teach_class_image_focus"):
    return client.post(reverse(url_name, kwargs={"pk": image.pk}), json.dumps(payload), content_type="application/json")


def _focus(image: ClassImage) -> tuple[int | None, int | None]:
    image.refresh_from_db()
    return (image.focus_x, image.focus_y)


def describe_the_focus_route():
    def it_hangs_off_the_per_image_url_base(db):
        match = resolve(f"{teach_image_url_base()}7/focus/")
        assert match.func is teach_class_image_focus
        assert match.kwargs == {"pk": 7}

    def it_stores_the_point_for_the_instructor(instructor_fixture, client):
        image = ClassImageFactory(class_offering=_published(instructor_fixture))
        client.force_login(instructor_fixture.user)
        resp = _post_focus(client, image, {"x": 10, "y": 20})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "position": "10% 20%"}
        assert _focus(image) == (10, 20)

    def it_resets_to_the_centre_on_nulls(instructor_fixture, client):
        image = ClassImageFactory(class_offering=_published(instructor_fixture), focus_x=10, focus_y=20)
        client.force_login(instructor_fixture.user)
        resp = _post_focus(client, image, {"x": None, "y": None})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "position": "50% 50%"}
        assert _focus(image) == (None, None)

    def it_lets_an_admin_set_it_on_anyones_class(admin_user, instructor_fixture, client):
        image = ClassImageFactory(class_offering=_published(instructor_fixture))
        client.force_login(admin_user)
        assert _post_focus(client, image, {"x": 0, "y": 100}).status_code == 200
        assert _focus(image) == (0, 100)

    def it_lets_an_admin_set_it_on_a_cancelled_class(admin_user, instructor_fixture, client):
        offering = _published(instructor_fixture)
        image = ClassImageFactory(class_offering=offering)
        offering.status = Status.CANCELLED
        offering.save(update_fields=["status"])
        client.force_login(admin_user)
        assert _post_focus(client, image, {"x": 40, "y": 60}).status_code == 200
        assert _focus(image) == (40, 60)

    def it_answers_on_the_old_admin_path_too(admin_user, instructor_fixture, client):
        image = ClassImageFactory(class_offering=_published(instructor_fixture))
        client.force_login(admin_user)
        resp = _post_focus(client, image, {"x": 33, "y": 66}, url_name="classes:admin_class_image_focus")
        assert resp.status_code == 200
        assert _focus(image) == (33, 66)

    @pytest.mark.parametrize(
        "payload",
        [
            {"x": 10},
            {"y": 10},
            {"x": "10", "y": 10},
            {"x": 10.5, "y": 10},
            {"x": True, "y": 10},
            {"x": 10, "y": 101},
            {"x": -1, "y": 10},
            {"x": None, "y": 10},
            {"x": 10, "y": None},
        ],
    )
    def it_400s_a_malformed_body_and_changes_nothing(instructor_fixture, client, payload):
        image = ClassImageFactory(class_offering=_published(instructor_fixture), focus_x=5, focus_y=6)
        client.force_login(instructor_fixture.user)
        resp = _post_focus(client, image, payload)
        assert resp.status_code == 400
        assert resp.json() == {"error": GALLERY_FOCUS_INVALID}
        assert _focus(image) == (5, 6)

    def it_400s_a_body_that_is_not_json(instructor_fixture, client):
        image = ClassImageFactory(class_offering=_published(instructor_fixture))
        client.force_login(instructor_fixture.user)
        resp = client.post(
            reverse("classes:teach_class_image_focus", kwargs={"pk": image.pk}), "x=1", content_type="text/plain"
        )
        assert resp.status_code == 400

    def it_404s_a_viewer_without_edit_on_the_class(instructor_fixture, stranger, client):
        image = ClassImageFactory(class_offering=_published(instructor_fixture))
        client.force_login(stranger.user)
        assert _post_focus(client, image, {"x": 10, "y": 20}).status_code == 404
        assert _focus(image) == (None, None)

    def it_404s_a_member_with_no_claim(member_user, instructor_fixture, client):
        image = ClassImageFactory(class_offering=_published(instructor_fixture))
        client.force_login(member_user)
        assert _post_focus(client, image, {"x": 10, "y": 20}).status_code == 404

    @pytest.mark.parametrize("closed_status", [Status.CANCELLED, Status.ARCHIVED])
    def it_404s_the_instructor_on_a_closed_class(instructor_fixture, client, closed_status):
        offering = _published(instructor_fixture)
        image = ClassImageFactory(class_offering=offering)
        offering.status = closed_status
        offering.save(update_fields=["status"])
        client.force_login(instructor_fixture.user)
        assert _post_focus(client, image, {"x": 10, "y": 20}).status_code == 404
        assert _focus(image) == (None, None)

    def it_404s_an_image_that_does_not_exist(admin_user, client):
        client.force_login(admin_user)
        resp = client.post(
            reverse("classes:teach_class_image_focus", kwargs={"pk": 999_999}),
            json.dumps({"x": 1, "y": 1}),
            content_type="application/json",
        )
        assert resp.status_code == 404

    def it_refuses_get_and_sends_anonymous_to_login(instructor_fixture, client):
        image = ClassImageFactory(class_offering=_published(instructor_fixture))
        url = reverse("classes:teach_class_image_focus", kwargs={"pk": image.pk})
        assert client.post(url, "{}", content_type="application/json").status_code == 302
        client.force_login(instructor_fixture.user)
        assert client.get(url).status_code == 405


def describe_the_composer_gallery():
    def _html(client, offering: ClassOffering) -> str:
        return client.get(reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})).content.decode()

    def _cards(html: str) -> list[str]:
        """Each server rendered card's opening tag, in grid order."""
        return re.findall(r'<div class="cls-image-cell has-image"[^>]*>', html)

    def it_renders_each_card_with_its_saved_position_and_both_tools(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT, gallery=0)
        first = ClassImageFactory(class_offering=offering, sort_order=0, focus_x=12, focus_y=34)
        second = ClassImageFactory(class_offering=offering, sort_order=1)
        client.force_login(instructor_fixture.user)
        html = _html(client, offering)
        cards = _cards(html)
        assert len(cards) == 2
        assert f'data-id="{first.pk}"' in cards[0] and 'data-position="12% 34%"' in cards[0]
        assert f'data-id="{second.pk}"' in cards[1] and 'data-position="50% 50%"' in cards[1]
        assert 'style="object-position: 12% 34%"' in html
        assert 'style="object-position: 50% 50%"' in html
        # Every card carries the badge and both buttons; CSS :first-child decides which show.
        grid = html[html.index('id="gallery-grid"') : html.index('id="gallery-count"')]
        assert grid.count('<span class="cls-image-cover-badge">Cover</span>') == 2
        assert grid.count('class="pl-btn pl-btn--sm pl-btn--ghost cls-image-make-cover">Make cover</button>') == 2
        assert grid.count('class="pl-btn pl-btn--sm pl-btn--ghost cls-image-set-focus">Set focus</button>') == 2
        assert ".cls-image-cell:first-child .cls-image-cover-badge { display: inline-block; }" in html
        assert ".cls-image-cell:first-child .cls-image-make-cover { display: none; }" in html

    def it_says_what_the_cover_does_and_ships_the_focus_panel_closed(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=Status.DRAFT)
        client.force_login(instructor_fixture.user)
        html = _html(client, offering)
        assert "The cover leads the gallery on your class page. Drag to reorder, or use Make cover." in html
        assert re.search(r'<div class="pl-modal-backdrop cls-focus-backdrop" id="gallery-focus" hidden', html)
        for control in ("data-focus-save>Save focus<", "data-focus-reset>Reset<", "data-focus-cancel>Cancel<"):
            assert control in html
        assert "${imageUrlBase}${card.dataset.id}/focus/" in html


def describe_the_class_page_gallery():
    def it_crops_the_main_slide_and_its_thumbnail_around_each_focus(instructor_fixture, client):
        offering = _published(instructor_fixture, gallery=0, slug="focus-page")
        ClassImageFactory(class_offering=offering, sort_order=0, focus_x=7, focus_y=91)
        ClassImageFactory(class_offering=offering, sort_order=1)
        html = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug})).content.decode()
        mains = re.findall(r'<img class="cls-gallery__img"[^>]*style="object-position: ([^"]+)"', html)
        thumbs = re.findall(
            r'<button type="button"\s+class="cls-gallery__thumb".*?<img[^>]*style="object-position: ([^"]+)"',
            html,
            re.S,
        )
        assert mains == ["7% 91%", "50% 50%"]
        assert thumbs == ["7% 91%", "50% 50%"]

    def it_leads_with_the_cover(instructor_fixture, client):
        offering = _published(instructor_fixture, gallery=0, slug="cover-page")
        later = ClassImageFactory(class_offering=offering, sort_order=5, alt_text="Second shot")
        cover = ClassImageFactory(class_offering=offering, sort_order=0, alt_text="Cover shot")
        html = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug})).content.decode()
        first_slide = re.search(r'data-slide-index="0"\s+data-slide-src="[^"]*"\s+data-slide-alt="([^"]*)"', html)
        assert first_slide is not None
        assert first_slide.group(1) == cover.alt_text
        assert later.alt_text in html


def describe_the_reorder_route_behind_make_cover():
    def _post_order(client, offering: ClassOffering, body: str):
        return client.post(
            reverse("classes:teach_class_image_reorder", kwargs={"pk": offering.pk}),
            body,
            content_type="application/json",
        )

    @pytest.mark.parametrize(
        "body",
        [
            json.dumps([1, 2]),
            json.dumps("order"),
            json.dumps(None),
            json.dumps(7),
            json.dumps({"order": 5}),
            json.dumps({"order": "12"}),
            json.dumps({"order": {"1": 0}}),
            json.dumps({"order": None}),
        ],
    )
    def it_400s_a_body_that_is_not_an_object_with_an_order_list(instructor_fixture, client, body):
        offering = _published(instructor_fixture, gallery=0)
        first = ClassImageFactory(class_offering=offering, sort_order=0)
        second = ClassImageFactory(class_offering=offering, sort_order=1)
        client.force_login(instructor_fixture.user)
        resp = _post_order(client, offering, body)
        assert resp.status_code == 400
        assert resp.json() == {"error": "Invalid payload."}
        assert [img.pk for img in offering.gallery_images.all()] == [first.pk, second.pk]

    def it_skips_ids_that_are_not_whole_numbers_or_not_this_classs(instructor_fixture, client):
        offering = _published(instructor_fixture, gallery=0)
        first = ClassImageFactory(class_offering=offering, sort_order=0)
        second = ClassImageFactory(class_offering=offering, sort_order=1)
        elsewhere = ClassImageFactory(sort_order=0)
        client.force_login(instructor_fixture.user)
        order = [[first.pk], {"id": first.pk}, True, str(first.pk), None, elsewhere.pk, second.pk, first.pk]
        resp = _post_order(client, offering, json.dumps({"order": order}))
        assert resp.status_code == 200
        first.refresh_from_db()
        second.refresh_from_db()
        elsewhere.refresh_from_db()
        assert (second.sort_order, first.sort_order) == (6, 7)
        assert elsewhere.sort_order == 0
        assert [img.pk for img in offering.gallery_images.all()] == [second.pk, first.pk]

    def it_makes_the_first_id_the_cover(instructor_fixture, client):
        offering = _published(instructor_fixture, gallery=0)
        one, two, three = (ClassImageFactory(class_offering=offering, sort_order=i) for i in range(3))
        client.force_login(instructor_fixture.user)
        assert _post_order(client, offering, json.dumps({"order": [three.pk, one.pk, two.pk]})).status_code == 200
        assert offering.gallery_display_images[0]["url"] == three.image.url
