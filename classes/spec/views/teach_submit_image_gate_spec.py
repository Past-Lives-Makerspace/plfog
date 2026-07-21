"""BDD specs for the mandatory-photo gate across every instructor submit path.

Covers ``teach_class_create``, ``teach_class_edit`` and ``teach_class_submit``:
an imageless draft is bounced (no 500, stays DRAFT, error surfaced); a valid
class with fewer than three gallery photos submits AND gets the soft nudge; a
class with three or more submits with no nudge.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from classes.factories import CategoryFactory, ClassImageFactory, ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering

_ERROR_FRAGMENT = "Add a photo before submitting"
_NUDGE_FRAGMENT = "3 or more photos"


@pytest.fixture
def instructor_fixture(db):
    user = UserFactory(username="gate-teacher@example.com")
    return InstructorFactory(user=user, full_legal_name="Gate Teacher", instructor_slug="gate-teacher")


def _image_file(name: str = "shot.png") -> SimpleUploadedFile:
    buf = BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def _add_gallery(offering: ClassOffering, count: int) -> None:
    for i in range(count):
        ClassImageFactory(class_offering=offering, image=_image_file(f"g{i}.png"), sort_order=i)


def _messages(response) -> list[str]:
    return [m.message for m in get_messages(response.wsgi_request)]


def _create_payload(cat, **extra) -> dict:
    payload = {
        "title": "Gate Class",
        "category": cat.pk,
        "description": "d",
        "prerequisites": "",
        "materials_included": "",
        "materials_to_bring": "",
        "safety_requirements": "",
        "age_guardian_note": "",
        "price_cents": 5000,
        "member_discount_pct": 10,
        "capacity": 6,
        "scheduling_model": "flexible",
        "scheduling_type": "single_session",
        "flexible_note": "",
        "recurring_pattern": "",
        "sessions-TOTAL_FORMS": "0",
        "sessions-INITIAL_FORMS": "0",
        "sessions-MIN_NUM_FORMS": "0",
        "sessions-MAX_NUM_FORMS": "1000",
        "faq-TOTAL_FORMS": "0",
        "faq-INITIAL_FORMS": "0",
        "faq-MIN_NUM_FORMS": "0",
        "faq-MAX_NUM_FORMS": "1000",
        "images-TOTAL_FORMS": "0",
        "images-INITIAL_FORMS": "0",
        "images-MIN_NUM_FORMS": "0",
        "images-MAX_NUM_FORMS": "1000",
        "action": "submit",
    }
    payload.update(extra)
    return payload


def _edit_payload(offering, **extra) -> dict:
    payload = {
        "title": offering.title,
        "category": offering.category_id,
        "description": "d",
        "prerequisites": "",
        "materials_included": "",
        "materials_to_bring": "",
        "safety_requirements": "",
        "age_guardian_note": "",
        "price_cents": offering.price_cents,
        "member_discount_pct": offering.member_discount_pct,
        "capacity": offering.capacity,
        "scheduling_model": "flexible",
        "scheduling_type": "single_session",
        "flexible_note": "",
        "recurring_pattern": "",
        "sessions-TOTAL_FORMS": "0",
        "sessions-INITIAL_FORMS": "0",
        "sessions-MIN_NUM_FORMS": "0",
        "sessions-MAX_NUM_FORMS": "1000",
        "faq-TOTAL_FORMS": "0",
        "faq-INITIAL_FORMS": "0",
        "faq-MIN_NUM_FORMS": "0",
        "faq-MAX_NUM_FORMS": "1000",
        "images-TOTAL_FORMS": "0",
        "images-INITIAL_FORMS": "0",
        "images-MIN_NUM_FORMS": "0",
        "images-MAX_NUM_FORMS": "1000",
        "action": "submit",
    }
    payload.update(extra)
    return payload


def describe_teach_class_submit_photo_gate():
    def it_bounces_an_imageless_draft_to_the_edit_page(instructor_fixture, client):
        draft = ClassOfferingFactory(instructor=instructor_fixture, image="", status=ClassOffering.Status.DRAFT)
        client.force_login(instructor_fixture.user)
        response = client.post(reverse("classes:teach_class_submit", kwargs={"pk": draft.pk}))
        assert response.status_code == 302
        assert response.url == reverse("classes:teach_class_edit", kwargs={"pk": draft.pk})
        draft.refresh_from_db()
        assert draft.status == ClassOffering.Status.DRAFT
        assert any(_ERROR_FRAGMENT in m for m in _messages(response))

    def it_submits_and_nudges_with_fewer_than_three_photos(instructor_fixture, client):
        draft = ClassOfferingFactory(instructor=instructor_fixture, status=ClassOffering.Status.DRAFT)
        client.force_login(instructor_fixture.user)
        response = client.post(reverse("classes:teach_class_submit", kwargs={"pk": draft.pk}))
        assert response.status_code == 302
        draft.refresh_from_db()
        assert draft.status == ClassOffering.Status.PENDING
        assert any(_NUDGE_FRAGMENT in m for m in _messages(response))

    def it_submits_without_a_nudge_at_three_or_more_photos(instructor_fixture, client):
        draft = ClassOfferingFactory(instructor=instructor_fixture, status=ClassOffering.Status.DRAFT)
        _add_gallery(draft, 3)
        client.force_login(instructor_fixture.user)
        response = client.post(reverse("classes:teach_class_submit", kwargs={"pk": draft.pk}))
        assert response.status_code == 302
        draft.refresh_from_db()
        assert draft.status == ClassOffering.Status.PENDING
        assert not any(_NUDGE_FRAGMENT in m for m in _messages(response))


def describe_teach_class_create_photo_gate():
    def it_keeps_an_imageless_class_as_draft_with_an_error(instructor_fixture, client):
        cat = CategoryFactory()
        client.force_login(instructor_fixture.user)
        response = client.post(reverse("classes:teach_class_create"), _create_payload(cat))
        assert response.status_code == 302
        offering = ClassOffering.objects.get(title="Gate Class")
        assert offering.status == ClassOffering.Status.DRAFT
        assert any(_ERROR_FRAGMENT in m for m in _messages(response))

    def it_submits_and_nudges_with_fewer_than_three_photos(instructor_fixture, client):
        cat = CategoryFactory()
        client.force_login(instructor_fixture.user)
        response = client.post(
            reverse("classes:teach_class_create"),
            _create_payload(cat, gallery_images=[_image_file("a.png")]),
        )
        assert response.status_code == 302
        offering = ClassOffering.objects.get(title="Gate Class")
        assert offering.status == ClassOffering.Status.PENDING
        assert any(_NUDGE_FRAGMENT in m for m in _messages(response))

    def it_submits_without_a_nudge_at_three_or_more_photos(instructor_fixture, client):
        cat = CategoryFactory()
        client.force_login(instructor_fixture.user)
        response = client.post(
            reverse("classes:teach_class_create"),
            _create_payload(cat, gallery_images=[_image_file("a.png"), _image_file("b.png"), _image_file("c.png")]),
        )
        assert response.status_code == 302
        offering = ClassOffering.objects.get(title="Gate Class")
        assert offering.status == ClassOffering.Status.PENDING
        assert not any(_NUDGE_FRAGMENT in m for m in _messages(response))


def describe_teach_class_edit_photo_gate():
    def it_keeps_an_imageless_class_as_draft_with_an_error(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, image="", status=ClassOffering.Status.DRAFT)
        client.force_login(instructor_fixture.user)
        response = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _edit_payload(offering),
        )
        assert response.status_code == 302
        assert response.url == reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.DRAFT
        assert any(_ERROR_FRAGMENT in m for m in _messages(response))

    def it_submits_and_nudges_with_fewer_than_three_photos(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=ClassOffering.Status.DRAFT)
        client.force_login(instructor_fixture.user)
        response = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _edit_payload(offering),
        )
        assert response.status_code == 302
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PENDING
        assert any(_NUDGE_FRAGMENT in m for m in _messages(response))

    def it_submits_without_a_nudge_at_three_or_more_photos(instructor_fixture, client):
        offering = ClassOfferingFactory(instructor=instructor_fixture, status=ClassOffering.Status.DRAFT)
        _add_gallery(offering, 3)
        client.force_login(instructor_fixture.user)
        response = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": offering.pk}),
            _edit_payload(offering),
        )
        assert response.status_code == 302
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PENDING
        assert not any(_NUDGE_FRAGMENT in m for m in _messages(response))
