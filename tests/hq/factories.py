"""Factories for the Holiday Bizarre HQ specs (Track C)."""

from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from tests.membership.factories import GuildFactory

from decimal import Decimal

from hq.models import (
    BarSale,
    BudgetLine,
    DoorCount,
    EventHub,
    GuildDemo,
    HubCard,
    HubGoal,
    LaunchTask,
    LedgerEntry,
    LedgerSettings,
    ShiftSignup,
    StaffTime,
    VendorFloor,
    VendorTable,
    VolunteerShift,
)


class EventHubFactory(DjangoModelFactory):
    """A closed hub with no event; pass ``is_open=True`` for the one the sidebar lands on."""

    class Meta:
        model = EventHub

    slug = factory.Sequence(lambda n: f"spec-hub-{n}")
    title = factory.Sequence(lambda n: f"Spec HQ {n}")
    eyebrow = "Past Lives Makerspace"
    is_open = False


class HubCardFactory(DjangoModelFactory):
    """A placeholder door."""

    class Meta:
        model = HubCard

    hub = factory.SubFactory(EventHubFactory)
    kind = HubCard.Kind.PLACEHOLDER
    title = factory.Sequence(lambda n: f"Spec Door {n}")
    who_for = "The spec team"
    bullets = "First thing\nSecond thing"
    sort_order = factory.Sequence(lambda n: n)


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


class VendorTableFactory(DjangoModelFactory):
    """A requested 6 ft table with no guild, no contact and no setup day, entered by nobody."""

    class Meta:
        model = VendorTable

    hub = factory.SubFactory(EventHubFactory)
    name = factory.Sequence(lambda n: f"Spec Vendor {n}")
    status = VendorTable.Status.REQUESTED


class VendorFloorFactory(DjangoModelFactory):
    class Meta:
        model = VendorFloor

    hub = factory.SubFactory(EventHubFactory)
    capacity = 24


class LedgerSettingsFactory(DjangoModelFactory):
    """A hub's ledger settings with Lee's defaults ($900 approved)."""

    class Meta:
        model = LedgerSettings

    hub = factory.SubFactory(EventHubFactory)


class BudgetLineFactory(DjangoModelFactory):
    """A Drinks line with nothing budgeted."""

    class Meta:
        model = BudgetLine

    hub = factory.SubFactory(EventHubFactory)
    name = factory.Sequence(lambda n: f"Spec Line {n}")
    category = BudgetLine.Category.DRINKS
    sort_order = factory.Sequence(lambda n: n)


class LedgerEntryFactory(DjangoModelFactory):
    """A $25 sponsored gift on a line of the same hub, with no receipt."""

    class Meta:
        model = LedgerEntry

    hub = factory.SubFactory(EventHubFactory)
    line = factory.SubFactory(BudgetLineFactory, hub=factory.SelfAttribute("..hub"))
    kind = LedgerEntry.Kind.SPONSORED
    source = factory.Sequence(lambda n: f"Spec Source {n}")
    amount_cents = 2_500


class BarSaleFactory(DjangoModelFactory):
    """One IPA tapped on the night, $6 by card, with no organiser."""

    class Meta:
        model = BarSale

    hub = factory.SubFactory(EventHubFactory)
    drink = BarSale.Drink.IPA
    qty = 1
    amount_cents = 600
    pay = BarSale.Pay.CARD
    source = BarSale.Source.QUICK


class DoorCountFactory(DjangoModelFactory):
    """One +1 tap on the door clicker, by nobody."""

    class Meta:
        model = DoorCount

    hub = factory.SubFactory(EventHubFactory)
    delta = 1


class LaunchTaskFactory(DjangoModelFactory):
    """A task not started, with no owner, due date or link."""

    class Meta:
        model = LaunchTask

    hub = factory.SubFactory(EventHubFactory)
    task = factory.Sequence(lambda n: f"Spec Task {n}")
    sort_order = factory.Sequence(lambda n: n)


class StaffTimeFactory(DjangoModelFactory):
    """Two hours of planning by Lee, linked to no member and logged by nobody."""

    class Meta:
        model = StaffTime

    hub = factory.SubFactory(EventHubFactory)
    who = "Lee"
    hours = Decimal("2")
    area = StaffTime.Area.PLANNING


class GuildDemoFactory(DjangoModelFactory):
    """An active guild on the list, not contacted and with no demo planned."""

    class Meta:
        model = GuildDemo

    hub = factory.SubFactory(EventHubFactory)
    guild = factory.SubFactory(GuildFactory)
