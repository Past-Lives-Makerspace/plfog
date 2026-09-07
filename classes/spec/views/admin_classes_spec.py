"""BDD specs for admin classes tab — routing + gating."""

from __future__ import annotations

from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse


def _image_file(name: str = "shot.png") -> SimpleUploadedFile:
    buf = BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def _real_image_file(name: str = "hero.png") -> SimpleUploadedFile:
    """A genuine PNG: the hero ``image`` form field runs Pillow validation, unlike gallery files."""
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (4, 4), (10, 20, 30)).save(buf, "PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def _future_session_fields() -> dict[str, str]:
    """One session ten days out, so a created class passes the publish readiness check."""
    from datetime import timedelta

    from django.utils import timezone

    start = timezone.now() + timedelta(days=10)
    return {
        "sessions-TOTAL_FORMS": "1",
        "sessions-0-starts_at": start.strftime("%Y-%m-%dT%H:%M"),
        "sessions-0-ends_at": (start + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
    }


def _publishable_fields() -> dict:
    """The readiness essentials the admin create form must carry to publish: photos + a real description."""
    from classes.factories import READY_DESCRIPTION

    return {
        "description": READY_DESCRIPTION,
        "image": _real_image_file(),
        "gallery_images": [_image_file("ready.png")],
        **_future_session_fields(),
    }


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
                "prerequisites": "",
                "materials_included": "",
                "materials_to_bring": "",
                "safety_requirements": "",
                "age_guardian_note": "",
                "flexible_note": "",
                "private_for_name": "",
                "recurring_pattern": "",
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
                **_publishable_fields(),
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
        from classes.factories import READY_DESCRIPTION
        from classes.models import ClassOffering

        client.force_login(admin_user)
        cat = CategoryFactory()
        inst = InstructorFactory()
        session_fields = _future_session_fields()
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
                "description": READY_DESCRIPTION,
                "image": _real_image_file(),
                "gallery_images": [_image_file("g.png")],
                "prerequisites": "",
                "materials_included": "",
                "materials_to_bring": "",
                "safety_requirements": "",
                "age_guardian_note": "",
                "flexible_note": "",
                "private_for_name": "",
                "recurring_pattern": "",
                "sessions-INITIAL_FORMS": "0",
                "sessions-MIN_NUM_FORMS": "0",
                "sessions-MAX_NUM_FORMS": "1000",
                "faq-TOTAL_FORMS": "0",
                "faq-INITIAL_FORMS": "0",
                "faq-MIN_NUM_FORMS": "0",
                "faq-MAX_NUM_FORMS": "1000",
                **session_fields,
                "images-TOTAL_FORMS": "0",
                "images-INITIAL_FORMS": "0",
                "images-MIN_NUM_FORMS": "0",
                "images-MAX_NUM_FORMS": "1000",
            },
        )
        assert response.status_code == 302
        offering = ClassOffering.objects.get(title="Admin Stamped")
        assert offering.slug == "admin-stamped-" + session_fields["sessions-0-starts_at"][:10]

    def it_saves_gallery_images_on_create(admin_user, client, db):
        from classes.factories import CategoryFactory, InstructorFactory
        from classes.factories import READY_DESCRIPTION
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
                "description": READY_DESCRIPTION,
                "image": _real_image_file(),
                **_future_session_fields(),
                "prerequisites": "",
                "materials_included": "",
                "materials_to_bring": "",
                "safety_requirements": "",
                "age_guardian_note": "",
                "flexible_note": "",
                "private_for_name": "",
                "recurring_pattern": "",
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
        from classes.factories import READY_DESCRIPTION
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
                "scheduling_model": "flexible",
                "sale_kind": "percent",
                "scheduling_type": "single_session",
                "description": READY_DESCRIPTION,
                "image": _real_image_file(),
                "prerequisites": "",
                "materials_included": "",
                "materials_to_bring": "",
                "safety_requirements": "",
                "age_guardian_note": "",
                "flexible_note": "We will pick a time together.",
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


