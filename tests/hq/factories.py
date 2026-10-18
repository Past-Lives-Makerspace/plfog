"""Factories for the Holiday Bizarre HQ specs (Track C)."""

from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from hq.models import EventHub, HubCard, HubGoal, ShiftSignup, VolunteerShift, WineBottle, WineryLogo


class EventHubFactory(DjangoModelFactory):
    """A closed hub with no event; pass ``is_open=True`` for the one the sidebar lands on."""

    class Meta:
        model = EventHub

    slug = factory.Sequence(lambda n: f"spec-hub-{n}")
    title = factory.Sequence(lambda n: f"Spec HQ {n}")
    eyebrow = "Past Lives Makerspace"
    is_open = False


class HubCardFactory(DjangoModelFactory):
    """A placeholder door, admins only. ``named=[member, ...]`` names people on it."""

    class Meta:
        model = HubCard
        skip_postgeneration_save = True

    hub = factory.SubFactory(EventHubFactory)
    kind = HubCard.Kind.PLACEHOLDER
    title = factory.Sequence(lambda n: f"Spec Door {n}")
    who_for = "The spec team"
    bullets = "First thing\nSecond thing"
    audience = HubCard.Audience.ADMINS
    sort_order = factory.Sequence(lambda n: n)

    @factory.post_generation
    def named(obj, create, extracted, **kwargs):  # noqa: N805  # factory-boy hook, obj is the instance
        if create and extracted:
            obj.named_members.set(extracted)


class HubGoalFactory(DjangoModelFactory):
    class Meta:
        model = HubGoal

    hub = factory.SubFactory(EventHubFactory)
    label = factory.Sequence(lambda n: f"Goal {n}")
    kind = HubGoal.Kind.MANUAL
    target = 100
    current_value = 0
    sort_order = factory.Sequence(lambda n: n)


class VolunteerShiftFactory(DjangoModelFactory):
    """A shift during the event needing two people."""

    class Meta:
        model = VolunteerShift

    hub = factory.SubFactory(EventHubFactory)
    phase = VolunteerShift.Phase.DURING
    role = factory.Sequence(lambda n: f"Shift role {n}")
    when_text = "5 to 7:30 PM"
    need = 2
    description = "Help where it is needed."
    sort_order = factory.Sequence(lambda n: n)


class ShiftSignupFactory(DjangoModelFactory):
    """A guest on a shift (no member)."""

    class Meta:
        model = ShiftSignup

    shift = factory.SubFactory(VolunteerShiftFactory)
    name = factory.Sequence(lambda n: f"Volunteer {n}")
    email = factory.Sequence(lambda n: f"volunteer{n}@example.com")
    phone = ""
    has_olcc = False
    note = ""


class WineBottleFactory(DjangoModelFactory):
    """One $25 red from Abacela given by a person, logged by a guest (no member, no photo)."""

    class Meta:
        model = WineBottle

    hub = factory.SubFactory(EventHubFactory)
    kind = WineBottle.Kind.RED
    name = factory.Sequence(lambda n: f"Bottle {n}")
    winery = "Abacela"
    value_cents = 2500
    qty = 1
    giver_name = factory.Sequence(lambda n: f"Giver {n}")
    giver_type = WineBottle.GiverType.PERSON
    giver_email = ""
    listing = WineBottle.Listing.NOT_ASKED
    credited_name = ""
    logged_by = None
    logged_by_name = ""


class WineryLogoFactory(DjangoModelFactory):
    """A logo for Abacela: a small generated PNG, resized to a JPEG on save."""

    class Meta:
        model = WineryLogo

    hub = factory.SubFactory(EventHubFactory)
    name = "Abacela"
    logo = factory.django.ImageField(filename="logo.png", width=64, height=64, color="white")
