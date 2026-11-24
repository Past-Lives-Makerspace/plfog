"""Discord leadership roles follow the app (#1148): the mapping, the reconcile, the task and its queueing.

Discord HTTP is mocked with ``respx``. The immediate task backend runs a task when the commit
callbacks fire, which a spec triggers with ``django_capture_on_commit_callbacks(execute=True)``.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from django.db import transaction
from django.utils import timezone

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
from membership.models import AdminCapability, GuildStaffMembership, LeadershipTab, Member
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
GUILD_LEADS = "333"
TOURS = "666"
TEACHERS = "777"
WOOD_LEAD = "888"  # a per guild lead role, no longer managed (#1270)
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
        "guild_leads_role_id": GUILD_LEADS,
        "tour_guides_role_id": TOURS,
        "instructors_role_id": TEACHERS,
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
    def it_gives_a_lead_guild_leads_leadership_and_guild_officers():
        facts = LeadershipFacts(led_guild_ids=frozenset({1}))
        assert desired_role_ids(facts, _settings()) == {GUILD_LEADS, LEADERSHIP, OFFICERS}

    def it_gives_other_staff_guild_officers_only():
        assert desired_role_ids(LeadershipFacts(other_staff=True), _settings()) == {OFFICERS}

    def it_gives_a_listed_member_leadership_and_guild_officers():
        assert desired_role_ids(LeadershipFacts(listed=True), _settings()) == {LEADERSHIP, OFFICERS}

    def it_gives_a_tour_guide_tour_guides_only():
        assert desired_role_ids(LeadershipFacts(tour_guide=True), _settings()) == {TOURS}

    def it_gives_an_instructor_instructors_only():
        assert desired_role_ids(LeadershipFacts(instructor=True), _settings()) == {TEACHERS}

    def it_gives_nothing_to_someone_with_no_facts():
        assert desired_role_ids(NO_FACTS, _settings()) == frozenset()

    def it_skips_blank_settings():
        facts = LeadershipFacts(led_guild_ids=frozenset({2}), listed=True, tour_guide=True, instructor=True)
        blank = _settings(
            leadership_role_id="",
            guild_officers_role_id="",
            guild_leads_role_id="",
            tour_guides_role_id="",
            instructors_role_id="",
        )
        assert desired_role_ids(facts, blank) == frozenset()

    def it_gives_the_union_for_someone_in_several_rows():
        facts = LeadershipFacts(led_guild_ids=frozenset({1}), other_staff=True, listed=True, instructor=True)
        assert desired_role_ids(facts, _settings()) == {GUILD_LEADS, LEADERSHIP, OFFICERS, TEACHERS}


def describe_leadership_role_settings():
    def it_manages_the_five_shared_roles_and_no_per_guild_lead_role():
        config = SiteConfiguration.load()
        config.discord_guild_leads_role_id = GUILD_LEADS
        config.discord_tour_guides_role_id = f" {TOURS} "
        config.discord_instructors_role_id = TEACHERS
        config.save()
        GuildFactory(name="Wood", discord_lead_role_id=WOOD_LEAD)
        settings = LeadershipRoleSettings.load()
        assert settings.managed == {LEADERSHIP, OFFICERS, GUILD_LEADS, TOURS, TEACHERS}

    def it_names_each_managed_role():
        settings = _settings()
        assert settings.role_name(LEADERSHIP) == "Leadership"
        assert settings.role_name(OFFICERS) == "Guild Officers"
        assert settings.role_name(GUILD_LEADS) == "Guild Leads"
        assert settings.role_name(TOURS) == "Tour Guides"
        assert settings.role_name(TEACHERS) == "Instructors"
        assert settings.role_name("123") == "123"

    def it_never_names_a_blank_id():
        assert _settings(instructors_role_id="").role_name("") == ""


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
        assert leadership_facts([admin.pk]) == {}

    def it_reads_a_tour_guide_grant():
        member = MemberFactory()
        AdminCapability.objects.create(member=member, capability=AdminCapability.Capability.TOUR_GUIDE)
        AdminCapability.objects.create(member=MemberFactory(), capability=AdminCapability.Capability.REFUNDS)
        assert leadership_facts([member.pk]) == {member.pk: LeadershipFacts(tour_guide=True)}
        assert set(leadership_facts()) == {member.pk}

    def it_reads_an_instructor_and_not_a_former_one():
        teacher = MemberFactory(instructor_oriented_at=timezone.now())
        former = MemberFactory(instructor_oriented_at=timezone.now(), status=Member.Status.FORMER)
        facts = leadership_facts([teacher.pk, former.pk])
        assert facts == {teacher.pk: LeadershipFacts(instructor=True)}

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
        _member_get("u1", [WOOD_LEAD, LEADERSHIP, MEMBER_ROLE])
        put = respx.put(url__regex=rf"{API}/guilds/srv/members/u1/roles/\d+").mock(return_value=httpx.Response(204))
        delete = respx.delete(url__regex=rf"{API}/guilds/srv/members/u1/roles/\d+").mock(
            return_value=httpx.Response(204)
        )

        changes = reconcile_member(member)

        assert changes.added == [OFFICERS]
        assert changes.removed == [LEADERSHIP]
        assert [call.request.url.path.rsplit("/", 1)[1] for call in put.calls] == [OFFICERS]
        assert [call.request.url.path.rsplit("/", 1)[1] for call in delete.calls] == [LEADERSHIP]

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
        config = SiteConfiguration.load()
        config.discord_guild_leads_role_id = GUILD_LEADS
        config.save()
        member = MemberFactory(discord_user_id="u1")
        LeadershipListingFactory(member=member)
        _member_get("u1", [LEADERSHIP, OFFICERS, GUILD_LEADS])  # no longer leads a guild
        delete = respx.delete(url__regex=r".+").mock(return_value=httpx.Response(204))
        changes = reconcile_member(member)
        assert changes.removed == [GUILD_LEADS]
        assert delete.call_count == 1

    @respx.mock
    def it_gives_a_tour_guide_and_an_instructor_their_roles():
        config = SiteConfiguration.load()
        config.discord_tour_guides_role_id = TOURS
        config.discord_instructors_role_id = TEACHERS
        config.save()
        member = MemberFactory(discord_user_id="u1", instructor_oriented_at=timezone.now())
        AdminCapability.objects.create(member=member, capability=AdminCapability.Capability.TOUR_GUIDE)
        _member_get("u1", [])
        respx.put(url__regex=r".+").mock(return_value=httpx.Response(204))
        assert reconcile_member(member).added == [TOURS, TEACHERS]

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
    @respx.mock
    def it_is_retired_and_takes_nothing_off_anyone():
        route = respx.route().mock(return_value=httpx.Response(204))
        remove_retired_role.call(WOOD_LEAD)
        assert not route.called


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
        config.discord_guild_leads_role_id = GUILD_LEADS
        config.save()
        GuildFactory(discord_role_ids=[OFFICERS])
        GuildFactory(name="No roles", discord_role_ids=[])
        settings = LeadershipRoleSettings.load()
        assert settings.managed == {GUILD_LEADS}
        assert settings.access_role_ids == {LEADERSHIP, OFFICERS}

    def it_never_wants_an_access_role():
        facts = LeadershipFacts(led_guild_ids=frozenset({1}), listed=True)
        assert desired_role_ids(facts, _settings(access_role_ids=frozenset({LEADERSHIP}))) == {GUILD_LEADS, OFFICERS}


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
        assert removed == [LEADERSHIP]

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
        assert strip_managed_roles("u1", current=[], settings=LeadershipRoleSettings()).ok
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
    def _check(**role_ids: str) -> dict[str, str]:
        ids = {
            "discord_leadership_role_id": "10",
            "discord_guild_officers_role_id": "20",
            "discord_guild_leads_role_id": "30",
            "discord_tour_guides_role_id": "",
            "discord_instructors_role_id": "",
        }
        ids.update(role_ids)
        return role_id_conflicts(member_role_id="1", role_ids=ids, guild_member_role_ids=["2", " 3 "])

    def it_passes_distinct_ids_and_blanks():
        assert _check() == {}

    def it_refuses_an_id_that_is_a_member_access_role():
        errors = _check(discord_leadership_role_id="1", discord_tour_guides_role_id="3")
        assert set(errors) == {"discord_leadership_role_id", "discord_tour_guides_role_id"}
        assert "member access role" in errors["discord_leadership_role_id"]

    def it_refuses_an_id_another_managed_role_already_uses():
        errors = _check(discord_guild_officers_role_id="10", discord_instructors_role_id=" 30 ")
        assert errors == {
            "discord_guild_officers_role_id": "This is already the Leadership role id. Each role needs its own.",
            "discord_instructors_role_id": "This is already the Guild Leads role id. Each role needs its own.",
        }


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
