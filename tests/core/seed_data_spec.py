"""BDD-style tests for the seed_data management command."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from education.models import MakerClass, Orientation
from membership.models import (
    FavoriteEvent,
    Guild,
    GuildVote,
    Lease,
    Member,
    MembershipPlan,
    Space,
)
from outreach.models import Event

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture()
def seed_db():
    call_command("seed_data")


def describe_seed_data_command():
    def it_creates_admin_and_demo_users(seed_db):
        assert User.objects.count() == 26  # 1 admin + 25 demo users
        assert User.objects.filter(is_superuser=True).exists()

    def it_creates_membership_plans(seed_db):
        assert MembershipPlan.objects.count() == 4

    def it_creates_members(seed_db):
        assert Member.objects.count() == 25

    def it_creates_guilds(seed_db):
        assert Guild.objects.count() == 14

    def it_creates_guild_votes(seed_db):
        assert GuildVote.objects.count() == 18

    def it_creates_spaces(seed_db):
        assert Space.objects.count() == 12

    def it_creates_leases(seed_db):
        assert Lease.objects.count() == 8

    def it_creates_maker_classes(seed_db):
        assert MakerClass.objects.count() == 8

    def it_creates_orientations(seed_db):
        assert Orientation.objects.count() == 4


def describe_seed_data_command_events():
    def it_creates_events(seed_db):
        assert Event.objects.count() == 4

    def it_creates_favorites(seed_db):
        assert FavoriteEvent.objects.count() == 10


def describe_seed_data_idempotency():
    def it_does_not_duplicate_on_second_run():
        call_command("seed_data")
        call_command("seed_data")

        assert User.objects.count() == 26
        assert MembershipPlan.objects.count() == 4
        assert Member.objects.count() == 25
        assert Guild.objects.count() == 14
        assert GuildVote.objects.count() == 18
        assert Space.objects.count() == 12
        assert Lease.objects.count() == 8
        assert MakerClass.objects.count() == 8
        assert Orientation.objects.count() == 4
        assert Event.objects.count() == 4
        assert FavoriteEvent.objects.count() == 10


def describe_seed_data_flush():
    def it_flushes_and_reseeds():
        call_command("seed_data")
        call_command("seed_data", flush=True)

        assert User.objects.count() == 26
        assert MembershipPlan.objects.count() == 4
        assert Member.objects.count() == 25
        assert Guild.objects.count() == 14
        assert GuildVote.objects.count() == 18
        assert Space.objects.count() == 12
        assert Lease.objects.count() == 8
        assert MakerClass.objects.count() == 8
        assert Orientation.objects.count() == 4
        assert Event.objects.count() == 4
        assert FavoriteEvent.objects.count() == 10
