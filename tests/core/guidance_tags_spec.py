"""BDD specs for ``{% new_badge %}`` and ``{% whats_new_button %}`` (#1007, part 3).

The changelog is patched per spec (``plfog.version.CHANGELOG``, read at call time), so nothing here
depends on the repo's fragments or on the calendar moving past them.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.template import Context, Template
from django.test import RequestFactory
from django.utils import timezone

from core.guidance import title_for
from core.whats_new import NEW_DAYS
from hub.view_as import ROLE_GUEST, ViewAs, compute_actual_roles
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db

BADGE = '<span class="pl-new" data-new-key="guild.cubbies">New!</span>'


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


def _member(username: str = "viewer", **fields: Any) -> Member:
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
    request.user = user if user is not None else AnonymousUser()
    request.view_as = ViewAs(actual=compute_actual_roles(user), picked=picked)
    return request


def _render(source: str, request: Any = None) -> str:
    return Template("{% load guidance %}" + source).render(Context({"request": request}))


def describe_new_badge():
    def it_renders_the_badge_for_an_unseen_entry_in_the_viewers_audiences_inside_the_window(changelog):
        changelog([_entry("cubbies", feature="guild.cubbies")])
        assert _render('{% new_badge "guild.cubbies" %}', _request(_member().user)) == BADGE

    def it_renders_nothing_once_the_entry_is_seen(changelog):
        changelog([_entry("cubbies", feature="guild.cubbies")])
        request = _request(_member(whats_new_seen=["cubbies"]).user)
        assert _render('{% new_badge "guild.cubbies" %}', request) == ""

    def it_renders_nothing_for_an_entry_outside_the_viewers_audiences(changelog):
        changelog([_entry("cubbies", feature="guild.cubbies", audiences=("admins",))])
        assert _render('{% new_badge "guild.cubbies" %}', _request(_member().user)) == ""

    def it_renders_nothing_for_an_entry_older_than_the_window(changelog):
        changelog([_entry("cubbies", feature="guild.cubbies", days_ago=NEW_DAYS + 1)])
        assert _render('{% new_badge "guild.cubbies" %}', _request(_member().user)) == ""

    def it_covers_the_keys_under_an_area_and_never_a_sibling(changelog):
        changelog([_entry("cubbies", feature="guild.cubbies")])
        request = _request(_member().user)
        assert _render('{% new_badge "guild.voting" %}', request) == ""
        assert _render('{% new_badge "guild" %}', request) == '<span class="pl-new" data-new-key="guild">New!</span>'

    def it_renders_nothing_for_a_signed_out_visitor_or_an_admin_previewing_as_a_guest(changelog):
        changelog([_entry("cubbies", feature="guild.cubbies")])
        assert _render('{% new_badge "guild.cubbies" %}', _request(None)) == ""
        admin = _member("admin", fog_role=Member.FogRole.ADMIN)
        assert _render('{% new_badge "guild.cubbies" %}', _request(admin.user, picked=ROLE_GUEST)) == ""

    def it_renders_nothing_outside_a_request():
        assert _render('{% new_badge "guild.cubbies" %}') == ""

    def it_costs_the_roles_query_once_however_many_badges_and_buttons_a_page_holds(
        changelog, django_assert_num_queries
    ):
        changelog([_entry("cubbies", feature="guild.cubbies"), _entry("events", feature="calendar.events")])
        member = _member()
        request = _request(member.user)
        twenty = '{% new_badge "guild.cubbies" %}' * 10 + '{% new_badge "calendar.events" %}' * 10
        with django_assert_num_queries(1):
            html = _render(twenty, request)
        assert html.count("New!") == 20
        with django_assert_num_queries(0):
            html = _render(twenty + '{% whats_new_button "guild" %}{% whats_new_button labelled=True %}', request)
        assert html.count("New!") == 20 and html.count("data-whats-new-button") == 2


def describe_an_unregistered_key():
    def it_raises_under_debug(settings):
        settings.DEBUG = True
        with pytest.raises(KeyError, match="'guild.nope' is not a feature"):
            _render('{% new_badge "guild.nope" %}', _request(_member().user))

    def it_logs_and_renders_nothing_in_production(settings, caplog):
        settings.DEBUG = False
        with caplog.at_level(logging.WARNING, logger="core.templatetags.guidance"):
            assert _render('{% whats_new_button "nope.nope" %}', _request(_member().user)) == ""
        assert "unregistered key 'nope.nope'" in caplog.text


def describe_whats_new_button():
    def it_renders_the_icon_button_for_a_key_an_entry_names_and_pulses_while_one_is_unseen(changelog):
        changelog([_entry("cubbies", feature="guild.cubbies")])
        html = _render('{% whats_new_button "guild" %}', _request(_member().user))
        assert 'class="pl-whats-new-btn pl-whats-new-btn--icon pl-whats-new-btn--unseen"' in html
        assert 'hx-get="/whats-new/?feature=guild"' in html and 'hx-target="#whats-new-body"' in html
        assert 'data-whats-new-key="guild"' in html
        assert 'aria-label="What\'s new in Guilds, something is new for you"' in html
        assert "sr-only" not in html
        assert "data-whats-new-dot" in html and "What's New<" not in html

    def it_renders_still_once_everything_under_the_key_is_seen(changelog):
        changelog([_entry("cubbies", feature="guild.cubbies")])
        html = _render('{% whats_new_button "guild" %}', _request(_member(whats_new_seen=["cubbies"]).user))
        assert "data-whats-new-button" in html and 'aria-label="What\'s new in Guilds"' in html
        assert "pl-whats-new-btn--unseen" not in html and "data-whats-new-dot" not in html

    def it_renders_nothing_for_a_key_no_entry_names(changelog):
        changelog([_entry("cubbies", feature="guild.cubbies")])
        assert _render('{% whats_new_button "admin" %}', _request(_member().user)) == ""

    def it_stays_after_the_keys_entries_leave_the_window_and_stops_pulsing(changelog):
        changelog([_entry("events", feature="admin.events", days_ago=NEW_DAYS + 1, audiences=("admins",))])
        admin = _member("admin", fog_role=Member.FogRole.ADMIN)
        html = _render('{% whats_new_button "admin" %}', _request(admin.user))
        assert 'aria-label="What\'s new in Admin Tools"' in html and "pl-whats-new-btn--unseen" not in html

    def it_names_a_feature_key_by_its_registered_title(changelog):
        changelog([_entry("cubbies", feature="guild.cubbies")])
        html = _render('{% whats_new_button "guild.cubbies" %}', _request(_member().user))
        assert 'aria-label="What\'s new in Guild Cubbies, something is new for you"' in html
        assert "?feature=guild.cubbies" in html

    def it_renders_homes_labelled_button_over_every_entry_with_no_key(changelog):
        changelog([_entry("fresh")])
        html = _render("{% whats_new_button labelled=True %}", _request(_member().user))
        assert 'hx-get="/whats-new/"' in html and "pl-whats-new-btn--icon" not in html
        assert "What's New<" in html and "data-whats-new-key" not in html and "data-whats-new-dot" in html
        assert '<span class="sr-only" data-whats-new-sr>, something is new for you</span>' in html

    def it_renders_nothing_for_a_viewer_the_modal_is_not_for(changelog):
        changelog([_entry("fresh")])
        assert _render("{% whats_new_button labelled=True %}", _request(None)) == ""
        guest = _member("guest", status=Member.Status.GUEST)
        assert _render("{% whats_new_button labelled=True %}", _request(guest.user)) == ""


def describe_title_for():
    @pytest.mark.parametrize(
        ("key", "title"),
        [
            ("guild.cubbies", "Guild Cubbies"),
            ("admin", "Admin Tools"),
            ("guild", "Guilds"),
            ("home", "Home"),
            ("classes", "Classes"),
            ("feedback", "Feedback"),
            ("account.door-code", "Door Code"),
        ],
    )
    def it_names_a_feature_by_its_title_and_an_area_by_its_own_name(key, title):
        assert title_for(key) == title
