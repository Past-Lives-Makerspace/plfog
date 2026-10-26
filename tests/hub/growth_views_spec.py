"""Specs for the admin Growth page at /manage/growth/ and its Admin Tools card (#795)."""

from __future__ import annotations

import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from classes.factories import RegistrationFactory
from classes.models import CmsActivity
from hub.growth import LAUNCH, LEGACY_SIGNUP_SOURCE
from tests.membership.factories import GuildFactory, MemberFactory

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("spring_launch")]

URL = reverse("hub_admin_growth")
PORTLAND = ZoneInfo("America/Los_Angeles")


def _login_admin(client: Client, username: str = "growth_admin") -> User:
    user = User.objects.create_superuser(username=username, password="pass", email=f"{username}@example.com")
    client.login(username=username, password="pass")
    return user


def _login_member(client: Client, username: str = "growth_member") -> User:
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    client.login(username=username, password="pass")
    return user


def _preview_as(client: Client, role: str) -> None:
    session = client.session
    session["view_as_role"] = role
    session.save()


def _recent(days_ago: int) -> datetime:
    day = timezone.localdate().toordinal() - days_ago
    return datetime.combine(date.fromordinal(day), time(12), tzinfo=PORTLAND)


def _seed(rows: int) -> None:
    """Signups, members and activity in the last few weeks, one signup from the old class site."""
    for i in range(rows):
        member = MemberFactory(full_legal_name=f"Zebulon Quartermaine {i}")
        type(member).objects.filter(pk=member.pk).update(created_at=_recent(i % 20))
        registration = RegistrationFactory(member=member, email=f"zebulon{i}@example.com")
        type(registration).objects.filter(pk=registration.pk).update(registered_at=_recent(i % 20))
        activity = CmsActivity.objects.create(kind=CmsActivity.Kind.CLASS_SUBMITTED)
        CmsActivity.objects.filter(pk=activity.pk).update(created_at=_recent(i % 20))
    old = RegistrationFactory(email="oldsite@example.com")
    CmsActivity.objects.create(
        kind=CmsActivity.Kind.REGISTRATION_CREATED, registration=old, payload={"source": LEGACY_SIGNUP_SOURCE}
    )


def describe_access():
    def it_sends_a_signed_out_visitor_to_log_in(client: Client):
        response = client.get(URL)
        assert response.status_code == 302
        assert "login" in response.url

    def it_refuses_a_member(client: Client):
        _login_member(client)
        assert client.get(URL).status_code == 403

    def it_refuses_a_guild_lead(client: Client):
        user = _login_member(client, "growth_lead")
        GuildFactory(guild_lead=user.member)  # type: ignore[attr-defined]
        assert client.get(URL).status_code == 403

    def it_refuses_an_admin_previewing_as_a_member(client: Client):
        _login_admin(client)
        _preview_as(client, "member")
        assert client.get(URL).status_code == 403

    def it_shows_an_admin_the_page(client: Client):
        _login_admin(client)
        response = client.get(URL)
        assert response.status_code == 200
        assert response.templates[0].name == "hub/admin/growth.html"


