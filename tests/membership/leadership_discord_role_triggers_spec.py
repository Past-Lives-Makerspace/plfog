"""Every change that moves who leads, guides or teaches enqueues a Discord role reconcile after commit (#1148, #1270).

Each spec captures the commit callbacks and reads which members a
:class:`~core.events.discord_leadership_roles._ReconcileWhenCommitted` was queued for; nothing
runs unless the spec asks with ``execute=True``. Discord HTTP is mocked with ``respx``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

import httpx
import pytest
import respx
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

from core.events.discord_leadership_roles import _ReconcileWhenCommitted
from core.models import SiteConfiguration
from hub.forms import GuildRoleForm, GuildVisibilityForm
from membership.models import AdminCapability, GuildStaffMembership, LeadershipListing, LeadershipTab, Member
from tests.membership.factories import (
    GuildFactory,
    GuildStaffMembershipFactory,
    LeadershipListingFactory,
    LeadershipTabFactory,
    MemberFactory,
)

pytestmark = pytest.mark.django_db

T = TypeVar("T")


API = "https://discord.com/api/v10"


@pytest.fixture
def discord_on(settings):
    settings.DISCORD_BOT_TOKEN = "bot-tok"
    config = SiteConfiguration.load()
    config.discord_server_id = "srv"
    config.discord_leadership_role_id = "111"
    config.discord_guild_officers_role_id = "222"
    config.save()
    return config


def _settled(django_capture_on_commit_callbacks: Callable[..., Any], build: Callable[[], T]) -> T:
    """Build the setup and fire its own commit callbacks, so a spec sees only what its action queues.

    A setup save queues a reconcile too, and one still waiting in the test's transaction
    absorbs a later one for the same member (one task per member per transaction).
    """
    with django_capture_on_commit_callbacks(execute=True):
        return build()


def _queued(callbacks: list) -> list[int]:
    return sorted(cb.member_id for cb in callbacks if isinstance(cb, _ReconcileWhenCommitted))


def describe_guild_lead():
    def it_queues_the_old_and_the_new_lead_on_assign(django_capture_on_commit_callbacks):
        old, new = MemberFactory(), MemberFactory()
        guild = GuildFactory(guild_lead=old)
        with django_capture_on_commit_callbacks() as callbacks:
            guild.assign_lead(new)
        assert _queued(callbacks) == sorted([old.pk, new.pk])

    def it_queues_the_new_lead_alone_when_there_was_none(django_capture_on_commit_callbacks):
        new = MemberFactory()
        guild = GuildFactory()
        with django_capture_on_commit_callbacks() as callbacks:
            guild.assign_lead(new)
        assert _queued(callbacks) == [new.pk]

    def it_queues_the_old_lead_on_clear(django_capture_on_commit_callbacks):
        old = MemberFactory()
        guild = GuildFactory(guild_lead=old)
        with django_capture_on_commit_callbacks() as callbacks:
            guild.clear_lead()
        assert _queued(callbacks) == [old.pk]

    def it_queues_nothing_when_clearing_a_guild_with_no_lead(django_capture_on_commit_callbacks):
        guild = GuildFactory()
        with django_capture_on_commit_callbacks() as callbacks:
            guild.clear_lead()
        assert _queued(callbacks) == []

    def it_is_reached_by_the_set_guild_lead_command(django_capture_on_commit_callbacks):
        new = MemberFactory()
        guild = GuildFactory(name="Wood Command")
        with django_capture_on_commit_callbacks() as callbacks:
            call_command("set_guild_lead", "--guild", str(guild.pk), "--member", new.primary_email)
        assert _queued(callbacks) == [new.pk]

    def it_queues_nothing_when_the_save_rolls_back(django_capture_on_commit_callbacks):
        new = MemberFactory()
        guild = GuildFactory()
        with django_capture_on_commit_callbacks() as callbacks:
            try:
                with transaction.atomic():
                    guild.assign_lead(new)
                    raise RuntimeError("roll back")
            except RuntimeError:
                pass
        assert _queued(callbacks) == []


def describe_guild_staff():
    def it_queues_the_member_on_add(django_capture_on_commit_callbacks):
        guild, member = GuildFactory(), MemberFactory()
        with django_capture_on_commit_callbacks() as callbacks:
            guild.add_staff(member, role=GuildStaffMembership.Role.SECRETARY)
        assert _queued(callbacks) == [member.pk]

    def it_queues_the_member_on_remove(django_capture_on_commit_callbacks):
        staff = GuildStaffMembershipFactory(role=GuildStaffMembership.Role.TREASURER)
        with django_capture_on_commit_callbacks() as callbacks:
            staff.guild.remove_staff(staff)
        assert _queued(callbacks) == [staff.member_id]

    def it_queues_the_member_on_a_role_change(django_capture_on_commit_callbacks):
        staff = GuildStaffMembershipFactory(role=GuildStaffMembership.Role.TREASURER)
        with django_capture_on_commit_callbacks() as callbacks:
            staff.retitle(GuildStaffMembership.Role.CO_LEAD, "")
        assert _queued(callbacks) == [staff.member_id]


def describe_guild_changes():
    def it_queues_the_lead_and_staff_when_the_guild_is_hidden(django_capture_on_commit_callbacks):
        lead = MemberFactory()
        guild = GuildFactory(guild_lead=lead)
        staff = GuildStaffMembershipFactory(guild=guild, role=GuildStaffMembership.Role.SECRETARY)
        form = GuildVisibilityForm({"is_active": "", "is_new_guild": ""}, instance=guild)
        assert form.is_valid()
        with django_capture_on_commit_callbacks() as callbacks:
            form.save()
        assert _queued(callbacks) == sorted([lead.pk, staff.member_id])

    def it_queues_nothing_when_only_new_guild_changes(django_capture_on_commit_callbacks):
        guild = GuildFactory(guild_lead=MemberFactory())
        form = GuildVisibilityForm({"is_active": "on", "is_new_guild": "on"}, instance=guild)
        assert form.is_valid()
        with django_capture_on_commit_callbacks() as callbacks:
            form.save()
        assert _queued(callbacks) == []

    def it_queues_nothing_when_a_guilds_member_role_ids_change(django_capture_on_commit_callbacks):
        guild = GuildFactory(guild_lead=MemberFactory(), discord_lead_role_id="4321")
        form = GuildRoleForm({"discord_role_ids": "77 78"}, instance=guild)
        assert form.is_valid()
        with django_capture_on_commit_callbacks() as callbacks:
            form.save()
        guild.refresh_from_db()
        assert guild.discord_role_ids == ["77", "78"]
        assert guild.discord_lead_role_id == "4321"
        assert _queued(callbacks) == []

    def it_queues_nothing_until_a_form_commits(django_capture_on_commit_callbacks):
        guild = GuildFactory(guild_lead=MemberFactory())
        visibility_form = GuildVisibilityForm({"is_active": "", "is_new_guild": ""}, instance=guild)
        assert visibility_form.is_valid()
        with django_capture_on_commit_callbacks() as callbacks:
            visibility_form.save(commit=False)
        assert _queued(callbacks) == []

    def it_refuses_superscript_digits_in_the_member_role_ids():
        form = GuildRoleForm({"discord_role_ids": "12 \u00b2"}, instance=GuildFactory())
        assert not form.is_valid()
        assert "discord_role_ids" in form.errors

    def it_queues_the_lead_when_the_guild_is_deleted(django_capture_on_commit_callbacks):
        lead = MemberFactory()
        guild = GuildFactory(guild_lead=lead)
        with django_capture_on_commit_callbacks() as callbacks:
            guild.soft_delete()
        assert _queued(callbacks) == [lead.pk]


def describe_tour_guide_permission():
    TOUR_GUIDE = AdminCapability.Capability.TOUR_GUIDE

    def it_queues_the_member_on_a_grant_and_a_revoke(django_capture_on_commit_callbacks):
        member = MemberFactory()
        with django_capture_on_commit_callbacks(execute=True) as granted:
            member.set_admin_capability(TOUR_GUIDE, True)
        assert _queued(granted) == [member.pk]
        with django_capture_on_commit_callbacks() as revoked:
            member.set_admin_capability(TOUR_GUIDE, False)
        assert _queued(revoked) == [member.pk]

    def it_queues_nothing_when_nothing_changes_or_for_another_permission(django_capture_on_commit_callbacks):
        member = _settled(django_capture_on_commit_callbacks, MemberFactory)
        _settled(django_capture_on_commit_callbacks, lambda: member.set_admin_capability(TOUR_GUIDE, True))
        with django_capture_on_commit_callbacks() as callbacks:
            member.set_admin_capability(TOUR_GUIDE, True)
            member.set_admin_capability(AdminCapability.Capability.REFUNDS, True)
            member.set_admin_capability(AdminCapability.Capability.REFUNDS, False)
            member.set_admin_capability(AdminCapability.Capability.WEBMASTER, False)
        assert _queued(callbacks) == []

    def it_queues_the_member_when_the_edit_page_adds_or_drops_it(django_capture_on_commit_callbacks):
        member = MemberFactory()
        with django_capture_on_commit_callbacks(execute=True) as added:
            member.sync_admin_capabilities([TOUR_GUIDE])
        assert _queued(added) == [member.pk]
        with django_capture_on_commit_callbacks(execute=True) as dropped:
            member.sync_admin_capabilities([AdminCapability.Capability.REFUNDS])
        assert _queued(dropped) == [member.pk]
        with django_capture_on_commit_callbacks() as untouched:
            member.sync_admin_capabilities([])
        assert _queued(untouched) == []


def describe_teaching():
    def it_queues_the_member_when_teaching_is_granted_once(django_capture_on_commit_callbacks):
        member = MemberFactory(instructor_oriented_at=None)
        with django_capture_on_commit_callbacks() as callbacks:
            member.grant_teaching(granted_by=None)
        assert _queued(callbacks) == [member.pk]
        with django_capture_on_commit_callbacks() as again:
            member.grant_teaching(granted_by=None)
        assert _queued(again) == []

    def it_queues_the_member_when_teaching_is_revoked(django_capture_on_commit_callbacks):
        member = _settled(
            django_capture_on_commit_callbacks, lambda: MemberFactory(instructor_oriented_at=timezone.now())
        )
        with django_capture_on_commit_callbacks() as callbacks:
            member.revoke_teaching(revoked_by=None)
        assert _queued(callbacks) == [member.pk]

    def it_queues_a_member_made_an_instructor_on_the_edit_page(django_capture_on_commit_callbacks):
        member = MemberFactory(instructor_oriented_at=None, instructor_slug="")
        with django_capture_on_commit_callbacks() as callbacks:
            member.apply_admin_role(Member.ADMIN_ROLE_INSTRUCTOR)
        assert _queued(callbacks) == [member.pk]

    def it_queues_a_member_whose_page_is_removed_while_teaching_is_locked(django_capture_on_commit_callbacks):
        member = _settled(
            django_capture_on_commit_callbacks,
            lambda: MemberFactory(instructor_oriented_at=None, instructor_slug="page-only"),
        )
        assert Member.objects.instructors().filter(pk=member.pk).exists()
        with django_capture_on_commit_callbacks() as callbacks:
            member.revoke_instructor(revoked_by=None)
        assert _queued(callbacks) == [member.pk]
        with django_capture_on_commit_callbacks() as again:
            member.revoke_instructor(revoked_by=None)
        assert _queued(again) == []

    def it_queues_the_member_when_an_instructor_page_is_minted_once(django_capture_on_commit_callbacks):
        member = MemberFactory(instructor_oriented_at=None, instructor_slug="")
        with django_capture_on_commit_callbacks(execute=True) as minted:
            assert member.ensure_instructor_slug()
        assert _queued(minted) == [member.pk]
        with django_capture_on_commit_callbacks() as again:
            assert not member.ensure_instructor_slug()
        assert _queued(again) == []


def describe_leadership_directory():
    def it_queues_a_member_listed_on_a_tab(django_capture_on_commit_callbacks):
        tab, member = LeadershipTabFactory(), MemberFactory()
        with django_capture_on_commit_callbacks() as callbacks:
            tab.list_member(member, "Treasurer", "")
        assert _queued(callbacks) == [member.pk]

    def it_queues_a_member_relisted_on_a_tab(django_capture_on_commit_callbacks):
        listing = LeadershipListingFactory(is_listed=False)
        with django_capture_on_commit_callbacks() as callbacks:
            listing.tab.list_member(listing.member, "Treasurer", "")
        assert _queued(callbacks) == [listing.member_id]

    def it_queues_a_member_taken_off_a_tab(django_capture_on_commit_callbacks):
        listing = _settled(django_capture_on_commit_callbacks, LeadershipListingFactory)
        with django_capture_on_commit_callbacks() as callbacks:
            listing.remove_from_tab()
        assert _queued(callbacks) == [listing.member_id]

    def it_queues_nothing_for_a_reorder_or_a_resave(django_capture_on_commit_callbacks):
        first = LeadershipListingFactory()
        second = LeadershipListingFactory(tab=first.tab)
        with django_capture_on_commit_callbacks() as callbacks:
            first.tab.reorder_listings([second.pk, first.pk])
            first.save()
        assert _queued(callbacks) == []

    def it_queues_nothing_for_an_unlisted_card_made(django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks() as callbacks:
            LeadershipListingFactory(is_listed=False)
        assert _queued(callbacks) == []

    def it_queues_the_listed_members_when_their_tab_is_deleted(django_capture_on_commit_callbacks):
        listed = _settled(django_capture_on_commit_callbacks, LeadershipListingFactory)
        hidden = LeadershipListingFactory(tab=listed.tab, is_listed=False)
        with django_capture_on_commit_callbacks() as callbacks:
            listed.tab.delete()
        assert _queued(callbacks) == [listed.member_id]
        assert not LeadershipListing.objects.filter(pk=hidden.pk).exists()

    def it_queues_nothing_for_a_card_on_the_guild_leads_tab(django_capture_on_commit_callbacks):
        tab = LeadershipTab.objects.get(kind=LeadershipTab.Kind.GUILD_LEADS)
        member = MemberFactory()
        with django_capture_on_commit_callbacks() as callbacks:
            tab.list_member(member, "Lead", "")
            tab.delete()
        assert _queued(callbacks) == []

    def it_queues_nothing_when_an_already_listed_member_gets_another_line(django_capture_on_commit_callbacks):
        listing = LeadershipListingFactory()
        with django_capture_on_commit_callbacks() as callbacks:
            listing.tab.list_member(listing.member, "Second line", "")
        assert _queued(callbacks) == []

    def it_queues_nothing_when_an_unlisted_card_is_taken_off_again(django_capture_on_commit_callbacks):
        listing = LeadershipListingFactory(is_listed=False)
        with django_capture_on_commit_callbacks() as callbacks:
            listing.remove_from_tab()
        assert _queued(callbacks) == []

    def it_queues_each_member_the_roster_import_lists(django_capture_on_commit_callbacks):
        from membership.leadership_roster import seed_roster

        member = MemberFactory(full_legal_name="Roster Person")
        roster = {
            "team": [{"name": "Roster Person", "discord": "", "roles": [{"title": "Chair", "email": ""}]}],
            "guild_channels": [],
        }
        with django_capture_on_commit_callbacks() as callbacks:
            seed_roster(roster)
        assert _queued(callbacks) == [member.pk]

    def it_queues_a_listed_stray_card_settled_onto_a_people_tab(django_capture_on_commit_callbacks):
        LeadershipTabFactory()
        stray = LeadershipListing.objects.create(tab=None, member=MemberFactory(), is_listed=True)
        hidden_stray = LeadershipListing.objects.create(tab=None, member=MemberFactory(), is_listed=False)
        with django_capture_on_commit_callbacks() as callbacks:
            assert LeadershipListing.objects.adopt_untabbed() == 2
        assert _queued(callbacks) == [stray.member_id]
        hidden_stray.refresh_from_db()
        assert hidden_stray.tab is not None

    def it_queues_nothing_when_a_tab_is_renamed(django_capture_on_commit_callbacks):
        listed = LeadershipListingFactory()
        tab = listed.tab
        with django_capture_on_commit_callbacks() as callbacks:
            tab.title = "Council"
            tab.save()
            tab.save(update_fields=["intro"])
        assert _queued(callbacks) == []

    def it_queues_nothing_for_a_new_tab(django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks() as callbacks:
            LeadershipTab.objects.add_people_tab("Board", "")
        assert _queued(callbacks) == []


def describe_discord_link():
    def it_queues_a_member_who_links_discord(django_capture_on_commit_callbacks):
        member = MemberFactory()
        with django_capture_on_commit_callbacks() as callbacks:
            member.link_discord("4242")
        assert _queued(callbacks) == [member.pk]

    def it_queues_no_reconcile_on_unlink(django_capture_on_commit_callbacks):
        member = MemberFactory(discord_user_id="4242")
        with django_capture_on_commit_callbacks() as callbacks:
            member.unlink_discord()
        assert _queued(callbacks) == []

    @respx.mock
    def it_leaves_a_sitting_leads_roles_alone_on_unlink(discord_on, django_capture_on_commit_callbacks):
        member = MemberFactory(discord_user_id="4242")
        GuildFactory(guild_lead=member)
        route = respx.route().mock(return_value=httpx.Response(204))
        with django_capture_on_commit_callbacks(execute=True):
            member.unlink_discord()
        assert not route.called

    @respx.mock
    def it_strips_the_old_account_then_reconciles_the_new_one_on_a_swap(discord_on, django_capture_on_commit_callbacks):
        member = MemberFactory(discord_user_id="4242")
        respx.get(f"{API}/guilds/srv/members/4242").mock(
            return_value=httpx.Response(200, json={"roles": ["111", "222", "999"]})
        )
        _settled(django_capture_on_commit_callbacks, lambda: LeadershipListingFactory(member=member))
        respx.get(f"{API}/guilds/srv/members/5353").mock(return_value=httpx.Response(200, json={"roles": []}))
        delete = respx.delete(url__regex=r".+").mock(return_value=httpx.Response(204))
        put = respx.put(url__regex=r".+").mock(return_value=httpx.Response(204))
        with django_capture_on_commit_callbacks(execute=True):
            member.link_discord("5353")
            assert not delete.called
        assert sorted(call.request.url.path for call in delete.calls) == [
            "/api/v10/guilds/srv/members/4242/roles/111",
            "/api/v10/guilds/srv/members/4242/roles/222",
        ]
        assert sorted(call.request.url.path for call in put.calls) == [
            "/api/v10/guilds/srv/members/5353/roles/111",
            "/api/v10/guilds/srv/members/5353/roles/222",
        ]

    @respx.mock
    def it_strips_nothing_when_relinking_the_same_account(discord_on, django_capture_on_commit_callbacks):
        member = MemberFactory(discord_user_id="4242")
        respx.get(f"{API}/guilds/srv/members/4242").mock(return_value=httpx.Response(200, json={"roles": ["111"]}))
        delete = respx.delete(url__regex=r".+").mock(return_value=httpx.Response(204))
        with django_capture_on_commit_callbacks(execute=True):
            member.link_discord("4242")
        # The reconcile alone runs: they earn nothing, so leadership comes off once.
        assert delete.call_count == 1


def describe_account_deletion():
    @respx.mock
    def it_takes_the_managed_roles_off_the_deleted_members_discord(discord_on, django_capture_on_commit_callbacks):
        from membership.services.account_deletion import delete_own_account
        from membership.services.provisioning import provision_user_for_member

        member = MemberFactory(discord_user_id="5151")
        provision_user_for_member(member)
        member.refresh_from_db()
        respx.get(f"{API}/guilds/srv/members/5151").mock(
            return_value=httpx.Response(200, json={"roles": ["111", "222", "999"]})
        )
        delete = respx.delete(url__regex=r".+").mock(return_value=httpx.Response(204))
        with django_capture_on_commit_callbacks(execute=True):
            delete_own_account(member)
        member.refresh_from_db()
        assert member.discord_user_id == ""
        assert sorted(call.request.url.path.rsplit("/", 1)[1] for call in delete.calls) == ["111", "222"]


def describe_end_to_end():
    @respx.mock
    def it_adds_the_roles_after_the_commit_and_survives_a_failing_discord(settings, django_capture_on_commit_callbacks):
        settings.DISCORD_BOT_TOKEN = "bot-tok"
        config = SiteConfiguration.load()
        config.discord_server_id = "srv"
        config.discord_leadership_role_id = "111"
        config.discord_guild_officers_role_id = "222"
        config.save()
        config.discord_guild_leads_role_id = "333"
        config.save()
        member = MemberFactory(discord_user_id="u1")
        guild = GuildFactory(discord_lead_role_id="444")  # an old per guild lead role, never given (#1270)
        respx.get("https://discord.com/api/v10/guilds/srv/members/u1").mock(
            return_value=httpx.Response(200, json={"roles": []})
        )
        put = respx.put(url__regex=r".+/roles/\d+").mock(return_value=httpx.Response(500))

        with django_capture_on_commit_callbacks(execute=True):
            guild.assign_lead(member)

        guild.refresh_from_db()
        assert guild.guild_lead_id == member.pk
        assert sorted(call.request.url.path.rsplit("/", 1)[1] for call in put.calls) == ["111", "222", "333"]

    @respx.mock
    def it_sends_nothing_for_an_unlinked_member(settings, django_capture_on_commit_callbacks):
        settings.DISCORD_BOT_TOKEN = "bot-tok"
        config = SiteConfiguration.load()
        config.discord_server_id = "srv"
        config.discord_leadership_role_id = "111"
        config.save()
        route = respx.route().mock(return_value=httpx.Response(204))
        with django_capture_on_commit_callbacks(execute=True):
            GuildFactory().assign_lead(MemberFactory())
        assert not route.called
