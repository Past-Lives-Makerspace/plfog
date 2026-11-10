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
    LeadershipRoleSyncError,
    reconcile_on_commit,
    remove_retired_role,
    role_id_conflicts,
    strip_leadership_roles,
    strip_managed_roles,
    strip_on_commit,
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


@pytest.fixture
def no_wait(monkeypatch):
    """Skip the real wait before a 429's retry; record what was asked for."""
    waits: list[float] = []
    monkeypatch.setattr(discord_roles, "_sleep", waits.append)
    return waits


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

    def it_answers_empty_with_a_blank_token(settings):
        settings.DISCORD_BOT_TOKEN = ""
        assert discord_roles.server_member_role_ids("srv") == {}

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
    def it_records_failures_tries_every_change_and_never_raises(no_wait):
        member = MemberFactory(discord_user_id="u1")
        LeadershipListingFactory(member=member)
        _member_get("u1", [])
        put = respx.put(url__regex=r".+").mock(
            side_effect=[httpx.Response(429), httpx.Response(429), httpx.ConnectError("down")]
        )
        changes = reconcile_member(member)
        assert changes.added == []
        assert changes.failed == [LEADERSHIP, OFFICERS]
        assert put.call_count == 3
        assert not changes.ok

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
    def it_tries_every_holder_then_fails_loudly(no_wait):
        _roster(("u1", ["777"]), ("u2", ["777"]))
        delete = respx.delete(url__regex=r".+").mock(side_effect=[httpx.ConnectError("down"), httpx.Response(204)])
        with pytest.raises(LeadershipRoleSyncError, match="off 1 Discord accounts"):
            remove_retired_role.call("777")
        assert delete.call_count == 2

    @respx.mock
    def it_skips_a_member_access_role():
        config = SiteConfiguration.load()
        config.discord_member_role_id = "888"
        config.save()
        GuildFactory(discord_role_ids=["777"])
        route = respx.route().mock(return_value=httpx.Response(204))
        remove_retired_role.call("777")
        remove_retired_role.call("888")
        assert not route.called

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
    def it_deletes_nothing_and_fails_when_the_member_list_cannot_be_read():
        respx.get(f"{API}/guilds/srv/members").mock(return_value=httpx.Response(403))
        delete = respx.delete(url__regex=r".+").mock(return_value=httpx.Response(204))
        with pytest.raises(LeadershipRoleSyncError, match="member list"):
            remove_retired_role.call("777")
        assert not delete.called


