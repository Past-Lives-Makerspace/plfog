"""The member Spotlight on hub pages (#709): Standard, Minimized, Expanded, who sees it, its cost."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

import hub.context_processors
from core.models import SiteConfiguration
from hub.spotlight import Spotlight
from membership.models import CommunityEvent, Member
from plfog.version import VERSION
from membership.services.provisioning import provision_user_for_member
from tests.membership.factories import CommunityEventFactory, MemberFactory
from tests.polls.factories import PollVoteFactory, poll_with

pytestmark = pytest.mark.django_db

HOME = reverse("hub_home")
#: The sidebar's version pill from before the Spotlight, the backup when the Spotlight is hidden.
BACKUP_PILL = 'class="pl-badge--version" onclick'


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


def _meeting(**kwargs: Any) -> CommunityEvent:
    start = timezone.now() + timedelta(days=5)
    defaults: dict[str, Any] = {
        "community": True,
        "title": "Zorblax Feature Request Meeting",
        "starts_at": start,
        "ends_at": start + timedelta(hours=1),
        "video_url": "https://meet.example.org/zorblax",
        "description": "Zorblax check-in on the portal.",
    }
    defaults.update(kwargs)
    return CommunityEventFactory(**defaults)


def _settings(**fields: Any) -> None:
    config = SiteConfiguration.load()
    for name, value in fields.items():
        setattr(config, name, value)
    config.save()


def _section(html: str, marker: str, end: str) -> str:
    return html.split(marker, 1)[1].split(end, 1)[0]


def _standard(html: str) -> str:
    sidebar = _section(html, 'class="hub-sidebar__spotlight"', "hub-sidebar__nav")
    return sidebar.split('data-spotlight-state="standard"', 1)[1]


def describe_standard():
    def it_shows_the_open_poll_as_buttons_past_polls_and_the_meeting():
        client, _member = _client_for()
        poll = poll_with("Laser", "Lathe", question="Zorblax next?")
        _settings(spotlight_meeting_event=_meeting())

        standard = _standard(client.get(HOME).content.decode())

        assert "This week's poll" in standard
        assert "Zorblax next?" in standard
        assert f'data-poll-choice="{poll.choices.get(text="Laser").pk}"' in standard
        assert '<input type="hidden" name="variant" value="spotlight">' in standard
        assert f'href="{reverse("polls:index")}" data-spotlight-past' in standard
        assert "Next Feature Request Meeting" in standard
        assert '<span class="pl-spotlight__virtual">Virtual</span>' in standard

    def it_has_details_as_its_only_button_beside_the_answers_and_minimize():
        client, _member = _client_for()
        _settings(spotlight_meeting_event=_meeting())

        standard = _standard(client.get(HOME).content.decode())

        assert standard.count('class="pl-btn ') == 1
        assert "data-spotlight-details" in standard

    def it_shows_results_with_the_members_answer_once_voted():
        client, member = _client_for()
        poll = poll_with("Laser", "Lathe")
        lathe = poll.choices.get(text="Lathe")
        PollVoteFactory(choice=lathe, member=member)

        standard = _standard(client.get(HOME).content.decode())

        assert "data-poll-choices" not in standard
        assert f'data-poll-result="{lathe.pk}" data-my-vote' in standard

    def it_replaces_the_version_number_in_the_sidebar():
        client, _member = _client_for()

        html = client.get(HOME).content.decode()

        assert BACKUP_PILL not in html
        assert 'id="changelog-modal"' not in html


def describe_minimized():
    def it_shows_the_admin_lines_and_the_date_pill():
        client, _member = _client_for()
        event = _meeting()
        _settings(
            spotlight_meeting_event=event,
            spotlight_first_line="Zorblax line one",
            spotlight_second_line="Zorblax line two",
        )

        html = client.get(HOME).content.decode()
        minimized = _section(html, 'data-spotlight-state="minimized"', "</button>")

        assert "Zorblax line one" in minimized
        assert "Zorblax line two" in minimized
        pill = Spotlight.load(None, timezone.now()).meeting
        assert pill is not None and f"data-spotlight-pill>{pill.pill}</span>" in minimized

    def it_falls_back_to_the_poll_question_and_the_meeting_name():
        client, _member = _client_for()
        poll_with("Laser", "Lathe", question="Zorblax fallback?")

        minimized = _section(client.get(HOME).content.decode(), 'data-spotlight-state="minimized"', "</button>")

        assert "data-spotlight-first>Zorblax fallback?</span>" in minimized
        assert "data-spotlight-second>Feature Request Meeting</span>" in minimized

    def it_carries_the_dot_and_the_seen_signature():
        client, _member = _client_for()
        poll = poll_with("Laser", "Lathe")

        html = client.get(HOME).content.decode()

        # x-cloak: hidden until the store decides, so a minimized member with nothing new never sees it flash.
        assert 'data-spotlight-dot x-show="$store.spotlight.unseen" x-cloak' in html
        assert f'data-spotlight-signature="{poll.pk}||"' in html

    def it_sets_the_remembered_state_before_the_first_paint():
        client, _member = _client_for()

        head = client.get(HOME).content.decode().split("</head>", 1)[0]

        assert "localStorage.getItem('plSpotlightMinimized') === '1'" in head
        assert head.index("js/spotlight.js") < head.index("js/alpine.min.js")


def describe_expanded():
    def it_shows_the_poll_the_meeting_and_have_an_idea():
        client, _member = _client_for()
        poll_with("Laser", "Lathe", question="Zorblax expanded?")
        event = _meeting()
        _settings(spotlight_meeting_event=event)

        panel = client.get(HOME).content.decode().split("data-spotlight-panel", 1)[1]

        assert "Zorblax expanded?" in panel
        assert "pl-poll-card--panel" in panel
        assert "Zorblax Feature Request Meeting" in panel
        assert "Zorblax check-in on the portal." in panel
        assert 'href="https://meet.example.org/zorblax"' in panel
        assert (
            f'href="/events/{event.pk}/event.ics" class="pl-btn pl-btn--ghost pl-btn--sm" hx-boost="false" data-pl-download download'
            in panel
        )
        assert f'href="/events/{event.pk}/"' in panel
        assert f'href="{reverse("hub_beta_feedback")}?category=feature"' in panel
        assert f'href="{reverse("hub_beta_feedback")}?category=bug"' in panel

    def it_leaves_out_join_online_without_a_video_link():
        client, _member = _client_for()
        _settings(spotlight_meeting_event=_meeting(video_url=""))

        panel = client.get(HOME).content.decode().split("data-spotlight-panel", 1)[1]

        assert "data-spotlight-panel-meeting" in panel
        assert "data-spotlight-join" not in panel
        assert '<span class="pl-spotlight__virtual">Virtual</span>' not in panel

    def it_ends_with_the_version_and_the_paged_changelog():
        from plfog.version import VERSION

        client, _member = _client_for()

        panel = client.get(HOME).content.decode().split("data-spotlight-panel", 1)[1]

        short = ".".join(VERSION.split(".")[:2])
        assert f"Version {short} · What changed" in panel
        assert 'id="spotlight-changelog" data-changelog-pages' in panel
        assert "var PER_PAGE = 5;" in panel


def describe_phones():
    def it_puts_the_spotlight_at_the_top_of_home():
        client, _member = _client_for()

        html = client.get(HOME).content.decode()
        main = html.split('id="main-content"', 1)[1]

        assert main.index("data-spotlight-home") < main.index("hub-page-title")

    def it_keeps_it_off_other_pages_main_area():
        client, _member = _client_for()

        assert "data-spotlight-home" not in client.get(reverse("polls:index")).content.decode()


def describe_who_sees_it():
    def it_never_shows_a_guest_the_spotlight():
        _settings(kiln_tickets_open=True)
        poll_with("Laser", "Lathe")
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


def describe_voting_from_the_spotlight():
    def it_returns_the_compact_card_with_the_answer_marked():
        client, _member = _client_for()
        poll = poll_with("Laser", "Lathe")

        response = client.post(
            reverse("polls:vote", args=[poll.pk]),
            {"choice": poll.choices.get(text="Laser").pk, "variant": "spotlight", "next": HOME},
            HTTP_HX_REQUEST="true",
        )

        html = response.content.decode()
        assert "pl-poll-card--spotlight" in html
        assert "This week's poll" in html
        assert "Your vote" in html
        assert 'id="poll-' not in html

    def it_ignores_an_unknown_variant():
        client, _member = _client_for()
        poll = poll_with("Laser", "Lathe")

        response = client.post(
            reverse("polls:vote", args=[poll.pk]),
            {"choice": poll.choices.first().pk, "variant": "zorblax"},
            HTTP_HX_REQUEST="true",
        )

        assert (
            response.content.decode()
            .lstrip()
            .startswith(f'<article class="pl-poll-card pl-poll-card--open" id="poll-{poll.pk}"')
        )


def describe_query_count():
    def it_costs_one_query_on_a_hub_page(monkeypatch: pytest.MonkeyPatch):
        poll_with("Laser", "Lathe")
        _settings(spotlight_meeting_event=_meeting())
        client, member = _client_for()
        client.get(HOME)  # warm per-session caches so both renders below do the same work

        with CaptureQueriesContext(connection) as with_spotlight:
            client.get(HOME)
        prebuilt = Spotlight.load(member, timezone.now())
        monkeypatch.setattr(hub.context_processors, "_spotlight", lambda _member: prebuilt)
        with CaptureQueriesContext(connection) as without_spotlight:
            client.get(HOME)

        assert len(with_spotlight) - len(without_spotlight) == 1


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

        panel = client.get(HOME).content.decode().split("data-spotlight-panel", 1)[1]
        login = Client().get("/accounts/login/").content.decode()

        for html in (panel, login):
            assert '<span class="changelog-entry__date">Oct 7, 2026</span>' in html
            assert "2026-10-07</span>" not in html


LATEST = {"title": "Zorblax latest", "date": "2026-10-07", "changes": ["One"], "slug": "701-zorblax"}


def describe_with_no_open_poll():
    def it_shows_the_latest_update_in_standard_linking_into_the_changelog(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("plfog.version.CHANGELOG", [LATEST])
        client, _member = _client_for()
        _settings(spotlight_meeting_event=_meeting())

        standard = _standard(client.get(HOME).content.decode())

        assert "data-poll-card" not in standard
        assert "Latest update" in standard
        assert 'href="#changelog-701-zorblax"' in standard
        assert (
            'data-anchor="changelog-701-zorblax" @click.prevent="$store.spotlight.showChange($el.dataset.anchor)"'
            in standard
        )
        assert ">Zorblax latest</a>" in standard
        assert "Oct 7, 2026" in standard

    def it_puts_the_latest_update_in_expanded_too(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("plfog.version.CHANGELOG", [LATEST])
        client, _member = _client_for()

        panel = client.get(HOME).content.decode().split("data-spotlight-panel", 1)[1]

        assert "data-spotlight-panel-update" in panel
        assert ">Zorblax latest</a>" in panel

    def it_falls_back_to_the_update_title_on_minimized_line_one(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("plfog.version.CHANGELOG", [LATEST])
        client, _member = _client_for()
        _settings(spotlight_meeting_event=_meeting())

        minimized = _section(client.get(HOME).content.decode(), 'data-spotlight-state="minimized"', "</button>")

        assert "data-spotlight-first>Zorblax latest</span>" in minimized

    def it_keeps_the_admins_first_line_over_the_update(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("plfog.version.CHANGELOG", [LATEST])
        client, _member = _client_for()
        _settings(spotlight_meeting_event=_meeting(), spotlight_first_line="Zorblax admin line")

        minimized = _section(client.get(HOME).content.decode(), 'data-spotlight-state="minimized"', "</button>")

        assert "data-spotlight-first>Zorblax admin line</span>" in minimized


def describe_with_no_poll_and_no_meeting():
    def it_shows_the_spotlight_while_the_toggle_is_on():
        client, _member = _client_for()

        html = client.get(HOME).content.decode()

        assert 'data-spotlight-state="quiet"' in html
        assert "data-spotlight-home" in html
        assert BACKUP_PILL not in html

    def it_is_quiet_the_version_pill_with_the_update_date_and_details(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("plfog.version.CHANGELOG", [LATEST])
        client, _member = _client_for()

        sidebar = _section(client.get(HOME).content.decode(), "hub-sidebar__spotlight--quiet", "hub-sidebar__nav")

        short = ".".join(VERSION.split(".")[:2])
        assert 'data-spotlight-state="quiet"' in sidebar
        assert f'title="Updated Oct 7, 2026" data-spotlight-quiet-version>v{short} · Oct 7</button>' in sidebar
        assert "data-spotlight-details" in sidebar
        assert "Zorblax latest" not in sidebar

    def it_leaves_out_the_card_minimized_and_past_polls():
        client, _member = _client_for()

        sidebar = _section(client.get(HOME).content.decode(), "hub-sidebar__spotlight--quiet", "hub-sidebar__nav")

        for gone in ('data-spotlight-state="standard"', 'data-spotlight-state="minimized"', "data-spotlight-past"):
            assert gone not in sidebar
        assert "data-spotlight-minimize" not in sidebar

    def it_goes_back_to_the_logo_and_version_while_the_toggle_is_off():
        client, _member = _client_for()
        _settings(spotlight_show_when_empty=False)

        html = client.get(HOME).content.decode()

        assert "data-spotlight" not in html
        assert BACKUP_PILL in html
        assert 'id="changelog-modal"' in html

    def it_comes_back_when_a_poll_opens_with_the_toggle_off():
        client, _member = _client_for()
        _settings(spotlight_show_when_empty=False)
        poll_with("Laser", "Lathe")

        html = client.get(HOME).content.decode()

        assert "hub-sidebar__spotlight" in html
        assert BACKUP_PILL not in html

    def it_still_costs_one_query_when_hidden(monkeypatch: pytest.MonkeyPatch):
        _settings(spotlight_show_when_empty=False)
        client, member = _client_for()
        client.get(HOME)

        with CaptureQueriesContext(connection) as with_spotlight:
            client.get(HOME)
        prebuilt = Spotlight.load(member, timezone.now())
        monkeypatch.setattr(hub.context_processors, "_spotlight", lambda _member: prebuilt)
        with CaptureQueriesContext(connection) as without_spotlight:
            client.get(HOME)

        assert len(with_spotlight) - len(without_spotlight) == 1

    def it_sits_above_the_divider_under_member_portal():
        client, _member = _client_for()

        html = client.get(HOME).content.decode()

        assert 'class="pl-brand pl-brand--quiet"' in html
        assert 'class="hub-sidebar__spotlight hub-sidebar__spotlight--quiet"' in html
