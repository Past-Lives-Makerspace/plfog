"""BDD specs for the printable class flyer (standalone print page, editor-gated)."""

from __future__ import annotations

from django.template.defaultfilters import date as date_filter
from django.urls import reverse
from django.utils.timezone import localtime

from classes.factories import ClassOfferingFactory, SeriesClassOfferingFactory
from classes.models import ClassOffering


def describe_class_flyer():
    def describe_access():
        def it_renders_for_the_owning_instructor(member_user, client, free_offering, db):
            free_offering.instructor = member_user.member
            free_offering.save(update_fields=["instructor"])
            client.force_login(member_user)
            resp = client.get(reverse("classes:class_flyer", args=[free_offering.pk]))
            assert resp.status_code == 200

        def it_renders_for_an_admin(admin_user, client, free_offering, db):
            client.force_login(admin_user)
            resp = client.get(reverse("classes:class_flyer", args=[free_offering.pk]))
            assert resp.status_code == 200

        def it_forbids_a_non_owner_member(member_user, client, free_offering, db):
            client.force_login(member_user)
            resp = client.get(reverse("classes:class_flyer", args=[free_offering.pk]))
            assert resp.status_code == 403

        def it_forbids_an_anonymous_visitor(client, free_offering, db):
            resp = client.get(reverse("classes:class_flyer", args=[free_offering.pk]))
            assert resp.status_code == 403

    def describe_content():
        def it_shows_the_title_qr_and_registration_url(admin_user, client, free_offering, db):
            client.force_login(admin_user)
            body = client.get(reverse("classes:class_flyer", args=[free_offering.pk])).content.decode()
            assert "Free Demo" in body  # the class title
            assert "<svg" in body  # inline QR
            assert free_offering.qr_url in body  # the scan-to-register permalink

        def it_is_a_standalone_page_without_member_chrome(admin_user, client, free_offering, db):
            client.force_login(admin_user)
            body = client.get(reverse("classes:class_flyer", args=[free_offering.pk])).content
            assert b"hub-sidebar" not in body
            assert b"pl-topbar" not in body

        def it_shows_the_studio_venue(admin_user, client, free_offering, db):
            client.force_login(admin_user)
            body = client.get(reverse("classes:class_flyer", args=[free_offering.pk])).content.decode()
            assert "2808 SE 9th Ave, Portland, OR 97202" in body

    def describe_hero_fallbacks():
        def it_renders_the_offerings_own_hero_image(admin_user, client, free_offering, db):
            # free_offering carries a factory-built hero image by default.
            client.force_login(admin_user)
            body = client.get(reverse("classes:class_flyer", args=[free_offering.pk])).content.decode()
            assert "pl-flyer__hero-img" in body
            assert "pl-flyer__hero-placeholder" not in body

        def it_falls_back_to_the_legacy_image_url(admin_user, client, db):
            offering = ClassOfferingFactory(image="", legacy_image_url="https://legacy.example/hero.jpg")
            client.force_login(admin_user)
            body = client.get(reverse("classes:class_flyer", args=[offering.pk])).content.decode()
            assert "pl-flyer__hero-img" in body
            assert "_legacy-image" in body  # served through the legacy-image proxy
            assert "pl-flyer__hero-placeholder" not in body

        def it_renders_a_placeholder_when_no_image(admin_user, client, db):
            offering = ClassOfferingFactory(title="Zebra Craft", image="", legacy_image_url="")
            client.force_login(admin_user)
            resp = client.get(reverse("classes:class_flyer", args=[offering.pk]))
            assert resp.status_code == 200
            body = resp.content.decode()
            assert "pl-flyer__hero-placeholder" in body
            assert ">Z</div>" in body  # graceful initial, no crash on missing image

    def describe_schedule():
        def it_renders_a_single_session_date(admin_user, client, free_offering, db):
            client.force_login(admin_user)
            body = client.get(reverse("classes:class_flyer", args=[free_offering.pk])).content.decode()
            session = free_offering.sessions.first()
            assert date_filter(localtime(session.starts_at), "l, F j, Y") in body
            assert "Series ·" not in body

        def it_lists_every_date_for_a_series(admin_user, client, db):
            offering = SeriesClassOfferingFactory(session_count=3)
            client.force_login(admin_user)
            body = client.get(reverse("classes:class_flyer", args=[offering.pk])).content.decode()
            assert "Series · 3 sessions" in body

        def it_notes_flexible_scheduling_when_no_sessions(admin_user, client, db):
            offering = ClassOfferingFactory(scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
            client.force_login(admin_user)
            body = client.get(reverse("classes:class_flyer", args=[offering.pk])).content.decode()
            assert "Flexible — arrange dates directly with the instructor." in body

        def it_shows_tba_when_no_sessions_and_not_flexible(admin_user, client, db):
            offering = ClassOfferingFactory(scheduling_model=ClassOffering.SchedulingModel.FIXED)
            client.force_login(admin_user)
            body = client.get(reverse("classes:class_flyer", args=[offering.pk])).content.decode()
            assert "Dates to be announced." in body

    def describe_price():
        def it_shows_free_for_a_free_class(admin_user, client, free_offering, db):
            client.force_login(admin_user)
            body = client.get(reverse("classes:class_flyer", args=[free_offering.pk])).content.decode()
            assert "<strong>Free</strong>" in body

        def it_shows_the_price_and_member_price_for_a_paid_class(admin_user, client, db):
            offering = ClassOfferingFactory(price_cents=5000, member_discount_pct=10)
            client.force_login(admin_user)
            body = client.get(reverse("classes:class_flyer", args=[offering.pk])).content.decode()
            assert "<strong>$50</strong>" in body
            assert "$45 for Past Lives members" in body

    def describe_edit_page_link():
        def it_links_the_flyer_from_the_admin_edit_page(admin_user, client, free_offering, db):
            client.force_login(admin_user)
            body = client.get(reverse("classes:admin_class_edit", args=[free_offering.pk])).content.decode()
            assert reverse("classes:class_flyer", args=[free_offering.pk]) in body
            assert "Open printable flyer" in body
