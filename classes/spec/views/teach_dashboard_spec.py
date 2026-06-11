"""BDD specs for the instructor dashboard (Plan 3)."""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import ClassImage, ClassOffering
from membership.models import Member


def _image_file(name: str = "shot.png") -> SimpleUploadedFile:
    buf = BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


@pytest.fixture
def instructor_fixture(db):
    user = UserFactory(username="teacher@example.com")
    instructor = InstructorFactory(user=user, full_legal_name="Teacher T", instructor_slug="teacher-t")
    return instructor


@pytest.fixture
def other_instructor(db):
    user = UserFactory(username="other@example.com")
    return InstructorFactory(user=user, full_legal_name="Other", instructor_slug="other")


def describe_instructor_access_gate():
    def it_allows_active_members(member_user, client):
        client.force_login(member_user)
        response = client.get(reverse("classes:teach_dashboard"))
        assert response.status_code == 200

    def it_blocks_anonymous(db, client):
        response = client.get(reverse("classes:teach_dashboard"))
        assert response.status_code == 302  # login redirect

    def it_blocks_inactive_instructor(db, client):
        user = UserFactory(username="inactive@example.com")
        InstructorFactory(user=user, status=Member.Status.FORMER)
        client.force_login(user)
        response = client.get(reverse("classes:teach_dashboard"))
        assert response.status_code == 403


def describe_instructor_dashboard():
    def it_shows_only_my_classes(instructor_fixture, other_instructor, client):
        mine = ClassOfferingFactory(
            instructor=instructor_fixture,
            title="Mine Class",
            slug="mine-class",
            status=ClassOffering.Status.DRAFT,
        )
        ClassOfferingFactory(
            instructor=other_instructor,
            title="Theirs Class",
            slug="theirs-class",
            status=ClassOffering.Status.PUBLISHED,
        )
        client.force_login(instructor_fixture.user)
        response = client.get(reverse("classes:teach_dashboard"))
        assert response.status_code == 200
        assert mine.title.encode() in response.content
        assert b"Theirs Class" not in response.content


