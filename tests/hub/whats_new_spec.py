"""BDD specs for What's New on the hub (#1007, part 2): the view, the seen POST, the Home button and the body.

The changelog is patched per spec (``plfog.version.CHANGELOG``, read at call time), so nothing here
depends on the repo's fragments or on the calendar.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import SimpleLazyObject

from hub.context_processors import hub_sidebar
from hub.view_as import SESSION_ROLE_KEY, ROLE_GUEST, ROLE_MEMBER, ViewAs, compute_actual_roles
from hub.whats_new_views import hub_whats_new, hub_whats_new_seen
from membership.models import Member
from tests.membership.factories import GuildFactory, GuildMembershipFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

#: Queries one htmx GET of the body costs for a member in a guild, with every entry on one guild tab:
#: the session, the user, the member, View As, every context processor the hub runs for a partial,
#: the roles query, the guild lookup behind Open it (once per feature key) and the seen write.
#: Never one per entry.
_BODY_QUERY_BUDGET = 23


def _day(days_ago: int = 0) -> str:
    return (timezone.localdate() - dt.timedelta(days=days_ago)).isoformat()


def _entry(slug: str, *, days_ago: int = 0, audiences: tuple[str, ...] = ("members",), **keys: Any) -> dict[str, Any]:
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


def _body(client: Client, query: str = "") -> str:
    response = client.get(reverse("hub_whats_new") + query, HTTP_HX_REQUEST="true")
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

    def it_answers_an_htmx_request_with_the_body_alone(client: Client, changelog):
        changelog([_entry("fresh")])
        _login(client)
        body = _body(client)
        assert "<html" not in body
        assert body.count("data-whats-new-tab=") == 3
        assert 'data-whats-new-tab="members"' in body and 'aria-current="page"' in body
        assert '<span id="whats-new-title" hx-swap-oob="innerHTML">What&#x27;s New</span>' in body
        assert 'data-whats-new-entry="fresh"' in body

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

    def describe_the_tab():
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
        def it_marks_every_entry_the_shown_tab_listed_and_no_other(client: Client, changelog):
            changelog([_entry("a"), _entry("b", days_ago=1), _entry("staff", audiences=("guild-staff",))])
            member = _login(client)
            _body(client)
            assert Member.objects.get(pk=member.pk).whats_new_seen == ["a", "b"]
            _body(client, "?tab=guild-staff")
            assert Member.objects.get(pk=member.pk).whats_new_seen == ["a", "b", "staff"]

        def it_leaves_the_dot_off_on_the_next_home_load_until_a_new_entry_arrives(client: Client, changelog):
            changelog([_entry("a")])
            _login(client)
            assert "data-whats-new-dot" in client.get(reverse("hub_home")).content.decode()
            _body(client)
            assert "data-whats-new-dot" not in client.get(reverse("hub_home")).content.decode()
            changelog([_entry("b"), _entry("a")])
            assert "data-whats-new-dot" in client.get(reverse("hub_home")).content.decode()

    def describe_feature_mode():
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
            assert "What&#x27;s New · Guild</span>" in body
            assert "What&#x27;s New · Guild Cubbies</span>" in _body(client, "?feature=guild.cubbies")
            assert 'href="/whats-new/?tab=admins&amp;feature=guild"' in body

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
        changelog([_entry(f"row-{i}", feature="guild.cubbies", guidance="link") for i in range(12)])
        with django_assert_num_queries(_BODY_QUERY_BUDGET):
            body = _body(client)  # twelve new entries, one write, the same count
        assert body.count("data-whats-new-open") == 12


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

    def it_refuses_a_key_outside_the_grammar(client: Client):
        _login(client)
        assert client.post(reverse("hub_whats_new_seen", args=["Guild Cubbies"])).status_code == 404

    def it_sends_a_guest_home():
        MembershipPlanFactory()
        user = User.objects.create_user(username="guest", password="pass")
        user.member.status = Member.Status.GUEST
        user.member.save(update_fields=["status"])
        response = hub_whats_new_seen(_direct_request(user, method="post"), key="guild")
        assert response["Location"] == reverse("hub_home")


def describe_the_whats_new_unseen_flag():
    def _request(user, picked=None):
        request = RequestFactory().get("/")
        request.user = user
        request.view_as = ViewAs(actual=compute_actual_roles(user), picked=picked)
        return request

    def it_is_false_for_a_signed_out_visitor(rf):
        from django.contrib.auth.models import AnonymousUser

        request = rf.get("/")
        request.user = AnonymousUser()
        assert hub_sidebar(request)["whats_new_unseen"] is False

    def it_is_lazy_and_costs_one_query_when_read(changelog, django_assert_num_queries):
        changelog([_entry("fresh")])
        MembershipPlanFactory()
        user = User.objects.create_user(username="lazy", password="pass")
        user.member
        flag = hub_sidebar(_request(user))["whats_new_unseen"]
        assert isinstance(flag, SimpleLazyObject)
        with django_assert_num_queries(1):
            assert bool(flag) is True

    def it_follows_the_seen_list_and_the_preview(changelog):
        changelog([_entry("fresh")])
        MembershipPlanFactory()
        user = User.objects.create_user(username="seen", password="pass")
        user.member.fog_role = Member.FogRole.ADMIN
        user.member.whats_new_seen = ["fresh"]
        user.member.save(update_fields=["fog_role", "whats_new_seen"])
        assert bool(hub_sidebar(_request(user))["whats_new_unseen"]) is False
        changelog([_entry("fresh"), _entry("newer")])
        assert bool(hub_sidebar(_request(user))["whats_new_unseen"]) is True
        assert bool(hub_sidebar(_request(user, picked=ROLE_GUEST))["whats_new_unseen"]) is False


def describe_the_home_button():
    def it_shows_with_the_dot_while_something_is_unseen(client: Client, changelog):
        changelog([_entry("fresh")])
        _login(client)
        html = client.get(reverse("hub_home")).content.decode()
        assert "data-whats-new-button" in html
        assert "pl-whats-new-btn--unseen" in html and "data-whats-new-dot" in html
        assert 'hx-get="/whats-new/"' in html and 'hx-target="#whats-new-body"' in html
        assert 'id="whats-new-body"' in html

    def it_shows_without_the_dot_once_everything_is_seen(client: Client, changelog):
        changelog([_entry("fresh")])
        _login(client, whats_new_seen=["fresh"])
        html = client.get(reverse("hub_home")).content.decode()
        assert "data-whats-new-button" in html
        assert "pl-whats-new-btn--unseen" not in html and "data-whats-new-dot" not in html

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
    def it_draws_a_chip_for_a_finer_audience_on_its_tab(client: Client, changelog):
        changelog([_entry("leads", audiences=("members", "guild-leads"))])
        _login(client)
        assert '<span class="pl-wn-chip" data-whats-new-chip>Guild Leads</span>' in _body(client, "?tab=guild-staff")
        assert "data-whats-new-chip" not in _body(client, "?tab=members")

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
        assert "Nothing new for admins in the last 30 days." in body
        assert "data-whats-new-entry" not in body

    def it_lists_the_steps_at_level_clip_and_the_tour_at_level_tour(client: Client, changelog):
        changelog(
            [
                _entry("clip", feature="home.door-code", guidance="clip"),
                _entry("tour", feature="home.tour", guidance="tour"),
                _entry("link", feature="home.polls", guidance="link"),
            ]
        )
        _login(client)
        body = _body(client)
        assert body.count("data-whats-new-steps") == 2
        assert "How it works" in body and "Press Tap to reveal to see the code" in body
        assert 'href="/home/?tour=member-welcome"' in body and "Take the tour" in body
        assert body.count("data-whats-new-open") == 3
