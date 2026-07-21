"""BDD specs for admin classes tab — routing + gating."""

from __future__ import annotations

from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse


def _image_file(name: str = "shot.png") -> SimpleUploadedFile:
    buf = BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def describe_status_filter():
    def it_shows_all_by_default(admin_user, client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        client.force_login(admin_user)
        ClassOfferingFactory(title="Drafty", status=ClassOffering.Status.DRAFT)
        ClassOfferingFactory(title="Up-n-Live", status=ClassOffering.Status.PUBLISHED)
        response = client.get(reverse("classes:admin_classes"))
        assert response.status_code == 200
        assert b"Drafty" in response.content
        assert b"Up-n-Live" in response.content

    def it_filters_by_status_when_param_given(admin_user, client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        client.force_login(admin_user)
        ClassOfferingFactory(title="Drafty", status=ClassOffering.Status.DRAFT)
        ClassOfferingFactory(title="Old-News", status=ClassOffering.Status.ARCHIVED)
        response = client.get(reverse("classes:admin_classes") + "?status=archived")
        assert response.status_code == 200
        assert b"Drafty" not in response.content
        assert b"Old-News" in response.content


def describe_classes_date_column():
    def it_annotates_the_first_and_last_session_dates(admin_user, client, db):
        from datetime import timedelta

        from django.utils import timezone

        from classes.factories import ClassOfferingFactory, ClassSessionFactory
        from classes.models import ClassOffering

        client.force_login(admin_user)
        offering = ClassOfferingFactory(title="Ranged", status=ClassOffering.Status.PUBLISHED)
        start = timezone.now()
        ClassSessionFactory(class_offering=offering, starts_at=start)
        ClassSessionFactory(class_offering=offering, starts_at=start + timedelta(days=5))
        response = client.get(reverse("classes:admin_classes"))
        row = next(c for c in response.context["page"] if c.pk == offering.pk)
        assert row.first_session.date() == start.date()
        assert row.last_session.date() == (start + timedelta(days=5)).date()

    def it_leaves_session_dates_empty_for_a_class_with_no_sessions(admin_user, client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        client.force_login(admin_user)
        offering = ClassOfferingFactory(title="No Sessions", status=ClassOffering.Status.DRAFT)
        response = client.get(reverse("classes:admin_classes"))
        row = next(c for c in response.context["page"] if c.pk == offering.pk)
        assert row.first_session is None
        assert row.last_session is None

    def it_counts_registrations_independently_of_session_count(admin_user, client, db):
        from datetime import timedelta

        from django.utils import timezone

        from classes.factories import ClassOfferingFactory, ClassSessionFactory, RegistrationFactory
        from classes.models import ClassOffering

        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        # Two sessions joined alongside the registrations would double a naive (non-distinct)
        # registration tally — this pins the count to the real number of registrations.
        ClassSessionFactory(class_offering=offering, starts_at=timezone.now())
        ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=1))
        RegistrationFactory(class_offering=offering)
        RegistrationFactory(class_offering=offering)
        response = client.get(reverse("classes:admin_classes"))
        row = next(c for c in response.context["page"] if c.pk == offering.pk)
        assert row.registration_count == 2


def describe_delete_class():
    def it_deletes_a_draft_with_no_registrations(admin_user, client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
        response = client.post(reverse("classes:admin_class_delete", kwargs={"pk": offering.pk}))
        assert response.status_code == 302
        assert not ClassOffering.objects.filter(pk=offering.pk).exists()

    def it_deletes_a_published_class_with_no_registrations(admin_user, client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        response = client.post(reverse("classes:admin_class_delete", kwargs={"pk": offering.pk}))
        assert response.status_code == 302
        assert not ClassOffering.objects.filter(pk=offering.pk).exists()

    def it_refuses_to_delete_when_registrations_exist(admin_user, client, db):
        from classes.factories import ClassOfferingFactory, RegistrationFactory
        from classes.models import ClassOffering

        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
        RegistrationFactory(class_offering=offering)
        response = client.post(reverse("classes:admin_class_delete", kwargs={"pk": offering.pk}))
        assert response.status_code == 302
        assert ClassOffering.objects.filter(pk=offering.pk).exists()

    def it_ignores_get_requests(admin_user, client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
        response = client.get(reverse("classes:admin_class_delete", kwargs={"pk": offering.pk}))
        assert response.status_code == 302
        assert ClassOffering.objects.filter(pk=offering.pk).exists()

    def describe_delete_button_visibility_on_detail():
        def it_shows_delete_on_a_published_class_with_no_registrations(admin_user, client, db):
            from classes.factories import ClassOfferingFactory
            from classes.models import ClassOffering

            client.force_login(admin_user)
            offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
            response = client.get(reverse("classes:admin_class_detail", kwargs={"pk": offering.pk}))
            assert response.status_code == 200
            delete_url = reverse("classes:admin_class_delete", kwargs={"pk": offering.pk})
            assert delete_url.encode() in response.content

        def it_hides_delete_when_class_has_registrations(admin_user, client, db):
            from classes.factories import ClassOfferingFactory, RegistrationFactory
            from classes.models import ClassOffering

            client.force_login(admin_user)
            offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
            RegistrationFactory(class_offering=offering)
            response = client.get(reverse("classes:admin_class_detail", kwargs={"pk": offering.pk}))
            assert response.status_code == 200
            delete_url = reverse("classes:admin_class_delete", kwargs={"pk": offering.pk})
            assert delete_url.encode() not in response.content


def describe_admin_classes_routing():
    def it_gates_tab_views_behind_admin_role(member_user, client):
        client.force_login(member_user)
        response = client.get(reverse("classes:admin_classes"))
        assert response.status_code == 403

    def it_renders_classes_tab_for_admin(admin_user, client):
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_classes"))
        assert response.status_code == 200
        assert b"Classes" in response.content


def describe_create_class():
    def it_renders_the_create_form_on_get(admin_user, client, db):
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_class_create"))
        assert response.status_code == 200

    def it_creates_a_class(admin_user, client, db):
        from classes.factories import CategoryFactory, InstructorFactory

        client.force_login(admin_user)
        cat = CategoryFactory()
        inst = InstructorFactory()
        response = client.post(
            reverse("classes:admin_class_create"),
            {
                "title": "New Class",
                "slug": "new-class",
                "category": cat.pk,
                "instructor": inst.pk,
                "price_cents": "50.00",
                "member_discount_pct": 10,
                "capacity": 6,
                "scheduling_model": "fixed",
                "sale_kind": "percent",
                "scheduling_type": "single_session",
                "description": "d",
                "prerequisites": "",
                "materials_included": "",
                "materials_to_bring": "",
                "safety_requirements": "",
                "age_guardian_note": "",
                "flexible_note": "",
                "private_for_name": "",
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
            },
        )
        assert response.status_code == 302
        from classes.models import ClassOffering

        created = ClassOffering.objects.get(title="New Class")
        assert created.status == ClassOffering.Status.PUBLISHED

    def it_no_longer_exposes_a_hand_typed_slug_field(admin_user, client, db):
        from classes.forms import ClassOfferingForm

        assert "slug" not in ClassOfferingForm().fields

    def it_date_stamps_the_slug_from_the_first_session(admin_user, client, db):
        from classes.factories import CategoryFactory, InstructorFactory
        from classes.models import ClassOffering

        client.force_login(admin_user)
        cat = CategoryFactory()
        inst = InstructorFactory()
        response = client.post(
            reverse("classes:admin_class_create"),
            {
                "title": "Admin Stamped",
                "category": cat.pk,
                "instructor": inst.pk,
                "price_cents": "50.00",
                "member_discount_pct": 10,
                "capacity": 6,
                "scheduling_model": "fixed",
                "sale_kind": "percent",
                "scheduling_type": "single_session",
                "description": "d",
                "prerequisites": "",
                "materials_included": "",
                "materials_to_bring": "",
                "safety_requirements": "",
                "age_guardian_note": "",
                "flexible_note": "",
                "private_for_name": "",
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
            },
        )
        assert response.status_code == 302
        offering = ClassOffering.objects.get(title="Admin Stamped")
        assert offering.slug == "admin-stamped-2026-08-20"

    def it_saves_gallery_images_on_create(admin_user, client, db):
        from classes.factories import CategoryFactory, InstructorFactory
        from classes.models import ClassImage, ClassOffering

        client.force_login(admin_user)
        cat = CategoryFactory()
        inst = InstructorFactory()
        response = client.post(
            reverse("classes:admin_class_create"),
            {
                "title": "Gallery Class",
                "slug": "gallery-class",
                "category": cat.pk,
                "instructor": inst.pk,
                "price_cents": "50.00",
                "member_discount_pct": 10,
                "capacity": 6,
                "scheduling_model": "fixed",
                "sale_kind": "percent",
                "scheduling_type": "single_session",
                "description": "d",
                "prerequisites": "",
                "materials_included": "",
                "materials_to_bring": "",
                "safety_requirements": "",
                "age_guardian_note": "",
                "flexible_note": "",
                "private_for_name": "",
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
                "gallery_images": [_image_file("a.png"), _image_file("b.png")],
            },
        )
        assert response.status_code == 302
        offering = ClassOffering.objects.get(title="Gallery Class")
        assert ClassImage.objects.filter(class_offering=offering).count() == 2

    def it_rejects_an_over_cap_gallery_batch_without_publishing(admin_user, client, db):
        from classes.factories import CategoryFactory, InstructorFactory
        from classes.models import ClassOffering

        client.force_login(admin_user)
        cat = CategoryFactory()
        inst = InstructorFactory()
        response = client.post(
            reverse("classes:admin_class_create"),
            {
                "title": "Too Many Photos",
                "slug": "too-many-photos",
                "category": cat.pk,
                "instructor": inst.pk,
                "price_cents": "50.00",
                "member_discount_pct": 10,
                "capacity": 6,
                "scheduling_model": "fixed",
                "sale_kind": "percent",
                "scheduling_type": "single_session",
                "description": "d",
                "prerequisites": "",
                "materials_included": "",
                "materials_to_bring": "",
                "safety_requirements": "",
                "age_guardian_note": "",
                "flexible_note": "",
                "private_for_name": "",
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
                "gallery_images": [_image_file(f"{i}.png") for i in range(11)],
            },
        )
        assert response.status_code == 200
        assert "at most 10 images" in response.content.decode().lower()
        assert not ClassOffering.objects.filter(slug="too-many-photos").exists()


def describe_edit_class():
    def it_renders_the_edit_form_on_get(admin_user, client, db):
        from classes.factories import ClassOfferingFactory

        client.force_login(admin_user)
        offering = ClassOfferingFactory()
        response = client.get(reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}))
        assert response.status_code == 200

    def it_saves_the_edit_on_post(admin_user, client, db):
        from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory

        client.force_login(admin_user)
        offering = ClassOfferingFactory(title="Old Title")
        response = client.post(
            reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}),
            {
                "title": "New Title",
                "slug": offering.slug,
                "category": CategoryFactory().pk,
                "instructor": InstructorFactory().pk,
                "price_cents": f"{offering.price_cents / 100:.2f}",
                "member_discount_pct": offering.member_discount_pct,
                "capacity": offering.capacity,
                "scheduling_model": offering.scheduling_model,
                "sale_kind": "percent",
                "scheduling_type": offering.scheduling_type,
                "description": offering.description,
                "prerequisites": "",
                "materials_included": "",
                "materials_to_bring": "",
                "safety_requirements": "",
                "age_guardian_note": "",
                "flexible_note": "",
                "private_for_name": "",
                "images-TOTAL_FORMS": "0",
                "images-INITIAL_FORMS": "0",
                "images-MIN_NUM_FORMS": "0",
                "images-MAX_NUM_FORMS": "1000",
                "sessions-TOTAL_FORMS": "0",
                "sessions-INITIAL_FORMS": "0",
                "sessions-MIN_NUM_FORMS": "0",
                "sessions-MAX_NUM_FORMS": "1000",
                "faq-TOTAL_FORMS": "0",
                "faq-INITIAL_FORMS": "0",
                "faq-MIN_NUM_FORMS": "0",
                "faq-MAX_NUM_FORMS": "1000",
            },
        )
        assert response.status_code == 302
        offering.refresh_from_db()
        assert offering.title == "New Title"

    def it_never_reslugs_an_existing_offering_on_edit(admin_user, client, db):
        from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory
        from classes.models import ClassOffering

        client.force_login(admin_user)
        offering = ClassOfferingFactory(
            title="Original",
            slug="original-2025-01-01",
            status=ClassOffering.Status.PUBLISHED,
        )
        response = client.post(
            reverse("classes:admin_class_edit", kwargs={"pk": offering.pk}),
            {
                "title": "Renamed To Something Else",
                "category": CategoryFactory().pk,
                "instructor": InstructorFactory().pk,
                "price_cents": f"{offering.price_cents / 100:.2f}",
                "member_discount_pct": offering.member_discount_pct,
                "capacity": offering.capacity,
                "scheduling_model": offering.scheduling_model,
                "sale_kind": "percent",
                "scheduling_type": offering.scheduling_type,
                "description": offering.description,
                "prerequisites": "",
                "materials_included": "",
                "materials_to_bring": "",
                "safety_requirements": "",
                "age_guardian_note": "",
                "flexible_note": "",
                "private_for_name": "",
                "images-TOTAL_FORMS": "0",
                "images-INITIAL_FORMS": "0",
                "images-MIN_NUM_FORMS": "0",
                "images-MAX_NUM_FORMS": "1000",
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
            },
        )
        assert response.status_code == 302
        offering.refresh_from_db()
        # Title and sessions changed, but the already-indexed slug must be left alone.
        assert offering.title == "Renamed To Something Else"
        assert offering.sessions.count() == 1
        assert offering.slug == "original-2025-01-01"


