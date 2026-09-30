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

    def it_renders_the_hero_adjust_tool_with_the_content_type_ids(instructor_fixture, client):
        """Issue #536, item 4: the preview passes the ids heroPlacement({...}) interpolates.

        Without them the rendered ``contentTypeId: ,`` is a JavaScript syntax error, the
        component never initialises, the hero shows at 50% 50% whatever the saved crop, and
        the Adjust tool the imported photo note sends instructors to is dead in every preview.
        """
        from django.contrib.contenttypes.models import ContentType

        from classes.models import Category

        draft = ClassOfferingFactory(
            instructor=instructor_fixture, slug="adjustable", status=ClassOffering.Status.DRAFT
        )
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:class_preview", kwargs={"pk": draft.pk})).content.decode()
        offering_ct = ContentType.objects.get_for_model(ClassOffering)
        assert "heroPlacement({" in html
        assert f"contentTypeId: {offering_ct.pk}," in html
        assert f"objectId: {draft.pk}," in html
        assert "contentTypeId: ," not in html
        # The category id is in the context too, for the category hero branch the public page shares.
        response = client.get(reverse("classes:class_preview", kwargs={"pk": draft.pk}))
        assert response.context["category_ct_id"] == ContentType.objects.get_for_model(Category).pk

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


def describe_booking_state_in_preview():
    """The preview must show the same sign-up state the public page will.

    Regression: the preview helper passed no ``is_bookable``, so the template's
    ``{% if not is_bookable %}`` branch fired for every class and a published,
    weeks-away class previewed as "Registration closed / has already started".
    """

    @pytest.fixture
    def future_published(instructor_fixture):
        from datetime import timedelta

        from django.utils import timezone

        from classes.factories import ClassSessionFactory

        offering = ClassOfferingFactory(
            instructor=instructor_fixture,
            slug="weeks-away",
            status=ClassOffering.Status.PUBLISHED,
            capacity=15,
        )
        start = timezone.now() + timedelta(days=25)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        return offering

    def it_offers_registration_for_a_future_class(admin_user, future_published, client):
        client.force_login(admin_user)
        body = client.get(reverse("classes:class_preview", kwargs={"pk": future_published.pk})).content.decode()
        # Match the rail markup, not bare words: the changelog panel on every page
        # may quote the phrase "Registration closed" in a release note.
        assert 'cp-detail__spots--full">Registration closed' not in body
        assert 'data-help-key="class.register"' in body

    def it_shows_the_schedule_for_a_future_class(admin_user, future_published, client):
        client.force_login(admin_user)
        body = client.get(reverse("classes:class_preview", kwargs={"pk": future_published.pk})).content.decode()
        assert "cp-detail__session-date" in body

    def it_still_closes_a_started_class(admin_user, instructor_fixture, client):
        from datetime import timedelta

        from django.utils import timezone

        from classes.factories import ClassSessionFactory

        offering = ClassOfferingFactory(
            instructor=instructor_fixture,
            slug="already-started",
            status=ClassOffering.Status.PUBLISHED,
        )
        start = timezone.now() - timedelta(days=1)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        client.force_login(admin_user)
        body = client.get(reverse("classes:class_preview", kwargs={"pk": offering.pk})).content.decode()
        assert 'cp-detail__spots--full">Registration closed' in body
        assert 'data-help-key="class.register"' not in body


def describe_page_parity_in_preview():
    """Whatever the public page shows around the class, the preview shows too.

    Regression: the preview once built its own, thinner context, so the hero
    placement widget got an empty content-type id (a JS syntax error that killed
    it), and the "Other Dates" and related-classes strips never rendered.
    """

    @pytest.fixture
    def category(db):
        from classes.factories import CategoryFactory

        return CategoryFactory(name="Smithing", slug="smithing")

    def _publish(title, slug, category, instructor, days_out):
        from datetime import timedelta

        from django.utils import timezone

        from classes.factories import ClassSessionFactory

        offering = ClassOfferingFactory(
            title=title,
            slug=slug,
            category=category,
            instructor=instructor,
            status=ClassOffering.Status.PUBLISHED,
        )
        start = timezone.now() + timedelta(days=days_out)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        return offering

    def it_gives_the_hero_widget_its_content_type_id(admin_user, instructor_fixture, category, client):
        offering = _publish("Forge Night", "forge-hero", category, instructor_fixture, days_out=3)
        offering.legacy_image_url = "https://img.example.com/forge.jpg"
        offering.save(update_fields=["legacy_image_url"])
        client.force_login(admin_user)
        body = client.get(reverse("classes:class_preview", kwargs={"pk": offering.pk})).content.decode()
        assert "contentTypeId: ," not in body
        assert "heroPlacement(" in body

    def it_lists_other_dates_of_the_same_class(admin_user, instructor_fixture, category, client):
        first = _publish("Forge Night with Glen", "forge-a", category, instructor_fixture, days_out=2)
        _publish("Forge Night with Glen", "forge-b", category, instructor_fixture, days_out=9)
        client.force_login(admin_user)
        body = client.get(reverse("classes:class_preview", kwargs={"pk": first.pk})).content.decode()
        # The related strip lists forge-b too (same guild); only the other-dates
        # markup proves the sibling strip rendered.
        assert 'class="cp-detail__other-date"' in body
        assert "cp-detail__other-date" in body.split("Other Dates for This Class", 1)[1][:2000]
        assert "/classes/forge-b/" in body

    def it_lists_related_classes_in_the_same_category(admin_user, instructor_fixture, category, client):
        offering = _publish("Forge Night", "forge-main", category, instructor_fixture, days_out=2)
        _publish("Anvil Basics", "anvil-basics", category, instructor_fixture, days_out=5)
        client.force_login(admin_user)
        body = client.get(reverse("classes:class_preview", kwargs={"pk": offering.pk})).content.decode()
        assert "anvil-basics" in body
