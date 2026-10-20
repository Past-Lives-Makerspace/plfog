"""Factories for the Holiday Bizarre HQ specs (Track C)."""

from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from hq.models import (
    BudgetLine,
    EventHub,
    HubCard,
    HubGoal,
    LedgerEntry,
    LedgerSettings,
    MarketingAction,
    ShiftSignup,
    Sponsor,
    SponsorLogEntry,
    VendorFloor,
    VendorTable,
    VolunteerShift,
    WineBottle,
    WineryLogo,
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


class MarketingActionFactory(DjangoModelFactory):
    """A flyer drop-off with no reach and no link, logged by nobody."""

    class Meta:
        model = MarketingAction

    hub = factory.SubFactory(EventHubFactory)
    kind = MarketingAction.Kind.FLYER
    details = factory.Sequence(lambda n: f"Spec action {n}")
    who = ""
    reach = 0
    link = ""


class SponsorFactory(DjangoModelFactory):
    """A prospect with no tier, no ask and no follow-up date, added by nobody."""

    class Meta:
        model = Sponsor

    hub = factory.SubFactory(EventHubFactory)
    organization = factory.Sequence(lambda n: f"Spec Sponsor {n}")
    status = Sponsor.Status.PROSPECT


class SponsorLogEntryFactory(DjangoModelFactory):
    class Meta:
        model = SponsorLogEntry

    sponsor = factory.SubFactory(SponsorFactory)
    text = factory.Sequence(lambda n: f"Spec contact {n}")


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