def describe_class_detail():
    def it_shows_the_detail_page(admin_user, client, db):
        from classes.factories import ClassOfferingFactory

        client.force_login(admin_user)
        offering = ClassOfferingFactory(title="Detailed Class")
        response = client.get(reverse("classes:admin_class_detail", kwargs={"pk": offering.pk}))
        assert response.status_code == 200
        assert b"Detailed Class" in response.content


def describe_approve_class():
    def it_transitions_pending_to_published(admin_user, client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PENDING)
        response = client.post(reverse("classes:admin_class_approve", kwargs={"pk": offering.pk}))
        assert response.status_code == 302
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED

    def it_ignores_get_on_approve_and_redirects(admin_user, client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PENDING)
        response = client.get(reverse("classes:admin_class_approve", kwargs={"pk": offering.pk}))
        assert response.status_code == 302
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PENDING

    def it_flashes_an_error_when_class_is_not_pending(admin_user, client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT)
        response = client.post(reverse("classes:admin_class_approve", kwargs={"pk": offering.pk}))
        assert response.status_code == 302
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.DRAFT


def describe_archive_class():
    def it_archives_class(admin_user, client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        response = client.post(reverse("classes:admin_class_archive", kwargs={"pk": offering.pk}))
        assert response.status_code == 302
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.ARCHIVED

    def it_ignores_get_on_archive_and_redirects(admin_user, client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        client.force_login(admin_user)
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        response = client.get(reverse("classes:admin_class_archive", kwargs={"pk": offering.pk}))
        assert response.status_code == 302
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.PUBLISHED


def describe_duplicate_class():
    def it_duplicates_as_draft(admin_user, client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        client.force_login(admin_user)
        src = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        response = client.post(reverse("classes:admin_class_duplicate", kwargs={"pk": src.pk}))
        assert response.status_code == 302
        assert ClassOffering.objects.count() == 2
        copy = ClassOffering.objects.exclude(pk=src.pk).first()
        assert copy.status == ClassOffering.Status.DRAFT
        assert "copy" in copy.title.lower()

    def it_ignores_get_on_duplicate_and_redirects(admin_user, client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        client.force_login(admin_user)
        src = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        response = client.get(reverse("classes:admin_class_duplicate", kwargs={"pk": src.pk}))
        assert response.status_code == 302
        assert ClassOffering.objects.count() == 1

    def it_gives_second_copy_a_unique_slug(admin_user, client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        client.force_login(admin_user)
        src = ClassOfferingFactory(slug="pottery", status=ClassOffering.Status.PUBLISHED)
        client.post(reverse("classes:admin_class_duplicate", kwargs={"pk": src.pk}))
        client.post(reverse("classes:admin_class_duplicate", kwargs={"pk": src.pk}))
        slugs = set(ClassOffering.objects.values_list("slug", flat=True))
        assert "pottery" in slugs
        assert "pottery-copy" in slugs
        assert "pottery-copy-2" in slugs
