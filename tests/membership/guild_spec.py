"""Tests for Guild and GuildVote models."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError
from django.utils import timezone

from membership.models import Guild, GuildVote, Lease, Member, MembershipPlan, Space
from tests.membership.factories import (
    GuildFactory,
    GuildVoteFactory,
    LeaseFactory,
    MemberFactory,
    SpaceFactory,
    VotingSessionFactory,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Guild
# ---------------------------------------------------------------------------


def describe_Guild():
    def it_creates_with_factory():
        guild = GuildFactory(name="Test Guild")
        assert guild.name == "Test Guild"
        assert guild.pk is not None

    def it_has_str_representation():
        guild = GuildFactory(name="Ceramics Guild")
        assert str(guild) == "Ceramics Guild"

    def it_can_have_guild_lead():
        member = MemberFactory()
        guild = GuildFactory(guild_lead=member)
        assert guild.guild_lead == member

    def it_allows_null_guild_lead():
        guild = GuildFactory(guild_lead=None)
        assert guild.guild_lead is None

    def it_has_notes_field():
        guild = GuildFactory(name="Notes Guild", notes="Some important notes")
        guild.refresh_from_db()
        assert guild.notes == "Some important notes"

    def it_has_created_at():
        guild = GuildFactory()
        assert guild.created_at is not None

    def it_enforces_unique_name():
        GuildFactory(name="Unique Guild")
        with pytest.raises(IntegrityError):
            GuildFactory(name="Unique Guild")


def describe_Guild_active_leases():
    def it_returns_active_leases():
        guild = GuildFactory()
        today = timezone.now().date()
        space = SpaceFactory()
        lease = LeaseFactory(
            tenant_obj=guild,
            space=space,
            start_date=today - timedelta(days=10),
        )
        active = list(guild.active_leases)
        assert len(active) == 1
        assert active[0].pk == lease.pk

    def it_excludes_ended_leases():
        guild = GuildFactory()
        today = timezone.now().date()
        space = SpaceFactory()
        LeaseFactory(
            tenant_obj=guild,
            space=space,
            start_date=today - timedelta(days=60),
            end_date=today - timedelta(days=1),
        )
        active = list(guild.active_leases)
        assert len(active) == 0

    def it_excludes_future_leases():
        guild = GuildFactory()
        today = timezone.now().date()
        space = SpaceFactory()
        LeaseFactory(
            tenant_obj=guild,
            space=space,
            start_date=today + timedelta(days=30),
        )
        active = list(guild.active_leases)
        assert len(active) == 0


def describe_Guild_ordering():
    def it_orders_by_name():
        g2 = GuildFactory(name="Zebra Guild")
        g1 = GuildFactory(name="Alpha Guild")
        guilds = list(Guild.objects.all())
        assert guilds == [g1, g2]


# ---------------------------------------------------------------------------
# GuildVote
# ---------------------------------------------------------------------------


def describe_GuildVote():
    def it_creates_with_factory():
        vote = GuildVoteFactory()
        assert vote.pk is not None

    def it_has_str_representation():
        vote = GuildVoteFactory(priority=1)
        assert "\u2192" in str(vote)
        assert "#1" in str(vote)

    def it_stores_priority():
        vote = GuildVoteFactory(priority=2)
        assert vote.priority == 2

    def it_references_member_and_guild():
        member = MemberFactory()
        guild = GuildFactory()
        vote = GuildVoteFactory(member=member, guild=guild, priority=1)
        assert vote.member == member
        assert vote.guild == guild

    def describe_unique_constraints():
        def it_enforces_unique_session_member_priority():
            session = VotingSessionFactory()
            vote1 = GuildVoteFactory(session=session, priority=1)
            with pytest.raises(IntegrityError):
                GuildVoteFactory(
                    session=session,
                    member_airtable_id=vote1.member_airtable_id,
                    priority=1,
                    guild=GuildFactory(),
                )

        def it_enforces_unique_session_member_guild():
            session = VotingSessionFactory()
            vote1 = GuildVoteFactory(session=session, priority=1)
            with pytest.raises(IntegrityError):
                GuildVoteFactory(
                    session=session,
                    member_airtable_id=vote1.member_airtable_id,
                    guild=vote1.guild,
                    priority=2,
                )

        def it_allows_same_member_in_different_sessions():
            session1 = VotingSessionFactory(name="Session 1")
            session2 = VotingSessionFactory(name="Session 2")
            guild = GuildFactory()
            v1 = GuildVoteFactory(session=session1, guild=guild, priority=1, member_airtable_id="rec123")
            v2 = GuildVoteFactory(session=session2, guild=guild, priority=1, member_airtable_id="rec123")
            assert v1.pk is not None
            assert v2.pk is not None

    def describe_ordering():
        def it_orders_by_session_member_then_priority():
            session = VotingSessionFactory()
            member = MemberFactory(full_legal_name="Test Member")
            guild_a = GuildFactory(name="Guild A")
            guild_b = GuildFactory(name="Guild B")
            v2 = GuildVoteFactory(
                session=session, member=member, guild=guild_b, priority=2, member_airtable_id="rec001"
            )
            v1 = GuildVoteFactory(
                session=session, member=member, guild=guild_a, priority=1, member_airtable_id="rec001"
            )
            votes = list(GuildVote.objects.filter(session=session, member_airtable_id="rec001"))
            assert votes == [v1, v2]


# ---------------------------------------------------------------------------
# Lease with Guild tenant (GenericFK)
# ---------------------------------------------------------------------------


def describe_lease_with_guild_tenant():
    def it_creates_lease_for_guild():
        guild = GuildFactory(name="Pottery Guild")
        space = SpaceFactory()
        today = timezone.now().date()
        lease = LeaseFactory(
            tenant_obj=guild,
            space=space,
            start_date=today - timedelta(days=5),
        )
        assert lease.tenant == guild
        assert lease.pk is not None

    def it_has_str_representation_with_guild():
        guild = GuildFactory(name="Woodworking")
        space = SpaceFactory(space_id="W-100", name="Workshop")
        lease = LeaseFactory(
            tenant_obj=guild,
            space=space,
            start_date=date(2024, 6, 1),
        )
        assert str(lease) == "Woodworking @ W-100 - Workshop (2024-06-01)"

    def it_appears_in_space_current_occupants():
        guild = GuildFactory(name="Current Occupant Guild")
        space = SpaceFactory()
        today = timezone.now().date()
        LeaseFactory(
            tenant_obj=guild,
            space=space,
            start_date=today - timedelta(days=5),
        )
        occupants = space.current_occupants
        assert len(occupants) == 1
        assert occupants[0] == guild

    def it_mixes_member_and_guild_occupants():
        member = MemberFactory()
        guild = GuildFactory()
        space = SpaceFactory()
        today = timezone.now().date()
        LeaseFactory(
            tenant_obj=member,
            space=space,
            start_date=today - timedelta(days=10),
            monthly_rent=Decimal("200.00"),
        )
        LeaseFactory(
            tenant_obj=guild,
            space=space,
            start_date=today - timedelta(days=5),
            monthly_rent=Decimal("300.00"),
        )
        occupants = space.current_occupants
        assert len(occupants) == 2
        occupant_types = {type(o) for o in occupants}
        assert Member in occupant_types
        assert Guild in occupant_types

    def it_calculates_space_revenue_with_guild_lease():
        guild = GuildFactory()
        space = SpaceFactory(manual_price=Decimal("800.00"))
        today = timezone.now().date()
        LeaseFactory(
            tenant_obj=guild,
            space=space,
            monthly_rent=Decimal("500.00"),
            start_date=today - timedelta(days=5),
        )
        assert space.actual_revenue == Decimal("500.00")


# ---------------------------------------------------------------------------
# Fixture loading (synthetic data)
# ---------------------------------------------------------------------------


def describe_fixture_loading():
    def it_loads_initial_data(tmp_path):
        """Create synthetic objects, serialize to fixture, flush, reload, and verify."""
        from django.core import serializers
        from django.core.management import call_command

        # 1. Create synthetic objects via factories
        guild_a = GuildFactory(name="Ceramics Guild")
        guild_b = GuildFactory(name="Glass Guild")
        space_a = SpaceFactory(space_id="A-100", name="Studio A", sublet_guild=guild_a)
        space_b = SpaceFactory(space_id="B-200", name="Workshop B")
        member = MemberFactory(full_legal_name="Fixture Test Member")
        today = timezone.now().date()
        lease = LeaseFactory(
            tenant_obj=guild_a,
            space=space_a,
            start_date=today - timedelta(days=10),
            monthly_rent=Decimal("350.00"),
        )

        # Collect PKs before flush
        guild_a_pk = guild_a.pk
        guild_b_pk = guild_b.pk
        space_a_pk = space_a.pk
        space_b_pk = space_b.pk
        member_pk = member.pk
        lease_pk = lease.pk
        plan_pk = member.membership_plan_id

        # 2. Serialize all relevant objects to JSON fixture
        objects = list(MembershipPlan.objects.filter(pk=plan_pk))
        objects += list(Guild.objects.filter(pk__in=[guild_a_pk, guild_b_pk]))
        objects += list(Member.objects.filter(pk=member_pk))
        objects += list(Space.objects.filter(pk__in=[space_a_pk, space_b_pk]))
        objects += list(Lease.objects.filter(pk=lease_pk))

        fixture_json = serializers.serialize("json", objects, indent=2)
        fixture_path = tmp_path / "test_fixture.json"
        fixture_path.write_text(fixture_json)

        # 3. Delete all created objects (order matters for FK constraints)
        Lease.objects.filter(pk=lease_pk).delete()
        Guild.objects.filter(pk__in=[guild_a_pk, guild_b_pk]).delete()
        Member.objects.filter(pk=member_pk).delete()
        Space.objects.filter(pk__in=[space_a_pk, space_b_pk]).delete()
        MembershipPlan.objects.filter(pk=plan_pk).delete()

        # Verify they are gone
        assert Guild.objects.filter(pk=guild_a_pk).count() == 0
        assert Space.objects.filter(pk=space_a_pk).count() == 0
        assert Member.objects.filter(pk=member_pk).count() == 0
        assert Lease.objects.filter(pk=lease_pk).count() == 0

        # 4. Load the fixture
        call_command("loaddata", str(fixture_path), verbosity=0)

        # 5. Verify objects were loaded correctly
        loaded_guild_a = Guild.objects.get(pk=guild_a_pk)
        assert loaded_guild_a.name == "Ceramics Guild"

        loaded_guild_b = Guild.objects.get(pk=guild_b_pk)
        assert loaded_guild_b.name == "Glass Guild"

        loaded_space_a = Space.objects.get(pk=space_a_pk)
        assert loaded_space_a.space_id == "A-100"
        assert loaded_space_a.name == "Studio A"

        loaded_space_b = Space.objects.get(pk=space_b_pk)
        assert loaded_space_b.space_id == "B-200"
        assert loaded_space_b.name == "Workshop B"

        loaded_member = Member.objects.get(pk=member_pk)
        assert loaded_member.full_legal_name == "Fixture Test Member"

        loaded_lease = Lease.objects.get(pk=lease_pk)
        assert loaded_lease.monthly_rent == Decimal("350.00")
        assert loaded_lease.space_id == space_a_pk
        assert loaded_lease.tenant == loaded_guild_a

        # Verify sublet_guild FK survived the round-trip
        assert loaded_space_a.sublet_guild == loaded_guild_a
        assert loaded_space_b.sublet_guild is None
        assert loaded_guild_a.sublets.count() == 1
        assert loaded_guild_a.sublets.first() == loaded_space_a
