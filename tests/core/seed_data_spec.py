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
        assert Guild.objects.count() >= 6

    def it_creates_buyables():
        call_command("seed_data", verbosity=0)
        assert Buyable.objects.count() >= 6

    def it_creates_users():
        call_command("seed_data", verbosity=0)
        assert User.objects.filter(username="admin@pastlives.space").exists()
        assert User.objects.filter(username="lead1@pastlives.space").exists()
        assert User.objects.filter(username="lead2@pastlives.space").exists()
        assert User.objects.filter(username="member@pastlives.space").exists()

    def it_creates_member_user_as_non_staff():
        call_command("seed_data", verbosity=0)
        member_user = User.objects.get(username="member@pastlives.space")
        assert not member_user.is_staff
        assert not member_user.is_superuser

    def it_creates_member_record_for_member_user():
        call_command("seed_data", verbosity=0)
        member_user = User.objects.get(username="member@pastlives.space")
        member = Member.objects.get(user=member_user)
        assert member.status == Member.Status.ACTIVE
        assert member.role == Member.Role.STANDARD

    def it_creates_guild_memberships():
        call_command("seed_data", verbosity=0)
        assert GuildMembership.objects.filter(is_lead=True).count() >= 4

    def it_creates_member_guild_memberships():
        call_command("seed_data", verbosity=0)
        member_user = User.objects.get(username="member@pastlives.space")
        memberships = GuildMembership.objects.filter(user=member_user)
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
        call_command("seed_data", verbosity=0)
        count2 = Guild.objects.count()
        assert count1 == count2

    def it_prints_success_messages_with_verbosity():
        out = StringIO()
        call_command("seed_data", verbosity=1, stdout=out)
        output = out.getvalue()
        assert "Seed data loaded successfully" in output
