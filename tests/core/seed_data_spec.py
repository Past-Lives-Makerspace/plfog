from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from membership.models import Buyable, Guild, GuildMembership, GuildWishlistItem, Lease, Member, Order, Space

pytestmark = pytest.mark.django_db

User = get_user_model()


def describe_seed_data():
    def it_creates_guilds():
        call_command("seed_data", verbosity=0)
        assert Guild.objects.count() >= 14

    def it_creates_buyables():
        call_command("seed_data", verbosity=0)
        assert Buyable.objects.count() >= 6

    def it_creates_admin_user():
        call_command("seed_data", verbosity=0)
        assert User.objects.filter(username="admin@pastlives.space").exists()
        admin_user = User.objects.get(username="admin@pastlives.space")
        assert admin_user.is_staff
        assert admin_user.is_superuser

    def it_creates_at_least_30_members():
        call_command("seed_data", verbosity=0)
        assert Member.objects.count() >= 30

    def it_creates_staff_as_employees():
        call_command("seed_data", verbosity=0)
        morlock = Member.objects.get(email="morlock@pastlives.space")
        assert morlock.role == Member.Role.EMPLOYEE

    def it_creates_guild_lead_memberships_for_every_guild():
        call_command("seed_data", verbosity=0)
        for guild in Guild.objects.all():
            assert guild.memberships.filter(is_lead=True).exists(), f"{guild.name} has no lead"

    def it_sets_guild_lead_fk_on_every_guild():
        call_command("seed_data", verbosity=0)
        for guild in Guild.objects.all():
            assert guild.guild_lead is not None, f"{guild.name} has no guild_lead FK"

    def it_creates_regular_member_guild_memberships():
        call_command("seed_data", verbosity=0)
        lane = User.objects.get(username="lane@pastlives.space")
        memberships = GuildMembership.objects.filter(user=lane)
        assert memberships.count() == 2
        assert not memberships.filter(is_lead=True).exists()

    def it_creates_wishlist_items():
        call_command("seed_data", verbosity=0)
        assert GuildWishlistItem.objects.count() >= 3

    def it_creates_spaces():
        call_command("seed_data", verbosity=0)
        assert Space.objects.count() >= 4


def describe_seed_data_advanced():
    def it_creates_leases():
        call_command("seed_data", verbosity=0)
        assert Lease.objects.count() >= 3

    def it_creates_orders():
        call_command("seed_data", verbosity=0)
        assert Order.objects.count() >= 3

    def it_is_idempotent():
        call_command("seed_data", verbosity=0)
        count1 = Guild.objects.count()
        member_count1 = Member.objects.count()
        call_command("seed_data", verbosity=0)
        count2 = Guild.objects.count()
        member_count2 = Member.objects.count()
        assert count1 == count2
        assert member_count1 == member_count2

    def it_prints_success_messages_with_verbosity():
        out = StringIO()
        call_command("seed_data", verbosity=1, stdout=out)
        output = out.getvalue()
        assert "Seed data loaded successfully" in output
