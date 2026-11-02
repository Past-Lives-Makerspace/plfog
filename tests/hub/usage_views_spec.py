"""BDD specs for the admin Usage page (#1078): admin only, counts by page and role, range and role filters, activity by kind."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import PageView, SiteActivity

pytestmark = pytest.mark.django_db

_URL = reverse("hub_admin_usage")


@pytest.fixture()
def admin_client(client: Client) -> Client:
    client.force_login(User.objects.create_superuser("usage_admin", "usage_admin@example.com", "pass"))
    return client


def _view(user: User, view_name: str, *, role: str = PageView.Role.MEMBER, days_ago: int = 1) -> None:
    PageView.objects.create(
        user=user,
        view_name=view_name,
        role=role,
        surface=PageView.Surface.MEMBERS,
        viewed_at=timezone.now() - timedelta(days=days_ago),
    )


def _people() -> tuple[User, User]:
    return (
        User.objects.create_user("zelda_quartz", "zelda.quartz@example.com", first_name="Zelda", last_name="Quartz"),
        User.objects.create_user("yuri_basalt", "yuri.basalt@example.com", first_name="Yuri", last_name="Basalt"),
    )


def _pages(response) -> dict[str, tuple[int, int]]:
    """Counts by page, leaving out the admin's own earlier visits to this page, which the log records too."""
    rows = response.context["board"].pages
    return {row.view_name: (row.views, row.people) for row in rows if row.view_name != "hub_admin_usage"}


def describe_access():
    def it_is_admin_only(client: Client):
        User.objects.create_user("plain", "plain@example.com", "pass")
        client.login(username="plain", password="pass")
        assert client.get(_URL).status_code == 403

    def it_refuses_an_admin_previewing_as_a_member(admin_client: Client):
        session = admin_client.session
        session["view_as_role"] = "member"
        session.save()
        assert admin_client.get(_URL).status_code == 403

    def it_sends_an_anonymous_visitor_to_sign_in(client: Client):
        assert client.get(_URL).status_code == 302


def describe_counts():
    def it_counts_views_and_distinct_people_by_page(admin_client: Client):
        zelda, yuri = _people()
        _view(zelda, "hub_home")
        _view(zelda, "hub_home")
        _view(yuri, "hub_home")
        _view(yuri, "hub_guilds")

        response = admin_client.get(_URL)

        assert response.status_code == 200
        assert _pages(response) == {"hub_home": (3, 2), "hub_guilds": (1, 1)}
        assert response.context["board"].least_used == []  # two pages: nothing beyond the Pages list
        assert "4 page views by 2 people" in response.content.decode()

    def it_shows_a_readable_name_with_the_view_name_under_it(admin_client: Client):
        zelda, _yuri = _people()
        _view(zelda, "classes:admin_class_detail")
        html = admin_client.get(_URL).content.decode()
        assert 'Class detail<span class="pl-usage__view-name">classes:admin_class_detail</span>' in html

    def it_lists_the_top_fifteen_and_the_bottom_eight_only_past_fifteen(admin_client: Client):
        zelda, _yuri = _people()
        for rank in range(18):  # page_00 is viewed 18 times, page_17 once
            for _ in range(18 - rank):
                _view(zelda, f"page_{rank:02d}")

        board = admin_client.get(_URL).context["board"]

        assert [row.view_name for row in board.pages] == [f"page_{rank:02d}" for rank in range(15)]
        assert board.page_count == 18  # this page's own visit is written after it renders
        assert [row.view_name for row in board.least_used] == [f"page_{rank:02d}" for rank in range(17, 9, -1)]

    def it_shows_no_least_used_card_with_fifteen_pages_or_fewer(admin_client: Client):
        zelda, _yuri = _people()
        _view(zelda, "hub_home")
        assert "data-usage-least-card" not in admin_client.get(_URL).content.decode()

    def it_names_no_one(admin_client: Client):
        zelda, _yuri = _people()
        _view(zelda, "hub_home")
        html = admin_client.get(_URL).content.decode()
        assert "Zelda" not in html and "Quartz" not in html and "zelda.quartz" not in html

    def it_filters_by_range(admin_client: Client):
        zelda, _yuri = _people()
        _view(zelda, "hub_home", days_ago=3)
        _view(zelda, "hub_guilds", days_ago=40)

        assert _pages(admin_client.get(_URL, {"range": "7"})) == {"hub_home": (1, 1)}
        assert _pages(admin_client.get(_URL)) == {"hub_home": (1, 1)}  # 30 days by default
        assert _pages(admin_client.get(_URL, {"range": "90"})) == {"hub_home": (1, 1), "hub_guilds": (1, 1)}

    def it_filters_by_role(admin_client: Client):
        zelda, yuri = _people()
        _view(zelda, "hub_home", role=PageView.Role.INSTRUCTOR)
        _view(yuri, "hub_guilds", role=PageView.Role.MEMBER)

        response = admin_client.get(_URL, {"role": "instructor"})

        assert _pages(response) == {"hub_home": (1, 1)}
        assert 'data-role="instructor" aria-current="true"' in response.content.decode()

    def it_falls_back_to_the_defaults_for_an_unknown_range_or_role(admin_client: Client):
        response = admin_client.get(_URL, {"range": "365", "role": "wizard"})
        assert (response.context["range_key"], response.context["role"]) == ("30", "")

    def it_counts_the_activity_log_by_kind_per_week(admin_client: Client):
        zelda, yuri = _people()
        SiteActivity.log(SiteActivity.Kind.LOGIN, actor=zelda)
        SiteActivity.log(SiteActivity.Kind.LOGIN, actor=yuri)
        SiteActivity.log(SiteActivity.Kind.VOTE_SUBMITTED, actor=yuri)

        activity = admin_client.get(_URL).context["board"].activity

        totals = {row.label: row.total for row in activity.rows}
        assert totals["Logged in"] == SiteActivity.objects.filter(kind=SiteActivity.Kind.LOGIN).count() >= 2
        assert totals["Submitted vote"] == 1
        assert sum(row.total for row in activity.rows) == SiteActivity.objects.count()