def describe_instructor_create_class():
    def it_creates_a_draft_with_sessions(instructor_fixture, client):
        cat = CategoryFactory()
        client.force_login(instructor_fixture.user)
        start = (timezone.now() + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M")
        end = (timezone.now() + timedelta(days=7, hours=2)).strftime("%Y-%m-%dT%H:%M")
        response = client.post(
            reverse("classes:teach_class_create"),
            {
                "title": "My New Class",
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
                "scheduling_model": "fixed",
                "flexible_note": "",
                "recurring_pattern": "",
                "sessions-TOTAL_FORMS": "1",
                "sessions-INITIAL_FORMS": "0",
                "sessions-MIN_NUM_FORMS": "0",
                "sessions-MAX_NUM_FORMS": "1000",
                "sessions-0-starts_at": start,
                "sessions-0-ends_at": end,
                "images-TOTAL_FORMS": "0",
                "images-INITIAL_FORMS": "0",
                "images-MIN_NUM_FORMS": "0",
                "images-MAX_NUM_FORMS": "1000",
                "action": "save",
            },
        )
        assert response.status_code == 302
        offering = ClassOffering.objects.get(title="My New Class")
        assert offering.instructor == instructor_fixture
        assert offering.status == ClassOffering.Status.DRAFT
        assert offering.sessions.count() == 1

    def it_submits_for_review_when_action_is_submit(instructor_fixture, client):
        cat = CategoryFactory()
        client.force_login(instructor_fixture.user)
        response = client.post(
            reverse("classes:teach_class_create"),
            {
                "title": "Submit-Me",
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
                "flexible_note": "",
                "recurring_pattern": "",
                "sessions-TOTAL_FORMS": "0",
                "sessions-INITIAL_FORMS": "0",
                "sessions-MIN_NUM_FORMS": "0",
                "sessions-MAX_NUM_FORMS": "1000",
                "images-TOTAL_FORMS": "0",
                "images-INITIAL_FORMS": "0",
                "images-MIN_NUM_FORMS": "0",
                "images-MAX_NUM_FORMS": "1000",
                "action": "submit",
            },
        )
        assert response.status_code == 302
        offering = ClassOffering.objects.get(title="Submit-Me")
        assert offering.status == ClassOffering.Status.PENDING

    def it_saves_gallery_images_on_create(instructor_fixture, client):
        cat = CategoryFactory()
        client.force_login(instructor_fixture.user)
        response = client.post(
            reverse("classes:teach_class_create"),
            {
                "title": "Gallery Class",
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
                "scheduling_model": "fixed",
                "flexible_note": "",
                "recurring_pattern": "",
                "sessions-TOTAL_FORMS": "0",
                "sessions-INITIAL_FORMS": "0",
                "sessions-MIN_NUM_FORMS": "0",
                "sessions-MAX_NUM_FORMS": "1000",
                "images-TOTAL_FORMS": "0",
                "images-INITIAL_FORMS": "0",
                "images-MIN_NUM_FORMS": "0",
                "images-MAX_NUM_FORMS": "1000",
                "action": "save",
                "gallery_images": [_image_file("x.png"), _image_file("y.png")],
            },
        )
        assert response.status_code == 302
        offering = ClassOffering.objects.get(title="Gallery Class")
        assert ClassImage.objects.filter(class_offering=offering).count() == 2


def describe_instructor_edit_class():
    def it_refuses_editing_other_instructors_classes(instructor_fixture, other_instructor, client):
        theirs = ClassOfferingFactory(instructor=other_instructor, slug="theirs-edit")
        client.force_login(instructor_fixture.user)
        response = client.get(reverse("classes:teach_class_edit", kwargs={"pk": theirs.pk}))
        assert response.status_code == 404

    def it_redirects_for_published_classes(instructor_fixture, client):
        mine = ClassOfferingFactory(
            instructor=instructor_fixture,
            slug="mine-published",
            status=ClassOffering.Status.PUBLISHED,
        )
        client.force_login(instructor_fixture.user)
        response = client.get(reverse("classes:teach_class_edit", kwargs={"pk": mine.pk}))
        assert response.status_code == 302

    def it_renders_the_edit_form_for_drafts(instructor_fixture, client):
        mine = ClassOfferingFactory(
            instructor=instructor_fixture,
            slug="mine-draft",
            status=ClassOffering.Status.DRAFT,
        )
        client.force_login(instructor_fixture.user)
        response = client.get(reverse("classes:teach_class_edit", kwargs={"pk": mine.pk}))
        assert response.status_code == 200


def describe_instructor_submit():
    def it_flips_draft_to_pending(instructor_fixture, client):
        mine = ClassOfferingFactory(
            instructor=instructor_fixture,
            slug="to-submit",
            status=ClassOffering.Status.DRAFT,
        )
        client.force_login(instructor_fixture.user)
        response = client.post(reverse("classes:teach_class_submit", kwargs={"pk": mine.pk}))
        assert response.status_code == 302
        mine.refresh_from_db()
        assert mine.status == ClassOffering.Status.PENDING


def describe_instructor_registrations():
    def it_shows_only_my_registrations(instructor_fixture, other_instructor, client):
        mine = ClassOfferingFactory(instructor=instructor_fixture, slug="m")
        theirs = ClassOfferingFactory(instructor=other_instructor, slug="t")
        r1 = RegistrationFactory(class_offering=mine, first_name="Mine", last_name="Guest")
        RegistrationFactory(class_offering=theirs, first_name="Other", last_name="Guest")
        client.force_login(instructor_fixture.user)
        response = client.get(reverse("classes:teach_registrations"))
        assert response.status_code == 200
        assert r1.first_name.encode() in response.content
        assert b"Other" not in response.content


def describe_instructor_profile():
    def it_redirects_to_hub_settings(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        response = client.get(reverse("classes:teach_profile"))
        assert response.status_code == 302
        assert response["Location"].endswith("/settings/?tab=profile")


def describe_instructor_class_create_invalid():
    def it_rerenders_form_on_invalid_post(instructor_fixture, client):
        """Missing required fields → form re-renders (line 519 fallback path)."""
        client.force_login(instructor_fixture.user)
        response = client.post(
            reverse("classes:teach_class_create"),
            {
                # Missing title, category, price — form will be invalid
                "sessions-TOTAL_FORMS": "0",
                "sessions-INITIAL_FORMS": "0",
                "sessions-MIN_NUM_FORMS": "0",
                "sessions-MAX_NUM_FORMS": "1000",
                "images-TOTAL_FORMS": "0",
                "images-INITIAL_FORMS": "0",
                "images-MIN_NUM_FORMS": "0",
                "images-MAX_NUM_FORMS": "1000",
                "action": "save",
            },
        )
        assert response.status_code == 200


def describe_instructor_class_edit_post():
    def it_updates_draft_without_submit(instructor_fixture, client):
        """Edit POST with action=save → plain update path (line 552-553)."""
        cat = CategoryFactory()
        mine = ClassOfferingFactory(
            instructor=instructor_fixture,
            slug="mine-update",
            status=ClassOffering.Status.DRAFT,
            category=cat,
            title="Original Title",
        )
        client.force_login(instructor_fixture.user)
        start = (timezone.now() + timedelta(days=10)).strftime("%Y-%m-%dT%H:%M")
        end = (timezone.now() + timedelta(days=10, hours=2)).strftime("%Y-%m-%dT%H:%M")
        response = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": mine.pk}),
            {
                "title": "Updated Title",
                "category": cat.pk,
                "description": "Updated description",
                "prerequisites": "",
                "materials_included": "",
                "materials_to_bring": "",
                "safety_requirements": "",
                "age_guardian_note": "",
                "price_cents": 3000,
                "member_discount_pct": 0,
                "capacity": 8,
                "scheduling_model": "fixed",
                "flexible_note": "",
                "recurring_pattern": "",
                "sessions-TOTAL_FORMS": "1",
                "sessions-INITIAL_FORMS": "0",
                "sessions-MIN_NUM_FORMS": "0",
                "sessions-MAX_NUM_FORMS": "1000",
                "sessions-0-starts_at": start,
                "sessions-0-ends_at": end,
                "images-TOTAL_FORMS": "0",
                "images-INITIAL_FORMS": "0",
                "images-MIN_NUM_FORMS": "0",
                "images-MAX_NUM_FORMS": "1000",
                "action": "save",
            },
        )
        assert response.status_code == 302
        mine.refresh_from_db()
        assert mine.title == "Updated Title"
        assert mine.status == ClassOffering.Status.DRAFT

    def it_submits_draft_for_review_when_action_is_submit(instructor_fixture, client):
        """Edit POST with action=submit + draft status → submit_for_review (lines 549-551)."""
        cat = CategoryFactory()
        mine = ClassOfferingFactory(
            instructor=instructor_fixture,
            slug="mine-submit-edit",
            status=ClassOffering.Status.DRAFT,
            category=cat,
        )
        client.force_login(instructor_fixture.user)
        start = (timezone.now() + timedelta(days=10)).strftime("%Y-%m-%dT%H:%M")
        end = (timezone.now() + timedelta(days=10, hours=2)).strftime("%Y-%m-%dT%H:%M")
        response = client.post(
            reverse("classes:teach_class_edit", kwargs={"pk": mine.pk}),
            {
                "title": mine.title,
                "category": cat.pk,
                "description": "d",
                "prerequisites": "",
                "materials_included": "",
                "materials_to_bring": "",
                "safety_requirements": "",
                "age_guardian_note": "",
                "price_cents": mine.price_cents,
                "member_discount_pct": mine.member_discount_pct,
                "capacity": mine.capacity,
                "scheduling_model": "fixed",
                "flexible_note": "",
                "recurring_pattern": "",
                "sessions-TOTAL_FORMS": "1",
                "sessions-INITIAL_FORMS": "0",
                "sessions-MIN_NUM_FORMS": "0",
                "sessions-MAX_NUM_FORMS": "1000",
                "sessions-0-starts_at": start,
                "sessions-0-ends_at": end,
                "images-TOTAL_FORMS": "0",
                "images-INITIAL_FORMS": "0",
                "images-MIN_NUM_FORMS": "0",
                "images-MAX_NUM_FORMS": "1000",
                "action": "submit",
            },
        )
        assert response.status_code == 302
        mine.refresh_from_db()
        assert mine.status == ClassOffering.Status.PENDING


def describe_instructor_discount_code_instructor_crud():
    def it_creates_a_discount_code(instructor_fixture, client):
        """Instructor creates a discount code via the Teaching portal (lines 647-649)."""
        client.force_login(instructor_fixture.user)
        response = client.post(
            reverse("classes:teach_discount_code_create"),
            {"code": "TEACH10", "discount_pct": 10, "is_active": "on"},
        )
        assert response.status_code == 302
        from classes.models import DiscountCode

        assert DiscountCode.objects.filter(code="TEACH10").exists()

    def it_renders_create_form_on_get(instructor_fixture, client):
        """GET on create renders the empty form (line 650)."""
        client.force_login(instructor_fixture.user)
        response = client.get(reverse("classes:teach_discount_code_create"))
        assert response.status_code == 200

    def it_edits_a_discount_code(instructor_fixture, client):
        """Instructor edits an existing discount code (lines 663-665)."""
        from classes.factories import DiscountCodeFactory

        code = DiscountCodeFactory(discount_pct=10)
        client.force_login(instructor_fixture.user)
        response = client.post(
            reverse("classes:teach_discount_code_edit", kwargs={"pk": code.pk}),
            {"code": code.code, "discount_pct": 30, "is_active": "on"},
        )
        assert response.status_code == 302
        code.refresh_from_db()
        assert code.discount_pct == 30

    def it_renders_edit_form_on_get(instructor_fixture, client):
        """GET on edit renders the filled form (line 666)."""
        from classes.factories import DiscountCodeFactory

        code = DiscountCodeFactory(discount_pct=15)
        client.force_login(instructor_fixture.user)
        response = client.get(reverse("classes:teach_discount_code_edit", kwargs={"pk": code.pk}))
        assert response.status_code == 200

    def it_deletes_a_discount_code(instructor_fixture, client):
        """POST to delete removes the code (lines 676-678)."""
        from classes.factories import DiscountCodeFactory
        from classes.models import DiscountCode

        code = DiscountCodeFactory()
        client.force_login(instructor_fixture.user)
        response = client.post(
            reverse("classes:teach_discount_code_delete", kwargs={"pk": code.pk}),
        )
        assert response.status_code == 302
        assert not DiscountCode.objects.filter(pk=code.pk).exists()

    def it_ignores_get_on_delete_and_redirects(instructor_fixture, client):
        """GET on delete does not delete, just redirects (line 679)."""
        from classes.factories import DiscountCodeFactory
        from classes.models import DiscountCode

        code = DiscountCodeFactory()
        client.force_login(instructor_fixture.user)
        response = client.get(
            reverse("classes:teach_discount_code_delete", kwargs={"pk": code.pk}),
        )
        assert response.status_code == 302
        assert DiscountCode.objects.filter(pk=code.pk).exists()


def describe_instructor_required_admin_without_instructor():
    def it_redirects_admin_with_no_instructor_profile(admin_user, client):
        """Admin is an active member so can access the teaching portal even without instructor_slug."""
        client.force_login(admin_user)
        response = client.get(reverse("classes:teach_dashboard"))
        assert response.status_code == 200
