"""BDD specs for the review queue and its archived view (spec D §6.4, §6.5, D17, D19).

The scoping here is the point: a guild lead sees their own guilds and NOT the space-wide
pages, which is where this deliberately diverges from ``editable_meeting_scopes``'s council
boolean (right for meetings, wrong for a site-wide wiki).
"""

from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import AlreadyResolved, Member, WikiPage, WikiReport
from tests.hub.wiki_mod_helpers import enable_wiki, login, login_lead
from tests.membership.factories import (
    GuildFactory,
    MemberFactory,
    WikiPageFactory,
    WikiReportFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _wiki_on(db):
    return enable_wiki()


def _queue_url() -> str:
    return reverse("hub_wiki_review")


def describe_scoping():
    def it_shows_a_lead_only_their_own_guilds(client: Client):
        _user, guild = login_lead(client, "q_lead")
        other = GuildFactory()
        mine = WikiReportFactory(page=WikiPageFactory(guild=guild), reason="Mine to fix.")
        WikiReportFactory(page=WikiPageFactory(guild=other), reason="Not mine at all.")
        response = client.get(_queue_url())
        assert response.status_code == 200
        assert b"Mine to fix." in response.content
        assert b"Not mine at all." not in response.content
        assert f"report-{mine.pk}".encode() in response.content

    def it_shows_a_lead_of_two_guilds_both(client: Client):
        user, first = login_lead(client, "q_lead_two")
        second = GuildFactory(guild_lead=user.member)
        WikiReportFactory(page=WikiPageFactory(guild=first), reason="From the first guild.")
        WikiReportFactory(page=WikiPageFactory(guild=second), reason="From the second guild.")
        response = client.get(_queue_url())
        assert b"From the first guild." in response.content
        assert b"From the second guild." in response.content

    def it_does_not_hand_a_lead_the_space_wide_pages(client: Client):
        # THE D16 divergence: editable_meeting_scopes grants the council scope to anyone
        # with lead authority in any guild, which is right for meetings and would hand
        # every guild lead the whole site-wide wiki.
        _user, guild = login_lead(client, "q_lead_space")
        WikiReportFactory(page=WikiPageFactory(guild=guild), reason="Inside my guild.")
        WikiReportFactory(page=WikiPageFactory(guild=None), reason="A space wide page.")
        response = client.get(_queue_url())
        assert b"Inside my guild." in response.content
        assert b"A space wide page." not in response.content

    def it_shows_an_admin_everything_including_space_wide(client: Client):
        login(client, "q_admin", fog_role=Member.FogRole.ADMIN)
        WikiReportFactory(page=WikiPageFactory(guild=GuildFactory()), reason="A guild report.")
        WikiReportFactory(page=WikiPageFactory(guild=None), reason="A space wide report.")
        response = client.get(_queue_url())
        assert b"A guild report." in response.content
        assert b"A space wide report." in response.content
        assert b"Showing every scope." in response.content

    def it_refuses_a_plain_member(client: Client):
        login(client, "q_plain")
        assert client.get(_queue_url()).status_code == 403

    def it_names_the_scopes_it_is_showing(client: Client):
        _user, guild = login_lead(client, "q_scope_line")
        response = client.get(_queue_url())
        assert f"Showing reports for {guild.name}.".encode() in response.content


def describe_ordering_and_paging():
    def it_puts_the_oldest_first(client: Client):
        login(client, "q_order", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory(guild=None)
        old = WikiReportFactory(page=page, reason="The older complaint.")
        WikiReport.objects.filter(pk=old.pk).update(created_at=timezone.now() - timezone.timedelta(days=5))
        WikiReportFactory(page=page, reason="The newer complaint.")
        body = client.get(_queue_url()).content
        assert body.index(b"The older complaint.") < body.index(b"The newer complaint.")

    def it_pages_the_reports_at_twenty_five(client: Client):
        login(client, "q_paginate", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory(guild=None)
        for _ in range(26):
            WikiReportFactory(page=page, reporter=MemberFactory())
        first = client.get(_queue_url())
        assert len(first.context["reports_page"].object_list) == 25
        second = client.get(_queue_url(), {"page": "2"})
        assert len(second.context["reports_page"].object_list) == 1

    def it_does_not_grow_a_query_per_report(client: Client, django_assert_max_num_queries):
        # for_scopes is ONE query and the cards read page, guild and reporter off it.
        # max_num_queries rather than an exact count: an exact assertion fails on a query
        # improvement too, which teaches people to raise the number instead of reading it.
        login(client, "q_n_plus_one", fog_role=Member.FogRole.ADMIN)
        for _ in range(10):
            guild = GuildFactory()
            for _row in range(3):
                WikiReportFactory(page=WikiPageFactory(guild=guild), reporter=MemberFactory())
        with django_assert_max_num_queries(25):
            assert client.get(_queue_url()).status_code == 200

    def it_leaves_the_safety_list_alone_when_the_reports_page(client: Client):
        # Only ONE paginator on this screen: table_pagination hard-codes ?page=, so two
        # would drive each other.
        login(client, "q_one_paginator", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory(guild=None)
        for _ in range(26):
            WikiReportFactory(page=page, reporter=MemberFactory())
        WikiPageFactory(guild=None, official=True, is_published=False, title="Held Safety Page")
        second = client.get(_queue_url(), {"page": "2"})
        assert b"Held Safety Page" in second.content


def describe_the_empty_states():
    def it_writes_a_sentence_rather_than_leaving_a_blank_region(client: Client):
        login(client, "q_empty", fog_role=Member.FogRole.ADMIN)
        response = client.get(_queue_url())
        assert b"Nothing to review. You are all caught up." in response.content

    def it_names_the_lead_scopes_in_the_empty_state(client: Client):
        _user, guild = login_lead(client, "q_empty_lead")
        response = client.get(_queue_url())
        assert f"Showing reports for {guild.name}.".encode() in response.content


def describe_marking_a_report_reviewed():
    def it_records_who_and_what(client: Client):
        user = login(client, "q_resolve", fog_role=Member.FogRole.ADMIN)
        report = WikiReportFactory(page=WikiPageFactory(guild=None))
        response = client.post(
            reverse("hub_wiki_report_resolve", args=[report.pk]),
            {"source": "queue", "resolution": "Fixed the sentence."},
        )
        assert response.status_code == 200
        report.refresh_from_db()
        assert report.resolved_by == user.member
        assert report.resolution == "Fixed the sentence."

    def it_answers_a_stale_tab_with_an_info_toast_and_no_overwrite(client: Client):
        login(client, "q_stale", fog_role=Member.FogRole.ADMIN)
        first_reviewer = MemberFactory()
        report = WikiReportFactory(page=WikiPageFactory(guild=None))
        report.resolve(by=first_reviewer, note="Already handled.")
        response = client.post(reverse("hub_wiki_report_resolve", args=[report.pk]), {"source": "queue"})
        assert response.status_code == 200
        assert json.loads(response["HX-Trigger"])["showToast"]["type"] == "info"
        report.refresh_from_db()
        assert report.resolved_by == first_reviewer
        assert report.resolution == "Already handled."

    def it_returns_the_written_empty_state_when_the_last_one_goes(client: Client):
        # It is swapped in INSIDE the Reported Pages card, so it speaks for that card:
        # claiming "nothing to review" with safety proposals still on screen below, and
        # reprinting the scope line, were both wrong.
        login(client, "q_last", fog_role=Member.FogRole.ADMIN)
        report = WikiReportFactory(page=WikiPageFactory(guild=None))
        response = client.post(reverse("hub_wiki_report_resolve", args=[report.pk]), {"source": "queue"})
        assert b"Nothing reported. You are all caught up." in response.content
        assert b"Nothing to review." not in response.content
        assert b"Showing every scope." not in response.content

    def it_refuses_a_member_who_cannot_moderate_that_page(client: Client):
        login(client, "q_not_mine")
        report = WikiReportFactory(page=WikiPageFactory(guild=GuildFactory()))
        assert client.post(reverse("hub_wiki_report_resolve", args=[report.pk])).status_code == 403

    def it_raises_a_domain_error_on_a_second_resolve(db):
        report = WikiReportFactory()
        report.resolve(by=MemberFactory())
        with pytest.raises(AlreadyResolved):
            report.resolve(by=MemberFactory())


def describe_marking_reviewed_from_the_banner():
    def it_swaps_the_banner_away_without_a_queue_round_trip(client: Client):
        login(client, "q_banner", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory(guild=None)
        report = WikiReportFactory(page=page, reason="A complaint about the fence.")
        page.mark_needs_review()
        response = client.post(reverse("hub_wiki_report_resolve", args=[report.pk]), {"source": "banner"})
        assert response.status_code == 200
        assert b'id="wiki-review-banner"' in response.content
        assert b"A complaint about the fence." not in response.content
        header = json.loads(response["HX-Trigger"])
        assert header["showToast"]["message"] == "Marked reviewed."
        assert header["close-modal"] == f"resolve-{report.pk}"

    def it_leaves_the_next_report_quoted_when_there_is_one(client: Client):
        login(client, "q_banner_next", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory(guild=None)
        first = WikiReportFactory(page=page, reason="The first complaint.")
        WikiReport.objects.filter(pk=first.pk).update(created_at=timezone.now() - timezone.timedelta(days=1))
        WikiReportFactory(page=page, reason="The second complaint.")
        page.mark_needs_review()
        response = client.post(reverse("hub_wiki_report_resolve", args=[first.pk]), {"source": "banner"})
        assert b"The second complaint." in response.content

    def it_offers_a_plain_member_neither_button(client: Client):
        login(client, "q_banner_member")
        page = WikiPageFactory(guild=None)
        WikiReportFactory(page=page, reason="A complaint anyone can read.")
        page.mark_needs_review()
        response = client.get(page.get_absolute_url())
        assert b"A complaint anyone can read." in response.content
        assert b"Mark Reviewed" not in response.content

    def it_prompts_a_staff_saver_without_auto_resolving(client: Client):
        # A staff edit is often unrelated to the report; silently closing somebody's
        # report because a typo got fixed is the failure the queue exists to prevent.
        user = login(client, "q_staff_save", fog_role=Member.FogRole.ADMIN)
        page = WikiPageFactory(guild=None)
        report = WikiReportFactory(page=page)
        page.mark_needs_review()
        response = client.post(
            reverse("hub_wiki_edit", args=[page.slug]),
            {
                "title": page.title,
                "body": "<p>Fixed the sentence.</p>",
                "base_revision": "",
                "facts-TOTAL_FORMS": "0",
                "facts-INITIAL_FORMS": "0",
                "facts-MIN_NUM_FORMS": "0",
                "facts-MAX_NUM_FORMS": "1000",
                "attachments-TOTAL_FORMS": "0",
                "attachments-INITIAL_FORMS": "0",
                "attachments-MIN_NUM_FORMS": "0",
                "attachments-MAX_NUM_FORMS": "1000",
            },
            follow=True,
        )
        assert b"still has a report open" in response.content
        report.refresh_from_db()
        assert report.resolved_at is None
        # The save itself landed, so the prompt is not a rejection.
        assert page.revisions.filter(author=user.member).exists()


def describe_the_entry_point():
    def it_offers_a_moderator_the_queue_from_the_wiki_home(client: Client):
        # Without this the queue is reachable only from a report banner on a page you
        # happen to be reading, so a held safety proposal and the ?archived=1 view both
        # sit behind a screen nobody arrives at.
        _user, guild = login_lead(client, "entry_lead")
        WikiReportFactory(page=WikiPageFactory(guild=guild))
        body = client.get(reverse("hub_wiki_home")).content
        assert reverse("hub_wiki_review").encode() in body
        assert b"Review queue (1)" in body

    def it_hides_it_from_a_plain_member(client: Client):
        login(client, "entry_member")
        assert reverse("hub_wiki_review").encode() not in client.get(reverse("hub_wiki_home")).content

    def it_counts_held_safety_proposals_as_well_as_reports(client: Client):
        # The badge counted reports only, so a lead with three proposals and no reports saw
        # a bare "Review queue" on the screen added to make proposals discoverable.
        _user, guild = login_lead(client, "entry_proposals")
        WikiPageFactory(guild=guild, official=True, is_published=False)
        WikiPageFactory(guild=guild, official=True, is_published=False)
        WikiReportFactory(page=WikiPageFactory(guild=guild))
        assert b"Review queue (3)" in client.get(reverse("hub_wiki_home")).content

    def it_drops_the_count_when_nothing_is_waiting(client: Client):
        login(client, "entry_quiet", fog_role=Member.FogRole.ADMIN)
        body = client.get(reverse("hub_wiki_home")).content
        assert b"Review queue<" in body or b"Review queue</a>" in body
        assert b"Review queue (" not in body


def describe_an_over_long_resolution_note():
    def it_is_never_dropped_behind_a_green_toast(client: Client):
        login(client, "resolve_long", fog_role=Member.FogRole.ADMIN)
        report = WikiReportFactory(page=WikiPageFactory(guild=None))
        response = client.post(
            reverse("hub_wiki_report_resolve", args=[report.pk]),
            {"source": "queue", "resolve_target": f"#report-{report.pk}", "resolution": "x" * 301},
        )
        assert response.status_code == 200
        report.refresh_from_db()
        assert report.resolved_at is None
        # Retargeted at the modal body it was typed into, with the text still in it.
        assert response["HX-Retarget"] == f"#resolve-{report.pk}-body"
        assert response["HX-Reswap"] == "innerHTML"
        assert b"x" * 301 in response.content
        assert "HX-Trigger" not in response

    def it_keeps_the_success_target_through_the_error(client: Client):
        login(client, "resolve_long2", fog_role=Member.FogRole.ADMIN)
        report = WikiReportFactory(page=WikiPageFactory(guild=None))
        response = client.post(
            reverse("hub_wiki_report_resolve", args=[report.pk]),
            {"source": "banner", "resolve_target": "#wiki-review-banner", "resolution": "x" * 301},
        )
        assert b'value="#wiki-review-banner"' in response.content
        assert b'value="banner"' in response.content


def describe_the_archived_view():
    def it_lists_a_leads_own_archived_pages_only(client: Client):
        _user, guild = login_lead(client, "q_arch_lead")
        WikiPageFactory(guild=guild, archived=True, title="My Removed Page")
        WikiPageFactory(guild=GuildFactory(), archived=True, title="Their Removed Page")
        response = client.get(_queue_url(), {"archived": "1"})
        assert b"My Removed Page" in response.content
        assert b"Their Removed Page" not in response.content

    def it_lists_everything_for_an_admin_newest_removal_first(client: Client):
        login(client, "q_arch_admin", fog_role=Member.FogRole.ADMIN)
        older = WikiPageFactory(guild=None, archived=True, title="Older Removal")
        WikiPage.objects.filter(pk=older.pk).update(archived_at=timezone.now() - timezone.timedelta(days=4))
        WikiPageFactory(guild=None, archived=True, title="Newer Removal")
        body = client.get(_queue_url(), {"archived": "1"}).content
        assert body.index(b"Newer Removal") < body.index(b"Older Removal")

    def it_writes_its_own_empty_state(client: Client):
        _user, _guild = login_lead(client, "q_arch_empty")
        response = client.get(_queue_url(), {"archived": "1"})
        assert b"Nothing has been archived in your scopes." in response.content

    def it_writes_the_admin_empty_state(client: Client):
        login(client, "q_arch_empty_admin", fog_role=Member.FogRole.ADMIN)
        response = client.get(_queue_url(), {"archived": "1"})
        assert b"Nothing has been archived." in response.content

    def it_still_refuses_a_plain_member(client: Client):
        login(client, "q_arch_plain")
        assert client.get(_queue_url(), {"archived": "1"}).status_code == 403