def describe_rate_limits():
    @respx.mock
    def it_waits_out_a_429_and_retries_once(no_wait):
        route = respx.put(url__regex=r".+").mock(
            side_effect=[httpx.Response(429, headers={"Retry-After": "2.5"}), httpx.Response(204)]
        )
        assert discord_roles.role_outcome("PUT", "srv", "u1", "1", retry=True) is discord_roles.RoleOutcome.DONE
        assert route.call_count == 2
        assert no_wait == [2.5]

    @respx.mock
    def it_caps_the_wait_and_reads_retry_after_from_the_body(no_wait):
        respx.put(url__regex=r".+").mock(
            side_effect=[httpx.Response(429, json={"retry_after": 60}), httpx.Response(204)]
        )
        discord_roles.role_outcome("PUT", "srv", "u1", "1", retry=True)
        assert no_wait == [10.0]

    @respx.mock
    def it_waits_a_second_when_discord_gives_no_usable_wait(no_wait):
        respx.put(url__regex=r".+").mock(
            side_effect=[
                httpx.Response(429, text="slow down"),
                httpx.Response(204),
                httpx.Response(429, headers={"Retry-After": "soon"}),
                httpx.Response(204),
                httpx.Response(429, json=["odd"]),
                httpx.Response(204),
            ]
        )
        for _ in range(3):
            assert discord_roles.role_outcome("PUT", "srv", "u1", "1", retry=True) is discord_roles.RoleOutcome.DONE
        assert no_wait == [1.0, 1.0, 1.0]

    def it_really_waits_outside_specs(monkeypatch):
        waited: list[float] = []
        monkeypatch.setattr(discord_roles.time, "sleep", waited.append)
        discord_roles._sleep(2.0)
        assert waited == [2.0]

    @respx.mock
    def it_fails_after_a_second_429(no_wait):
        route = respx.delete(url__regex=r".+").mock(return_value=httpx.Response(429))
        assert discord_roles.role_outcome("DELETE", "srv", "u1", "1", retry=True) is discord_roles.RoleOutcome.FAILED
        assert route.call_count == 2

    @respx.mock
    def it_fails_fast_without_waiting_unless_asked(no_wait):
        route = respx.delete(url__regex=r".+").mock(return_value=httpx.Response(429))
        respx.get(f"{API}/guilds/srv/members/u1").mock(return_value=httpx.Response(429))
        assert discord_roles.role_outcome("DELETE", "srv", "u1", "1") is discord_roles.RoleOutcome.FAILED
        assert discord_roles.remove_role("srv", "u1", "1") is False
        assert discord_roles.read_member_roles("srv", "u1").failed
        assert route.call_count == 2
        assert no_wait == []

    @respx.mock
    def it_calls_a_403_refused_a_blank_id_benign_and_a_5xx_failed():
        respx.put(f"{API}/guilds/srv/members/u1/roles/1").mock(return_value=httpx.Response(403))
        respx.put(f"{API}/guilds/srv/members/u1/roles/2").mock(return_value=httpx.Response(503))
        assert discord_roles.role_outcome("PUT", "srv", "u1", "1") is discord_roles.RoleOutcome.REFUSED
        assert discord_roles.assign_role("srv", "u1", "1") is False
        assert discord_roles.role_outcome("PUT", "srv", "", "1") is discord_roles.RoleOutcome.BENIGN
        assert discord_roles.role_outcome("PUT", "srv", "u1", "2") is discord_roles.RoleOutcome.FAILED

    @respx.mock
    def it_retries_a_read_and_calls_a_missing_member_quiet(no_wait):
        respx.get(f"{API}/guilds/srv/members/u1").mock(
            side_effect=[httpx.Response(429), httpx.Response(200, json={"roles": ["5"]})]
        )
        respx.get(f"{API}/guilds/srv/members/u2").mock(return_value=httpx.Response(404))
        respx.get(f"{API}/guilds/srv/members/u3").mock(return_value=httpx.Response(500))
        assert discord_roles.read_member_roles("srv", "u1", retry=True) == discord_roles.RolesRead({"5"})
        assert discord_roles.read_member_roles("srv", "u2") == discord_roles.RolesRead(None)
        assert discord_roles.read_member_roles("srv", "u3") == discord_roles.RolesRead(None, failed=True)

    @respx.mock
    def it_keeps_the_join_and_leave_sync_from_raising_or_waiting(no_wait):
        member = MemberFactory(discord_user_id="u1")
        guild = GuildFactory(discord_role_ids=["5", "6"])
        put = respx.put(url__regex=r".+").mock(
            side_effect=[httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(403)]
        )
        discord_roles.on_membership_changed(guild, member, joined=True)
        assert put.call_count == 2
        assert no_wait == []


def describe_server_members():
    @respx.mock
    def it_reads_each_members_username_and_roles():
        respx.get(f"{API}/guilds/srv/members").mock(
            return_value=httpx.Response(
                200, json=[{"user": {"id": "1", "username": "amber"}, "roles": ["5"]}, {"user": {"id": "2"}}]
            )
        )
        found = discord_roles.server_members("srv")
        assert found == {
            "1": discord_roles.ServerMember(username="amber", roles={"5"}),
            "2": discord_roles.ServerMember(username="", roles=set()),
        }
        assert found["2"].names == ()

    @respx.mock
    def it_reads_a_display_name_and_a_nickname():
        respx.get(f"{API}/guilds/srv/members").mock(
            return_value=httpx.Response(
                200, json=[{"user": {"id": "1", "username": "a", "global_name": "Amber"}, "nick": "Amb", "roles": []}]
            )
        )
        assert discord_roles.server_members("srv")["1"].names == ("a", "Amber", "Amb")


