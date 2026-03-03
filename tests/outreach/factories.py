from __future__ import annotations

from decimal import Decimal

import factory
from django.utils import timezone

from outreach.models import Buyable, BuyablePurchase, Event, Lead, Tour
from tests.core.factories import UserFactory


class LeadFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Lead

    name = factory.Faker("name")
    email = factory.Sequence(lambda n: f"lead{n}@example.com")
    status = Lead.Status.NEW


class TourFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Tour

    lead = factory.SubFactory(LeadFactory)
    scheduled_at = factory.LazyFunction(timezone.now)
    status = Tour.Status.SCHEDULED


class EventFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Event

    name = factory.Sequence(lambda n: f"Event {n}")
    starts_at = factory.LazyFunction(timezone.now)
    is_published = False


class BuyableFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Buyable

    name = factory.Sequence(lambda n: f"Buyable {n}")
    unit_price = Decimal("10.00")
    is_active = True


class BuyablePurchaseFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = BuyablePurchase

    buyable = factory.SubFactory(BuyableFactory)
    user = factory.SubFactory(UserFactory)
    quantity = 1
