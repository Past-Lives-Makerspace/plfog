"""BDD specs for the instructor dashboard (Plan 3)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone as dt_timezone
from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from classes.factories import (
    READY_DESCRIPTION,
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import ClassImage, ClassOffering
from membership.models import Member


def _image_file(name: str = "shot.png") -> SimpleUploadedFile:
    # A real PNG: the gallery refuses bytes Pillow cannot open (#498).
    buf = BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def _real_image_file(name: str = "hero.png") -> SimpleUploadedFile:
    """A genuine PNG — the hero ``image`` form field runs Pillow validation, unlike gallery files."""
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (4, 4), (10, 20, 30)).save(buf, "PNG")
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
        InstructorFactory(user=user, status=Member.Status.INVITED)
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
                "sale_kind": "percent",
                "scheduling_type": "single_session",
                "flexible_note": "",
                "recurring_pattern": "",
                "sessions-TOTAL_FORMS": "1",
                "sessions-INITIAL_FORMS": "0",
                "sessions-MIN_NUM_FORMS": "0",
                "sessions-MAX_NUM_FORMS": "1000",
                "faq-TOTAL_FORMS": "0",
                "faq-INITIAL_FORMS": "0",
                "faq-MIN_NUM_FORMS": "0",
                "faq-MAX_NUM_FORMS": "1000",
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
                "description": READY_DESCRIPTION,
                "prerequisites": "",
                "materials_included": "",
                "materials_to_bring": "",
                "safety_requirements": "",
                "age_guardian_note": "",
                "price_cents": 5000,
                "member_discount_pct": 10,
                "capacity": 6,
                "scheduling_model": "flexible",
                "sale_kind": "percent",
                "scheduling_type": "single_session",
                "flexible_note": "We will find a time together.",
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
                # A class needs its own hero plus a gallery photo to pass the submit gate.
                "image": _real_image_file(),
                "gallery_images": [_image_file("x.png")],
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
                "sale_kind": "percent",
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
                "action": "save",
                "gallery_images": [_image_file("x.png"), _image_file("y.png")],
            },
        )
        assert response.status_code == 302
        offering = ClassOffering.objects.get(title="Gallery Class")
        assert ClassImage.objects.filter(class_offering=offering).count() == 2

    def it_refuses_a_gallery_file_that_is_not_an_image_and_saves_nothing(instructor_fixture, client):
        from django.core.files.storage import default_storage

        from classes.models import CLASS_IMAGE_PREFIX

        def stored() -> set[str]:
            try:
                return set(default_storage.listdir(CLASS_IMAGE_PREFIX)[1])
            except FileNotFoundError:
                return set()

        # A hero no other spec uploads, so its content-addressed key is new to storage.
        buf = BytesIO()
        Image.new("RGB", (8, 8), (201, 17, 88)).save(buf, "PNG")
        hero = SimpleUploadedFile("hero.png", buf.getvalue(), content_type="image/png")
        before = stored()
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
                "sale_kind": "percent",
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
                "action": "save",
                "image": hero,
                "gallery_images": [
                    _image_file("x.png"),
                    SimpleUploadedFile("y.png", b"notes", content_type="image/png"),
                ],
            },
        )
        assert response.status_code == 200
        assert "not a photo we can open" in response.content.decode()
        assert not ClassOffering.objects.filter(title="Gallery Class").exists()
        assert not ClassImage.objects.exists()
        assert stored() == before  # the hero form.save() wrote is removed with the row

    def it_date_stamps_the_slug_from_the_first_session(instructor_fixture, client):
        cat = CategoryFactory()
        client.force_login(instructor_fixture.user)
        response = client.post(
            reverse("classes:teach_class_create"),
            {
                "title": "Date Stamped",
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
                "sale_kind": "percent",
                "scheduling_type": "single_session",
                "flexible_note": "",
                "recurring_pattern": "",
                "sessions-TOTAL_FORMS": "1",
                "sessions-INITIAL_FORMS": "0",
                "sessions-MIN_NUM_FORMS": "0",
                "sessions-MAX_NUM_FORMS": "1000",
                "faq-TOTAL_FORMS": "0",
                "faq-INITIAL_FORMS": "0",
                "faq-MIN_NUM_FORMS": "0",
                "faq-MAX_NUM_FORMS": "1000",
                "sessions-0-starts_at": "2026-08-20T18:00",
                "sessions-0-ends_at": "2026-08-20T20:00",
                "images-TOTAL_FORMS": "0",
                "images-INITIAL_FORMS": "0",
                "images-MIN_NUM_FORMS": "0",
                "images-MAX_NUM_FORMS": "1000",
                "action": "save",
            },
        )
        assert response.status_code == 302
        offering = ClassOffering.objects.get(title="Date Stamped")
        # Not the title-only provisional slug ("date-stamped") — the finalized, date-stamped one.
        assert offering.slug == "date-stamped-2026-08-20"


def describe_instructor_edit_class():
    def it_refuses_editing_other_instructors_classes(instructor_fixture, other_instructor, client):
        theirs = ClassOfferingFactory(instructor=other_instructor, slug="theirs-edit")
        client.force_login(instructor_fixture.user)
        response = client.get(reverse("classes:teach_class_edit", kwargs={"pk": theirs.pk}))
        assert response.status_code == 404

    def it_opens_the_light_edit_page_for_published_classes(instructor_fixture, client):
        mine = ClassOfferingFactory(
            instructor=instructor_fixture, slug="pub-light", status=ClassOffering.Status.PUBLISHED
        )
        client.force_login(instructor_fixture.user)
        response = client.get(reverse("classes:teach_class_edit", kwargs={"pk": mine.pk}))
        assert response.status_code == 200
        assert b"This class is live." in response.content

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
            ready=True,
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
    def it_states_the_live_public_page_and_links_to_profile_settings(instructor_fixture, client):
        client.force_login(instructor_fixture.user)
        response = client.get(reverse("classes:teach_profile"))
        assert response.status_code == 200
        html = response.content.decode()
        card = html[
            html.index('data-card="instructor-page"') : html.index(
                "</section>", html.index('data-card="instructor-page"')
            )
        ]
        assert "Your public instructor page is live:" in card
        assert reverse("classes:public_instructor", kwargs={"slug": instructor_fixture.instructor_slug}).encode() in (
            response.content
        )
        assert b"/settings/?tab=profile" in response.content


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
                "faq-TOTAL_FORMS": "0",
                "faq-INITIAL_FORMS": "0",
                "faq-MIN_NUM_FORMS": "0",
                "faq-MAX_NUM_FORMS": "1000",
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
                "sale_kind": "percent",
                "scheduling_type": "single_session",
                "flexible_note": "",
                "recurring_pattern": "",
                "sessions-TOTAL_FORMS": "1",
                "sessions-INITIAL_FORMS": "0",
                "sessions-MIN_NUM_FORMS": "0",
                "sessions-MAX_NUM_FORMS": "1000",
                "faq-TOTAL_FORMS": "0",
                "faq-INITIAL_FORMS": "0",
                "faq-MIN_NUM_FORMS": "0",
                "faq-MAX_NUM_FORMS": "1000",
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
                "description": READY_DESCRIPTION,
                "prerequisites": "",
                "materials_included": "",
                "materials_to_bring": "",
                "safety_requirements": "",
                "age_guardian_note": "",
                "price_cents": mine.price_cents,
                "member_discount_pct": mine.member_discount_pct,
                "capacity": mine.capacity,
                "scheduling_model": "fixed",
                "sale_kind": "percent",
                "scheduling_type": "single_session",
                "flexible_note": "",
                "recurring_pattern": "",
                "sessions-TOTAL_FORMS": "1",
                "sessions-INITIAL_FORMS": "0",
                "sessions-MIN_NUM_FORMS": "0",
                "sessions-MAX_NUM_FORMS": "1000",
                "faq-TOTAL_FORMS": "0",
                "faq-INITIAL_FORMS": "0",
                "faq-MIN_NUM_FORMS": "0",
                "faq-MAX_NUM_FORMS": "1000",
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

        code = DiscountCodeFactory(discount_pct=10, created_by=instructor_fixture.user)
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

        code = DiscountCodeFactory(discount_pct=15, created_by=instructor_fixture.user)
        client.force_login(instructor_fixture.user)
        response = client.get(reverse("classes:teach_discount_code_edit", kwargs={"pk": code.pk}))
        assert response.status_code == 200

    def it_deletes_a_discount_code(instructor_fixture, client):
        """POST to delete removes the code (lines 676-678)."""
        from classes.factories import DiscountCodeFactory
        from classes.models import DiscountCode

        code = DiscountCodeFactory(created_by=instructor_fixture.user)
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

        code = DiscountCodeFactory(created_by=instructor_fixture.user)
        client.force_login(instructor_fixture.user)
        response = client.get(
            reverse("classes:teach_discount_code_delete", kwargs={"pk": code.pk}),
        )
        assert response.status_code == 302
        assert DiscountCode.objects.filter(pk=code.pk).exists()


def describe_instructor_required_admin_without_instructor():
    def it_sends_a_locked_admin_to_the_marketing_page_like_any_member(admin_user, client):
        """The teaching grant applies to admins too — no instructor_oriented_at means the gate."""
        client.force_login(admin_user)
        response = client.get(reverse("classes:teach_dashboard"))
        assert response.status_code == 302
        assert response.url == reverse("classes:teach_overview")

    def it_admits_an_admin_once_unlocked(admin_user, client):
        member = Member.objects.get(user=admin_user)
        member.instructor_oriented_at = timezone.now()
        member.save(update_fields=["instructor_oriented_at"])
        client.force_login(admin_user)
        response = client.get(reverse("classes:teach_dashboard"))
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# My Classes: sortable headers, the Date(s) column, facet and sort together, pagination (#544)
# ---------------------------------------------------------------------------


def _rows(client, query: str = "") -> list[str]:
    response = client.get(reverse("classes:teach_dashboard") + query)
    assert response.status_code == 200
    return [c.slug for c in response.context["page"]]


def _lean(instructor, **kwargs) -> ClassOffering:
    """A class with no hero and no gallery photo: the list reads neither, and a page of 26 adds up."""
    return ClassOfferingFactory(instructor=instructor, image="", gallery=0, **kwargs)


def _dated(instructor) -> None:
    """Three classes: one dated early, one late, one with no sessions at all."""
    now = timezone.now()
    early = _lean(instructor, title="Early", slug="sort-early")
    ClassSessionFactory(class_offering=early, starts_at=now + timedelta(days=1))
    late = _lean(instructor, title="Late", slug="sort-late")
    ClassSessionFactory(class_offering=late, starts_at=now + timedelta(days=30))
    _lean(instructor, title="Undated", slug="sort-undated")


def describe_instructor_list_sorting():
    def it_lists_newest_created_first_by_default(instructor_fixture, client):
        _lean(instructor_fixture, slug="first-made")
        _lean(instructor_fixture, slug="second-made")
        client.force_login(instructor_fixture.user)
        assert _rows(client) == ["second-made", "first-made"]

    def it_sorts_by_title_both_ways(instructor_fixture, client):
        _lean(instructor_fixture, title="Bravo", slug="bravo")
        _lean(instructor_fixture, title="Alpha", slug="alpha")
        client.force_login(instructor_fixture.user)
        assert _rows(client, "?sort=title&dir=asc") == ["alpha", "bravo"]
        assert _rows(client, "?sort=title&dir=desc") == ["bravo", "alpha"]

    def it_sorts_by_guild_type(instructor_fixture, client):
        _lean(instructor_fixture, slug="wood", category=CategoryFactory(name="Woodshop"))
        _lean(instructor_fixture, slug="metal", category=CategoryFactory(name="Metalshop"))
        client.force_login(instructor_fixture.user)
        assert _rows(client, "?sort=category__name&dir=asc") == ["metal", "wood"]
        assert _rows(client, "?sort=category__name&dir=desc") == ["wood", "metal"]

    def it_sorts_by_first_session_with_undated_classes_last_either_way(instructor_fixture, client):
        _dated(instructor_fixture)
        client.force_login(instructor_fixture.user)
        assert _rows(client, "?sort=first_session&dir=asc") == ["sort-early", "sort-late", "sort-undated"]
        assert _rows(client, "?sort=first_session&dir=desc") == ["sort-late", "sort-early", "sort-undated"]

    def it_sorts_by_status(instructor_fixture, client):
        _lean(instructor_fixture, slug="live", status=ClassOffering.Status.PUBLISHED)
        _lean(instructor_fixture, slug="draft", status=ClassOffering.Status.DRAFT)
        client.force_login(instructor_fixture.user)
        assert _rows(client, "?sort=lifecycle_order&dir=asc") == ["draft", "live"]
        assert _rows(client, "?sort=lifecycle_order&dir=desc") == ["live", "draft"]

    def it_sorts_by_registrations(instructor_fixture, client):
        _lean(instructor_fixture, slug="quiet")
        busy = _lean(instructor_fixture, slug="busy")
        RegistrationFactory(class_offering=busy)
        client.force_login(instructor_fixture.user)
        assert _rows(client, "?sort=registration_count&dir=desc") == ["busy", "quiet"]
        assert _rows(client, "?sort=registration_count&dir=asc") == ["quiet", "busy"]

    def it_falls_back_to_the_default_order_for_an_unknown_sort(instructor_fixture, client):
        _lean(instructor_fixture, slug="first-made")
        _lean(instructor_fixture, slug="second-made")
        client.force_login(instructor_fixture.user)
        response = client.get(reverse("classes:teach_dashboard") + "?sort=nonsense")
        assert response.status_code == 200
        assert [c.slug for c in response.context["page"]] == ["second-made", "first-made"]
        assert response.context["sort"] == "created_at"
        assert "sort=nonsense" not in response.content.decode()

    def it_marks_the_sorted_header_and_glyphs_every_other(instructor_fixture, client):
        _lean(instructor_fixture)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_dashboard") + "?sort=title&dir=asc").content.decode()
        assert html.count("aria-sort=") == 1
        assert '<th aria-sort="ascending">' in html
        assert html.count('class="pl-sort-header__glyph"') == 5
        assert html.count('class="pl-sort-header pl-sort-header--active"') == 1


def describe_instructor_list_facets_and_sort_together():
    def it_keeps_the_facet_when_sorting_and_the_sort_when_switching_facets(instructor_fixture, client):
        _lean(instructor_fixture, slug="draft-one", status=ClassOffering.Status.DRAFT)
        _lean(instructor_fixture, slug="live-one", status=ClassOffering.Status.PUBLISHED)
        client.force_login(instructor_fixture.user)
        response = client.get(reverse("classes:teach_dashboard") + "?facet=needs_attention&sort=title&dir=asc")
        assert response.context["selected_facet"].key == "needs_attention"
        assert [c.slug for c in response.context["page"]] == ["draft-one"]
        header_links = re.findall(r'<a class="pl-sort-header[^"]*" href="([^"]+)"', response.content.decode())
        assert len(header_links) == 5
        assert all("facet=needs_attention" in link for link in header_links)
        chips = {row.key: row for row in response.context["facets"]}
        assert chips["needs_attention"].is_selected
        assert all("sort=title" in row.url and "dir=asc" in row.url for row in chips.values())
        assert chips[""].url == "?sort=title&dir=asc"
        assert chips["upcoming"].url == "?sort=title&dir=asc&facet=upcoming"

    def it_never_echoes_a_junk_facet(instructor_fixture, client):
        _lean(instructor_fixture)
        client.force_login(instructor_fixture.user)
        response = client.get(reverse("classes:teach_dashboard") + "?facet=bogus&sort=title")
        assert response.context["selected_facet"].key == ""
        assert "facet=bogus" not in response.content.decode()

    def it_paginates_at_25_and_the_page_link_keeps_the_facet_and_the_sort(instructor_fixture, client):
        for i in range(26):
            _lean(instructor_fixture, title=f"Draft {i:02d}", slug=f"draft-{i:02d}", status=ClassOffering.Status.DRAFT)
        client.force_login(instructor_fixture.user)
        first = client.get(reverse("classes:teach_dashboard") + "?facet=needs_attention&sort=title&dir=asc")
        assert len(first.context["page"]) == 25
        assert first.context["page"].paginator.num_pages == 2
        link = re.search(r'href="(\?page=2[^"]*)"', first.content.decode())
        assert link is not None
        page_link = link.group(1).replace("&amp;", "&")
        assert "facet=needs_attention" in page_link
        assert "sort=title" in page_link
        assert "dir=asc" in page_link
        second = client.get(reverse("classes:teach_dashboard") + page_link)
        assert [c.slug for c in second.context["page"]] == ["draft-25"]
        assert second.context["selected_facet"].key == "needs_attention"


def describe_instructor_list_markup():
    def it_renders_the_dates_like_the_admin_list(instructor_fixture, client):
        # 20:00 UTC is early afternoon in America/Los_Angeles, so the calendar day holds.
        ranged = _lean(instructor_fixture, slug="ranged")
        ClassSessionFactory(class_offering=ranged, starts_at=datetime(2026, 10, 10, 20, tzinfo=dt_timezone.utc))
        ClassSessionFactory(class_offering=ranged, starts_at=datetime(2026, 10, 22, 20, tzinfo=dt_timezone.utc))
        single = _lean(instructor_fixture, slug="single")
        ClassSessionFactory(class_offering=single, starts_at=datetime(2026, 11, 3, 20, tzinfo=dt_timezone.utc))
        _lean(instructor_fixture, slug="undated")
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_dashboard")).content.decode()
        assert '<td class="pl-class-list__dates">Oct 10 – Oct 22, 2026</td>' in html
        assert '<td class="pl-class-list__dates">Nov 3, 2026</td>' in html
        assert html.count('<span class="pl-class-list__no-dates">') == 1

    def it_uses_the_admin_table_styling_with_no_inline_styles(instructor_fixture, client):
        _lean(instructor_fixture, status=ClassOffering.Status.PUBLISHED)
        _lean(instructor_fixture, status=ClassOffering.Status.DRAFT)
        ClassOfferingFactory(instructor=instructor_fixture, status=ClassOffering.Status.DRAFT, ready=True)
        _lean(instructor_fixture, status=ClassOffering.Status.ARCHIVED)
        client.force_login(instructor_fixture.user)
        html = client.get(reverse("classes:teach_dashboard")).content.decode()
        table = html[html.index('<div class="admin-table-wrap">') : html.index("</table>")]
        assert "style=" not in table
        assert '<td class="pl-class-list__actions">' in table
        assert '<tr class="pl-class-list__row--archived">' in table
        assert "Submit for review" in table