def describe_member_access_roles():
    def it_never_manages_the_member_role_or_a_guild_member_role():
        config = SiteConfiguration.load()
        config.discord_member_role_id = LEADERSHIP
        config.save()
        GuildFactory(discord_role_ids=[OFFICERS], discord_lead_role_id="333")
        GuildFactory(name="Clash", discord_lead_role_id=OFFICERS)
        settings = LeadershipRoleSettings.load()
        assert settings.managed == {"333"}
        assert settings.access_role_ids == {LEADERSHIP, OFFICERS}

    def it_never_wants_an_access_role():
        facts = LeadershipFacts(led_guild_ids=frozenset({1}), listed=True)
        assert desired_role_ids(facts, _settings(access_role_ids=frozenset({LEADERSHIP}))) == {WOOD_LEAD, OFFICERS}


def describe_failing_tasks():
    @respx.mock
    def it_fails_the_reconcile_task_after_trying_every_change(no_wait):
        member = MemberFactory(discord_user_id="u1")
        LeadershipListingFactory(member=member)
        _member_get("u1", [])
        put = respx.put(url__regex=r".+").mock(side_effect=[httpx.Response(500), httpx.Response(204)])
        with pytest.raises(LeadershipRoleSyncError, match=f"member {member.pk}: {LEADERSHIP}"):
            reconcile_leadership_roles.call(member.pk)
        assert put.call_count == 2

    @respx.mock
    def it_fails_the_reconcile_task_when_the_roles_cannot_be_read():
        member = MemberFactory(discord_user_id="u1")
        respx.get(f"{API}/guilds/srv/members/u1").mock(return_value=httpx.Response(500))
        with pytest.raises(LeadershipRoleSyncError, match="Could not read"):
            reconcile_leadership_roles.call(member.pk)


def describe_refused_roles():
    @respx.mock
    def it_fails_the_reconcile_task_on_a_403():
        member = MemberFactory(discord_user_id="u1")
        LeadershipListingFactory(member=member)
        _member_get("u1", [])
        respx.put(url__regex=r".+").mock(return_value=httpx.Response(403))
        with pytest.raises(LeadershipRoleSyncError, match=LEADERSHIP):
            reconcile_leadership_roles.call(member.pk)

    @respx.mock
    def it_fails_the_retire_task_on_a_403():
        respx.get(f"{API}/guilds/srv/members").mock(
            return_value=httpx.Response(200, json=[{"user": {"id": "u1"}, "roles": ["777"]}])
        )
        respx.delete(url__regex=r".+").mock(return_value=httpx.Response(403))
        with pytest.raises(LeadershipRoleSyncError):
            remove_retired_role.call("777")


