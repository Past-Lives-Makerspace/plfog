"""The member Spotlight on hub pages (#709): the static version card (#935), Details, who sees it, and its cost."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.factories import AnnouncementFactory
from core.models import SiteConfiguration
from membership.models import Member
from membership.services.provisioning import provision_user_for_member
from plfog.version import VERSION
from tests.membership.factories import CommunityEventFactory, MemberFactory
from tests.polls.factories import poll_with

pytestmark = pytest.mark.django_db

HOME = reverse("hub_home")
FEEDBACK = reverse("hub_beta_feedback")
#: The sidebar's version pill from before the Spotlight, which guests and former members keep.
BACKUP_PILL = 'class="pl-badge--version" onclick'
LATEST = {"title": "Zorblax latest", "date": "2026-10-07", "changes": ["One"], "slug": "701-zorblax"}


def _client_for(status: str = Member.Status.ACTIVE) -> tuple[Client, Member]:
    member = MemberFactory()
    provision_user_for_member(member)
    member.refresh_from_db()
    if member.status != status:
        member.status = status
        member.save(update_fields=["status"])
    client = Client()
    client.force_login(member.user)
    return client, member


def _sidebar_card(html: str) -> str:
    return html.split('class="hub-sidebar__spotlight"', 1)[1].split("hub-sidebar__nav", 1)[0]


def _panel(html: str) -> str:
    return html.split("data-spotlight-panel", 1)[1]


def describe_the_version_card():
    @pytest.mark.parametrize("url", [HOME, FEEDBACK])
    def it_shows_the_version_pill_the_update_date_and_details_on_every_hub_page(
        url: str, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr("plfog.version.CHANGELOG", [LATEST])
        client, _member = _client_for()

        card = _sidebar_card(client.get(url).content.decode())

        short = ".".join(VERSION.split(".")[:2])
        assert 'data-spotlight data-spotlight-state="quiet"' in card
        assert f"data-spotlight-quiet-version>v{short}</button>" in card
        assert 'title="Oct 7, 2026" data-spotlight-quiet-date>Updated Oct 7</span>' in card
        assert (
            card.index("data-spotlight-quiet-version")
            < card.index("data-spotlight-quiet-date")
            < card.index("data-spotlight-details")
        )
        assert card.count("<button") == 2  # the version pill and Details
        assert "Zorblax latest" not in card

    def it_leaves_out_the_date_with_an_empty_changelog(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("plfog.version.CHANGELOG", [])
        client, _member = _client_for()

        card = _sidebar_card(client.get(FEEDBACK).content.decode())

        assert "data-spotlight-quiet-version" in card
        assert "data-spotlight-quiet-date" not in card

    def it_never_carries_a_message_a_meeting_or_a_minimized_state():
        client, _member = _client_for()
        AnnouncementFactory(body="<p>Zorblax live notice</p>")
        start = timezone.now() + timedelta(days=5)
        CommunityEventFactory(
            community=True, title="Zorblax Feature Request Meeting", starts_at=start, ends_at=start + timedelta(hours=1)
        )

        card = _sidebar_card(client.get(FEEDBACK).content.decode())

        assert "Zorblax live notice" not in card
        assert "Zorblax Feature Request Meeting" not in card
        for gone in (
            "data-spotlight-announcements",
            "data-spotlight-meeting",
            'data-spotlight-state="standard"',
            'data-spotlight-state="minimized"',
            "data-spotlight-minimize",
            "data-spotlight-dot",
            "data-spotlight-signature",
        ):
            assert gone not in card

    def it_carries_no_poll_even_while_one_is_open():
        # #925 AC 5: polls came back on Home and guild pages, never in the Spotlight.
        client, _member = _client_for()
        poll_with("Laser", "Lathe", question="Zorblax spotlight poll?")

        card = _sidebar_card(client.get(HOME).content.decode())

        assert "data-poll-card" not in card
        assert "Zorblax spotlight poll?" not in card

    def it_replaces_the_version_number_and_sits_above_the_divider():
        client, _member = _client_for()

        html = client.get(FEEDBACK).content.decode()

        assert BACKUP_PILL not in html
        assert 'id="changelog-modal"' not in html
        assert 'class="pl-brand pl-brand--quiet"' in html
        assert 'class="hub-sidebar__spotlight"' in html

    def it_has_no_copy_at_the_top_of_home_on_phones():
        client, _member = _client_for()

        html = client.get(HOME).content.decode()

        assert "data-spotlight-home" not in html
        assert "pl-spotlight-home" not in html

    def it_sets_nothing_before_the_first_paint_and_loads_its_store_before_alpine():
        client, _member = _client_for()

        head = client.get(FEEDBACK).content.decode().split("</head>", 1)[0]

        assert "plSpotlightMinimized" not in head
        assert "spotlightMin" not in head
        assert head.index("js/spotlight.js") < head.index("js/alpine.min.js")


def describe_details():
    def it_shows_the_latest_update_then_have_an_idea_then_the_version(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("plfog.version.CHANGELOG", [LATEST])
        client, _member = _client_for()

        panel = _panel(client.get(FEEDBACK).content.decode())

        short = ".".join(VERSION.split(".")[:2])
        assert 'href="#changelog-701-zorblax"' in panel
        assert (
            'data-anchor="changelog-701-zorblax" @click.prevent="$store.spotlight.showChange($el.dataset.anchor)"'
            in panel
        )
        assert ">Zorblax latest</a>" in panel
        assert f'href="{FEEDBACK}?category=feature"' in panel
        assert f'href="{FEEDBACK}?category=bug"' in panel
        assert f"Version {short} · What Changed" in panel
        order = [
            panel.index(marker)
            for marker in ("data-spotlight-panel-update", "data-spotlight-suggest", "data-spotlight-version")
        ]
        assert order == sorted(order)

    def it_leaves_out_the_update_with_an_empty_changelog(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("plfog.version.CHANGELOG", [])
        client, _member = _client_for()

        panel = _panel(client.get(FEEDBACK).content.decode())

        assert "data-spotlight-panel-update" not in panel
        assert "data-spotlight-suggest" in panel

    def it_shows_no_spotlight_messages_and_no_meeting():
        client, _member = _client_for()
        AnnouncementFactory(body="<p>Zorblax panel notice</p>")

        panel = _panel(client.get(FEEDBACK).content.decode())

        assert "data-spotlight-panel-announcements" not in panel
        assert "data-spotlight-panel-meeting" not in panel
        assert "Zorblax panel notice" not in panel
        assert "Next Feature Request Meeting" not in panel

    def it_ends_with_the_paged_changelog():
        client, _member = _client_for()

        panel = _panel(client.get(FEEDBACK).content.decode())

        assert 'id="spotlight-changelog" data-changelog-pages' in panel
        assert "var PER_PAGE = 5;" in panel


def describe_who_sees_it():
    def it_never_shows_a_guest_the_spotlight():
        config = SiteConfiguration.load()
        config.kiln_tickets_open = True
        config.save()
        client, _member = _client_for(Member.Status.GUEST)

        html = client.get(reverse("kiln:mine")).content.decode()

        assert "data-spotlight" not in html
        assert BACKUP_PILL in html
        assert 'id="changelog-modal"' in html

    def it_leaves_the_login_page_with_the_paged_plain_changelog():
        html = Client().get("/accounts/login/").content.decode()

        assert "data-spotlight" not in html
        assert 'id="changelog-modal"' in html
        assert 'id="changelog-modal-pages" data-changelog-pages' in html


def describe_query_count():
    def it_reads_nothing_for_the_card_on_a_hub_page():
        # #935: no messages and no meeting, so no settings row and no announcements read for it.
        AnnouncementFactory()
        client, _member = _client_for()
        client.get(FEEDBACK)  # warm per-session caches

        with CaptureQueriesContext(connection) as captured:
            html = client.get(FEEDBACK).content.decode()

        assert "data-spotlight-details" in html
        assert not any('"core_announcement"' in query["sql"] for query in captured.captured_queries)


def describe_changelog_dates():
    def it_writes_dates_the_way_the_portal_does():
        from hub.templatetags.hub_tags import changelog_date

        assert changelog_date("2026-10-07") == "Oct 7, 2026"

    def it_refuses_a_date_that_is_not_iso():
        from hub.templatetags.hub_tags import changelog_date

        with pytest.raises(ValueError):
            changelog_date("October 7")

    def it_shows_written_dates_in_the_panel_and_the_plain_modal(monkeypatch: pytest.MonkeyPatch):
        entry = {"title": "Zorblax release", "date": "2026-10-07", "changes": ["One"], "slug": "zorblax"}
        monkeypatch.setattr("plfog.version.CHANGELOG", [entry])
        client, _member = _client_for()

        panel = _panel(client.get(HOME).content.decode())
        login = Client().get("/accounts/login/").content.decode()

        for html in (panel, login):
            assert '<span class="changelog-entry__date">Oct 7, 2026</span>' in html
            assert "2026-10-07</span>" not in html
