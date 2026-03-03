"""Tests for outreach app models."""

from __future__ import annotations

from decimal import Decimal

import pytest

from outreach.models import Lead, Tour
from tests.core.factories import UserFactory
from tests.membership.factories import GuildFactory
from tests.outreach.factories import (
    BuyableFactory,
    BuyablePurchaseFactory,
    EventFactory,
    LeadFactory,
    TourFactory,
)


# ---------------------------------------------------------------------------
# Lead
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def describe_lead():
    def it_has_str_representation():
        lead = LeadFactory(name="Jane Smith")
        assert str(lead) == "Jane Smith"

    def it_defaults_status_to_new():
        lead = LeadFactory()
        assert lead.status == Lead.Status.NEW

    def it_defaults_greenlighted_for_membership_to_false():
        lead = LeadFactory()
        assert lead.greenlighted_for_membership is False

    def it_can_be_greenlighted_for_membership():
        lead = LeadFactory(greenlighted_for_membership=True)
        assert lead.greenlighted_for_membership is True

    def it_supports_all_status_choices():
        assert Lead.Status.NEW == "new"
        assert Lead.Status.CONTACTED == "contacted"
        assert Lead.Status.TOURED == "toured"
        assert Lead.Status.CONVERTED == "converted"
        assert Lead.Status.LOST == "lost"

    def it_allows_blank_email():
        lead = LeadFactory(email="")
        assert lead.email == ""

    def it_allows_blank_phone():
        lead = LeadFactory(phone="")
        assert lead.phone == ""

    def it_allows_blank_source():
        lead = LeadFactory(source="")
        assert lead.source == ""

    def it_stores_source():
        lead = LeadFactory(source="Instagram")
        assert lead.source == "Instagram"

    def it_stores_interests():
        lead = LeadFactory(interests="Woodworking, ceramics")
        lead.refresh_from_db()
        assert lead.interests == "Woodworking, ceramics"


# ---------------------------------------------------------------------------
# Tour
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def describe_tour():
    def it_has_str_representation():
        lead = LeadFactory(name="Alice Brown")
        tour = TourFactory(lead=lead, status=Tour.Status.SCHEDULED)
        assert str(tour) == "Tour for Alice Brown (scheduled)"

    def it_defaults_status_to_scheduled():
        tour = TourFactory()
        assert tour.status == Tour.Status.SCHEDULED

    def it_belongs_to_a_lead():
        lead = LeadFactory(name="Bob Jones")
        tour = TourFactory(lead=lead)
        assert tour.lead == lead

    def it_allows_null_claimed_by():
        tour = TourFactory(claimed_by=None)
        assert tour.claimed_by is None

    def it_can_be_claimed_by_a_user():
        user = UserFactory(username="tour-guide")
        tour = TourFactory(claimed_by=user)
        tour.refresh_from_db()
        assert tour.claimed_by == user

    def it_supports_all_status_choices():
        assert Tour.Status.SCHEDULED == "scheduled"
        assert Tour.Status.CLAIMED == "claimed"
        assert Tour.Status.COMPLETED == "completed"
        assert Tour.Status.CANCELLED == "cancelled"
        assert Tour.Status.NO_SHOW == "no_show"

    def it_lead_related_name_returns_tours():
        lead = LeadFactory()
        tour = TourFactory(lead=lead)
        assert tour in lead.tours.all()


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def describe_event():
    def it_has_str_representation():
        event = EventFactory(name="Open House Night")
        assert str(event) == "Open House Night"

    def it_defaults_is_published_to_false():
        event = EventFactory()
        assert event.is_published is False

    def it_defaults_is_recurring_to_false():
        event = EventFactory()
        assert event.is_recurring is False

    def it_allows_null_guild():
        event = EventFactory(guild=None)
        assert event.guild is None

    def it_can_be_assigned_to_a_guild():
        guild = GuildFactory(name="Ceramics Guild")
        event = EventFactory(guild=guild)
        event.refresh_from_db()
        assert event.guild == guild

    def it_can_be_published():
        event = EventFactory(is_published=True)
        assert event.is_published is True

    def it_can_be_set_as_recurring():
        event = EventFactory(is_recurring=True)
        assert event.is_recurring is True

    def it_stores_location():
        event = EventFactory(location="Main Studio Floor")
        assert event.location == "Main Studio Floor"

    def it_allows_null_ends_at():
        event = EventFactory(ends_at=None)
        assert event.ends_at is None

    def it_guild_related_name_returns_events():
        guild = GuildFactory(name="Woodworking Guild")
        event = EventFactory(guild=guild)
        assert event in guild.events.all()


