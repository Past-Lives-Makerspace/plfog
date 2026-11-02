"""BDD specs for What's New on the hub (#1007, part 2): the view, Show older, the seen POST, the Home button and the body.

The changelog is patched per spec (``plfog.version.CHANGELOG``, read at call time), so nothing here
depends on the repo's fragments or on the calendar.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import SimpleLazyObject

from core.whats_new import PAGE_SIZE
from hub.context_processors import hub_sidebar
from hub.view_as import SESSION_ROLE_KEY, ROLE_GUEST, ROLE_MEMBER, ViewAs, compute_actual_roles
from hub.whats_new_views import hub_whats_new, hub_whats_new_seen
from membership.models import Member
from tests.membership.factories import GuildFactory, GuildMembershipFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

#: Queries one htmx GET of the first page costs for a member in a guild, every entry on one guild tab:
#: the session, the user, the member, View As, every context processor the hub runs for a partial,
#: the roles query, the guild lookup behind Open it (once per resolver, however many keys share it)
#: and the one write of the seen and opened lists. Never one per entry.
_BODY_QUERY_BUDGET = 23


def _day(days_ago: int = 0) -> str:
    return (timezone.localdate() - dt.timedelta(days=days_ago)).isoformat()


def _entry(slug: str, *, days_ago: int = 0, audiences: tuple[str, ...] = ("members",), **keys: Any) -> dict[str, Any]:
    entry = {
        "date": _day(days_ago),
        "title": slug.replace("-", " ").capitalize(),
        "changes": [f"{slug} changed"],
        "slug": slug,
        "audiences": list(audiences),
        "feature": keys.get("feature", ""),
        "guidance": keys.get("guidance", "none"),
    }
    if "target" in keys:
        entry["target"] = keys["target"]
    if "shots" in keys:  # only when given, as the fragment writes it (#1050)
        entry["shots"] = keys["shots"]
    return entry


#: Two shots as a fragment's ``as_entry`` writes them (#1050).
_SHOTS = [
    {"caption": "On Home, press What's New.", "frame": "desktop"},
    {"caption": "Pick the tab for your role.", "frame": "phone"},
]


@pytest.fixture
def changelog(monkeypatch):
    def _set(entries: list[dict[str, Any]]) -> None:
        monkeypatch.setattr("plfog.version.CHANGELOG", entries)

    return _set


def _login(client: Client, username: str = "viewer", **fields: Any) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    for name, value in fields.items():
        setattr(member, name, value)
    if fields:
        member.save(update_fields=list(fields))
    client.login(username=username, password="pass")
    return member


def _direct_request(user: User, method: str = "get"):
    """A request to the view itself, past the middleware, as the lockout cases need."""
    request = getattr(RequestFactory(), method)("/whats-new/")
    request.user = user
    request.view_as = ViewAs(actual=compute_actual_roles(user), picked=None)
    return request


def _preview(client: Client, role: str) -> None:
    session = client.session
    session[SESSION_ROLE_KEY] = role
    session.save()


def _body(client: Client, query: str = "", current: str = "") -> str:
    """The modal body as htmx loads it; ``current`` is the page the modal is open on (HX-Current-URL, #1051)."""
    headers = {"HTTP_HX_CURRENT_URL": f"http://testserver{current}"} if current else {}
    response = client.get(reverse("hub_whats_new") + query, HTTP_HX_REQUEST="true", **headers)
    assert response.status_code == 200
    return response.content.decode()


def describe_hub_whats_new():
    def it_requires_login(client: Client):
        response = client.get(reverse("hub_whats_new"))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    @pytest.mark.parametrize("status", [Member.Status.GUEST, Member.Status.FORMER])
    def it_sends_a_guest_or_former_member_home(status):
        # The lockout middleware turns these away before any hub view; the view's own gate is the
        # one asked here, by calling it without the middleware.
        MembershipPlanFactory()
        user = User.objects.create_user(username="locked", password="pass")
        user.member.status = status
        user.member.save(update_fields=["status"])
        response = hub_whats_new(_direct_request(user))
        assert response.status_code == 302
        assert response["Location"] == reverse("hub_home")

    def it_sends_an_account_with_no_member_home(client: Client):
        user = User.objects.create_user(username="orphan", password="pass")
        Member.objects.filter(user=user).delete()
        client.login(username="orphan", password="pass")
        assert client.get(reverse("hub_whats_new"))["Location"] == reverse("hub_home")

    def it_sends_an_admin_previewing_as_a_guest_home_and_writes_nothing(client: Client, changelog):
        changelog([_entry("fresh")])
        member = _login(client, fog_role=Member.FogRole.ADMIN)
        _preview(client, ROLE_GUEST)
        response = client.get(reverse("hub_whats_new"), HTTP_HX_REQUEST="true")
        assert response.status_code == 302
        assert response["Location"] == reverse("hub_home")
        assert client.post(reverse("hub_whats_new_seen", args=["guild"]))["Location"] == reverse("hub_home")
        assert Member.objects.get(pk=member.pk).whats_new_seen == []

    def it_answers_an_htmx_request_with_the_body_alone(client: Client, changelog):
        changelog([_entry("fresh")])
        _login(client)
        body = _body(client)
        assert "<html" not in body
        assert body.count("data-whats-new-tab=") == 4
        assert 'data-whats-new-tab="members"' in body and 'aria-current="page"' in body
        assert '<span id="whats-new-title" hx-swap-oob="innerHTML">What&#x27;s New</span>' in body
        assert 'data-whats-new-entry="fresh"' in body
        assert "data-whats-new-older" not in body

    def it_answers_a_plain_get_with_a_page_that_opens_the_modal(client: Client, changelog):
        changelog([_entry("fresh")])
        _login(client)
        response = client.get(reverse("hub_whats_new"))
        assert response.status_code == 200
        assert "hub/whats_new.html" in [t.name for t in response.templates]
        html = response.content.decode()
        assert 'id="whats-new-body"' in html and 'data-whats-new-entry="fresh"' in html
        assert "$dispatch('open-modal', 'whats-new')" in html
        assert "whats-new-title" in html and "hx-swap-oob" not in html

    def it_treats_a_history_restore_as_a_page(client: Client, changelog):
        changelog([_entry("fresh")])
        _login(client)
        response = client.get(
            reverse("hub_whats_new"), HTTP_HX_REQUEST="true", **{"HTTP_HX_HISTORY_RESTORE_REQUEST": "true"}
        )
        assert "hub/whats_new.html" in [t.name for t in response.templates]

    def it_copies_the_tab_links_htmx_attributes_onto_the_phone_picker(client: Client, changelog):
        changelog([_entry("fresh")])
        _login(client)
        body = _body(client)
        picker = body[body.index('<nav class="pl-tab-picker__menu"') :]
        assert picker.count('hx-target="#whats-new-body"') == 4
        assert 'hx-get="/whats-new/?tab=instructors"' in picker and 'hx-get="/whats-new/?tab=admins"' in picker
        assert picker.index("For Members") < picker.index("For Instructors") < picker.index("For Guild Staff")

    def describe_the_tab():
        def _tab_keys(body: str) -> list[str]:
            return re.findall(r'data-whats-new-tab="([a-z-]+)"', body)

        @pytest.mark.parametrize("fields", [{}, {"fog_role": Member.FogRole.ADMIN}])
        def it_draws_the_four_tabs_in_order_to_a_member_with_no_roles_and_to_an_admin(
            client: Client, changelog, fields
        ):
            # Every tab shows to everyone (#1007's rule, kept for #1052); roles decide only Open it and unseen.
            changelog([_entry("fresh")])
            _login(client, **fields)
            body = _body(client)
            assert _tab_keys(body) == ["members", "instructors", "guild-staff", "admins"]
            row = body[: body.index('<div class="pl-wn-pane"')]
            assert row.index(">For Members<") < row.index(">For Instructors<") < row.index(">For Guild Staff<")
            assert row.index(">For Guild Staff<") < row.index(">For Admins<")

        def it_opens_on_instructors_for_an_instructor_with_an_unseen_instructor_entry(client: Client, changelog):
            changelog([_entry("teaching", audiences=("instructors",)), _entry("members", audiences=("members",))])
            _login(client, instructor_slug="ada", whats_new_seen=["members"])
            assert 'data-whats-new-pane="instructors"' in _body(client)
            other = Client()
            _login(other, "plain", whats_new_seen=["members"])
            assert 'data-whats-new-pane="members"' in _body(other)

        def it_opens_on_the_first_tab_with_something_unseen_for_the_viewer(client: Client, changelog):
            changelog([_entry("staff", audiences=("guild-staff",)), _entry("members", audiences=("members",))])
            lead = _login(client, whats_new_seen=["members"])
            GuildFactory(guild_lead=lead)
            assert 'data-whats-new-pane="guild-staff"' in _body(client)

        def it_opens_on_members_with_nothing_unseen(client: Client, changelog):
            changelog([_entry("staff", audiences=("guild-staff",))])
            _login(client)
            assert 'data-whats-new-pane="members"' in _body(client)

        def it_honours_the_query_and_falls_back_on_an_unknown_one(client: Client, changelog):
            changelog([_entry("fresh")])
            _login(client)
            assert 'data-whats-new-pane="admins"' in _body(client, "?tab=admins")
            assert 'data-whats-new-pane="members"' in _body(client, "?tab=everyone")

    def describe_seen():
        def it_marks_every_entry_the_shown_page_listed_and_no_other(client: Client, changelog):
            changelog([_entry("a"), _entry("b", days_ago=1), _entry("staff", audiences=("guild-staff",))])
            member = _login(client)
            _body(client)
            assert Member.objects.get(pk=member.pk).whats_new_seen == ["a", "b"]
            _body(client, "?tab=guild-staff")
            assert Member.objects.get(pk=member.pk).whats_new_seen == ["a", "b", "staff"]

        def it_marks_the_open_over_everything_it_could_show_listed_or_not(client: Client, changelog):
            # The buttons' list, not the badges': every tab and every page, so one open quiets the button.
            rows = [_entry(f"row-{i:02d}", days_ago=i + 1) for i in range(PAGE_SIZE + 2)]
            changelog([_entry("cubbies", feature="guild.cubbies"), *rows, _entry("staff", audiences=("guild-staff",))])
            member = _login(client)
            _body(client, "?feature=guild")
            assert Member.objects.get(pk=member.pk).whats_new_opened == ["cubbies"]
            _body(client)
            member.refresh_from_db()
            assert member.whats_new_opened == sorted(
                ["cubbies", "staff", *(f"row-{i:02d}" for i in range(PAGE_SIZE + 2))]
            )
            assert len(member.whats_new_seen) == PAGE_SIZE and "staff" not in member.whats_new_seen
            # Show older lists more, and writes seen alone.
            _body(client, f"?tab=members&after={member.whats_new_seen[-1]}")
            member.refresh_from_db()
            assert len(member.whats_new_seen) == PAGE_SIZE + 3 and "staff" not in member.whats_new_seen

        def it_leaves_the_dot_off_on_the_next_home_load_until_a_new_entry_arrives(client: Client, changelog):
            # One open covers a tab the body did not show and entries past its first page (Felix, 2026-10-29).
            rows = [_entry(f"row-{i:02d}", days_ago=i) for i in range(PAGE_SIZE + 1)]
            changelog([*rows, _entry("for-admins", audiences=("admins",))])
            _login(client, fog_role=Member.FogRole.ADMIN)
            assert "data-whats-new-dot" in client.get(reverse("hub_home")).content.decode()
            _body(client, "?tab=members")
            assert "data-whats-new-dot" not in client.get(reverse("hub_home")).content.decode()
            changelog([_entry("b"), *rows, _entry("for-admins", audiences=("admins",))])
            assert "data-whats-new-dot" in client.get(reverse("hub_home")).content.decode()

    def describe_show_older():
        def it_lists_a_page_and_a_show_older_button_that_loads_the_next_page_alone(client: Client, changelog):
            changelog([_entry(f"row-{i:02d}", days_ago=i) for i in range(PAGE_SIZE + 5)])
            member = _login(client)
            body = _body(client)
            assert body.count("data-whats-new-entry=") == PAGE_SIZE
            assert f'data-whats-new-entry="row-{PAGE_SIZE - 1:02d}"' in body
            older = f"/whats-new/?tab=members&amp;after=row-{PAGE_SIZE - 1:02d}"
            assert f'hx-get="{older}" hx-target="this" hx-swap="outerHTML" data-whats-new-older' in body
            assert Member.objects.get(pk=member.pk).whats_new_seen == [f"row-{i:02d}" for i in range(PAGE_SIZE)]

            page = _body(client, older.removeprefix("/whats-new/").replace("&amp;", "&"))
            assert page.count("data-whats-new-entry=") == 5
            assert "data-whats-new-tab" not in page and "<html" not in page and "whats-new-title" not in page
            assert "data-whats-new-older" not in page
            assert page.count('class="pl-wn-date"') == 5
            assert Member.objects.get(pk=member.pk).whats_new_seen == [f"row-{i:02d}" for i in range(PAGE_SIZE + 5)]

        def it_draws_no_eyebrow_when_a_page_continues_the_date_the_last_one_ended_on(client: Client, changelog):
            changelog([_entry(f"row-{i:02d}") for i in range(PAGE_SIZE + 1)] + [_entry("next-day", days_ago=1)])
            _login(client)
            page = _body(client, f"?tab=members&after=row-{PAGE_SIZE - 1:02d}")
            assert page.count('class="pl-wn-date"') == 1
            assert page.index(f'data-whats-new-entry="row-{PAGE_SIZE:02d}"') < page.index('class="pl-wn-date"')

        def it_keeps_the_feature_through_the_pages(client: Client, changelog):
            changelog([_entry(f"row-{i:02d}", feature="guild.cubbies", days_ago=i) for i in range(PAGE_SIZE + 1)])
            _login(client)
            body = _body(client, "?feature=guild")
            assert f'hx-get="/whats-new/?tab=members&amp;feature=guild&amp;after=row-{PAGE_SIZE - 1:02d}"' in body

        def it_refuses_to_continue_after_an_entry_the_tab_does_not_have(client: Client, changelog):
            changelog([_entry("fresh")])
            _login(client)
            response = client.get(reverse("hub_whats_new") + "?tab=members&after=gone", HTTP_HX_REQUEST="true")
            assert response.status_code == 404

        def it_starts_a_plain_get_at_the_top(client: Client, changelog):
            changelog([_entry("a"), _entry("b", days_ago=1)])
            _login(client)
            response = client.get(reverse("hub_whats_new") + "?after=a")
            assert "hub/whats_new.html" in [t.name for t in response.templates]
            assert 'data-whats-new-entry="a"' in response.content.decode()

    def describe_feature_mode():
        def it_opens_on_the_features_own_first_tab_with_something_unseen(client: Client, changelog):
            # A feature button (#1030) must not land on an empty For Members tab because a members entry elsewhere is unseen.
            changelog(
                [
                    _entry("cal", feature="calendar.events"),
                    _entry("events", feature="admin.events", audiences=("admins",)),
                ]
            )
            _login(client, fog_role=Member.FogRole.ADMIN)
            html = _body(client, "?feature=admin.events")
            assert 'data-whats-new-tab="admins"' in html and 'data-whats-new-entry="events"' in html
            assert html.index('class="vote-tab vote-tab--active"') < html.index('data-whats-new-entry="events"')
            assert 'aria-current="page" hx-get="/whats-new/?tab=admins&amp;feature=admin.events"' in html

        def it_limits_the_body_to_the_key_and_its_children_with_counts_and_a_title(client: Client, changelog):
            changelog(
                [
                    _entry("cubbies", feature="guild.cubbies", audiences=("members", "guild-staff")),
                    _entry("voting", feature="guild.voting"),
                    _entry("tools", feature="admin.tools", audiences=("admins",)),
                ]
            )
            _login(client)
            body = _body(client, "?feature=guild")
            assert 'data-whats-new-entry="cubbies"' in body and 'data-whats-new-entry="voting"' in body
            assert 'data-whats-new-entry="tools"' not in body
            assert "For Members (2)" in body and "For Guild Staff (1)" in body and ">For Admins<" in body
            assert ">For Instructors<" in body
            assert "What&#x27;s New · Guilds</span>" in body  # core.guidance.AREA_TITLES names an area
            assert "What&#x27;s New · Guild Cubbies</span>" in _body(client, "?feature=guild.cubbies")
            assert 'href="/whats-new/?tab=admins&amp;feature=guild"' in body

        def it_lands_on_a_tab_that_has_an_entry_for_a_feature_whose_entries_are_for_instructors(
            client: Client, changelog
        ):
            # Email Members' button for a member who is not an instructor: nothing unseen, so the first tab with
            # anything of the feature's, which is For Instructors since #1052, not an empty For Members.
            changelog([_entry("past-students", feature="admin.email-members", audiences=("instructors",))])
            _login(client)
            html = _body(client, "?feature=admin.email-members")
            assert 'data-whats-new-pane="instructors"' in html and 'data-whats-new-entry="past-students"' in html
            assert "For Instructors (1)" in html and ">For Members<" in html
            assert "data-whats-new-open" not in html  # listed for everyone, opened only by an instructor

        def it_refuses_a_key_that_is_neither_registered_nor_an_area_of_one(client: Client):
            _login(client)
            assert client.get(reverse("hub_whats_new") + "?feature=guild.nothing").status_code == 404
            assert client.get(reverse("hub_whats_new") + "?feature=Guild").status_code == 404

    def it_costs_a_fixed_number_of_queries_however_many_entries_there_are(
        client: Client, changelog, django_assert_num_queries
    ):
        member = _login(client)
        GuildMembershipFactory(member=member, guild=GuildFactory(slug="ceramics"))
        changelog([_entry("warm-up")])
        _body(client)  # the first request after login also writes the session; not what is measured
        changelog([_entry("one", feature="guild.cubbies", guidance="link")])
        with django_assert_num_queries(_BODY_QUERY_BUDGET):
            body = _body(client)  # lists one new entry, so the seen list is written
        assert body.count("data-whats-new-open") == 1
        keys = (
            "guild.cubbies",
            "guild.orientations",
            "guild.reservations",
            "guild.wiki",
            "guild.meetings",
            "guild.polls",
        )
        changelog([_entry(f"row-{i}", feature=keys[i % 6], guidance="link") for i in range(PAGE_SIZE)])
        with django_assert_num_queries(_BODY_QUERY_BUDGET):
            body = _body(client)  # a full page over six guild keys, one write, the same count
        assert body.count("data-whats-new-open") == PAGE_SIZE
        changelog(
            [_entry(f"shot-{i}", feature="guild.cubbies", guidance="shots", shots=_SHOTS) for i in range(PAGE_SIZE)]
        )
        with django_assert_num_queries(_BODY_QUERY_BUDGET):
            body = _body(client)  # a full page of steppers: a picture's URL comes from the storage, never a query
        assert body.count('data-whats-new-shots="2"') == PAGE_SIZE


def describe_the_show_arrival():
    """``?show=<key>`` on a hub page carries the entry it names for guidance.js to ring (#1051)."""

    def it_carries_the_entrys_title_and_first_bullet_for_a_key_an_entry_names(client: Client, changelog):
        changelog([_entry("open-dest", feature="home.whats-new", target="home.whats-new-button")])
        _login(client)
        html = client.get(reverse("hub_home") + "?show=home.whats-new-button").content.decode()
        assert (
            '<script id="pl-guidance-show" type="application/json">'
            '{"target": "home.whats-new-button", "title": "Open dest", "body": "open-dest changed"}</script>'
        ) in html
        # The control itself is on the page: Home's labelled What's New button carries the key.
        assert 'data-whats-new-button data-guidance-target="home.whats-new-button"' in html

    def it_carries_nothing_without_the_param_for_a_key_no_entry_names_or_outside_the_grammar(client: Client, changelog):
        changelog([_entry("open-dest", feature="home.whats-new", target="home.whats-new-button")])
        _login(client)
        for query in ("", "?show=home.door-chip", "?show=Home+Button", "?show=home"):
            assert "pl-guidance-show" not in client.get(reverse("hub_home") + query).content.decode(), query


def describe_hub_whats_new_seen():
    def it_marks_the_keys_entries_seen_and_answers_204(client: Client, changelog):
        changelog(
            [_entry("cubbies", feature="guild.cubbies"), _entry("voting", feature="guild.voting"), _entry("plain")]
        )
        member = _login(client)
        response = client.post(reverse("hub_whats_new_seen", args=["guild"]))
        assert response.status_code == 204
        assert Member.objects.get(pk=member.pk).whats_new_seen == ["cubbies", "voting"]

    def it_is_post_only(client: Client):
        _login(client)
        assert client.get(reverse("hub_whats_new_seen", args=["guild"])).status_code == 405

    def it_refuses_a_key_outside_the_grammar_or_the_registry(client: Client):
        _login(client)
        assert client.post(reverse("hub_whats_new_seen", args=["Guild Cubbies"])).status_code == 404
        assert client.post(reverse("hub_whats_new_seen", args=["guild.nothing"])).status_code == 404

    def it_sends_a_guest_home():
        MembershipPlanFactory()
        user = User.objects.create_user(username="guest", password="pass")
        user.member.status = Member.Status.GUEST
        user.member.save(update_fields=["status"])
        response = hub_whats_new_seen(_direct_request(user, method="post"), key="guild")
        assert response["Location"] == reverse("hub_home")


def describe_the_whats_new_unopened_flag():
    def _request(user, picked=None):
        request = RequestFactory().get("/")
        request.user = user
        request.view_as = ViewAs(actual=compute_actual_roles(user), picked=picked)
        return request

    def it_is_false_for_a_signed_out_visitor(rf):
        from django.contrib.auth.models import AnonymousUser

        request = rf.get("/")
        request.user = AnonymousUser()
        assert hub_sidebar(request)["whats_new_unopened"] is False

    def it_is_lazy_and_costs_one_query_when_read(changelog, django_assert_num_queries):
        changelog([_entry("fresh")])
        MembershipPlanFactory()
        user = User.objects.create_user(username="lazy", password="pass")
        user.member
        flag = hub_sidebar(_request(user))["whats_new_unopened"]
        assert isinstance(flag, SimpleLazyObject)
        with django_assert_num_queries(1):
            assert bool(flag) is True

    def it_follows_the_opened_list_and_the_preview(changelog):
        changelog([_entry("fresh")])
        MembershipPlanFactory()
        user = User.objects.create_user(username="opened", password="pass")
        user.member.fog_role = Member.FogRole.ADMIN
        user.member.whats_new_opened = ["fresh"]
        user.member.save(update_fields=["fog_role", "whats_new_opened"])
        assert bool(hub_sidebar(_request(user))["whats_new_unopened"]) is False
        changelog([_entry("fresh"), _entry("newer")])
        assert bool(hub_sidebar(_request(user))["whats_new_unopened"]) is True
        assert bool(hub_sidebar(_request(user, picked=ROLE_GUEST))["whats_new_unopened"]) is False


def describe_the_home_button():
    def it_shows_with_the_dot_while_something_is_unseen(client: Client, changelog):
        changelog([_entry("fresh")])
        _login(client)
        html = client.get(reverse("hub_home")).content.decode()
        assert "data-whats-new-button" in html
        assert "pl-whats-new-btn--unseen" in html and "data-whats-new-dot" in html
        assert 'hx-get="/whats-new/"' in html and 'hx-target="#whats-new-body"' in html
        assert 'id="whats-new-body"' in html

    def it_shows_without_the_dot_once_an_open_has_covered_everything(client: Client, changelog):
        changelog([_entry("fresh")])
        _login(client, whats_new_opened=["fresh"])
        html = client.get(reverse("hub_home")).content.decode()
        assert "data-whats-new-button" in html
        assert "pl-whats-new-btn--unseen" not in html and "data-whats-new-dot" not in html

    def it_keeps_the_dot_while_the_entry_is_only_seen_through_a_badge(client: Client, changelog):
        # A badge click marks the entry seen; the button settles only on an open of What's New.
        changelog([_entry("fresh")])
        _login(client, whats_new_seen=["fresh"])
        html = client.get(reverse("hub_home")).content.decode()
        assert "pl-whats-new-btn--unseen" in html and "data-whats-new-dot" in html

    def it_hides_from_an_admin_previewing_as_a_guest(client: Client, changelog):
        changelog([_entry("fresh")])
        _login(client, fog_role=Member.FogRole.ADMIN)
        _preview(client, ROLE_GUEST)
        assert "data-whats-new-button" not in client.get(reverse("hub_home")).content.decode()

    def it_shows_an_admin_previewing_as_a_member_the_members_dot(client: Client, changelog):
        changelog([_entry("admins-only", audiences=("admins",))])
        _login(client, fog_role=Member.FogRole.ADMIN)
        assert "data-whats-new-dot" in client.get(reverse("hub_home")).content.decode()
        _preview(client, ROLE_MEMBER)
        html = client.get(reverse("hub_home")).content.decode()
        assert "data-whats-new-button" in html and "data-whats-new-dot" not in html


def describe_the_body():
    def it_names_the_feature_on_the_button_and_lands_on_the_target(client: Client, changelog):
        # #1051: never a bare Open it; ?show=<target> rides along when the entry names a control.
        changelog(
            [
                _entry("home", feature="home.whats-new", target="home.whats-new-button"),
                _entry("code", feature="home.door-code"),
            ]
        )
        _login(client)
        body = _body(client, current=reverse("classes:teach_overview"))
        assert (
            'href="/home/?show=home.whats-new-button" class="hub-btn hub-btn--sm" data-whats-new-open>Open What&#x27;s New</a>'
            in body
        )
        assert 'href="/home/" class="hub-btn hub-btn--sm" data-whats-new-open>Open Door Code</a>' in body
        assert "Open it" not in body and "data-whats-new-show" not in body

    def it_shows_me_on_the_page_the_entry_lives_on_and_nothing_without_a_target(client: Client, changelog):
        # The modal open on the Teach page: a classes.teach entry never links back to the Teach page (#1051).
        changelog(
            [
                _entry("with-target", feature="classes.teach", target="classes.new-class"),
                _entry("without", feature="classes.teach"),
                _entry("elsewhere", feature="home.door-code"),
            ]
        )
        _login(client)
        body = _body(client, current=reverse("classes:teach_overview"))
        assert (
            '<button type="button" class="hub-btn hub-btn--sm" data-whats-new-show="classes.new-class" '
            'data-show-title="With target" data-show-body="with-target changed">Show me</button>'
        ) in body
        without = body[body.index('data-whats-new-entry="without"') : body.index('data-whats-new-entry="elsewhere"')]
        assert (
            "data-whats-new-open" not in without
            and "data-whats-new-show" not in without
            and "pl-wn-actions" not in without
        )
        assert body.count("data-whats-new-open") == 1 and 'href="/home/"' in body
        # Elsewhere the same entries link to the Teach page, the one with a target carrying it.
        away = _body(client, current=reverse("hub_home"))
        teach = reverse("classes:teach_overview")
        assert f'href="{teach}?show=classes.new-class"' in away and away.count(f'href="{teach}"') == 1
        assert "data-whats-new-show" not in away

    def it_draws_a_chip_for_a_finer_audience_on_its_tab(client: Client, changelog):
        changelog([_entry("leads", audiences=("members", "guild-leads"))])
        _login(client)
        assert '<span class="pl-wn-chip" data-whats-new-chip>Guild Leads</span>' in _body(client, "?tab=guild-staff")
        assert "data-whats-new-chip" not in _body(client, "?tab=members")

    def it_lists_an_instructors_entry_on_for_instructors_and_draws_no_instructors_chip(client: Client, changelog):
        # 1042-past-students (instructors), a members and instructors entry, and 1040-send-once (three roles).
        changelog(
            [
                _entry("past-students", audiences=("instructors",)),
                _entry("both", audiences=("members", "instructors")),
                _entry("send-once", audiences=("guild-staff", "instructors", "admins")),
            ]
        )
        _login(client)
        members = _body(client, "?tab=members")
        assert 'data-whats-new-entry="both"' in members and 'data-whats-new-entry="past-students"' not in members
        instructors = _body(client, "?tab=instructors")
        for slug in ("past-students", "both", "send-once"):
            assert f'data-whats-new-entry="{slug}"' in instructors
        assert "data-whats-new-chip" not in members and "data-whats-new-chip" not in instructors
        assert 'data-whats-new-entry="send-once"' in _body(client, "?tab=guild-staff")
        assert 'data-whats-new-entry="send-once"' in _body(client, "?tab=admins")

    def it_hides_open_it_for_a_role_the_viewer_lacks_and_shows_it_for_one_they_hold(client: Client, changelog):
        changelog([_entry("staff", feature="guild.staff", guidance="link", audiences=("guild-leads",))])
        _login(client, "plain")
        body = _body(client, "?tab=guild-staff")
        assert 'data-whats-new-entry="staff"' in body and "data-whats-new-open" not in body
        lead_client = Client()
        lead = _login(lead_client, "lead")
        GuildFactory(guild_lead=lead, slug="wood")
        body = _body(lead_client, "?tab=guild-staff")
        assert 'href="/guilds/' in body and "?tab=staff" in body and "data-whats-new-open" in body
        assert "data-whats-new-tour" not in body

    def it_says_so_on_a_tab_with_nothing(client: Client, changelog):
        changelog([_entry("fresh")])
        _login(client)
        body = _body(client, "?tab=admins")
        assert "Nothing here for admins yet." in body
        assert "data-whats-new-entry" not in body and "data-whats-new-older" not in body
        assert "Nothing here for guild staff yet." in _body(client, "?tab=guild-staff")
        assert "Nothing here for instructors yet." in _body(client, "?tab=instructors")

    def it_lists_the_registry_steps_and_the_tour_for_a_tour_entry_without_shots(client: Client, changelog):
        # A frozen entry from before #1050: its level promised teaching and it has no shots of its own.
        changelog(
            [
                _entry("tour", feature="home.tour", guidance="tour"),
                _entry("link", feature="home.polls", guidance="link"),
            ]
        )
        _login(client)
        body = _body(client)
        assert body.count("data-whats-new-steps") == 1
        assert "How it works" in body and "Take the tour banner at the top of Home" in body
        assert 'href="/home/?tour=member-welcome"' in body and "Take the tour" in body
        assert body.count("data-whats-new-open") == 2
        assert "data-whats-new-shots" not in body


def describe_the_shots_stepper():
    """An entry at shots or tour walks through its pictures (#1050); the browser's part is tests/e2e/whats_new_shots_spec.py."""

    def it_draws_every_picture_with_its_caption_the_count_the_buttons_the_list_and_the_lightbox(
        client: Client, changelog
    ):
        changelog([_entry("1050-shots", feature="home.whats-new", guidance="shots", shots=_SHOTS)])
        _login(client)
        body = _body(client)
        assert 'data-whats-new-shots="2"' in body
        assert 'Step <span x-text="step + 1">1</span> of 2' in body
        # The stepper's two pictures and the lightbox's two, every one lazy and captioned, at the storage's URL.
        assert body.count('loading="lazy"') == 4
        assert (
            'src="/media/guidance/1050-shots/step-1.png" alt="On Home, press What&#x27;s New." loading="lazy"' in body
        )
        assert 'src="/media/guidance/1050-shots/step-2.png" alt="Pick the tab for your role." loading="lazy"' in body
        assert "data-whats-new-shots-list" in body and body.count('class="pl-wn-shots__step"') == 2
        assert "data-whats-new-shots-prev" in body and "data-whats-new-shots-next" in body
        assert "data-whats-new-lightbox" in body
        assert "data-whats-new-steps" not in body  # the captions are the steps; the registry's lines stay out
        assert "data-whats-new-open" in body  # Open it still follows the entry

    def it_draws_the_stepper_at_level_tour_beside_the_tour_link(client: Client, changelog):
        changelog([_entry("tour", feature="home.tour", guidance="tour", shots=_SHOTS[:1])])
        _login(client)
        body = _body(client)
        assert 'data-whats-new-shots="1"' in body and "data-whats-new-tour" in body
        assert "data-whats-new-steps" not in body

    def it_draws_no_stepper_at_link_or_for_a_frozen_entry(client: Client, changelog):
        changelog(
            [
                _entry("link", feature="home.whats-new", guidance="link"),
                {"version": "2.0.0", "date": _day(1), "title": "Old news", "changes": ["It happened."]},
            ]
        )
        _login(client)
        body = _body(client)
        assert "data-whats-new-entry" in body
        assert "data-whats-new-shots" not in body and "data-whats-new-lightbox" not in body
