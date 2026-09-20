"""Data-migration spec for 0176 — seeding the first Leadership & Admin Team roster (#464).

Uses Django's ``MigrationExecutor`` (the 0112 precedent) so fixtures are built with the
historical models at the pre-seed state. Each test restores the schema to head in a
``finally`` so the rest of the suite sees the current DB.
"""

from __future__ import annotations

from importlib import import_module

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

_APP = "membership"
_BEFORE = "0175_leadership_directory"
_AFTER = "0176_seed_leadership_roster"

_migration = import_module("membership.migrations.0176_seed_leadership_roster")


def _migrate(target: str):
    """Migrate the membership app to ``target`` and return that state's historical apps."""
    executor = MigrationExecutor(connection)
    executor.migrate([(_APP, target)])
    return executor.loader.project_state([(_APP, target)]).apps


def _make_member(apps, name: str, **fields):
    Plan = apps.get_model(_APP, "MembershipPlan")
    plan = Plan.objects.first() or Plan.objects.create(name="Seed plan", monthly_price=0)
    return apps.get_model(_APP, "Member").objects.create(full_legal_name=name, membership_plan=plan, **fields)


def _make_guild(apps, name: str, **fields):
    return apps.get_model(_APP, "Guild").objects.create(name=name, slug=name.lower().replace(" ", "-"), **fields)


def _listings(apps) -> list[tuple[str, int, bool]]:
    rows = apps.get_model(_APP, "LeadershipListing").objects.order_by("sort_order")
    return list(rows.values_list("member__full_legal_name", "sort_order", "is_listed"))


@pytest.mark.django_db(transaction=True)
def describe_migration_0176_seed_leadership_roster():
    def it_seeds_matched_members_in_roster_order_with_their_roles():
        try:
            apps = _migrate(_BEFORE)
            _make_member(apps, "Lee Mendelsohn ")  # production names carry a trailing space
            _make_member(apps, "Brandon Morlock", preferred_name="Morlock")
            _make_member(apps, "Sushuma Thornburgh", status="former")  # status is not a gate

            apps = _migrate(_AFTER)

            assert _listings(apps) == [
                ("Brandon Morlock", 0, True),
                ("Lee Mendelsohn ", 3, True),
                ("Sushuma Thornburgh", 5, True),
            ]
            Role = apps.get_model(_APP, "LeadershipRole")
            lee = Role.objects.filter(listing__member__full_legal_name="Lee Mendelsohn ").order_by("sort_order")
            assert list(lee.values_list("title", "email", "sort_order")) == [
                ("Membership Director and Internal Operations Director", "membership@pastlives.space", 0),
                ("Class Administrator", "lee@pastlives.space", 1),
            ]
        finally:
            _migrate(_AFTER)

    def it_falls_back_to_the_discord_handle_or_id_when_the_name_matches_nobody():
        try:
            apps = _migrate(_BEFORE)
            _make_member(apps, "P. V.", discord_handle="@phoebex")  # handle, case-insensitive
            _make_member(apps, "A. S.", discord_user_id="712772431961653299")  # the post's <@id> form

            apps = _migrate(_AFTER)

            assert [row[0] for row in _listings(apps)] == ["A. S.", "P. V."]
        finally:
            _migrate(_AFTER)

    def it_skips_a_name_with_no_single_match_and_says_so(capsys):
        try:
            apps = _migrate(_BEFORE)
            _make_member(apps, "Dixie Junius")
            _make_member(apps, "Dixie Junius Jr")  # two name matches; the handle @Dixie matches nobody
            _make_member(apps, "Shane Stewart")
            _make_member(apps, "Shane Stewart II")  # two name matches and no handle to fall back on

            apps = _migrate(_AFTER)

            assert _listings(apps) == []
            out = capsys.readouterr().out
            assert "Dixie Junius" in out
            assert "Shane Stewart" in out
        finally:
            _migrate(_AFTER)

    def it_writes_channel_names_only_for_guilds_matched_exactly_once(capsys):
        try:
            apps = _migrate(_BEFORE)
            wood = _make_guild(apps, "Woodworking Guild")
            named = _make_guild(apps, "Glass Guild", discord_channel_name="#glass-already")
            _make_guild(apps, "Tech Guild")
            _make_guild(apps, "Tech Guild Annex")  # two matches: skipped
            _make_guild(apps, "Writers Guild", is_active=False)  # inactive: never matched

            apps = _migrate(_AFTER)

            Guild = apps.get_model(_APP, "Guild")
            assert Guild.objects.get(pk=wood.pk).discord_channel_name == "#🦫-woodworkers"
            assert Guild.objects.get(pk=named.pk).discord_channel_name == "#glass-already"
            assert set(
                Guild.objects.filter(name__startswith="Tech").values_list("discord_channel_name", flat=True)
            ) == {""}
            assert Guild.objects.get(name="Writers Guild").discord_channel_name == ""
            out = capsys.readouterr().out
            assert "Tech" in out
            assert "Writers" in out
        finally:
            _migrate(_AFTER)

    def it_changes_no_guild_lead():
        try:
            apps = _migrate(_BEFORE)
            jesse = _make_member(apps, "Jesse Dohmann")
            _make_member(apps, "Shawn Fox")
            guild = _make_guild(apps, "Leatherwork Guild", guild_lead=jesse)

            apps = _migrate(_AFTER)

            assert apps.get_model(_APP, "Guild").objects.get(pk=guild.pk).guild_lead_id == jesse.pk
        finally:
            _migrate(_AFTER)

    def it_is_idempotent():
        try:
            apps = _migrate(_BEFORE)
            _make_member(apps, "Phoebe Valenti")
            _make_guild(apps, "Ceramics Guild")

            apps = _migrate(_AFTER)
            Listing = apps.get_model(_APP, "LeadershipListing")
            Role = apps.get_model(_APP, "LeadershipRole")
            assert (Listing.objects.count(), Role.objects.count()) == (1, 1)

            _migration.seed_roster(apps, None)

            assert (Listing.objects.count(), Role.objects.count()) == (1, 1)
        finally:
            _migrate(_AFTER)

    def it_reverse_removes_only_what_it_seeded():
        try:
            apps = _migrate(_BEFORE)
            _make_member(apps, "Phoebe Valenti")
            by_hand = _make_member(apps, "Someone Else")
            seeded_guild = _make_guild(apps, "Ceramics Guild")
            hand_guild = _make_guild(apps, "Glass Guild", discord_channel_name="#glass-by-hand")

            apps = _migrate(_AFTER)
            Listing = apps.get_model(_APP, "LeadershipListing")
            Listing.objects.create(member_id=by_hand.pk, is_listed=True, sort_order=9)
            assert Listing.objects.count() == 2

            apps = _migrate(_BEFORE)  # unapplies 0176: the real reverse runs

            Listing = apps.get_model(_APP, "LeadershipListing")
            assert list(Listing.objects.values_list("member_id", flat=True)) == [by_hand.pk]
            Guild = apps.get_model(_APP, "Guild")
            assert Guild.objects.get(pk=seeded_guild.pk).discord_channel_name == ""
            assert Guild.objects.get(pk=hand_guild.pk).discord_channel_name == "#glass-by-hand"
        finally:
            _migrate(_AFTER)