# ---------------------------------------------------------------------------
# Buyable
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def describe_buyable():
    def it_has_str_representation():
        buyable = BuyableFactory(name="Safety Glasses")
        assert str(buyable) == "Safety Glasses"

    def it_has_formatted_price_property():
        buyable = BuyableFactory(unit_price=Decimal("14.99"))
        assert buyable.formatted_price == "$14.99"

    def it_formats_whole_dollar_price():
        buyable = BuyableFactory(unit_price=Decimal("25.00"))
        assert buyable.formatted_price == "$25.00"

    def it_defaults_is_active_to_true():
        buyable = BuyableFactory()
        assert buyable.is_active is True

    def it_defaults_total_quantity_sold_to_zero():
        buyable = BuyableFactory()
        assert buyable.total_quantity_sold == 0

    def it_can_be_deactivated():
        buyable = BuyableFactory(is_active=False)
        assert buyable.is_active is False

    def it_allows_null_guild():
        buyable = BuyableFactory(guild=None)
        assert buyable.guild is None

    def it_can_be_assigned_to_a_guild():
        guild = GuildFactory(name="Metal Guild")
        buyable = BuyableFactory(guild=guild)
        buyable.refresh_from_db()
        assert buyable.guild == guild

    def it_stores_unit_price():
        buyable = BuyableFactory(unit_price=Decimal("99.95"))
        buyable.refresh_from_db()
        assert buyable.unit_price == Decimal("99.95")

    def it_guild_related_name_returns_buyables():
        guild = GuildFactory(name="Print Guild")
        buyable = BuyableFactory(guild=guild)
        assert buyable in guild.buyables.all()


# ---------------------------------------------------------------------------
# BuyablePurchase
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def describe_buyable_purchase():
    def it_has_str_representation():
        user = UserFactory(username="purchaser")
        buyable = BuyableFactory(name="Ear Protection")
        purchase = BuyablePurchaseFactory(buyable=buyable, user=user, quantity=2)
        assert str(purchase) == f"Ear Protection x2 - {user}"

    def it_calculates_total_cost():
        buyable = BuyableFactory(unit_price=Decimal("15.00"))
        purchase = BuyablePurchaseFactory(buyable=buyable, quantity=3)
        assert purchase.total_cost == Decimal("45.00")

    def it_calculates_total_cost_for_single_item():
        buyable = BuyableFactory(unit_price=Decimal("22.50"))
        purchase = BuyablePurchaseFactory(buyable=buyable, quantity=1)
        assert purchase.total_cost == Decimal("22.50")

    def it_belongs_to_a_buyable():
        buyable = BuyableFactory(name="Work Gloves")
        purchase = BuyablePurchaseFactory(buyable=buyable)
        assert purchase.buyable == buyable

    def it_belongs_to_a_user():
        user = UserFactory(username="buyer-user")
        purchase = BuyablePurchaseFactory(user=user)
        assert purchase.user == user

    def it_defaults_quantity_to_one():
        purchase = BuyablePurchaseFactory()
        assert purchase.quantity == 1

    def it_stores_custom_quantity():
        purchase = BuyablePurchaseFactory(quantity=5)
        assert purchase.quantity == 5

    def it_buyable_related_name_returns_purchases():
        buyable = BuyableFactory()
        purchase = BuyablePurchaseFactory(buyable=buyable)
        assert purchase in buyable.purchases.all()

    def it_user_related_name_returns_purchases():
        user = UserFactory(username="related-buyer")
        purchase = BuyablePurchaseFactory(user=user)
        assert purchase in user.buyable_purchases.all()

    def it_allows_null_order():
        purchase = BuyablePurchaseFactory(order=None)
        assert purchase.order is None