def describe_benign_outcomes():
    @respx.mock
    def it_counts_a_member_who_left_mid_reconcile_as_through():
        member = MemberFactory(discord_user_id="u1")
        LeadershipListingFactory(member=member)
        _member_get("u1", [])
        respx.put(url__regex=r".+").mock(return_value=httpx.Response(404))
        changes = reconcile_member(member)
        assert changes.ok
        assert changes.added == []

    def it_queues_no_strip_for_a_blank_discord_id(django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks() as callbacks:
            strip_on_commit("  ")
        assert callbacks == []


def describe_strip_managed_roles():
    @respx.mock
    def it_takes_every_managed_role_off_and_leaves_the_rest():
        GuildFactory(discord_lead_role_id=WOOD_LEAD)
        _member_get("u1", [LEADERSHIP, WOOD_LEAD, MEMBER_ROLE])
        delete = respx.delete(url__regex=r".+").mock(return_value=httpx.Response(204))
        strip_leadership_roles.call("u1")
        removed = sorted(call.request.url.path.rsplit("/", 1)[1] for call in delete.calls)
        assert removed == sorted([LEADERSHIP, WOOD_LEAD])

    @respx.mock
    def it_fails_the_task_when_a_removal_fails():
        _member_get("u1", [LEADERSHIP])
        respx.delete(url__regex=r".+").mock(return_value=httpx.Response(500))
        with pytest.raises(LeadershipRoleSyncError, match="Discord account u1"):
            strip_leadership_roles.call("u1")

    @respx.mock
    def it_sends_nothing_for_a_blank_id_a_blank_server_or_no_managed_role():
        route = respx.route().mock(return_value=httpx.Response(204))
        assert strip_managed_roles("").ok
        assert strip_managed_roles(
            "u1", current=[], settings=_settings(leadership_role_id="", guild_officers_role_id="", lead_role_ids={})
        ).ok
        config = SiteConfiguration.load()
        config.discord_server_id = ""
        config.save()
        assert strip_managed_roles("u1").ok
        assert not route.called

    @respx.mock
    def it_leaves_someone_off_the_server_alone():
        respx.get(f"{API}/guilds/srv/members/u1").mock(return_value=httpx.Response(404))
        delete = respx.delete(url__regex=r".+").mock(return_value=httpx.Response(204))
        strip_leadership_roles.call("u1")
        assert not delete.called


def describe_role_id_conflicts():
    def _check(**overrides: object) -> tuple[dict[str, str], dict[int, str]]:
        values: dict[str, object] = {
            "member_role_id": "1",
            "leadership_role_id": "10",
            "guild_officers_role_id": "20",
            "guilds": {1: ("30", ["2"], True), 2: ("40", ["3"], True)},
        }
        values.update(overrides)
        return role_id_conflicts(**values)  # type: ignore[arg-type]

    def it_passes_distinct_ids():
        assert _check() == ({}, {})

    def it_refuses_a_site_id_that_is_a_member_access_role():
        site, _guilds = _check(leadership_role_id="1", guild_officers_role_id="3")
        assert set(site) == {"discord_leadership_role_id", "discord_guild_officers_role_id"}

    def it_refuses_officers_equal_to_leadership():
        site, _guilds = _check(guild_officers_role_id="10")
        assert "own role" in site["discord_guild_officers_role_id"]

    def it_refuses_lead_ids_that_clash():
        _site, guilds = _check(
            guilds={
                1: ("2", ["2"], True),
                2: ("10", [], True),
                3: ("30", [], True),
                4: ("30", [], True),
                5: ("", [], True),
            }
        )
        assert set(guilds) == {1, 2, 4}
        assert "Another guild" in guilds[4]

    def it_judges_only_the_site_fields_when_the_guild_rows_are_not_posted():
        site, guilds = _check(
            leadership_role_id="30",
            guild_officers_role_id="40",
            guilds={1: ("30", [], True), 2: ("40", [], False), 3: ("2", ["2"], True)},
            check_guild_rows=False,
        )
        assert set(site) == {"discord_leadership_role_id", "discord_guild_officers_role_id"}
        assert guilds == {}

    def it_lets_a_hidden_or_deleted_guilds_old_lead_id_be_reused():
        _site, guilds = _check(guilds={1: ("30", [], False), 2: ("30", [], True), 3: ("30", [], False)})
        assert guilds == {}

    def it_still_checks_a_hidden_guilds_member_roles_and_lead_against_access():
        site, guilds = _check(leadership_role_id="9", guilds={1: ("1", ["9"], False)})
        assert set(site) == {"discord_leadership_role_id"}
        assert set(guilds) == {1}


def describe_scan_fallback():
    def it_registers_plainly_when_the_commit_list_has_an_unexpected_shape(django_capture_on_commit_callbacks):
        from django.db import connection

        with django_capture_on_commit_callbacks() as callbacks:
            connection.run_on_commit.append(("odd",))  # type: ignore[arg-type]
            try:
                reconcile_on_commit(42)
            finally:
                connection.run_on_commit.remove(("odd",))  # type: ignore[arg-type]
        assert [cb.member_id for cb in callbacks if isinstance(cb, _ReconcileWhenCommitted)] == [42]
