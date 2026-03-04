from io import StringIO
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from membership.models import Buyable, Guild, GuildMembership, GuildWishlistItem, Lease, Member, Order, Space

pytestmark = pytest.mark.django_db

User = get_user_model()

EXAMPLE_FILE = str(Path(__file__).resolve().parent.parent.parent / "core" / "data" / "seed_members.example.json")


def _seed(**kwargs):
    call_command("seed_data", members_file=EXAMPLE_FILE, verbosity=0, **kwargs)


def describe_seed_data():
    def it_creates_guilds():
        _seed()
        assert Guild.objects.count() >= 14

    def it_creates_buyables():
        _seed()
        assert Buyable.objects.count() >= 6

    def it_creates_admin_user():
        _seed()
        assert User.objects.filter(username="admin@pastlives.space").exists()
        admin_user = User.objects.get(username="admin@pastlives.space")
        assert admin_user.is_staff
        assert admin_user.is_superuser

    def it_creates_at_least_30_members():
        _seed()
        assert Member.objects.count() >= 30

    def it_creates_staff_as_employees():
        _seed()
        staff_member = Member.objects.get(email="alex.admin@example.pastlives.space")
        assert staff_member.role == Member.Role.EMPLOYEE

    def it_creates_guild_lead_memberships_for_every_guild():
        _seed()
        for guild in Guild.objects.all():
            assert guild.memberships.filter(is_lead=True).exists(), f"{guild.name} has no lead"

    def it_sets_guild_lead_fk_on_every_guild():
        _seed()
        for guild in Guild.objects.all():
            assert guild.guild_lead is not None, f"{guild.name} has no guild_lead FK"

    def it_creates_regular_member_guild_memberships():
        _seed()
        river = User.objects.get(username="river.chen@example.pastlives.space")
        memberships = GuildMembership.objects.filter(user=river)
        assert memberships.count() == 2
        assert not memberships.filter(is_lead=True).exists()

    def it_creates_wishlist_items():
        _seed()
        assert GuildWishlistItem.objects.count() >= 3

    def it_creates_spaces():
        _seed()
        assert Space.objects.count() >= 4


def describe_seed_data_advanced():
    def it_creates_leases():
        _seed()
        assert Lease.objects.count() >= 3

    def it_creates_orders():
        _seed()
        assert Order.objects.count() >= 3

    def it_is_idempotent():
        _seed()
        count1 = Guild.objects.count()
        member_count1 = Member.objects.count()
        _seed()
        count2 = Guild.objects.count()
        member_count2 = Member.objects.count()
        assert count1 == count2
        assert member_count1 == member_count2

    def it_prints_success_messages_with_verbosity():
        out = StringIO()
        call_command("seed_data", members_file=EXAMPLE_FILE, verbosity=1, stdout=out)
        output = out.getvalue()
        assert "Seed data loaded successfully" in output

    def it_errors_when_members_file_not_found():
        with pytest.raises(CommandError, match="Members file not found"):
            call_command("seed_data", members_file="/nonexistent/path.json", verbosity=0)
