"""BDD spec for the ?guild=<slug> catalog filter on classes:public_list.

Filters offerings by category__guild__slug, composes as AND with the other browse filters,
resolves selected_guild for the active-filter heading + guild-specific empty state, and
persists via a hidden form input. Template assertions target rendered markup (class names,
tags) so a structural regression is caught.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory, InstructorFactory
from classes.models import ClassOffering
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db


def _publish(title, slug, category, *, instructor=None, price_cents=5000, days_out=7):
    """Publish a bookable offering in ``category`` with a future session."""
    offering = ClassOfferingFactory(
        title=title,
        slug=slug,
        category=category,
        instructor=instructor or InstructorFactory(),
        status=ClassOffering.Status.PUBLISHED,
        price_cents=price_cents,
    )
    start = timezone.now() + timedelta(days=days_out)
    ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def describe_public_list_guild_filter():
    def it_filters_offerings_by_category__guild__slug(client):
        ceramics = GuildFactory(name="Ceramics", slug="ceramics")
        woodshop = GuildFactory(name="Woodshop", slug="woodshop")
        _publish("Wheel Throwing", "wheel-throwing", CategoryFactory(guild=ceramics))
        _publish("Chisel Basics", "chisel-basics", CategoryFactory(guild=woodshop))

        response = client.get(reverse("classes:public_list") + "?guild=ceramics")

        assert b"Wheel Throwing" in response.content
        assert b"Chisel Basics" not in response.content

    def it_ands_the_guild_filter_with_category(client):
        guild = GuildFactory(name="Ceramics", slug="ceramics")
        hand = CategoryFactory(name="Handbuilding", slug="handbuilding", guild=guild)
        wheel = CategoryFactory(name="Wheel", slug="wheel", guild=guild)
        _publish("Pinch Pots", "pinch-pots", hand)
        _publish("Centering", "centering", wheel)

        response = client.get(reverse("classes:public_list") + "?guild=ceramics&category=wheel")

        assert b"Centering" in response.content
        assert b"Pinch Pots" not in response.content

    def it_ands_the_guild_filter_with_price_and_instructor(client):
        guild = GuildFactory(name="Ceramics", slug="ceramics")
        category = CategoryFactory(guild=guild)
        deenie = InstructorFactory(full_legal_name="Deenie", instructor_slug="deenie")
        marlo = InstructorFactory(full_legal_name="Marlo", instructor_slug="marlo")
        _publish("Cheap Deenie", "cheap-deenie", category, instructor=deenie, price_cents=500)
        _publish("Pricey Deenie", "pricey-deenie", category, instructor=deenie, price_cents=9000)
        _publish("Cheap Marlo", "cheap-marlo", category, instructor=marlo, price_cents=500)

        by_price = client.get(reverse("classes:public_list") + "?guild=ceramics&max_price=9")
        assert b"Cheap Deenie" in by_price.content
        assert b"Cheap Marlo" in by_price.content
        assert b"Pricey Deenie" not in by_price.content

        by_instructor = client.get(reverse("classes:public_list") + "?guild=ceramics&instructor=deenie")
        assert b"Cheap Deenie" in by_instructor.content
        assert b"Pricey Deenie" in by_instructor.content
        assert b"Cheap Marlo" not in by_instructor.content

    def it_ignores_a_blank_or_absent_guild_param(client):
        ceramics = GuildFactory(name="Ceramics", slug="ceramics")
        woodshop = GuildFactory(name="Woodshop", slug="woodshop")
        _publish("Wheel Throwing", "wheel-throwing", CategoryFactory(guild=ceramics))
        _publish("Chisel Basics", "chisel-basics", CategoryFactory(guild=woodshop))

        absent = client.get(reverse("classes:public_list"))
        blank = client.get(reverse("classes:public_list") + "?guild=")
        for response in (absent, blank):
            assert b"Wheel Throwing" in response.content
            assert b"Chisel Basics" in response.content
            assert response.context["selected_guild"] is None

    def it_sets_selected_guild_in_context_when_the_slug_resolves(client):
        guild = GuildFactory(name="Ceramics", slug="ceramics")
        _publish("Wheel Throwing", "wheel-throwing", CategoryFactory(guild=guild))

        response = client.get(reverse("classes:public_list") + "?guild=ceramics")

        assert response.context["selected_guild"] == guild
        assert response.context["selected_guild_slug"] == "ceramics"

    def it_leaves_selected_guild_none_for_an_unknown_slug(client):
        _publish("Wheel Throwing", "wheel-throwing", CategoryFactory(guild=GuildFactory(slug="ceramics")))

        response = client.get(reverse("classes:public_list") + "?guild=no-such-guild")

        assert response.context["selected_guild"] is None
        # Unknown slug → zero rows → generic empty copy, never the guild-specific one.
        body = response.content.decode()
        assert "No classes match your filters." in body
        assert "No classes scheduled for" not in body

    def describe_template_state():
        def it_renders_the_classes_in_guild_heading(client):
            guild = GuildFactory(name="Ceramics", slug="ceramics")
            _publish("Wheel Throwing", "wheel-throwing", CategoryFactory(guild=guild))

            body = client.get(reverse("classes:public_list") + "?guild=ceramics").content.decode()

            assert '<h2 class="cp-results__guild">Classes in Ceramics' in body
            assert ">View all classes</a>" in body

        def it_shows_the_guild_specific_empty_state(client):
            # Guild resolves but owns no bookable classes → guild-specific empty copy.
            GuildFactory(name="Ceramics", slug="ceramics")

            body = client.get(reverse("classes:public_list") + "?guild=ceramics").content.decode()

            assert "<em>No classes scheduled for Ceramics yet.</em>" in body
            assert "No classes match your filters." not in body

        def it_keeps_the_guild_filter_in_the_form(client):
            GuildFactory(name="Ceramics", slug="ceramics")

            body = client.get(reverse("classes:public_list") + "?guild=ceramics").content.decode()

            # Assert on the input tag itself so a nested-<form>/structure regression is caught.
            assert '<input type="hidden" name="guild" value="ceramics">' in body

        def it_omits_the_hidden_guild_input_when_no_guild_is_selected(client):
            _publish("Wheel Throwing", "wheel-throwing", CategoryFactory(guild=GuildFactory(slug="ceramics")))

            body = client.get(reverse("classes:public_list")).content.decode()

            assert 'name="guild"' not in body