def describe_page():
    def it_shows_the_last_90_days_by_default(client: Client):
        _login_admin(client)
        body = client.get(URL).content.decode()
        assert re.search(r'data-preset="90" aria-current="true"', body)
        assert "(90 days). Each tile compares it with the 90 days before" in body
        assert body.count("data-tile>") == 5
        assert 'data-mode="week" aria-current="true"' in body
        assert '<option value="cms_actions" selected>' in body

    def it_counts_the_seeded_rows_and_leaves_the_old_site_signup_to_the_source_card(client: Client):
        _seed(3)
        _login_admin(client)
        body = client.get(URL).content.decode()
        assert re.search(r"data-source-foot>4 signups in this range: 3 in the new system \(0 guests, 3 members\)", body)
        assert "and 1 imported from the old system." in body

    def it_shows_no_personal_data(client: Client):
        _seed(2)
        _login_admin(client)
        body = client.get(URL).content.decode()
        assert "Zebulon" not in body
        assert "zebulon0@example.com" not in body
        assert "oldsite@example.com" not in body

    def it_keeps_the_range_in_the_metric_and_mode_controls(client: Client):
        _login_admin(client)
        body = client.get(URL, {"range": "custom", "date_from": "2026-05-04", "date_to": "2026-05-31"}).content.decode()
        assert (
            'href="?range=custom&amp;date_from=2026-05-04&amp;date_to=2026-05-31&amp;metric=cms_actions&amp;mode=month"'
            in body
        )
        assert '<input type="hidden" name="date_from" value="2026-05-04">' in body
        assert 'data-preset="custom" aria-current="true"' in body
        assert "growth-cms-actions-2026-05-04-to-2026-05-31.png" in body

    def it_offers_since_launch_and_since_cms_launch(client: Client, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("hub.growth.LAUNCH", LAUNCH)  # the real launch, too close to compare with
        _login_admin(client)
        body = client.get(URL, {"range": "cms"}).content.decode()
        assert ">Since Launch</a>" in body
        assert 'data-preset="cms" aria-current="true">Since CMS Launch</a>' in body
        assert "Too little before the CMS launch to compare with." in body
        assert body.count(">Since CMS launch</span>") == 5

    def it_names_the_launch_day_the_page_counts_from(client: Client, spring_launch: date):
        _login_admin(client)
        assert "since it went live on March 27, 2026." in client.get(URL).content.decode()

    def it_keeps_the_metric_and_mode_in_the_preset_links(client: Client):
        _login_admin(client)
        body = client.get(URL, {"metric": "new_members", "mode": "total"}).content.decode()
        assert 'href="?range=30&amp;metric=new_members&amp;mode=total"' in body
        assert "New Members, Running Total" in body

    def it_shows_a_wrong_range_s_error_on_to(client: Client):
        _login_admin(client)
        body = client.get(URL, {"range": "custom", "date_from": "2026-05-31", "date_to": "2026-05-04"}).content.decode()
        assert "To is before From. Pick a later day." in body

    def it_draws_every_chart_at_both_sizes_with_a_download(client: Client):
        _login_admin(client)
        body = client.get(URL).content.decode()
        assert body.count("pl-growth__svg--wide") == 3
        assert body.count("pl-growth__svg--narrow") == 3
        assert 'data-chart-download="growth-cms-activity.png"' in body
        assert 'data-chart-download="growth-signup-source.png"' in body
        assert "js/chart_download" in body

    def it_leaves_out_google_analytics_for_now(client: Client):
        _login_admin(client)
        assert "pl-growth__ga" not in client.get(URL).content.decode()


def describe_query_count():
    def it_does_not_grow_with_the_rows(client: Client, django_assert_num_queries):
        _login_admin(client)
        _seed(1)
        client.get(URL)  # warm the per-request caches the hub shell reads
        with django_assert_num_queries(QUERIES):
            client.get(URL)
        _seed(15)
        with django_assert_num_queries(QUERIES):
            client.get(URL)


#: The whole page as an admin sees it: the hub shell's reads plus the dashboard's aggregates. 35 with the Holiday
#: Bizarre sidebar entry (HQ card sharing): one EXISTS over the HQs for an admin. 36 with the Spotlight's live
#: announcements (#890), its second query. 34 since #935: the sidebar's Spotlight is a static card that reads nothing.
QUERIES = 34


def describe_admin_tools_card():
    def it_links_an_admin_to_the_page(client: Client):
        _login_admin(client)
        assert URL in client.get(reverse("hub_admin_tools")).content.decode()

    def it_is_hidden_from_a_guild_lead(client: Client):
        user = _login_member(client, "card_lead")
        GuildFactory(guild_lead=user.member)  # type: ignore[attr-defined]
        assert URL not in client.get(reverse("hub_admin_tools")).content.decode()
