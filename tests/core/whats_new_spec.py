"""BDD specs for ``core.whats_new`` (#1007, part 2): a viewer's audiences, their entries and their seen state.

The changelog is patched per spec (``plfog.version.CHANGELOG``, read at call time), so nothing here
depends on what the repo's fragments say this week or on the calendar moving past them.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import RequestFactory
from django.utils import timezone

from core.whats_new import (
    NEW_DAYS,
    PAGE_SIZE,
    Entry,
    audiences_for,
    can_open,
    entries_for,
    history_slug,
    mark_seen,
    open_url,
    unseen,
    whats_new_for,
    window_start,
)
from hub.view_as import ROLE_GUEST, ROLE_MEMBER, ViewAs, compute_actual_roles
from membership.models import AdminCapability, GuildStaffMembership, Member
from tests.membership.factories import (
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildMembershipFactory,
    GuildStaffMembershipFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db


def _day(days_ago: int = 0) -> str:
    return (timezone.localdate() - dt.timedelta(days=days_ago)).isoformat()


def _entry(slug: str, *, days_ago: int = 0, audiences: tuple[str, ...] = ("members",), **keys: Any) -> dict[str, Any]:
    """A changelog entry as ``Fragment.as_entry`` writes one."""
    return {
        "date": _day(days_ago),
        "title": slug.replace("-", " ").capitalize(),
        "changes": [f"{slug} changed"],
        "slug": slug,
        "audiences": list(audiences),
        "feature": keys.get("feature", ""),
        "guidance": keys.get("guidance", "none"),
    }


@pytest.fixture
def changelog(monkeypatch):
    def _set(entries: list[dict[str, Any]]) -> None:
        monkeypatch.setattr("plfog.version.CHANGELOG", entries)

    return _set


def _member(username: str = "viewer", **fields: Any) -> Member:
    """A linked member (the signal builds it from the user, once a plan exists)."""
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    for name, value in fields.items():
        setattr(member, name, value)
    if fields:
        member.save(update_fields=list(fields))
    return member


def _request(user: User | None, picked: str | None = None):
    request = RequestFactory().get("/")
    request.user = user
    request.view_as = ViewAs(actual=compute_actual_roles(user), picked=picked)
    return request


def describe_audiences_for():
    def it_puts_an_active_member_under_members_alone():
        member = _member()
        assert audiences_for(_request(member.user)) == {"members"}

    def it_adds_instructors_for_a_public_instructor_profile():
        member = _member(instructor_slug="ada")
        assert audiences_for(_request(member.user)) == {"members", "instructors"}

    def it_reads_guild_staff_guild_leads_and_class_reviewers_off_the_lead_fk():
        member = _member()
        GuildFactory(guild_lead=member)
        assert audiences_for(_request(member.user)) == {"members", "guild-staff", "guild-leads", "class-reviewers"}

    @pytest.mark.parametrize(
        ("role", "finer"),
        [
            (GuildStaffMembership.Role.CO_LEAD, "guild-leads"),
            (GuildStaffMembership.Role.TREASURER, "treasurers"),
            (GuildStaffMembership.Role.ORIENTER, "orientors"),
        ],
    )
    def it_maps_a_preset_staff_role_to_its_finer_key(role, finer):
        member = _member()
        GuildStaffMembershipFactory(member=member, role=role)
        assert audiences_for(_request(member.user)) == {"members", "guild-staff", finer}

    def it_gives_a_custom_titled_staff_member_guild_staff_and_nothing_finer():
        member = _member()
        GuildStaffMembershipFactory(member=member, custom=True)
        assert audiences_for(_request(member.user)) == {"members", "guild-staff"}

    def it_adds_class_reviewers_for_the_reviewer_switch():
        member = _member()
        GuildStaffMembershipFactory(member=member, role=GuildStaffMembership.Role.SECRETARY, can_review_classes=True)
        assert audiences_for(_request(member.user)) == {"members", "guild-staff", "class-reviewers"}

    def it_adds_equipment_managers_for_an_equipment_staff_row_without_guild_staff():
        member = _member()
        EquipmentStaffMembershipFactory(member=member)
        assert audiences_for(_request(member.user)) == {"members", "equipment-managers"}

    def it_puts_a_guild_officer_under_guild_staff():
        member = _member(fog_role=Member.FogRole.GUILD_OFFICER)
        assert audiences_for(_request(member.user)) == {"members", "guild-staff"}

    def it_puts_an_admin_under_admins_and_guild_staff():
        member = _member(fog_role=Member.FogRole.ADMIN)
        assert audiences_for(_request(member.user)) == {"members", "guild-staff", "admins"}

    def it_puts_a_capability_holder_under_admins():
        member = _member()
        AdminCapability.objects.create(member=member, capability=AdminCapability.Capability.BILLING_APPROVER)
        assert audiences_for(_request(member.user)) == {"members", "admins"}

    def describe_view_as():
        def it_shows_an_admin_previewing_as_a_member_the_member_audiences():
            member = _member(fog_role=Member.FogRole.ADMIN)
            AdminCapability.objects.create(member=member, capability=AdminCapability.Capability.REFUNDS)
            assert audiences_for(_request(member.user, picked=ROLE_MEMBER)) == {"members"}

        def it_keeps_the_guild_an_admin_leads_while_previewing_as_a_member():
            member = _member(fog_role=Member.FogRole.ADMIN)
            GuildFactory(guild_lead=member)
            assert audiences_for(_request(member.user, picked=ROLE_MEMBER)) == {
                "members",
                "guild-staff",
                "guild-leads",
                "class-reviewers",
            }

        def it_gives_an_admin_previewing_as_a_guest_nothing():
            member = _member(fog_role=Member.FogRole.ADMIN)
            assert audiences_for(_request(member.user, picked=ROLE_GUEST)) == frozenset()

    def describe_who_is_nobody():
        @pytest.mark.parametrize("status", [Member.Status.GUEST, Member.Status.FORMER])
        def it_gives_a_guest_or_former_member_nothing(status):
            member = _member(status=status)
            assert audiences_for(_request(member.user)) == frozenset()

        def it_gives_an_account_with_no_member_nothing():
            user = User.objects.create_user(username="orphan", password="pass")
            Member.objects.filter(user=user).delete()
            user = User.objects.get(pk=user.pk)
            assert audiences_for(_request(user)) == frozenset()

    def it_costs_one_query_however_many_roles_there_are(django_assert_num_queries):
        member = _member(fog_role=Member.FogRole.GUILD_OFFICER, instructor_slug="ada")
        GuildFactory(guild_lead=member)
        GuildStaffMembershipFactory(member=member, role=GuildStaffMembership.Role.TREASURER, can_review_classes=True)
        EquipmentStaffMembershipFactory(member=member)
        AdminCapability.objects.create(member=member, capability=AdminCapability.Capability.EQUIPMENT)
        request = _request(member.user)
        request.user.member  # the member the page already loaded; not this module's cost
        with django_assert_num_queries(1):
            held = audiences_for(request)
        assert held == {
            "members",
            "instructors",
            "guild-staff",
            "guild-leads",
            "treasurers",
            "class-reviewers",
            "equipment-managers",
            "admins",
        }
        with django_assert_num_queries(0):
            assert audiences_for(request) == held


def describe_history_slug():
    def it_is_twelve_hex_characters_stable_for_a_date_and_title_and_different_for_another():
        slug = history_slug("2026-10-20", "Old news")
        assert len(slug) == 12 and int(slug, 16) >= 0
        assert slug == history_slug("2026-10-20", "Old news")
        assert slug != history_slug("2026-10-20", "Older news")
        assert slug != history_slug("2026-10-21", "Old news")


def describe_entries_for():
    def it_lists_every_entry_newest_first_whatever_its_date(changelog):
        changelog([_entry("new"), _entry("edge", days_ago=NEW_DAYS), _entry("old", days_ago=NEW_DAYS + 400)])
        request = _request(_member().user)
        assert [entry.slug for entry in entries_for(request)] == ["new", "edge", "old"]

    def it_lists_an_entry_on_every_tab_its_audiences_roll_up_to(changelog):
        changelog(
            [
                _entry("for-all", audiences=("members", "guild-staff", "admins")),
                _entry("for-orientors", audiences=("orientors",)),
                _entry("for-instructors", audiences=("instructors",)),
                _entry("for-admins", audiences=("admins",)),
            ]
        )
        request = _request(_member().user)
        assert [e.slug for e in entries_for(request, tab="members")] == ["for-all", "for-instructors"]
        assert [e.slug for e in entries_for(request, tab="guild-staff")] == ["for-all", "for-orientors"]
        assert [e.slug for e in entries_for(request, tab="admins")] == ["for-all", "for-admins"]

    def it_limits_to_a_feature_key_and_everything_under_it(changelog):
        changelog(
            [
                _entry("cubbies", feature="guild.cubbies"),
                _entry("voting", feature="guild.voting"),
                _entry("tools", feature="admin.tools"),
                _entry("plain"),
            ]
        )
        request = _request(_member().user)
        assert [e.slug for e in entries_for(request, feature="guild")] == ["cubbies", "voting"]
        assert [e.slug for e in entries_for(request, feature="guild.cubbies")] == ["cubbies"]
        assert [e.slug for e in entries_for(request, feature="guild.cub")] == []

    def it_treats_a_frozen_history_entry_as_a_members_entry_with_nothing_to_open(changelog):
        changelog([{"version": "2.60.0", "date": _day(2), "title": "Old news", "changes": ["It happened."]}])
        request = _request(_member().user)
        (entry,) = entries_for(request)
        assert entry == Entry(
            slug=history_slug(_day(2), "Old news"),
            date=_day(2),
            title="Old news",
            changes=("It happened.",),
            audiences=frozenset({"members"}),
            feature="",
            guidance="none",
        )
        assert entry.on_tab("members") and not entry.on_tab("guild-staff")
        assert entry.steps == ()

    def it_groups_the_body_by_date_with_chips_for_a_finer_audience(changelog):
        changelog(
            [
                _entry("today-a", audiences=("members", "guild-leads")),
                _entry("today-b", audiences=("treasurers", "orientors")),
                _entry("earlier", days_ago=3, audiences=("guild-staff",)),
            ]
        )
        groups = whats_new_for(_request(_member().user)).groups_for("guild-staff")
        assert [(group.date, [row.entry.slug for row in group.rows]) for group in groups] == [
            (_day(0), ["today-a", "today-b"]),
            (_day(3), ["earlier"]),
        ]
        assert [row.chips for row in groups[0].rows] == [["Guild Leads"], ["Orientors", "Treasurers"]]
        assert whats_new_for(_request(_member("other").user)).groups_for("members")[0].rows[0].chips == []


def describe_page_for():
    def it_shows_a_page_at_a_time_and_says_what_show_older_asks_after(changelog):
        changelog([_entry(f"row-{i:02d}", days_ago=i) for i in range(PAGE_SIZE + 5)])
        news = whats_new_for(_request(_member().user))
        first = news.page_for("members")
        assert first.slugs == [f"row-{i:02d}" for i in range(PAGE_SIZE)]
        assert first.older_after == f"row-{PAGE_SIZE - 1:02d}"
        assert first.joins_previous is False
        older = news.page_for("members", after=first.older_after)
        assert older.slugs == [f"row-{i:02d}" for i in range(PAGE_SIZE, PAGE_SIZE + 5)]
        assert older.older_after == ""
        assert [group.date for group in older.groups] == [_day(i) for i in range(PAGE_SIZE, PAGE_SIZE + 5)]

    def it_has_no_show_older_when_the_page_is_exactly_full(changelog):
        changelog([_entry(f"row-{i:02d}") for i in range(PAGE_SIZE)])
        assert whats_new_for(_request(_member().user)).page_for("members").older_after == ""

    def it_says_when_a_page_continues_the_date_the_page_before_ended_on(changelog):
        changelog([_entry(f"row-{i:02d}") for i in range(PAGE_SIZE + 1)] + [_entry("next-day", days_ago=1)])
        news = whats_new_for(_request(_member().user))
        older = news.page_for("members", after=f"row-{PAGE_SIZE - 1:02d}")
        assert older.joins_previous is True
        assert [group.date for group in older.groups] == [_day(0), _day(1)]

    def it_pages_within_the_tab_and_the_feature(changelog):
        changelog([_entry(f"row-{i:02d}", feature="guild.cubbies" if i % 2 else "", days_ago=i) for i in range(50)])
        news = whats_new_for(_request(_member().user))
        page = news.page_for("members", "guild")
        assert page.slugs == [f"row-{i:02d}" for i in range(1, 2 * PAGE_SIZE, 2)]
        assert news.page_for("members", "guild", after=page.older_after).slugs == [
            f"row-{i:02d}" for i in range(41, 50, 2)
        ]

    def it_refuses_to_continue_after_an_entry_the_tab_does_not_have(changelog):
        changelog([_entry("here"), _entry("staff-only", audiences=("guild-staff",))])
        news = whats_new_for(_request(_member().user))
        with pytest.raises(LookupError, match="No entry 'gone' on the members tab"):
            news.page_for("members", after="gone")
        with pytest.raises(LookupError):
            news.page_for("members", after="staff-only")


def describe_can_open_and_open_url():
    def it_opens_only_for_a_viewer_in_the_entrys_audiences():
        entry = Entry.from_changelog(_entry("staff-only", audiences=("guild-leads",)))
        assert can_open(entry, frozenset({"members", "guild-staff", "guild-leads"}))
        assert not can_open(entry, frozenset({"members", "guild-staff", "orientors"}))

    def it_resolves_where_the_feature_lives_for_the_member():
        member = _member()
        GuildMembershipFactory(member=member, guild=GuildFactory(slug="ceramics"))
        entry = Entry.from_changelog(_entry("cubbies", feature="guild.cubbies", guidance="link"))
        assert open_url(entry, member) == "/guilds/ceramics/?tab=cubbies"

    def it_hides_open_it_for_an_entry_with_no_feature_or_a_member_with_nothing_to_open_it_on():
        member = _member()
        assert open_url(Entry.from_changelog(_entry("plain")), member) == ""
        assert open_url(Entry.from_changelog(_entry("cubbies", feature="guild.cubbies")), member) == ""

    def it_looks_the_members_guild_up_once_for_every_entry_that_opens_on_it(changelog, django_assert_num_queries):
        keys = (
            "guild.cubbies",
            "guild.orientations",
            "guild.reservations",
            "guild.wiki",
            "guild.meetings",
            "guild.polls",
        )
        changelog([_entry(key.replace(".", "-"), feature=key, guidance="link") for key in keys] + [_entry("plain")])
        member = _member()
        GuildMembershipFactory(member=member, guild=GuildFactory(slug="ceramics"))
        news = whats_new_for(_request(member.user))
        news.audiences  # the roles query, paid once per request, is not what is measured
        # One resolver for the six: the staffed guilds, then the joined ones, and no more.
        with django_assert_num_queries(2):
            rows = [row for group in news.groups_for("members") for row in group.rows]
        assert [row.open_url for row in rows] == [f"/guilds/ceramics/?tab={key.split('.')[1]}" for key in keys] + [""]

    def it_lists_the_steps_at_clip_and_tour_and_not_at_link():
        for level in ("clip", "tour"):
            assert Entry.from_changelog(_entry("x", feature="guild.cubbies", guidance=level)).steps
        assert Entry.from_changelog(_entry("x", feature="guild.cubbies", guidance="link")).steps == ()

    def it_offers_the_tour_at_level_tour_to_a_member_the_tour_is_for(changelog):
        changelog(
            [
                _entry("tour", feature="home.tour", guidance="tour"),
                _entry("staff", feature="guild.staff", guidance="tour"),
            ]
        )
        rows = {
            row.entry.slug: row
            for group in whats_new_for(_request(_member().user)).groups_for("members")
            for row in group.rows
        }
        assert rows["tour"].tour_url == "/home/?tour=member-welcome"
        # guild.staff's tour is for a guild lead; a plain member gets the entry with no tour link.
        assert rows["staff"].tour_url == ""


def describe_unseen_and_mark_seen():
    def it_is_unseen_until_marked_and_only_for_an_entry_in_the_viewers_audiences(changelog):
        changelog([_entry("for-members"), _entry("for-staff", audiences=("guild-staff",))])
        member = _member()
        assert unseen(_request(member.user)) is True
        assert mark_seen(member, ["for-members"]) is True
        assert member.whats_new_seen == ["for-members"]
        assert unseen(_request(member.user)) is False
        staff_entry = Entry.from_changelog(_entry("for-staff", audiences=("guild-staff",)))
        assert whats_new_for(_request(member.user)).is_unseen(staff_entry) is False

    def it_never_counts_an_entry_outside_the_window_as_unseen(changelog):
        changelog([_entry("old", days_ago=NEW_DAYS + 1), _entry("edge", days_ago=NEW_DAYS)])
        news = whats_new_for(_request(_member().user))
        assert [entry.slug for entry in news.entries_for(tab="members")] == ["old", "edge"]
        assert [entry.slug for entry in news.window] == ["edge"]
        assert news.is_unseen(news.entries[0]) is False
        assert news.is_unseen(news.entries[1]) is True

    def it_prunes_the_stored_list_to_the_window_on_every_write(changelog):
        changelog([_entry("new"), _entry("old", days_ago=NEW_DAYS + 1)])
        member = _member(whats_new_seen=["old", "vanished"])
        assert mark_seen(member, ["new", "old"]) is True
        assert Member.objects.get(pk=member.pk).whats_new_seen == ["new"]

    def it_writes_nothing_when_nothing_changes(changelog, django_assert_num_queries):
        changelog([_entry("new")])
        member = _member(whats_new_seen=["new"])
        with django_assert_num_queries(0):
            assert mark_seen(member, ["new"]) is False
            assert mark_seen(member, []) is False
        assert Member.objects.get(pk=member.pk).whats_new_seen == ["new"]

    def it_opens_on_the_first_tab_with_something_unseen_else_members(changelog):
        changelog(
            [
                _entry("for-admins", audiences=("admins",)),
                _entry("for-staff", audiences=("guild-staff",)),
                _entry("old-for-members", days_ago=NEW_DAYS + 1),
            ]
        )
        lead = _member("lead")
        GuildFactory(guild_lead=lead)
        assert whats_new_for(_request(lead.user)).default_tab() == "guild-staff"
        admin = _member("admin", fog_role=Member.FogRole.ADMIN, whats_new_seen=["for-staff"])
        assert whats_new_for(_request(admin.user)).default_tab() == "admins"
        assert whats_new_for(_request(_member("plain").user)).default_tab() == "members"

    def it_starts_the_window_thirty_days_back():
        assert window_start(dt.date(2026, 10, 29)) == dt.date(2026, 9, 29)
