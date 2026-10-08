"""The version pill opens the "Building with you" panel on hub pages, and only there (#699)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

import hub.context_processors
from core.building_with_you import BuildingWithYou
from core.models import SiteConfiguration
from membership.models import CommunityEvent, Member
from membership.services.provisioning import provision_user_for_member
from tests.membership.factories import CommunityEventFactory, MemberFactory

pytestmark = pytest.mark.django_db

# Old enough that no release makes the pill loud on its own; a spec that wants a fresh one says so.
QUIET_CHANGELOG: list[dict[str, Any]] = [
    {"title": "Zorblax shipped first", "date": "2001-01-03", "changes": ["One"], "slug": "zorblax-1"},
    {"title": "Zorblax shipped second", "date": "2001-01-02", "changes": ["Two"], "slug": "zorblax-2"},
    {"title": "Zorblax shipped third", "date": "2001-01-01", "changes": ["Three"], "slug": "zorblax-3"},
    {"title": "Zorblax shipped fourth", "date": "2000-12-31", "changes": ["Four"], "slug": "zorblax-4"},
]


@pytest.fixture(autouse=True)
def _quiet_changelog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("plfog.version.CHANGELOG", QUIET_CHANGELOG)


def _member_client(status: str = Member.Status.ACTIVE) -> Client:
    member = MemberFactory()
    provision_user_for_member(member)
    member.refresh_from_db()
    if member.status != status:
        member.status = status
        member.save(update_fields=["status"])
    client = Client()
    client.force_login(member.user)
    return client


def _meeting(starts_in: timedelta = timedelta(days=10), **kwargs: Any) -> CommunityEvent:
    start = timezone.now() + starts_in
    defaults: dict[str, Any] = {
        "community": True,
        "title": "Zorblax Feature Meeting",
        "starts_at": start,
        "ends_at": start + timedelta(hours=1),
        "recurrence": CommunityEvent.Recurrence.NONE,
        "video_url": "https://meet.example.org/zorblax",
    }
    defaults.update(kwargs)
    return CommunityEventFactory(**defaults)


def _set(**fields: Any) -> None:
    config = SiteConfiguration.load()
    for name, value in fields.items():
        setattr(config, name, value)
    config.save()


def _home(client: Client | None = None) -> str:
    return (client or _member_client()).get(reverse("hub_home")).content.decode()


def describe_the_panel_on_a_hub_page():
    def it_shows_the_meeting_with_its_links():
        event = _meeting()
        _set(feature_meeting_event=event)

        html = _home()

        assert 'id="bwy-meeting"' in html
        assert "Zorblax Feature Meeting" in html
        assert 'href="https://meet.example.org/zorblax"' in html
        assert f'href="/events/{event.pk}/event.ics"' in html
        assert f'href="/events/{event.pk}/"' in html

    def it_shows_the_meeting_time_in_portland():
        start = timezone.now() + timedelta(days=10)
        _set(feature_meeting_event=_meeting(starts_in=start - timezone.now()))

        html = _home()

        assert timezone.localtime(start).strftime("%a %b %-d, %-I:%M") in html

    def it_leaves_out_join_online_without_a_video_link():
        _set(feature_meeting_event=_meeting(video_url=""))

        html = _home()

        assert 'id="bwy-meeting"' in html
        assert 'id="bwy-join"' not in html

    def it_has_no_meeting_block_with_nothing_picked():
        assert 'id="bwy-meeting"' not in _home()

    def it_has_no_meeting_block_when_the_meeting_is_over():
        _set(feature_meeting_event=_meeting(starts_in=-timedelta(days=3)))

        assert 'id="bwy-meeting"' not in _home()

    def it_asks_with_feature_request_preselected():
        html = _home()

        assert f'href="{reverse("hub_beta_feedback")}?category=feature"' in html

    def it_lists_being_built_now_escaped():
        _set(being_built_now="Zorblax kiln queue\n\n<b>Zorblax bold</b>\n")

        html = _home()

        assert 'id="bwy-building"' in html
        assert "<li>Zorblax kiln queue</li>" in html
        assert "<li>&lt;b&gt;Zorblax bold&lt;/b&gt;</li>" in html

    def it_hides_being_built_now_when_empty():
        _set(being_built_now="")

        assert 'id="bwy-building"' not in _home()

    def it_lists_the_three_newest_releases_and_reveals_the_rest_in_place():
        html = _home()
        panel, everything = html.split('id="changelog-all"', 1)

        assert "Zorblax shipped third" in panel.split('id="bwy-shipped"', 1)[1]
        assert "Zorblax shipped fourth" not in panel
        assert 'x-show="showAll"' in everything.split(">", 1)[0]
        assert 'id="changelog-zorblax-4"' in everything
        assert 'id="bwy-see-all"' in panel

    def it_links_the_backlog_in_a_new_tab():
        html = _home()

        assert (
            '<a href="https://github.com/orgs/Past-Lives-Makerspace/projects/1" target="_blank" '
            'rel="noopener noreferrer" id="bwy-backlog">'
        ) in html

    def it_hides_the_backlog_link_when_blank():
        _set(backlog_url="")

        assert 'id="bwy-backlog"' not in _home()

    def it_offers_whats_being_built_in_the_phone_profile_menu():
        html = _home()

        assert 'class="pl-profile__dropdown-item pl-profile__dropdown-mobile-item" id="whats-being-built"' in html
        assert html.index('id="whats-being-built"') < html.index(reverse("hub_user_settings"))


def describe_the_news_dot():
    def it_is_off_with_no_news():
        html = _home()

        assert 'id="menu-news-dot"' not in html
        assert "pl-badge--version-new" not in html

    def it_is_on_when_the_meeting_is_within_72_hours():
        _set(feature_meeting_event=_meeting(starts_in=timedelta(hours=71)))

        html = _home()

        assert 'id="menu-news-dot"' in html
        assert "pl-badge--version-new" in html
        assert " · new</button>" in html

    def it_is_off_when_the_meeting_is_further_out():
        _set(feature_meeting_event=_meeting(starts_in=timedelta(hours=73)))

        assert 'id="menu-news-dot"' not in _home()

    def it_is_on_when_a_release_shipped_today(monkeypatch: pytest.MonkeyPatch):
        today = timezone.localdate().isoformat()
        monkeypatch.setattr("plfog.version.CHANGELOG", [{**QUIET_CHANGELOG[0], "date": today}])

        assert 'id="menu-news-dot"' in _home()


def describe_where_the_panel_is_not():
    def it_leaves_the_login_page_plain():
        html = Client().get("/accounts/login/").content.decode()

        assert 'id="changelog-modal"' in html
        assert 'id="building-with-you"' not in html

    def it_leaves_the_unfold_admin_plain():
        User.objects.create_superuser(username="bwyadmin", email="bwyadmin@x.com", password="p")
        client = Client()
        client.login(username="bwyadmin", password="p")

        html = client.get("/admin/").content.decode()

        assert 'id="changelog-modal"' in html
        assert 'id="building-with-you"' not in html

    def it_leaves_a_guest_account_on_the_plain_changelog():
        _set(kiln_tickets_open=True, feature_meeting_event=_meeting(starts_in=timedelta(hours=5)))
        client = _member_client(status=Member.Status.GUEST)

        html = client.get(reverse("kiln:mine")).content.decode()

        assert 'id="changelog-modal"' in html
        assert 'id="building-with-you"' not in html
        assert 'id="whats-being-built"' not in html
        assert 'id="menu-news-dot"' not in html


def describe_query_count():
    def it_costs_one_query_for_the_panel_and_its_meeting(monkeypatch: pytest.MonkeyPatch):
        _set(feature_meeting_event=_meeting(), being_built_now="Zorblax kiln queue")
        client = _member_client()
        _home(client)  # warm per-session caches so both renders below do the same work

        with CaptureQueriesContext(connection) as with_panel:
            _home(client)
        prebuilt = SiteConfiguration.load_with_feature_meeting().building_with_you(timezone.now())

        def _prebuilt() -> BuildingWithYou:
            return prebuilt

        monkeypatch.setattr(hub.context_processors, "_building_with_you", _prebuilt)
        with CaptureQueriesContext(connection) as without_panel:
            _home(client)

        assert len(with_panel) - len(without_panel) == 1
