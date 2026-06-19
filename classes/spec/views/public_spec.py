"""BDD specs for public classes portal — list, detail, category, instructor."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
)
from classes.models import ClassOffering
from membership.models import Member


@pytest.fixture
def published_class(db):
    category = CategoryFactory(name="Ceramics", slug="ceramics")
    instructor = InstructorFactory(full_legal_name="Deenie", instructor_slug="deenie")
    offering = ClassOfferingFactory(
        title="Intro to Wheel Throwing",
        slug="intro-to-wheel-throwing",
        category=category,
        instructor=instructor,
        status=ClassOffering.Status.PUBLISHED,
    )
    ClassSessionFactory(
        class_offering=offering,
        starts_at=timezone.now() + timedelta(days=7),
        ends_at=timezone.now() + timedelta(days=7, hours=2),
    )
    return offering


def describe_coerce_dollars_to_cents():
    def it_returns_zero_for_invalid_string():
        from classes.views import _coerce_dollars_to_cents

        assert _coerce_dollars_to_cents("abc") == 0

    def it_returns_zero_for_none():
        from classes.views import _coerce_dollars_to_cents

        assert _coerce_dollars_to_cents(None) == 0

    def it_converts_valid_dollar_amount():
        from classes.views import _coerce_dollars_to_cents

        assert _coerce_dollars_to_cents("12.50") == 1250


def describe_public_list():
    def it_renders_hero_and_published_classes(published_class, client):
        response = client.get(reverse("classes:public_list"))
        assert response.status_code == 200
        assert b"Classes" in response.content
        assert b"Intro to Wheel Throwing" in response.content
        assert b"Deenie" in response.content

    def it_hides_draft_and_pending_classes(published_class, client):
        ClassOfferingFactory(
            title="Secret Draft",
            slug="secret-draft",
            category=published_class.category,
            instructor=published_class.instructor,
            status=ClassOffering.Status.DRAFT,
        )
        response = client.get(reverse("classes:public_list"))
        assert b"Secret Draft" not in response.content

    def it_hides_private_classes(published_class, client):
        private_offering = ClassOfferingFactory(
            title="Private Lesson",
            slug="private-lesson",
            category=published_class.category,
            instructor=published_class.instructor,
            status=ClassOffering.Status.PUBLISHED,
            is_private=True,
        )
        ClassSessionFactory(
            class_offering=private_offering,
            starts_at=timezone.now() + timedelta(days=4),
            ends_at=timezone.now() + timedelta(days=4, hours=2),
        )
        response = client.get(reverse("classes:public_list"))
        assert b"Private Lesson" not in response.content

    def it_hides_published_classes_with_no_upcoming_sessions(db, client):
        category = CategoryFactory()
        instructor = InstructorFactory()
        ClassOfferingFactory(
            title="Brand New Class",
            slug="brand-new-class",
            category=category,
            instructor=instructor,
            status=ClassOffering.Status.PUBLISHED,
        )
        response = client.get(reverse("classes:public_list"))
        assert b"Brand New Class" not in response.content

    def it_hides_published_classes_whose_only_sessions_are_past(db, client):
        category = CategoryFactory()
        instructor = InstructorFactory()
        stale = ClassOfferingFactory(
            title="Past Class",
            slug="past-class",
            category=category,
            instructor=instructor,
            status=ClassOffering.Status.PUBLISHED,
        )
        past_start = timezone.now() - timedelta(days=10)
        ClassSessionFactory(
            class_offering=stale,
            starts_at=past_start,
            ends_at=past_start + timedelta(hours=2),
        )
        response = client.get(reverse("classes:public_list"))
        assert b"Past Class" not in response.content

    def it_includes_flexible_classes_even_without_sessions(db, client):
        category = CategoryFactory()
        instructor = InstructorFactory()
        ClassOfferingFactory(
            title="Flexible Workshop",
            slug="flexible-workshop",
            category=category,
            instructor=instructor,
            status=ClassOffering.Status.PUBLISHED,
            scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
        )
        response = client.get(reverse("classes:public_list"))
        assert response.status_code == 200
        assert b"Flexible Workshop" in response.content

    def it_filters_to_selected_category(published_class, client):
        other_cat = CategoryFactory(name="Blacksmithing", slug="blacksmithing")
        other_offering = ClassOfferingFactory(
            title="Intro to Forging",
            slug="intro-to-forging",
            category=other_cat,
            instructor=published_class.instructor,
            status=ClassOffering.Status.PUBLISHED,
        )
        ClassSessionFactory(
            class_offering=other_offering,
            starts_at=timezone.now() + timedelta(days=3),
            ends_at=timezone.now() + timedelta(days=3, hours=2),
        )
        response = client.get(reverse("classes:public_list") + "?category=ceramics")
        assert b"Intro to Wheel Throwing" in response.content
        assert b"Intro to Forging" not in response.content

    def it_filters_by_instructor_slug(published_class, client):
        other_instructor = InstructorFactory(full_legal_name="Newcomer", instructor_slug="newcomer")
        other = ClassOfferingFactory(
            title="Newcomer Class",
            slug="newcomer-class",
            category=published_class.category,
            instructor=other_instructor,
            status=ClassOffering.Status.PUBLISHED,
        )
        ClassSessionFactory(
            class_offering=other,
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, hours=2),
        )
        response = client.get(reverse("classes:public_list") + "?instructor=deenie")
        assert b"Intro to Wheel Throwing" in response.content
        assert b"Newcomer Class" not in response.content

    def it_filters_by_min_and_max_price(published_class, client):
        cheap = ClassOfferingFactory(
            title="Cheap Class",
            slug="cheap-class",
            category=published_class.category,
            instructor=published_class.instructor,
            status=ClassOffering.Status.PUBLISHED,
            price_cents=500,
        )
        ClassSessionFactory(
            class_offering=cheap,
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, hours=2),
        )
        # published_class has price_cents=5000 ($50); filter max=$4 to exclude it and include the $5 class
        response = client.get(reverse("classes:public_list") + "?min_price=1&max_price=9")
        assert b"Intro to Wheel Throwing" not in response.content
        assert b"Cheap Class" in response.content

    def it_filters_members_only(db, client):
        cat = CategoryFactory()
        inst = InstructorFactory()
        members_only = ClassOfferingFactory(
            title="Members Class",
            slug="members-class",
            category=cat,
            instructor=inst,
            status=ClassOffering.Status.PUBLISHED,
            member_discount_pct=15,
        )
        no_discount = ClassOfferingFactory(
            title="Open Class",
            slug="open-class",
            category=cat,
            instructor=inst,
            status=ClassOffering.Status.PUBLISHED,
            member_discount_pct=0,
        )
        ClassSessionFactory(
            class_offering=members_only,
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, hours=2),
        )
        ClassSessionFactory(
            class_offering=no_discount,
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, hours=2),
        )
        response = client.get(reverse("classes:public_list") + "?members_only=1")
        assert b"Members Class" in response.content
        assert b"Open Class" not in response.content

    def it_filters_free_classes(db, client):
        cat = CategoryFactory()
        inst = InstructorFactory()
        free = ClassOfferingFactory(
            title="Free Workshop",
            slug="free-workshop",
            category=cat,
            instructor=inst,
            status=ClassOffering.Status.PUBLISHED,
            price_cents=0,
        )
        paid = ClassOfferingFactory(
            title="Paid Workshop",
            slug="paid-workshop",
            category=cat,
            instructor=inst,
            status=ClassOffering.Status.PUBLISHED,
            price_cents=2000,
        )
        ClassSessionFactory(
            class_offering=free,
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, hours=2),
        )
        ClassSessionFactory(
            class_offering=paid,
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, hours=2),
        )
        response = client.get(reverse("classes:public_list") + "?free=1")
        assert b"Free Workshop" in response.content
        assert b"Paid Workshop" not in response.content

    def it_filters_upcoming_classes(db, client):
        cat = CategoryFactory()
        inst = InstructorFactory()
        upcoming = ClassOfferingFactory(
            title="Upcoming Class",
            slug="upcoming-class",
            category=cat,
            instructor=inst,
            status=ClassOffering.Status.PUBLISHED,
        )
        ClassOfferingFactory(
            title="No Session Class",
            slug="no-session-class",
            category=cat,
            instructor=inst,
            status=ClassOffering.Status.PUBLISHED,
        )
        ClassSessionFactory(
            class_offering=upcoming,
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, hours=2),
        )
        response = client.get(reverse("classes:public_list") + "?upcoming=1")
        assert b"Upcoming Class" in response.content
        assert b"No Session Class" not in response.content

    def it_returns_partial_html_for_htmx_requests(published_class, client):
        response = client.get(
            reverse("classes:public_list"),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert b"cp-results__summary" in response.content
        # Partial doesn't include the full page hero
        assert b"<html" not in response.content


def describe_public_category():
    def it_404s_unknown_category(db, client):
        response = client.get(reverse("classes:public_category", kwargs={"slug": "no-such-cat"}))
        assert response.status_code == 404

    def it_renders_only_classes_in_the_category(published_class, client):
        other_cat = CategoryFactory(name="Woodworking", slug="woodworking")
        other_offering = ClassOfferingFactory(
            title="Intro to Chisels",
            slug="intro-to-chisels",
            category=other_cat,
            instructor=published_class.instructor,
            status=ClassOffering.Status.PUBLISHED,
        )
        ClassSessionFactory(
            class_offering=other_offering,
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, hours=2),
        )
        response = client.get(reverse("classes:public_category", kwargs={"slug": "ceramics"}))
        assert response.status_code == 200
        assert b"Intro to Wheel Throwing" in response.content
        assert b"Intro to Chisels" not in response.content


def describe_public_class_detail():
    def it_renders_the_detail_page(published_class, client):
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published_class.slug}))
        assert response.status_code == 200
        assert b"Intro to Wheel Throwing" in response.content
        assert b"Deenie" in response.content
        assert b"Schedule" in response.content
        assert b"2808 SE 9th Ave" in response.content

    def it_404s_on_draft_classes(db, client):
        offering = ClassOfferingFactory(status=ClassOffering.Status.DRAFT, slug="secret")
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
        assert response.status_code == 404

    def it_404s_on_private_classes(db, client):
        offering = ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED,
            is_private=True,
            slug="private-one",
        )
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
        assert response.status_code == 404

    def it_shows_sold_out_when_no_spots_remain(published_class, client):
        from classes.factories import RegistrationFactory
        from classes.models import Registration

        for _ in range(published_class.capacity):
            RegistrationFactory(class_offering=published_class, status=Registration.Status.CONFIRMED)
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published_class.slug}))
        assert response.status_code == 200
        assert b"Sold out" in response.content

    def it_renders_a_unique_seo_title_and_meta_description(published_class, client):
        from django.utils.html import escape

        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published_class.slug}))
        html = response.content.decode()
        assert response.status_code == 200
        assert escape(published_class.seo_title) in html
        assert 'name="description"' in html
        assert escape(published_class.seo_description[:30]) in html


def describe_catalog_grouping():
    def _publish(title, slug, category, instructor, days_out, capacity=6):
        offering = ClassOfferingFactory(
            title=title,
            slug=slug,
            category=category,
            instructor=instructor,
            status=ClassOffering.Status.PUBLISHED,
            capacity=capacity,
        )
        ClassSessionFactory(
            class_offering=offering,
            starts_at=timezone.now() + timedelta(days=days_out),
            ends_at=timezone.now() + timedelta(days=days_out, hours=2),
        )
        return offering

    def it_shows_a_repeated_class_once_with_a_pick_a_date_list(db, client):
        cat = CategoryFactory(name="Smithing", slug="smithing")
        inst = InstructorFactory(full_legal_name="Glen", instructor_slug="glen")
        for i in range(3):
            _publish("Blacksmithing 101 with Glen", f"bs-{i}", cat, inst, days_out=i + 1)

        response = client.get(reverse("classes:public_list"))

        # The class title renders once even though it is offered on three dates.
        assert response.content.count(b"Blacksmithing 101 with Glen") == 1
        assert b"Pick a date" in response.content
        assert b"3 dates" in response.content

    def it_truncates_to_four_dates_with_a_more_indicator(db, client):
        cat = CategoryFactory()
        inst = InstructorFactory()
        for i in range(6):
            _publish("Big Series with Glen", f"big-{i}", cat, inst, days_out=i + 1)

        response = client.get(reverse("classes:public_list"))

        assert b"+2 more dates" in response.content

    def it_shows_per_date_seat_counts(db, client):
        from classes.factories import RegistrationFactory
        from classes.models import Registration

        cat = CategoryFactory()
        inst = InstructorFactory()
        full_date = _publish("Repeat Class", "rep-a", cat, inst, days_out=1)
        _publish("Repeat Class", "rep-b", cat, inst, days_out=2)
        for _ in range(full_date.capacity):
            RegistrationFactory(class_offering=full_date, status=Registration.Status.CONFIRMED)

        response = client.get(reverse("classes:public_list"))

        # One date is full while the other still has seats — both shown per-date.
        assert b"Full" in response.content
        assert b"6 spots" in response.content

    def it_renders_one_card_per_group_not_per_dated_offering(db, client):
        cat = CategoryFactory(name="Forge", slug="forge")
        inst = InstructorFactory()
        for i in range(4):
            _publish("Anvil Time with Glen", f"anvil-{i}", cat, inst, days_out=i + 1)

        response = client.get(reverse("classes:public_list"))

        # Four dated offerings collapse to a single browsable card (one cls-title each).
        assert response.content.count(b"cls-title") == 1

    def it_lists_other_dates_on_the_detail_page(db, client):
        cat = CategoryFactory(name="Smithing", slug="smithing")
        inst = InstructorFactory(full_legal_name="Glen", instructor_slug="glen")
        first = _publish("Forge Night with Glen", "forge-a", cat, inst, days_out=2)
        _publish("Forge Night with Glen", "forge-b", cat, inst, days_out=9)

        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": first.slug}))

        assert b"Other dates for this class" in response.content
        assert b"forge-b" in response.content

    def it_omits_other_dates_when_a_class_stands_alone(published_class, client):
        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": published_class.slug}))

        assert b"Other dates for this class" not in response.content

    def it_skips_sibling_lookup_when_the_grouping_key_is_blank(db, client):
        # A title-less offering yields an empty grouping key and must stand alone
        # rather than collapsing with every other keyless row.
        cat = CategoryFactory()
        inst = InstructorFactory()
        offering = ClassOfferingFactory(
            title="",
            slug="blank-title",
            category=cat,
            instructor=inst,
            status=ClassOffering.Status.PUBLISHED,
        )
        assert offering.grouping_key == ""
        ClassSessionFactory(
            class_offering=offering,
            starts_at=timezone.now() + timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1, hours=2),
        )

        response = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))

        assert response.status_code == 200
        assert b"Other dates for this class" not in response.content


def describe_public_instructor():
    def it_404s_inactive_instructor(db, client):
        instructor = InstructorFactory(instructor_slug="retired", status=Member.Status.FORMER)
        response = client.get(reverse("classes:public_instructor", kwargs={"slug": instructor.instructor_slug}))
        assert response.status_code == 404

    def it_renders_profile_with_current_classes(published_class, client):
        response = client.get(
            reverse("classes:public_instructor", kwargs={"slug": published_class.instructor.instructor_slug})
        )
        assert response.status_code == 200
        assert b"Deenie" in response.content
        assert b"Intro to Wheel Throwing" in response.content


def describe_google_analytics_gate():
    def it_omits_ga_tag_when_id_not_set(published_class, client):
        response = client.get(reverse("classes:public_list"))
        assert b"googletagmanager.com" not in response.content

    def it_injects_ga_tag_when_id_is_configured(published_class, client):
        from core.models import SiteConfiguration

        site = SiteConfiguration.load()
        site.google_analytics_measurement_id = "G-TEST123"
        site.save()
        response = client.get(reverse("classes:public_list"))
        assert b"googletagmanager.com" in response.content
        assert b"G-TEST123" in response.content


def describe_hero_management_buttons():
    def it_crosses_to_the_members_host_on_the_public_surface(admin_user, published_class, client):
        client.force_login(admin_user)
        with override_settings(PUBLIC_HOSTS=["testserver"], MEMBER_BASE_URL="https://members.example"):
            response = client.get(reverse("classes:public_list"))
        assert b'href="https://members.example/classes/admin/"' in response.content

    def it_stays_relative_on_the_members_surface(admin_user, published_class, client):
        client.force_login(admin_user)
        response = client.get(reverse("classes:public_list"))
        assert b'href="/classes/admin/"' in response.content
        assert b"members.example" not in response.content


def describe_public_topbar_member_chrome():
    @pytest.fixture
    def member_persona_user(db):
        from django.contrib.auth import get_user_model
        from membership.models import Member, MembershipPlan

        plan, _ = MembershipPlan.objects.get_or_create(name="Standard", defaults={"monthly_price": "50.00"})
        user, _ = get_user_model().objects.get_or_create(
            username="fog@example.com", defaults={"email": "fog@example.com"}
        )
        Member.objects.update_or_create(
            user=user,
            defaults={
                "full_legal_name": "Fog Member",
                "fog_role": Member.FogRole.MEMBER,
                "membership_plan": plan,
                "status": Member.Status.ACTIVE,
                "airtable_record_id": "recFOG123",
            },
        )
        return user

    def it_shows_the_fog_cluster_on_the_public_surface(member_persona_user, published_class, client):
        client.force_login(member_persona_user)
        with override_settings(PUBLIC_HOSTS=["testserver"]):
            response = client.get(reverse("classes:public_list"))
        assert b"cp-topbar__account-item--ext" in response.content
        assert b"cp-topbar__account-pill" in response.content

    def it_hides_the_fog_cluster_on_the_members_surface(member_persona_user, published_class, client):
        client.force_login(member_persona_user)
        response = client.get(reverse("classes:public_list"))
        assert b"cp-topbar__account-item--ext" not in response.content


def describe_card_image_fallback():
    def it_shows_the_category_color_logo_when_class_has_no_image(client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED,
            scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
            image="",
            category__name="Woodworking",
        )
        resp = client.get(reverse("classes:public_list"))
        assert resp.status_code == 200
        assert "img/guild_logos/woodworking_color.svg" in resp.content.decode()

    def it_shows_the_past_lives_mark_when_category_has_no_logo(client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED,
            scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
            image="",
            category__name="Creative Business",
        )
        resp = client.get(reverse("classes:public_list"))
        assert resp.status_code == 200
        assert "cls-img-ph--logo" in resp.content.decode()
        assert "img/favicon.png" in resp.content.decode()


def describe_detail_hero_fallback():
    def it_shows_the_category_color_logo_when_no_images(client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        offering = ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED,
            image="",
            category__name="Glass",
        )
        resp = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
        assert resp.status_code == 200
        assert "img/guild_logos/glass_color.svg" in resp.content.decode()

    def it_shows_the_past_lives_mark_when_category_has_no_logo(client, db):
        from classes.factories import ClassOfferingFactory
        from classes.models import ClassOffering

        offering = ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED,
            image="",
            category__name="Education",
        )
        resp = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug}))
        assert resp.status_code == 200
        assert "cp-detail__hero-logo" in resp.content.decode()
        assert "img/favicon.png" in resp.content.decode()
