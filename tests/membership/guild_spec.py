"""Tests for Guild model."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.db.models.signals import post_save
from django.test import override_settings
from django.utils import timezone
from factory.django import mute_signals

from core.events import resolvers
from core.events.registry import Recipients
from membership.models import Guild, Lease, Member, MembershipPlan, Space
from tests.membership.factories import (
    GuildFactory,
    GuildMembershipFactory,
    LeaseFactory,
    MemberFactory,
    SpaceFactory,
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
        Guild.objects.create(name="Duplicate Guild")
        with pytest.raises(IntegrityError):
            Guild.objects.create(name="Duplicate Guild")


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
        names = [g.name for g in guilds]
        assert names == sorted(names)
        assert g1 in guilds
        assert g2 in guilds


def describe_announcement_recipients():
    def _joined(guild, *, email="member@example.com", status=Member.Status.ACTIVE, link_user=True):
        """An ACTIVE member joined to ``guild``, linked to an email-bearing User by default.

        Mirrors the event-spine conftest's ``linked_member`` (mute the auto-provision signal so
        attaching our own User doesn't spawn a second Member). ``email=""`` or ``link_user=False``
        build members the resolver must drop.
        """
        member = MemberFactory(status=status)
        if link_user:
            with mute_signals(post_save):
                user = User.objects.create_user(username=f"gm_{member.pk}", email=email)
            member.user = user
            member.save(update_fields=["user"])
        GuildMembershipFactory(guild=guild, member=member)
        return member

    def it_counts_active_members_with_email():
        guild = GuildFactory()
        _joined(guild, email="a@example.com")
        _joined(guild, email="b@example.com")
        recipients = guild.announcement_recipients()
        assert len(recipients) == 2
        assert {user.email for user, _reason in recipients} == {"a@example.com", "b@example.com"}

    def it_excludes_members_with_no_email():
        guild = GuildFactory()
        _joined(guild, email="has@example.com")
        _joined(guild, email="")  # linked User but blank email → dropped
        _joined(guild, link_user=False)  # unlinked member → dropped
        recipients = guild.announcement_recipients()
        assert len(recipients) == 1
        assert [user.email for user, _reason in recipients] == ["has@example.com"]

    def it_excludes_inactive_members():
        guild = GuildFactory()
        _joined(guild, email="active@example.com", status=Member.Status.ACTIVE)
        _joined(guild, email="former@example.com", status=Member.Status.FORMER)
        recipients = guild.announcement_recipients()
        assert [user.email for user, _reason in recipients] == ["active@example.com"]

    def it_is_empty_for_a_guild_with_no_members():
        guild = GuildFactory()
        assert guild.announcement_recipients() == []

    def it_matches_the_send_resolver():
        guild = GuildFactory()
        _joined(guild, email="one@example.com")
        _joined(guild, email="two@example.com")
        assert guild.announcement_recipients() == resolvers.resolve(Recipients.GUILD_MEMBERS, {"guild": guild})


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


def describe_guild_soft_delete():
    def it_sets_deleted_at():
        guild = GuildFactory(name="Soft Delete Me")
        assert guild.deleted_at is None
        guild.soft_delete()
        guild.refresh_from_db()
        assert guild.deleted_at is not None

    def it_hides_the_guild_from_the_default_manager():
        guild = GuildFactory(name="Hidden Guild")
        guild.soft_delete()
        assert not Guild.objects.filter(pk=guild.pk).exists()

    def it_keeps_the_guild_in_all_objects():
        guild = GuildFactory(name="Recoverable Guild")
        guild.soft_delete()
        assert Guild.all_objects.filter(pk=guild.pk).exists()

    def it_preserves_relations_via_the_base_manager():
        guild = GuildFactory(name="Has A Lease")
        lease = LeaseFactory(tenant_obj=guild)
        guild.soft_delete()
        # The lease's GenericFK still resolves the (now-hidden) guild via the base manager.
        lease.refresh_from_db()
        assert lease.tenant == guild
        assert Guild.all_objects.get(pk=guild.pk) == guild

    def it_can_be_restored_by_clearing_deleted_at():
        guild = GuildFactory(name="Comes Back")
        guild.soft_delete()
        guild.deleted_at = None
        guild.save(update_fields=["deleted_at"])
        assert Guild.objects.filter(pk=guild.pk).exists()

    def it_avoids_slug_collision_with_a_soft_deleted_guild():
        # A new guild whose name slugifies onto a *soft-deleted* guild's slug must still get a
        # unique slug — _unique_slug checks all_objects, so the DB unique constraint can't blow up.
        deleted = GuildFactory(name="Glass")
        assert deleted.slug == "glass"
        deleted.soft_delete()
        fresh = GuildFactory(name="Glass!")
        assert fresh.slug == "glass-2"


def describe_directory():
    def it_defaults_is_featured_to_false():
        guild = GuildFactory()
        assert guild.is_featured is False

    def it_lists_only_active_guilds():
        GuildFactory(name="Active Guild", is_active=True)
        GuildFactory(name="Retired Guild", is_active=False)
        names = list(Guild.objects.directory().values_list("name", flat=True))
        assert "Active Guild" in names
        assert "Retired Guild" not in names

    def it_orders_featured_first_then_alphabetical():
        GuildFactory(name="Zebra Guild", is_featured=False)
        GuildFactory(name="Alpha Guild", is_featured=False)
        GuildFactory(name="Woodworking", is_featured=True)
        names = list(Guild.objects.directory().values_list("name", flat=True))
        assert names == ["Woodworking", "Alpha Guild", "Zebra Guild"]

    def it_excludes_soft_deleted_guilds():
        gone = GuildFactory(name="Gone Guild")
        gone.soft_delete()
        assert "Gone Guild" not in list(Guild.objects.directory().values_list("name", flat=True))


# ---------------------------------------------------------------------------
# Vanity URL, QR code, and essential_rules (flyer feature)
# ---------------------------------------------------------------------------


def describe_vanity_url():
    @override_settings(MEMBER_BASE_URL="https://pastlives.app")
    def it_is_the_member_host_g_slug_path():
        guild = GuildFactory(name="Ceramics")
        assert guild.vanity_url == f"https://pastlives.app/g/{guild.slug}/"


def describe_qr_svg():
    @override_settings(MEMBER_BASE_URL="https://pastlives.app")
    def it_returns_non_empty_svg_markup():
        guild = GuildFactory(name="Ceramics")
        svg = guild.qr_svg()
        assert "<svg" in svg
        assert svg.strip() != ""

    @override_settings(MEMBER_BASE_URL="https://pastlives.app")
    def it_encodes_the_vanity_url():
        from membership.qr import qr_svg

        guild = GuildFactory(name="Ceramics")
        # The QR is generated from the vanity URL specifically (not the slug or guest URL).
        assert guild.qr_svg() == qr_svg(guild.vanity_url)

    @override_settings(MEMBER_BASE_URL="https://pastlives.app")
    def it_differs_for_a_different_guild():
        one = GuildFactory(name="Ceramics")
        two = GuildFactory(name="Woodworking")
        assert one.qr_svg() != two.qr_svg()


def describe_qr_png_bytes():
    @override_settings(MEMBER_BASE_URL="https://pastlives.app")
    def it_returns_bytes_with_the_png_magic_header():
        guild = GuildFactory(name="Ceramics")
        png = guild.qr_png_bytes()
        assert isinstance(png, bytes)
        assert png.startswith(b"\x89PNG\r\n\x1a\n")


def describe_essential_rules():
    def it_defaults_to_an_empty_string():
        guild = GuildFactory(name="Ceramics")
        assert guild.essential_rules == ""