def describe_mine_filter():
    def it_filters_to_classes_taught_by_me(admin_user, client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        me = admin_user.member
        ClassOfferingFactory(title="Mine Taught", instructor=me, status=ClassOffering.Status.PUBLISHED)
        ClassOfferingFactory(title="Not Mine Taught", status=ClassOffering.Status.PUBLISHED)
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_classes") + "?mine=1")
        assert b"Mine Taught" in response.content
        assert b"Not Mine Taught" not in response.content

    def it_includes_classes_i_authored_but_do_not_teach(admin_user, client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        me = admin_user.member
        # created_by=me, instructor is a different member (factory default) → still mine.
        ClassOfferingFactory(title="I Authored This", created_by=me, status=ClassOffering.Status.PUBLISHED)
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_classes") + "?mine=1")
        assert b"I Authored This" in response.content

    def it_excludes_classes_where_i_am_neither(admin_user, client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        me = admin_user.member
        ClassOfferingFactory(title="Keep Mine", instructor=me, status=ClassOffering.Status.PUBLISHED)
        # NULL instructor AND NULL author — a memberful "mine" must not match these either.
        ClassOfferingFactory(
            title="Orphan Class", instructor=None, created_by=None, status=ClassOffering.Status.PUBLISHED
        )
        ClassOfferingFactory(title="Someone Elses", status=ClassOffering.Status.PUBLISHED)
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_classes") + "?mine=1")
        assert b"Keep Mine" in response.content
        assert b"Orphan Class" not in response.content
        assert b"Someone Elses" not in response.content

    def it_composes_with_status_and_search(admin_user, client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        me = admin_user.member
        ClassOfferingFactory(title="Alpha Published", instructor=me, status=ClassOffering.Status.PUBLISHED)
        ClassOfferingFactory(title="Alpha Draft", instructor=me, status=ClassOffering.Status.DRAFT)
        ClassOfferingFactory(title="Zeta Published", instructor=me, status=ClassOffering.Status.PUBLISHED)
        ClassOfferingFactory(title="Alpha NotMine", status=ClassOffering.Status.PUBLISHED)
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_classes") + "?mine=1&status=upcoming&q=Alpha")
        assert b"Alpha Published" in response.content
        assert b"Alpha Draft" not in response.content  # wrong status
        assert b"Zeta Published" not in response.content  # does not match q
        assert b"Alpha NotMine" not in response.content  # not mine

    def it_shows_the_pill_without_an_instructor_slug(admin_user, client, db):
        # The admin_user's member has no instructor_slug — the exact condition that
        # hid the old toggle. The pill must render anyway.
        assert not admin_user.member.instructor_slug
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_classes"))
        assert b"My Classes" in response.content

    def it_returns_empty_for_a_user_with_no_member(client, db):
        from classes.factories import ClassOfferingFactory, UserFactory
        from classes.models import ClassOffering

        # A superuser passes the admin gate even with no linked Member.
        user = UserFactory(username="super-nomember@example.com", is_superuser=True, is_staff=True)
        user.member.delete()
        # A NULL-instructor/NULL-author class must NOT leak to a memberless "mine".
        ClassOfferingFactory(
            title="Orphan Leak", instructor=None, created_by=None, status=ClassOffering.Status.PUBLISHED
        )
        client.force_login(user)
        response = client.get(reverse("classes:admin_classes") + "?mine=1")
        assert response.status_code == 200
        assert b"Orphan Leak" not in response.content
        assert response.context["mine_count"] == 0

    def it_ignores_bogus_mine_values(admin_user, client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        me = admin_user.member
        ClassOfferingFactory(title="My One", instructor=me, status=ClassOffering.Status.PUBLISHED)
        ClassOfferingFactory(title="Other One", status=ClassOffering.Status.PUBLISHED)
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_classes") + "?mine=yes")
        assert response.context["mine_active"] is False
        assert b"My One" in response.content
        assert b"Other One" in response.content

    def it_counts_my_classes_across_all_statuses(admin_user, client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        me = admin_user.member
        ClassOfferingFactory(instructor=me, status=ClassOffering.Status.PUBLISHED)
        ClassOfferingFactory(instructor=me, status=ClassOffering.Status.DRAFT)
        ClassOfferingFactory(instructor=me, status=ClassOffering.Status.ARCHIVED)
        ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)  # not mine
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_classes") + "?status=published")
        # Count is global — all three of mine, not just the published one.
        assert response.context["mine_count"] == 3

    def it_counts_my_classes_ignoring_the_search_box(admin_user, client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        me = admin_user.member
        ClassOfferingFactory(title="Findable Mine", instructor=me, status=ClassOffering.Status.PUBLISHED)
        ClassOfferingFactory(title="Hidden By Search", instructor=me, status=ClassOffering.Status.PUBLISHED)
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_classes") + "?q=Findable")
        assert response.context["mine_count"] == 2

    def it_preserves_mine_in_status_pill_urls(admin_user, client, db):
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_classes") + "?mine=1&q=pottery")
        for url, _label, _count, _selected in response.context["status_filters"]:
            assert "mine=1" in url
            assert "q=pottery" in url

    def it_preserves_mine_in_base_params(admin_user, client, db):
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_classes") + "?mine=1")
        assert "mine=1" in response.context["base_params"]

    def it_preserves_mine_when_searching(admin_user, client, db):
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_classes") + "?mine=1&status=published&instructor=5")
        html = response.content.decode()
        # The search form carries the sibling filters as hidden inputs.
        assert '<input type="hidden" name="mine" value="1">' in html
        assert '<input type="hidden" name="status" value="published">' in html
        assert '<input type="hidden" name="instructor" value="5">' in html
        # Clearing the search drops only q, keeping mine and the rest.
        clear = response.context["search_clear_url"]
        assert "mine=1" in clear
        assert "status=published" in clear
        assert "q=" not in clear

    def it_strips_bogus_mine_from_computed_urls(admin_user, client, db):
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_classes") + "?mine=yes&status=published")
        # No pill / clear URL echoes the bogus value or carries a mine at all (it is off).
        for url, _label, _count, _selected in response.context["status_filters"]:
            assert "mine" not in url
        assert "mine" not in response.context["search_clear_url"]
        assert "mine" not in response.context["mine_clear_url"]
        assert "mine" not in response.context["instructor_clear_url"]
        # The toggle is the turn-ON link → a clean mine=1, never mine=yes.
        assert "mine=yes" not in response.context["mine_toggle_url"]
        assert "mine=1" in response.context["mine_toggle_url"]

    def it_renders_the_mine_empty_state_with_a_clear_link(admin_user, client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        # The admin owns no classes → mine=1 is empty.
        ClassOfferingFactory(title="Someone Elses Only", status=ClassOffering.Status.PUBLISHED)
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_classes") + "?mine=1")
        html = response.content.decode()
        assert "not listed as instructor or author" in html
        assert "Show all classes" in html
        # The clear link drops only mine.
        assert "mine" not in response.context["mine_clear_url"]

    def it_uses_the_real_user_under_view_as_preview(admin_user, client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        me = admin_user.member
        ClassOfferingFactory(title="Real Mine", instructor=me, status=ClassOffering.Status.PUBLISHED)
        ClassOfferingFactory(title="Not Real Mine", status=ClassOffering.Status.PUBLISHED)
        client.force_login(admin_user)
        session = client.session
        session["view_as_role"] = "member"
        session.save()
        response = client.get(reverse("classes:admin_classes") + "?mine=1")
        # Mine follows the real request.user.member, not the previewed role.
        assert b"Real Mine" in response.content
        assert b"Not Real Mine" not in response.content


def describe_table_search_component():
    def it_leaves_other_table_search_callers_unchanged():
        from django.template.loader import render_to_string

        # A caller that passes neither preserved_fields nor clear_url (e.g. the
        # categories admin) gets no hidden inputs and the bare href="?" clear link.
        html = render_to_string("components/table_search.html", {"q": "anything", "placeholder": "Search…"})
        assert 'type="hidden"' not in html
        assert 'href="?"' in html


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
        offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.PENDING)
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
