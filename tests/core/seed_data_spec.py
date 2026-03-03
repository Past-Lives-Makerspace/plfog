"""BDD-style tests for the seed_data management command."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from billing.models import Invoice, MemberSubscription, Order, Payout, RevenueSplit, SubscriptionPlan
from core.models import Setting
from education.models import ClassDiscountCode, MakerClass, Orientation
from membership.models import (
    Guild,
    GuildDocument,
    GuildMembership,
    GuildVote,
    GuildWishlistItem,
    Lease,
    Member,
    MemberSchedule,
    MembershipPlan,
    ScheduleBlock,
    Space,
)
from outreach.models import Buyable, BuyablePurchase, Event, Lead, Tour
from tools.models import Document, Rentable, Rental, Tool, ToolReservation

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture()
def seed_db():
    call_command("seed_data")


def describe_seed_data_command():
    def it_creates_expected_settings(seed_db):
        assert Setting.objects.count() == 4

    def it_creates_admin_and_demo_users(seed_db):
        assert User.objects.count() == 35
        assert User.objects.filter(is_superuser=True).exists()

    def it_creates_membership_plans(seed_db):
        assert MembershipPlan.objects.count() == 4

    def it_creates_members(seed_db):
        assert Member.objects.count() == 34

    def it_creates_guilds(seed_db):
        assert Guild.objects.count() == 14

    def it_creates_guild_memberships(seed_db):
        assert GuildMembership.objects.count() == 39

    def it_creates_guild_votes(seed_db):
        assert GuildVote.objects.count() == 18

    def it_creates_spaces(seed_db):
        assert Space.objects.count() == 54

    def it_creates_leases(seed_db):
        assert Lease.objects.count() == 19

    def it_creates_tools(seed_db):
        assert Tool.objects.count() == 45

    def it_creates_tool_reservations(seed_db):
        assert ToolReservation.objects.count() == 7

    def it_creates_revenue_splits(seed_db):
        assert RevenueSplit.objects.count() == 3

    def it_creates_rentables(seed_db):
        assert Rentable.objects.count() == 5

    def it_creates_rentals(seed_db):
        assert Rental.objects.count() == 5

    def it_creates_orders(seed_db):
        assert Order.objects.count() == 15

    def it_creates_invoices(seed_db):
        assert Invoice.objects.count() == 10

    def it_creates_payouts(seed_db):
        assert Payout.objects.count() == 5

    def it_creates_subscription_plans(seed_db):
        assert SubscriptionPlan.objects.count() == 3

    def it_creates_member_subscriptions(seed_db):
        assert MemberSubscription.objects.count() == 12

    def it_creates_discount_codes(seed_db):
        assert ClassDiscountCode.objects.count() == 4

    def it_creates_maker_classes(seed_db):
        assert MakerClass.objects.count() == 8

    def it_creates_orientations(seed_db):
        assert Orientation.objects.count() == 8

    def it_creates_leads(seed_db):
        assert Lead.objects.count() == 12

    def it_creates_tours(seed_db):
        assert Tour.objects.count() == 8

    def it_creates_events(seed_db):
        assert Event.objects.count() == 4

    def it_creates_buyables(seed_db):
        assert Buyable.objects.count() == 5

    def it_creates_buyable_purchases(seed_db):
        assert BuyablePurchase.objects.count() == 20

    def it_creates_member_schedules(seed_db):
        assert MemberSchedule.objects.count() == 10

    def it_creates_schedule_blocks(seed_db):
        assert ScheduleBlock.objects.count() == 28

    def it_creates_guild_documents(seed_db):
        assert GuildDocument.objects.count() == 6

    def it_creates_guild_wishlist_items(seed_db):
        assert GuildWishlistItem.objects.count() == 17

    def it_creates_tool_documents(seed_db):
        assert Document.objects.count() == 5


def describe_seed_data_idempotency():
    def it_does_not_duplicate_on_second_run():
        call_command("seed_data")
        call_command("seed_data")

        assert Setting.objects.count() == 4
        assert User.objects.count() == 35
        assert MembershipPlan.objects.count() == 4
        assert Member.objects.count() == 34
        assert Guild.objects.count() == 14
        assert GuildMembership.objects.count() == 39
        assert GuildVote.objects.count() == 18
        assert GuildDocument.objects.count() == 6
        assert GuildWishlistItem.objects.count() == 17
        assert Space.objects.count() == 54
        assert Lease.objects.count() == 19
        assert Tool.objects.count() == 45
        assert ToolReservation.objects.count() == 7
        assert RevenueSplit.objects.count() == 3
        assert Rentable.objects.count() == 5
        assert Rental.objects.count() == 5
        assert Order.objects.count() == 15
        assert Invoice.objects.count() == 10
        assert Payout.objects.count() == 5
        assert SubscriptionPlan.objects.count() == 3
        assert MemberSubscription.objects.count() == 12
        assert ClassDiscountCode.objects.count() == 4
        assert MakerClass.objects.count() == 8
        assert Orientation.objects.count() == 8
        assert Lead.objects.count() == 12
        assert Tour.objects.count() == 8
        assert Event.objects.count() == 4
        assert Buyable.objects.count() == 5
        assert BuyablePurchase.objects.count() == 20
        assert MemberSchedule.objects.count() == 10
        assert ScheduleBlock.objects.count() == 28
        assert Document.objects.count() == 5


def describe_seed_data_flush():
    def it_flushes_and_reseeds():
        call_command("seed_data")
        call_command("seed_data", flush=True)

        assert Setting.objects.count() == 4
        assert User.objects.count() == 35
        assert MembershipPlan.objects.count() == 4
        assert Member.objects.count() == 34
        assert Guild.objects.count() == 14
        assert GuildMembership.objects.count() == 39
        assert GuildVote.objects.count() == 18
        assert GuildDocument.objects.count() == 6
        assert GuildWishlistItem.objects.count() == 17
        assert Space.objects.count() == 54
        assert Lease.objects.count() == 19
        assert Tool.objects.count() == 45
        assert ToolReservation.objects.count() == 7
        assert RevenueSplit.objects.count() == 3
        assert Rentable.objects.count() == 5
        assert Rental.objects.count() == 5
        assert Order.objects.count() == 15
        assert Invoice.objects.count() == 10
        assert Payout.objects.count() == 5
        assert SubscriptionPlan.objects.count() == 3
        assert MemberSubscription.objects.count() == 12
        assert ClassDiscountCode.objects.count() == 4
        assert MakerClass.objects.count() == 8
        assert Orientation.objects.count() == 8
        assert Lead.objects.count() == 12
        assert Tour.objects.count() == 8
        assert Event.objects.count() == 4
        assert Buyable.objects.count() == 5
        assert BuyablePurchase.objects.count() == 20
        assert MemberSchedule.objects.count() == 10
        assert ScheduleBlock.objects.count() == 28
        assert Document.objects.count() == 5
