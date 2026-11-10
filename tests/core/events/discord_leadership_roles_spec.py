"""Discord leadership roles follow the app (#1148): the mapping, the reconcile, the task and its queueing.

Discord HTTP is mocked with ``respx``. The immediate task backend runs a task when the commit
callbacks fire, which a spec triggers with ``django_capture_on_commit_callbacks(execute=True)``.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from django.db import transaction

from core.events import discord_roles
from core.events.discord_leadership_roles import (
    NO_FACTS,
    LeadershipFacts,
    LeadershipRoleSettings,
    _ReconcileWhenCommitted,
    desired_role_ids,
    leadership_facts,
    plan_changes,
    reconcile_leadership_roles,
    reconcile_member,
    reconcile_on_commit,
    remove_retired_role,
)
from core.models import SiteConfiguration
from membership.models import GuildStaffMembership, LeadershipTab, Member
from tests.membership.factories import (
    GuildFactory,
    GuildStaffMembershipFactory,
    LeadershipListingFactory,
    MemberFactory,
)

pytestmark = pytest.mark.django_db

API = "https://discord.com/api/v10"
LEADERSHIP = "111"
OFFICERS = "222"
WOOD_LEAD = "333"
MEMBER_ROLE = "999"


@pytest.fixture(autouse=True)
def _configured(settings):
    settings.DISCORD_BOT_TOKEN = "bot-tok"
    config = SiteConfiguration.load()
    config.discord_server_id = "srv"
    config.discord_leadership_role_id = LEADERSHIP
    config.discord_guild_officers_role_id = OFFICERS
    config.save()


def _settings(**overrides: object) -> LeadershipRoleSettings:
    values: dict[str, object] = {
        "leadership_role_id": LEADERSHIP,
        "guild_officers_role_id": OFFICERS,
        "lead_role_ids": {1: WOOD_LEAD},
        "guild_names": {1: "Woodworkers"},
    }
    values.update(overrides)
    return LeadershipRoleSettings(**values)  # type: ignore[arg-type]


def _member_get(user_id: str, roles: list[str]) -> respx.Route:
    return respx.get(f"{API}/guilds/srv/members/{user_id}").mock(
        return_value=httpx.Response(200, json={"roles": roles, "user": {"id": user_id}})
    )


def describe_member_role_ids():
    @respx.mock
    def it_reads_the_members_roles():
        _member_get("u1", ["1", "2"])
        assert discord_roles.member_role_ids("srv", "u1") == {"1", "2"}

    def it_reads_nothing_with_a_blank_token(settings):
        settings.DISCORD_BOT_TOKEN = ""
        assert discord_roles.member_role_ids("srv", "u1") is None

    def it_reads_nothing_with_a_blank_id():
        assert discord_roles.member_role_ids("srv", "") is None

    @respx.mock
    def it_answers_none_when_they_are_not_on_the_server():
        respx.get(f"{API}/guilds/srv/members/u1").mock(return_value=httpx.Response(404, text="Unknown Member"))
        assert discord_roles.member_role_ids("srv", "u1") is None

    @respx.mock
    def it_answers_none_on_a_network_error():
        respx.get(f"{API}/guilds/srv/members/u1").mock(side_effect=httpx.ConnectError("down"))
        assert discord_roles.member_role_ids("srv", "u1") is None

    @respx.mock
    def it_answers_none_on_a_body_that_is_not_json():
        respx.get(f"{API}/guilds/srv/members/u1").mock(return_value=httpx.Response(200, text="<html>"))
        assert discord_roles.member_role_ids("srv", "u1") is None

    @respx.mock
    def it_answers_none_on_a_body_without_roles():
        respx.get(f"{API}/guilds/srv/members/u1").mock(return_value=httpx.Response(200, json={"user": {}}))
        assert discord_roles.member_role_ids("srv", "u1") is None


def describe_server_member_role_ids():
    @respx.mock
    def it_pages_through_the_member_list():
        first = [{"user": {"id": str(n)}, "roles": ["7"]} for n in range(1, 1001)]
        second = [{"user": {"id": "2000"}, "roles": []}]
        route = respx.get(f"{API}/guilds/srv/members").mock(
            side_effect=[httpx.Response(200, json=first), httpx.Response(200, json=second)]
        )
        found = discord_roles.server_member_role_ids("srv")
        assert found is not None
        assert len(found) == 1001
        assert found["1"] == {"7"}
        assert found["2000"] == set()
        assert route.calls[1].request.url.params["after"] == "1000"

    def it_reads_nothing_with_a_blank_token(settings):
        settings.DISCORD_BOT_TOKEN = ""
        assert discord_roles.server_member_role_ids("srv") is None

    @respx.mock
    def it_answers_none_when_a_page_fails():
        respx.get(f"{API}/guilds/srv/members").mock(return_value=httpx.Response(403, text="intent off"))
        assert discord_roles.server_member_role_ids("srv") is None


def describe_desired_role_ids():
    def it_gives_a_lead_their_guilds_lead_role_leadership_and_guild_officers():
        facts = LeadershipFacts(led_guild_ids=frozenset({1}))
        assert desired_role_ids(facts, _settings()) == {WOOD_LEAD, LEADERSHIP, OFFICERS}

    def it_gives_other_staff_guild_officers_only():
        assert desired_role_ids(LeadershipFacts(other_staff=True), _settings()) == {OFFICERS}

    def it_gives_a_listed_member_leadership_and_guild_officers():
        assert desired_role_ids(LeadershipFacts(listed=True), _settings()) == {LEADERSHIP, OFFICERS}

    def it_gives_nothing_to_someone_with_no_facts():
        assert desired_role_ids(NO_FACTS, _settings()) == frozenset()

    def it_skips_a_guild_with_no_lead_role_and_blank_settings():
        facts = LeadershipFacts(led_guild_ids=frozenset({2}), listed=True)
        assert desired_role_ids(facts, _settings(leadership_role_id="", guild_officers_role_id="")) == frozenset()

    def it_gives_the_union_for_someone_in_several_rows():
        facts = LeadershipFacts(led_guild_ids=frozenset({1}), other_staff=True, listed=True)
        assert desired_role_ids(facts, _settings()) == {WOOD_LEAD, LEADERSHIP, OFFICERS}


def describe_leadership_role_settings():
    def it_manages_every_set_id_including_hidden_and_deleted_guilds():
        GuildFactory(name="Wood", discord_lead_role_id=WOOD_LEAD)
        GuildFactory(name="Hidden", is_active=False, discord_lead_role_id="444")
        gone = GuildFactory(name="Gone", discord_lead_role_id="555")
        gone.soft_delete()
        GuildFactory(name="No role")
        settings = LeadershipRoleSettings.load()
        assert settings.managed == {LEADERSHIP, OFFICERS, WOOD_LEAD, "444", "555"}

    def it_names_each_managed_role():
        settings = _settings()
        assert settings.role_name(LEADERSHIP) == "leadership"
        assert settings.role_name(OFFICERS) == "Guild Officers"
        assert settings.role_name(WOOD_LEAD) == "Woodworkers Lead"
        assert settings.role_name("123") == "123"


def describe_leadership_facts():
    def it_reads_a_lead():
        member = MemberFactory()
        guild = GuildFactory(guild_lead=member)
        assert leadership_facts([member.pk])[member.pk] == LeadershipFacts(led_guild_ids=frozenset({guild.pk}))

    def it_reads_a_co_lead_as_leading():
        staff = GuildStaffMembershipFactory(role=GuildStaffMembership.Role.CO_LEAD)
        facts = leadership_facts([staff.member_id])[staff.member_id]
        assert facts.led_guild_ids == {staff.guild_id}
        assert facts.other_staff is False

    @pytest.mark.parametrize(
        "role",
        [GuildStaffMembership.Role.SECRETARY, GuildStaffMembership.Role.TREASURER, GuildStaffMembership.Role.ORIENTER],
    )
    def it_reads_other_preset_staff_as_officers(role):
        staff = GuildStaffMembershipFactory(role=role)
        assert leadership_facts([staff.member_id])[staff.member_id] == LeadershipFacts(other_staff=True)

    def it_reads_a_custom_title_as_an_officer():
        staff = GuildStaffMembershipFactory(custom=True)
        assert leadership_facts([staff.member_id])[staff.member_id] == LeadershipFacts(other_staff=True)

    def it_reads_a_listing_on_a_people_tab():
        listing = LeadershipListingFactory()
        assert leadership_facts([listing.member_id])[listing.member_id] == LeadershipFacts(listed=True)

    def it_ignores_an_unlisted_card_and_a_card_on_the_guild_leads_tab():
        unlisted = LeadershipListingFactory(is_listed=False)
        guild_leads_tab = LeadershipTab.objects.get(kind=LeadershipTab.Kind.GUILD_LEADS)
        on_guild_leads = LeadershipListingFactory(tab=guild_leads_tab)
        assert leadership_facts([unlisted.member_id, on_guild_leads.member_id]) == {}

    def it_ignores_hidden_and_deleted_guilds():
        lead = MemberFactory()
        GuildFactory(guild_lead=lead, is_active=False)
        deleted = GuildFactory(guild_lead=lead)
        deleted.soft_delete()
        staff = GuildStaffMembershipFactory(guild=GuildFactory(is_active=False), role="secretary")
        assert leadership_facts([lead.pk, staff.member_id]) == {}

    def it_gives_nothing_for_a_fog_role():
        admin = MemberFactory(fog_role=Member.FogRole.ADMIN)
        officer = MemberFactory(fog_role=Member.FogRole.GUILD_OFFICER)
        assert leadership_facts([admin.pk, officer.pk]) == {}

    def it_reads_everyone_when_no_ids_are_given():
        lead = MemberFactory()
        GuildFactory(guild_lead=lead)
        listing = LeadershipListingFactory()
        assert set(leadership_facts()) == {lead.pk, listing.member_id}


def describe_plan_changes():
    def it_adds_what_is_missing_and_removes_managed_roles_not_earned():
        to_add, to_remove = plan_changes(LeadershipFacts(other_staff=True), {LEADERSHIP, MEMBER_ROLE}, _settings())
        assert to_add == [OFFICERS]
        assert to_remove == [LEADERSHIP]


def describe_reconcile_member():
    @respx.mock
    def it_adds_desired_roles_removes_unearned_managed_roles_and_leaves_others_alone():
        guild = GuildFactory(discord_lead_role_id=WOOD_LEAD)
        member = MemberFactory(discord_user_id="u1")
        GuildStaffMembershipFactory(guild=guild, member=member, role=GuildStaffMembership.Role.TREASURER)
        GuildFactory(name="Old", discord_lead_role_id="444")
        _member_get("u1", ["444", LEADERSHIP, MEMBER_ROLE])
        put = respx.put(url__regex=rf"{API}/guilds/srv/members/u1/roles/\d+").mock(return_value=httpx.Response(204))
        delete = respx.delete(url__regex=rf"{API}/guilds/srv/members/u1/roles/\d+").mock(
            return_value=httpx.Response(204)
        )

        changes = reconcile_member(member)

        assert changes.added == [OFFICERS]
        assert set(changes.removed) == {"444", LEADERSHIP}
        assert [call.request.url.path.rsplit("/", 1)[1] for call in put.calls] == [OFFICERS]
        removed = {call.request.url.path.rsplit("/", 1)[1] for call in delete.calls}
        assert removed == {"444", LEADERSHIP}
        assert MEMBER_ROLE not in removed

    @respx.mock
    def it_sends_nothing_when_the_roles_already_match():
        member = MemberFactory(discord_user_id="u1")
        LeadershipListingFactory(member=member)
        _member_get("u1", [LEADERSHIP, OFFICERS, MEMBER_ROLE])
        writes = respx.route(method__in=["PUT", "DELETE"]).mock(return_value=httpx.Response(204))
        assert reconcile_member(member).added == []
        assert not writes.called

    @respx.mock
    def it_keeps_roles_still_earned_through_another_row():
        member = MemberFactory(discord_user_id="u1")
        LeadershipListingFactory(member=member)
        _member_get("u1", [LEADERSHIP, OFFICERS, WOOD_LEAD])
        GuildFactory(discord_lead_role_id=WOOD_LEAD)  # no longer leads it
        delete = respx.delete(url__regex=r".+").mock(return_value=httpx.Response(204))
        changes = reconcile_member(member)
        assert changes.removed == [WOOD_LEAD]
        assert delete.call_count == 1

    @respx.mock
    def it_gives_a_fog_role_admin_nothing_and_takes_leadership_away():
        admin = MemberFactory(discord_user_id="u1", fog_role=Member.FogRole.ADMIN)
        _member_get("u1", [LEADERSHIP])
        put = respx.put(url__regex=r".+").mock(return_value=httpx.Response(204))
        respx.delete(url__regex=r".+").mock(return_value=httpx.Response(204))
        changes = reconcile_member(admin)
        assert changes.removed == [LEADERSHIP]
        assert not put.called

    @respx.mock
    def it_skips_a_member_without_linked_discord():
        member = MemberFactory(discord_user_id="")
        LeadershipListingFactory(member=member)
        route = respx.route().mock(return_value=httpx.Response(204))
        assert reconcile_member(member).added == []
        assert not route.called

    @respx.mock
    def it_sends_nothing_with_a_blank_server_id():
        config = SiteConfiguration.load()
        config.discord_server_id = ""
        config.save()
        member = MemberFactory(discord_user_id="u1")
        route = respx.route().mock(return_value=httpx.Response(204))
        reconcile_member(member)
        assert not route.called

    @respx.mock
    def it_sends_nothing_when_no_role_id_is_set():
        config = SiteConfiguration.load()
        config.discord_leadership_role_id = ""
        config.discord_guild_officers_role_id = ""
        config.save()
        member = MemberFactory(discord_user_id="u1")
        route = respx.route().mock(return_value=httpx.Response(204))
        reconcile_member(member)
        assert not route.called

    @respx.mock
    def it_writes_nothing_when_the_roles_cannot_be_read():
        member = MemberFactory(discord_user_id="u1")
        LeadershipListingFactory(member=member)
        respx.get(f"{API}/guilds/srv/members/u1").mock(return_value=httpx.Response(500))
        writes = respx.route(method__in=["PUT", "DELETE"]).mock(return_value=httpx.Response(204))
        reconcile_member(member)
        assert not writes.called

    @respx.mock
    def it_counts_only_what_discord_accepted_and_never_raises():
        member = MemberFactory(discord_user_id="u1")
        LeadershipListingFactory(member=member)
        _member_get("u1", [])
        respx.put(url__regex=r".+").mock(side_effect=[httpx.Response(429), httpx.ConnectError("down")])
        changes = reconcile_member(member)
        assert changes.added == []

    @respx.mock
    def it_does_not_count_a_removal_discord_refused():
        member = MemberFactory(discord_user_id="u1")
        _member_get("u1", [LEADERSHIP])
        respx.delete(url__regex=r".+").mock(return_value=httpx.Response(403))
        assert reconcile_member(member).removed == []


def describe_reconcile_leadership_roles_task():
    @respx.mock
    def it_reconciles_the_member_by_id():
        member = MemberFactory(discord_user_id="u1")
        LeadershipListingFactory(member=member)
        _member_get("u1", [])
        put = respx.put(url__regex=r".+").mock(return_value=httpx.Response(204))
        reconcile_leadership_roles.call(member.pk)
        assert put.call_count == 2

    @respx.mock
    def it_skips_a_member_that_is_gone():
        route = respx.route().mock(return_value=httpx.Response(204))
        reconcile_leadership_roles.call(987654)
        assert not route.called


def describe_reconcile_on_commit():
    def _queued(callbacks: list) -> list[int]:
        return [cb.member_id for cb in callbacks if isinstance(cb, _ReconcileWhenCommitted)]

    def it_queues_one_task_per_member_per_transaction(django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks() as callbacks:
            with transaction.atomic():
                reconcile_on_commit(1, None, 2)
                reconcile_on_commit(2, 1)
        assert _queued(callbacks) == [1, 2]

    def it_queues_again_after_the_earlier_callback_fired(django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            reconcile_on_commit(987654)
        with django_capture_on_commit_callbacks() as callbacks:
            reconcile_on_commit(987654)
        assert _queued(callbacks) == [987654]

    def it_queues_nothing_when_the_save_rolls_back(django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks() as callbacks:
            try:
                with transaction.atomic():
                    reconcile_on_commit(5)
                    raise RuntimeError("roll back")
            except RuntimeError:
                pass
        assert _queued(callbacks) == []

    @respx.mock
    def it_runs_the_task_when_the_transaction_commits(django_capture_on_commit_callbacks):
        member = MemberFactory(discord_user_id="u1")
        GuildFactory(guild_lead=member)
        get = _member_get("u1", [LEADERSHIP, OFFICERS])
        with django_capture_on_commit_callbacks(execute=True):
            reconcile_on_commit(member.pk)
        assert get.call_count == 1

    def it_names_its_callback_for_djangos_error_log():
        assert _ReconcileWhenCommitted(1).__qualname__ == "reconcile_leadership_roles_on_commit"


def describe_remove_retired_role():
    def _roster(*entries: tuple[str, list[str]]) -> respx.Route:
        return respx.get(f"{API}/guilds/srv/members").mock(
            return_value=httpx.Response(200, json=[{"user": {"id": uid}, "roles": roles} for uid, roles in entries])
        )

    @respx.mock
    def it_takes_the_old_role_off_every_holder_and_nothing_else():
        _roster(("u1", ["777", MEMBER_ROLE]), ("u2", [MEMBER_ROLE]), ("u3", ["777"]))
        delete = respx.delete(url__regex=r".+").mock(return_value=httpx.Response(204))
        remove_retired_role.call("777")
        assert sorted(call.request.url.path for call in delete.calls) == [
            "/api/v10/guilds/srv/members/u1/roles/777",
            "/api/v10/guilds/srv/members/u3/roles/777",
        ]

    @respx.mock
    def it_keeps_going_when_discord_refuses_one():
        _roster(("u1", ["777"]), ("u2", ["777"]))
        delete = respx.delete(url__regex=r".+").mock(side_effect=[httpx.Response(429), httpx.ConnectError("down")])
        remove_retired_role.call("777")
        assert delete.call_count == 2

    @respx.mock
    def it_skips_an_id_still_used_as_another_guilds_lead_role():
        GuildFactory(discord_lead_role_id="777")
        route = respx.route().mock(return_value=httpx.Response(204))
        remove_retired_role.call("777")
        assert not route.called

    @pytest.mark.parametrize("role_id", [LEADERSHIP, OFFICERS, "", "  "])
    @respx.mock
    def it_skips_a_blank_id_and_the_leadership_and_officers_ids(role_id):
        route = respx.route().mock(return_value=httpx.Response(204))
        remove_retired_role.call(role_id)
        assert not route.called

    @respx.mock
    def it_skips_when_the_server_id_is_blank():
        config = SiteConfiguration.load()
        config.discord_server_id = ""
        config.save()
        route = respx.route().mock(return_value=httpx.Response(204))
        remove_retired_role.call("777")
        assert not route.called

    @respx.mock
    def it_deletes_nothing_when_the_member_list_cannot_be_read():
        respx.get(f"{API}/guilds/srv/members").mock(return_value=httpx.Response(403))
        delete = respx.delete(url__regex=r".+").mock(return_value=httpx.Response(204))
        remove_retired_role.call("777")
        assert not delete.called
