"""BDD tests for the seed_data management command."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from membership.models import (
    Buyable,
    Guild,
    GuildWishlistItem,
    Lease,
    Member,
    MembershipPlan,
    Space,
)

User = get_user_model()

ADMIN_EMAIL = "admin@pastlives.space"


def _seed(**kwargs: object) -> None:
    call_command("seed_data", stdout=open("/dev/null", "w"), **kwargs)  # noqa: SIM115


@pytest.mark.django_db
def describe_seed_data_counts():
    def it_creates_30_members() -> None:
        _seed()
        assert Member.objects.count() == 30

    def it_creates_14_guilds() -> None:
        _seed()
        assert Guild.objects.count() == 14

    def it_creates_admin_superuser() -> None:
        _seed()
        admin = User.objects.get(email=ADMIN_EMAIL)
        assert admin.is_staff is True
        assert admin.is_superuser is True

    def it_creates_3_membership_plans() -> None:
        _seed()
        assert MembershipPlan.objects.count() == 3

    def it_creates_13_buyables() -> None:
        _seed()
        assert Buyable.objects.count() == 13

    def it_creates_4_spaces() -> None:
        _seed()
        assert Space.objects.count() == 4

    def it_creates_3_leases() -> None:
        _seed()
        assert Lease.objects.count() == 3

    def it_creates_3_wishlist_items() -> None:
        _seed()
        assert GuildWishlistItem.objects.count() == 3


@pytest.mark.django_db
def describe_seed_data_roles():
    def it_sets_staff_flag_on_first_7_users() -> None:
        _seed()
        staff_members = Member.objects.filter(role=Member.Role.EMPLOYEE)
        assert staff_members.count() == 7
        for member in staff_members:
            assert member.user is not None
            assert member.user.is_staff is True

    def it_assigns_guild_leads_to_all_14_guilds() -> None:
        _seed()
        for guild in Guild.objects.all():
            assert guild.guild_lead is not None, f"{guild.name} has no guild lead"

    def it_applies_guild_links() -> None:
        _seed()
        woodworking = Guild.objects.get(name="Woodworking")
        assert len(woodworking.links) == 2
        assert woodworking.links[0]["name"] == "Instagram"


@pytest.mark.django_db
def describe_seed_data_safety():
    def it_contains_no_pastlives_emails() -> None:
        _seed()
        for member in Member.objects.all():
            assert "@pastlives.space" not in member.email

    def it_uses_example_emails_for_members() -> None:
        _seed()
        for member in Member.objects.all():
            if member.email:
                assert member.email.endswith("@example.com")

    def it_produces_deterministic_output() -> None:
        _seed()
        names_first = list(Member.objects.order_by("pk").values_list("full_legal_name", flat=True))
        _seed(flush=True)
        names_second = list(Member.objects.order_by("pk").values_list("full_legal_name", flat=True))
        assert names_first == names_second


@pytest.mark.django_db
def describe_seed_data_idempotency():
    def it_skips_when_data_exists() -> None:
        _seed()
        count_before = Member.objects.count()
        _seed()
        assert Member.objects.count() == count_before

    def it_recreates_data_on_flush() -> None:
        _seed()
        assert Member.objects.count() == 30
        _seed(flush=True)
        assert Member.objects.count() == 30

    def it_preserves_admin_on_flush() -> None:
        _seed()
        admin = User.objects.get(email=ADMIN_EMAIL)
        admin_pk = admin.pk
        _seed(flush=True)
        assert User.objects.filter(email=ADMIN_EMAIL, pk=admin_pk).exists()
