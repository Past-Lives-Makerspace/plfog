"""Every change the Discord leadership directory post shows queues one sync after commit (#1149).

Each spec fires its setup's own commit callbacks first, then captures what its action
queues and counts the :class:`~core.events.discord_leadership_directory._SyncWhenCommitted`
callbacks. Nothing runs unless a spec asks with ``execute=True``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypeVar

import httpx
import pytest
import respx
from django.db import transaction

from core.events.discord_leadership_directory import _SyncWhenCommitted
from core.models import SiteConfiguration
from hub.forms import GuildVisibilityForm
from membership.models import Guild, GuildStaffMembership, LeadershipPage, LeadershipTab
from tests.membership.factories import (
    GuildFactory,
    GuildStaffMembershipFactory,
    LeadershipListingFactory,
    LeadershipRoleFactory,
    LeadershipTabFactory,
    MemberFactory,
)

pytestmark = pytest.mark.django_db

T = TypeVar("T")


def _settled(capture: Callable[..., Any], build: Callable[[], T]) -> T:
    """Build the setup and fire its own callbacks, so a spec counts only what its action queues."""
    with capture(execute=True):
        return build()


def _syncs(callbacks: list) -> int:
    return len([cb for cb in callbacks if isinstance(cb, _SyncWhenCommitted)])


def _people() -> LeadershipTab:
    return LeadershipTab.objects.get(kind=LeadershipTab.Kind.PEOPLE, title="Leadership")


def describe_leadership_directory_edits():
    def it_syncs_on_a_page_title_or_lead_edit(django_capture_on_commit_callbacks):
        page = _settled(django_capture_on_commit_callbacks, LeadershipPage.load)
        with django_capture_on_commit_callbacks() as callbacks:
            page.hero_title = "Our Leaders"
            page.save()
        assert _syncs(callbacks) == 1

    def it_syncs_on_a_tab_added_renamed_reordered_or_deleted(django_capture_on_commit_callbacks):
        for action in (
            lambda: LeadershipTab.objects.add_people_tab("Council", ""),
            lambda: LeadershipTab.objects.filter(title="Leadership").first().save(),
            lambda: LeadershipTab.objects.reorder(list(reversed(LeadershipTab.objects.values_list("pk", flat=True)))),
        ):
            with django_capture_on_commit_callbacks(execute=True) as callbacks:
                action()
            assert _syncs(callbacks) == 1
        tab = _settled(django_capture_on_commit_callbacks, lambda: LeadershipTabFactory(title="Gone"))
        with django_capture_on_commit_callbacks() as callbacks:
            tab.delete()
        assert _syncs(callbacks) == 1

    def it_syncs_on_a_person_listed_unlisted_or_reordered(django_capture_on_commit_callbacks):
        tab, member = _people(), _settled(django_capture_on_commit_callbacks, MemberFactory)
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            listing = tab.list_member(member, "Treasurer", "")
        assert _syncs(callbacks) == 1
        other = _settled(django_capture_on_commit_callbacks, lambda: tab.list_member(MemberFactory(), "Chair", ""))
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            tab.reorder_listings([other.pk, listing.pk])
        assert _syncs(callbacks) == 1
        with django_capture_on_commit_callbacks() as callbacks:
            listing.remove_from_tab()
        assert _syncs(callbacks) == 1

    def it_syncs_on_a_role_line_added_edited_or_removed(django_capture_on_commit_callbacks):
        listing = _settled(django_capture_on_commit_callbacks, lambda: LeadershipListingFactory(tab=_people()))
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            role = listing.add_role("Secretary", "")
        assert _syncs(callbacks) == 1
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            role.email = "sec@example.com"
            role.save()
        assert _syncs(callbacks) == 1
        with django_capture_on_commit_callbacks() as callbacks:
            role.delete()
        assert _syncs(callbacks) == 1

    def it_queues_one_sync_for_several_changes_in_one_transaction(django_capture_on_commit_callbacks):
        listing = _settled(django_capture_on_commit_callbacks, lambda: LeadershipListingFactory(tab=_people()))
        with django_capture_on_commit_callbacks() as callbacks, transaction.atomic():
            listing.add_role("One", "")
            listing.add_role("Two", "")
            tab = _people()
            tab.intro = "Hello"
            tab.save()
        assert _syncs(callbacks) == 1

    def it_queues_nothing_when_the_change_rolls_back(django_capture_on_commit_callbacks):
        listing = _settled(django_capture_on_commit_callbacks, lambda: LeadershipListingFactory(tab=_people()))
        with django_capture_on_commit_callbacks() as callbacks:
            with pytest.raises(RuntimeError), transaction.atomic():
                listing.add_role("Gone", "")
                raise RuntimeError
        assert _syncs(callbacks) == 0


def describe_guild_changes():
    def it_syncs_on_a_lead_assigned_or_cleared(django_capture_on_commit_callbacks):
        guild, member = _settled(django_capture_on_commit_callbacks, lambda: (GuildFactory(), MemberFactory()))
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            guild.assign_lead(member)
        assert _syncs(callbacks) == 1
        with django_capture_on_commit_callbacks() as callbacks:
            guild.clear_lead()
        assert _syncs(callbacks) == 1

    def it_syncs_on_a_co_lead_added_retitled_or_removed(django_capture_on_commit_callbacks):
        guild, member = _settled(django_capture_on_commit_callbacks, lambda: (GuildFactory(), MemberFactory()))
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            staff = guild.add_staff(member, role=GuildStaffMembership.Role.CO_LEAD)
        assert _syncs(callbacks) == 1
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            staff.retitle(GuildStaffMembership.Role.TREASURER, "")
        assert _syncs(callbacks) == 1
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            staff.retitle(GuildStaffMembership.Role.CO_LEAD, "")
        assert _syncs(callbacks) == 1
        with django_capture_on_commit_callbacks() as callbacks:
            guild.remove_staff(staff)
        assert _syncs(callbacks) == 1

    def it_does_not_sync_for_a_staff_role_the_post_does_not_show(django_capture_on_commit_callbacks):
        guild, member = _settled(django_capture_on_commit_callbacks, lambda: (GuildFactory(), MemberFactory()))
        with django_capture_on_commit_callbacks() as callbacks:
            guild.add_staff(member, role=GuildStaffMembership.Role.TREASURER)
        assert _syncs(callbacks) == 0

    def it_syncs_when_a_guild_is_hidden_shown_or_deleted(django_capture_on_commit_callbacks):
        guild = _settled(django_capture_on_commit_callbacks, GuildFactory)
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            form = GuildVisibilityForm({"is_active": "", "is_new_guild": ""}, instance=guild)
            assert form.is_valid()
            form.save()
        assert _syncs(callbacks) == 1
        with django_capture_on_commit_callbacks() as callbacks:
            Guild.objects.get(pk=guild.pk).soft_delete()
        assert _syncs(callbacks) == 1

    def it_syncs_when_an_active_guild_changes_its_contact_email(django_capture_on_commit_callbacks):
        guild = _settled(django_capture_on_commit_callbacks, lambda: Guild.objects.get(pk=GuildFactory().pk))
        with django_capture_on_commit_callbacks() as callbacks:
            guild.contact_email = "guild@example.com"
            guild.save()
        assert _syncs(callbacks) == 1

    def it_does_not_sync_for_a_hidden_guild_or_a_guild_loaded_without_its_fields(django_capture_on_commit_callbacks):
        hidden = _settled(
            django_capture_on_commit_callbacks, lambda: Guild.objects.get(pk=GuildFactory(is_active=False).pk)
        )
        partial = Guild.objects.only("pk", "is_active").get(pk=GuildFactory().pk)
        with django_capture_on_commit_callbacks() as callbacks:
            hidden.name = "Still Hidden"
            hidden.save()
            partial.is_new_guild = True
            partial.save(update_fields=["is_new_guild"])
        assert _syncs(callbacks) == 0

    def it_syncs_when_an_active_guild_is_renamed_and_not_on_other_saves(django_capture_on_commit_callbacks):
        guild = _settled(django_capture_on_commit_callbacks, lambda: Guild.objects.get(pk=GuildFactory().pk))
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            guild.about = "New words"
            guild.save()
        assert _syncs(callbacks) == 0
        with django_capture_on_commit_callbacks() as callbacks:
            guild.name = "Renamed Guild"
            guild.save()
        assert _syncs(callbacks) == 1


def describe_member_changes():
    def it_syncs_when_a_listed_person_changes_their_name(django_capture_on_commit_callbacks):
        listing = _settled(django_capture_on_commit_callbacks, lambda: LeadershipListingFactory(tab=_people()))
        member = type(listing.member).objects.get(pk=listing.member_id)
        with django_capture_on_commit_callbacks() as callbacks:
            member.preferred_name = "New Name"
            member.save()
        assert _syncs(callbacks) == 1

    def it_syncs_when_a_listed_person_changes_their_handle_or_its_privacy(django_capture_on_commit_callbacks):
        listing = _settled(django_capture_on_commit_callbacks, lambda: LeadershipListingFactory(tab=_people()))
        member = type(listing.member).objects.get(pk=listing.member_id)
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            member.discord_handle = "new_handle"
            member.save()
        assert _syncs(callbacks) == 1
        with django_capture_on_commit_callbacks() as callbacks:
            member.directory_visibility["discord_handle"] = False
            member.save(update_fields=["directory_visibility"])
        assert _syncs(callbacks) == 1

    def it_syncs_when_a_lead_or_co_lead_links_or_unlinks_discord(django_capture_on_commit_callbacks):
        lead = _settled(django_capture_on_commit_callbacks, lambda: GuildFactory(guild_lead=MemberFactory()).guild_lead)
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            lead.link_discord("123456789")
        assert _syncs(callbacks) == 1
        co = _settled(django_capture_on_commit_callbacks, lambda: GuildStaffMembershipFactory().member)
        co.discord_user_id = "55"
        co._loaded_directory_identity = co._directory_identity()
        with django_capture_on_commit_callbacks() as callbacks:
            co.unlink_discord()
        assert _syncs(callbacks) == 1

    def it_does_not_sync_for_someone_not_on_the_directory(django_capture_on_commit_callbacks):
        unlisted = _settled(
            django_capture_on_commit_callbacks, lambda: LeadershipListingFactory(tab=_people(), is_listed=False)
        )
        member = type(unlisted.member).objects.get(pk=unlisted.member_id)
        with django_capture_on_commit_callbacks() as callbacks:
            member.preferred_name = "Quiet"
            member.save()
            member.link_discord("42")
        assert _syncs(callbacks) == 0

    def it_does_not_sync_for_a_save_that_leaves_the_name_and_discord_alone(django_capture_on_commit_callbacks):
        listing = _settled(django_capture_on_commit_callbacks, lambda: LeadershipListingFactory(tab=_people()))
        member = type(listing.member).objects.get(pk=listing.member_id)
        with django_capture_on_commit_callbacks() as callbacks:
            member.pronouns = "they/them"
            member.save()
            member.preferred_name = "Not Written"
            member.save(update_fields=["pronouns"])
        assert _syncs(callbacks) == 0


def describe_end_to_end():
    @respx.mock
    def it_edits_the_post_once_after_a_burst_of_saves(settings, django_capture_on_commit_callbacks):
        settings.DISCORD_BOT_TOKEN = "bot-tok"
        config = SiteConfiguration.load()
        config.discord_leadership_directory_channel_id = "968"
        config.discord_leadership_directory_message_ids = ["501"]
        config.save()
        edit = respx.patch("https://discord.com/api/v10/channels/968/messages/501").mock(
            return_value=httpx.Response(200, json={})
        )
        with django_capture_on_commit_callbacks(execute=True):
            listing = LeadershipListingFactory(tab=_people(), member=MemberFactory(preferred_name="Jo"))
            LeadershipRoleFactory(listing=listing, title="Treasurer")
            LeadershipRoleFactory(listing=listing, title="Lead Engineer")
        assert edit.call_count == 1
        embeds = json.loads(edit.calls[0].request.content)["embeds"]
        assert any("**Jo**\nTreasurer · Lead Engineer" in embed.get("description", "") for embed in embeds)
