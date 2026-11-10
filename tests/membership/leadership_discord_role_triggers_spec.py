"""Every change that moves who leads what enqueues a Discord leadership role reconcile after commit (#1148).

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

from core.events.discord_leadership_roles import _ReconcileWhenCommitted
from core.models import SiteConfiguration
from hub.forms import GuildRoleForm, GuildVisibilityForm
from membership.models import GuildStaffMembership, LeadershipListing, LeadershipTab
from tests.membership.factories import (
    GuildFactory,
    GuildStaffMembershipFactory,
    LeadershipListingFactory,
    LeadershipTabFactory,
    MemberFactory,
)

pytestmark = pytest.mark.django_db

T = TypeVar("T")


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

    def it_queues_the_lead_and_co_leads_when_the_lead_role_id_changes(django_capture_on_commit_callbacks):
        lead = MemberFactory()
        guild = GuildFactory(guild_lead=lead)
        co_lead = GuildStaffMembershipFactory(guild=guild, role=GuildStaffMembership.Role.CO_LEAD)
        form = GuildRoleForm({"discord_role_ids": "", "discord_lead_role_id": "4321"}, instance=guild)
        assert form.is_valid()
        with django_capture_on_commit_callbacks() as callbacks:
            form.save()
        guild.refresh_from_db()
        assert guild.discord_lead_role_id == "4321"
        assert _queued(callbacks) == sorted([lead.pk, co_lead.member_id])

    @pytest.mark.parametrize("new_id", ["5555", ""])
    @respx.mock
    def it_takes_the_replaced_lead_role_off_its_holders_after_commit(
        new_id, settings, django_capture_on_commit_callbacks
    ):
        settings.DISCORD_BOT_TOKEN = "bot-tok"
        config = SiteConfiguration.load()
        config.discord_server_id = "srv"
        config.save()
        guild = GuildFactory(discord_lead_role_id="4321")
        respx.get("https://discord.com/api/v10/guilds/srv/members").mock(
            return_value=httpx.Response(200, json=[{"user": {"id": "u9"}, "roles": ["4321", "1"]}])
        )
        delete = respx.delete(url__regex=r".+").mock(return_value=httpx.Response(204))
        form = GuildRoleForm({"discord_role_ids": "", "discord_lead_role_id": new_id}, instance=guild)
        assert form.is_valid()
        with django_capture_on_commit_callbacks(execute=True):
            form.save()
            assert not delete.called
        assert [call.request.url.path for call in delete.calls] == ["/api/v10/guilds/srv/members/u9/roles/4321"]

    @respx.mock
    def it_retires_nothing_when_a_lead_role_id_is_first_set(settings, django_capture_on_commit_callbacks):
        settings.DISCORD_BOT_TOKEN = "bot-tok"
        config = SiteConfiguration.load()
        config.discord_server_id = "srv"
        config.save()
        guild = GuildFactory()
        route = respx.route().mock(return_value=httpx.Response(200, json=[]))
        form = GuildRoleForm({"discord_role_ids": "", "discord_lead_role_id": "4321"}, instance=guild)
        assert form.is_valid()
        with django_capture_on_commit_callbacks(execute=True):
            form.save()
        assert not route.called

    def it_queues_nothing_when_the_lead_role_id_is_unchanged(django_capture_on_commit_callbacks):
        guild = GuildFactory(guild_lead=MemberFactory(), discord_lead_role_id="4321")
        form = GuildRoleForm({"discord_role_ids": "77", "discord_lead_role_id": "4321"}, instance=guild)
        assert form.is_valid()
        with django_capture_on_commit_callbacks() as callbacks:
            form.save()
        assert _queued(callbacks) == []

    def it_queues_nothing_until_a_form_commits(django_capture_on_commit_callbacks):
        guild = GuildFactory(guild_lead=MemberFactory())
        role_form = GuildRoleForm({"discord_role_ids": "", "discord_lead_role_id": "4321"}, instance=guild)
        visibility_form = GuildVisibilityForm({"is_active": "", "is_new_guild": ""}, instance=guild)
        assert role_form.is_valid()
        assert visibility_form.is_valid()
        with django_capture_on_commit_callbacks() as callbacks:
            role_form.save(commit=False)
            visibility_form.save(commit=False)
        assert _queued(callbacks) == []

    def it_refuses_a_lead_role_id_that_is_not_a_number():
        form = GuildRoleForm({"discord_role_ids": "", "discord_lead_role_id": "Wood Lead"}, instance=GuildFactory())
        assert not form.is_valid()
        assert "digits only" in form.errors["discord_lead_role_id"][0]

    def it_queues_the_lead_when_the_guild_is_deleted(django_capture_on_commit_callbacks):
        lead = MemberFactory()
        guild = GuildFactory(guild_lead=lead)
        with django_capture_on_commit_callbacks() as callbacks:
            guild.soft_delete()
        assert _queued(callbacks) == [lead.pk]


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

    def it_queues_the_listed_members_when_a_tab_changes_kind(django_capture_on_commit_callbacks):
        listed = _settled(django_capture_on_commit_callbacks, LeadershipListingFactory)
        LeadershipTab.objects.filter(kind=LeadershipTab.Kind.GUILD_LEADS).delete()
        tab = listed.tab
        with django_capture_on_commit_callbacks() as callbacks:
            tab.kind = LeadershipTab.Kind.GUILD_LEADS
            tab.save()
        assert _queued(callbacks) == [listed.member_id]

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

    def it_queues_nothing_on_unlink(django_capture_on_commit_callbacks):
        member = MemberFactory(discord_user_id="4242")
        with django_capture_on_commit_callbacks() as callbacks:
            member.unlink_discord()
        assert _queued(callbacks) == []


def describe_end_to_end():
    @respx.mock
    def it_adds_the_roles_after_the_commit_and_survives_a_failing_discord(settings, django_capture_on_commit_callbacks):
        settings.DISCORD_BOT_TOKEN = "bot-tok"
        config = SiteConfiguration.load()
        config.discord_server_id = "srv"
        config.discord_leadership_role_id = "111"
        config.discord_guild_officers_role_id = "222"
        config.save()
        member = MemberFactory(discord_user_id="u1")
        guild = GuildFactory(discord_lead_role_id="333")
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
