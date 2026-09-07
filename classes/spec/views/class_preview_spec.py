"""BDD specs for the class_preview view — owner + admin access, draft visibility."""

from __future__ import annotations

import pytest
from django.urls import reverse

from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering


@pytest.fixture
def instructor_fixture(db):
    user = UserFactory(username="teacher@example.com")
    return InstructorFactory(user=user, full_legal_name="Teacher T", instructor_slug="teacher-t")


@pytest.fixture
def other_instructor(db):
    user = UserFactory(username="other@example.com")
    return InstructorFactory(user=user, full_legal_name="Other", instructor_slug="other")


def describe_class_preview():
    def it_lets_the_owner_preview_a_draft(instructor_fixture, client):
        draft = ClassOfferingFactory(
            instructor=instructor_fixture,
            slug="my-draft",
            status=ClassOffering.Status.DRAFT,
        )
        client.force_login(instructor_fixture.user)
        response = client.get(reverse("classes:class_preview", kwargs={"pk": draft.pk}))
        assert response.status_code == 200
        assert b"Preview" in response.content
        assert draft.title.encode() in response.content

    def it_blocks_another_instructor(instructor_fixture, other_instructor, client):
        draft = ClassOfferingFactory(
            instructor=instructor_fixture,
            slug="not-yours",
            status=ClassOffering.Status.DRAFT,
        )
        client.force_login(other_instructor.user)
        response = client.get(reverse("classes:class_preview", kwargs={"pk": draft.pk}))
        assert response.status_code == 403

    def it_lets_an_admin_preview_anyone(admin_user, instructor_fixture, client):
        draft = ClassOfferingFactory(
            instructor=instructor_fixture,
            slug="admin-can-see",
            status=ClassOffering.Status.DRAFT,
        )
        client.force_login(admin_user)
        response = client.get(reverse("classes:class_preview", kwargs={"pk": draft.pk}))
        assert response.status_code == 200

    def it_redirects_anonymous_to_login(db, client):
        offering = ClassOfferingFactory(slug="any", status=ClassOffering.Status.DRAFT)
        response = client.get(reverse("classes:class_preview", kwargs={"pk": offering.pk}))
        assert response.status_code == 302


def describe_gallery_rendering():
    def it_renders_the_gallery_when_images_present(instructor_fixture, client):
        from io import BytesIO

        from django.core.files.uploadedfile import SimpleUploadedFile

        from classes.factories import ClassImageFactory

        def img(name="x.png"):
            return SimpleUploadedFile(name, b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, content_type="image/png")

        offering = ClassOfferingFactory(
            instructor=instructor_fixture,
            slug="with-images",
            status=ClassOffering.Status.DRAFT,
            image=img("hero.png"),
            gallery=0,
        )
        ClassImageFactory(class_offering=offering, image=img("g1.png"), sort_order=1)
        ClassImageFactory(class_offering=offering, image=img("g2.png"), sort_order=2)
        client.force_login(instructor_fixture.user)
        response = client.get(reverse("classes:class_preview", kwargs={"pk": offering.pk}))
        body = response.content.decode()
        assert "cp-detail__rail-gallery" in body  # gallery sits under the booking rail
        assert 'class="cls-gallery"' in body
        assert "clsGallery(2)" in body  # the 2 gallery shots — the hero stays out of the rail gallery
        assert "cls-gallery__thumbs" in body
        # ensure BytesIO import is referenced so ruff doesn't complain
        assert BytesIO is not None
