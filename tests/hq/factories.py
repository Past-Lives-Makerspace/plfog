"""Factories for the Holiday Bizarre HQ specs (Track C)."""

from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from hq.models import EventHub, HubCard, HubGoal


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
