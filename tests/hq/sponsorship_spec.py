"""The Sponsorship Tracker (Track D, part 2): the door, its gates, the band in two queries, Follow up by the Portland
date, + Add a Sponsor, the pipeline's order and filters, the inline edit with its status lines, the contact log,
Delete, the CSV, the tiers and the seed's adoption of the placeholder."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from hq import services
from hq.exports import SPONSORS_CSV_HEADERS
from hq.forms import SponsorEditForm, SponsorForm
from hq.holiday_bizarre import SPONSOR_TIERS, WINE_WALL_BENEFIT
from hq.models import BUILT_DOORS, HubCard, HubGoal, Sponsor, SponsorLogEntry
from hub.view_as import ROLE_GUEST
from tests.hq.conftest import signed_in
from tests.hq.factories import HubCardFactory, HubGoalFactory, SponsorFactory, SponsorLogEntryFactory

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
S = Sponsor.Status


def _page(hub: Any) -> str:
    return reverse("hq:sponsors", args=[hub.slug])


def _add(hub: Any) -> str:
    return reverse("hq:sponsor_add", args=[hub.slug])


def _edit(sponsor: Sponsor) -> str:
    return reverse("hq:sponsor_edit", args=[sponsor.hub.slug, sponsor.pk])


def _log(sponsor: Sponsor) -> str:
    return reverse("hq:sponsor_log", args=[sponsor.hub.slug, sponsor.pk])


def _delete(sponsor: Sponsor) -> str:
    return reverse("hq:sponsor_delete", args=[sponsor.hub.slug, sponsor.pk])


def _csv(hub: Any) -> str:
    return reverse("hq:sponsors_csv", args=[hub.slug])


def _add_post(**fields: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "organization": "OnPoint Community Credit Union",
        "tier": Sponsor.Tier.GOLD,
        "ask": "2500",
        "owner": "Augustin",
        "contact_name": "Dana Reyes",
        "contact_role": "Branch manager",
        "email": "Dana@OnPoint.example",
        "phone": "503-555-0100",
        "next_step": "Send sponsor deck",
        "follow_up_by": "2026-11-10",
        "fit_note": "PLM and 16 guild accounts bank with them",
    }
    data.update(fields)
    return data


def _edit_post(sponsor: Sponsor, **fields: Any) -> dict[str, Any]:
    """The row's form as it renders for ``sponsor``, with ``fields`` changed, under the row's prefix."""
    values: dict[str, Any] = {
        key: value for key, value in SponsorEditForm.initial_for(sponsor).items() if value not in (None, False)
    }
    values.update(fields)
    data = {f"sponsor-{sponsor.pk}-{key}": value for key, value in values.items()}
    data["filter"] = data.pop(f"sponsor-{sponsor.pk}-filter", "all")
    return data


def _toast(response: Any) -> str:
    return json.loads(response["HX-Trigger"])["showToast"]["message"]


@pytest.fixture
def card(open_hub) -> HubCard:
    """The Sponsorship Tracker door on the open hub, open to everyone so the read-only view can be seen."""
    return HubCardFactory(
        hub=open_hub, kind=HubCard.Kind.SPONSORSHIP, title="Sponsorship Tracker", audience=HubCard.Audience.PUBLIC
    )


@pytest.fixture
def hub(card):
    return card.hub


def describe_the_door():
    def it_is_a_built_door_that_opens_the_tracker_from_the_hq_page(card):
        body = Client().get(card.hub.get_absolute_url()).content.decode()

        assert HubCard.Kind.SPONSORSHIP in BUILT_DOORS
        assert card.url == _page(card.hub) == f"/hq/{card.hub.slug}/sponsors/"
        assert f'href="{_page(card.hub)}" class="pl-hq-card" data-hq-card-link' in body

    def it_carries_lees_header_and_guidance_line(hub):
        body = Client().get(_page(hub)).content.decode()

        assert "Sponsorship Tracker</h1>" in body
        assert f"data-hq-sp-subline>{hub.title} · Cash sponsors · Team only</p>" in body
        assert f'href="{hub.get_absolute_url()}" class="pl-hq-sp__back"' in body
        assert (
            "<strong>When you ask:</strong> say sponsor, gift or in-kind support, not donate. Past Lives Makerspace is "
            "an Oregon nonprofit corporation. Gifts and sponsorships are not tax-deductible.</p>"
        ) in body


def describe_who_may_open_it():
    def it_shows_a_signed_out_visitor_the_band_names_tiers_and_statuses_only(hub):
        sponsor = SponsorFactory(
            hub=hub,
            organization="Rocking Frog Cafe",
            tier=Sponsor.Tier.SILVER,
            status=S.ASKED,
            amount_cents=123_400,
            fit_note="Spec fit note",
            benefits="Spec benefits promised",
            owner="Spec Asker",
            contact_name="Pat Contact",
            contact_role="Spec Role",
            email="pat@frog.example",
            phone="503-555-0199",
            note="Spec private note",
            next_step="Spec next step",
            history="Spec gave a raffle prize",
            follow_up_by=timezone.localdate(),
        )
        SponsorLogEntryFactory(sponsor=sponsor, text="Spec log line", by="Spec Logger")

        response = Client().get(_page(hub))

        body = response.content.decode()
        assert response.status_code == 200
        assert "data-hq-sp-band" in body
        assert "Rocking Frog Cafe" in body
        assert "Silver ($1,000)" in body
        assert 'data-hq-sp-status="asked">Asked<' in body
        assert "data-hq-sp-due-count>1 follow-up scheduled with the team.</p>" in body
        for private in (
            "$1,234",
            "data-hq-sp-amount",
            "Spec fit note",
            "Spec benefits promised",
            "Spec Asker",
            "Pat Contact",
            "Spec Role",
            "pat@frog.example",
            "503-555-0199",
            "Spec private note",
            "Spec next step",
            "Spec gave a raffle prize",
            "Spec log line",
            "Spec Logger",
            "data-hq-sp-add",
            "data-hq-sp-edit-form",
            "data-hq-sp-log",
            "data-hq-sp-csv",
            "data-hq-sp-follow-up=",
            'data-hq-sp-filter="donor2025"',
        ):
            assert private not in body, private

    def it_says_nothing_is_scheduled_to_a_visitor_with_no_follow_ups(hub):
        body = Client().get(_page(hub)).content.decode()

        assert "data-hq-sp-due-empty>Nothing scheduled.</p>" in body
        assert "data-hq-sp-empty>No sponsors yet.</p>" in body

    def it_ignores_the_2025_donors_filter_for_a_non_organiser(hub):
        SponsorFactory(hub=hub, organization="Spec Gave Before", history="A gift card")
        SponsorFactory(hub=hub, organization="Spec New Friend")

        body = Client().get(f"{_page(hub)}?filter=donor2025").content.decode()

        assert "Spec Gave Before" in body
        assert "Spec New Friend" in body
        assert 'data-hq-sp-filter="all" aria-current="true"' in body

    def it_is_a_404_for_a_member_on_an_admins_card(card, member_client):
        card.audience = HubCard.Audience.ADMINS
        card.save(update_fields=["audience"])

        assert member_client.get(_page(card.hub)).status_code == 404

    def it_sends_a_signed_out_visitor_through_login_on_a_members_card(card):
        card.audience = HubCard.Audience.MEMBERS
        card.save(update_fields=["audience"])

        response = Client().get(_page(card.hub))

        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def it_is_a_404_when_hidden_for_all_but_admins(card, member_client, admin_client):
        card.is_hidden = True
        card.save(update_fields=["is_hidden"])

        assert member_client.get(_page(card.hub)).status_code == 404
        assert admin_client.get(_page(card.hub)).status_code == 200

    def it_is_a_404_on_a_hub_with_no_sponsorship_door(open_hub, admin_client):
        assert admin_client.get(_page(open_hub)).status_code == 404

    def it_gives_the_forms_and_details_to_a_named_member_on_an_admins_card(card, member):
        card.audience = HubCard.Audience.ADMINS
        card.save(update_fields=["audience"])
        card.named_members.set([member])
        SponsorFactory(hub=card.hub, email="pat@frog.example")

        body = signed_in(member).get(_page(card.hub)).content.decode()

        assert "data-hq-sp-add-form" in body
        assert "data-hq-sp-edit-form" in body
        assert "pat@frog.example" in body
        assert "data-hq-sp-csv" in body

    def it_refuses_every_write_and_the_csv_to_anyone_but_an_organiser(hub, member_client):
        sponsor = SponsorFactory(hub=hub)

        for client in (member_client, Client()):
            assert client.post(_add(hub), _add_post()).status_code == 403
            assert client.post(_edit(sponsor), _edit_post(sponsor, status=S.RECEIVED)).status_code == 403
            assert client.post(_log(sponsor), {f"log-{sponsor.pk}-text": "Called"}).status_code == 403
            assert client.post(_delete(sponsor)).status_code == 403
            assert client.get(_csv(hub)).status_code == 403
        sponsor.refresh_from_db()
        assert sponsor.status == S.PROSPECT
        assert Sponsor.objects.count() == 1
        assert not SponsorLogEntry.objects.exists()

    def it_renders_a_previewing_admin_read_only_but_lets_them_act(hub, admin_client):
        session = admin_client.session
        session["view_as_role"] = ROLE_GUEST
        session.save()
        SponsorFactory(hub=hub, email="pat@frog.example")

        body = admin_client.get(_page(hub)).content.decode()

        assert "data-hq-sp-add-form" not in body
        assert "pat@frog.example" not in body
        assert admin_client.post(_add(hub), _add_post()).status_code == 302
        assert admin_client.get(_csv(hub)).status_code == 200
        assert Sponsor.objects.count() == 2

    def it_takes_posts_only(hub, admin_client):
        sponsor = SponsorFactory(hub=hub)

        for url in (_add(hub), _edit(sponsor), _log(sponsor), _delete(sponsor)):
            assert admin_client.get(url).status_code == 405, url


def describe_the_band():
    def it_sums_received_committed_and_asked_and_counts_prospects_in_two_queries(hub, django_assert_num_queries):
        SponsorFactory(hub=hub, status=S.RECEIVED, amount_cents=150_000, ask_cents=200_000)
        SponsorFactory(hub=hub, status=S.RECEIVED, amount_cents=50_000)
        SponsorFactory(hub=hub, status=S.COMMITTED, amount_cents=90_000, ask_cents=90_000)
        SponsorFactory(hub=hub, status=S.ASKED, ask_cents=250_000, amount_cents=10_000)
        SponsorFactory(hub=hub, status=S.ASKED, ask_cents=50_000)
        SponsorFactory.create_batch(3, hub=hub, status=S.PROSPECT, ask_cents=100_000)
        SponsorFactory(hub=hub, status=S.DECLINED, ask_cents=500_000, amount_cents=500_000)
        SponsorFactory(status=S.RECEIVED, amount_cents=999_900)  # another hub's

        with django_assert_num_queries(2):
            band = services.sponsor_band(hub)

        assert (band.received_cents, band.committed_cents, band.asked_cents) == (200_000, 90_000, 300_000)
        assert (band.open_asks, band.prospects, band.goal) == (2, 3, 10_000)
        assert band.secured_cents == 290_000
        assert (band.received_display, band.committed_display, band.asked_display, band.secured_display) == (
            "$2,000",
            "$900",
            "$3,000",
            "$2,900",
        )

    def it_reads_zero_on_an_empty_tracker(hub):
        band = services.sponsor_band(hub)

        assert (band.received_cents, band.committed_cents, band.asked_cents, band.open_asks, band.prospects) == (
            0,
            0,
            0,
            0,
            0,
        )

    def it_caps_both_segments_of_the_meter_at_the_goal(hub):
        HubGoalFactory(hub=hub, label="Raised", is_money=True, target=1_000)
        SponsorFactory(hub=hub, status=S.RECEIVED, amount_cents=60_000)
        SponsorFactory(hub=hub, status=S.COMMITTED, amount_cents=70_000)

        band = services.sponsor_band(hub)

        assert band.goal_cents == 100_000
        assert band.received_meter_cents == 60_000
        assert band.secured_meter_cents == 100_000

        SponsorFactory(hub=hub, status=S.RECEIVED, amount_cents=90_000)
        band = services.sponsor_band(hub)

        assert (band.received_meter_cents, band.secured_meter_cents) == (100_000, 100_000)

    def it_aims_at_the_raised_goal_first_then_a_money_goal_then_ten_thousand(hub):
        assert services.sponsor_goal(hub) == 10_000

        HubGoalFactory(hub=hub, label="Bottles", kind=HubGoal.Kind.BOTTLES, target=100)
        HubGoalFactory(hub=hub, label="Wine money", is_money=True, target=0)
        assert services.sponsor_goal(hub) == 10_000

        HubGoalFactory(hub=hub, label="Bar takings", is_money=True, target=3_000, sort_order=1)
        assert services.sponsor_goal(hub) == 3_000

        HubGoalFactory(hub=hub, label="RAISED", kind=HubGoal.Kind.MANUAL, target=12_000, sort_order=5)
        assert services.sponsor_goal(hub) == 12_000

    def it_renders_lees_four_tiles_the_meter_and_the_legend(hub):
        HubGoalFactory(hub=hub, label="Raised", is_money=True, target=10_000)
        SponsorFactory(hub=hub, status=S.RECEIVED, amount_cents=150_000)
        SponsorFactory(hub=hub, status=S.COMMITTED, amount_cents=50_000)
        SponsorFactory(hub=hub, status=S.ASKED, ask_cents=90_000)

        body = Client().get(_page(hub)).content.decode()

        assert "data-hq-sp-figure>$1,500</strong>" in body
        assert "data-hq-sp-helper>$2,000 secured with commitments</span>" in body
        assert "data-hq-sp-helper>Said yes, not paid yet</span>" in body
        assert "data-hq-sp-figure>$900</strong>" in body
        assert "data-hq-sp-helper>1 open ask</span>" in body
        assert "data-hq-sp-helper>Not asked yet</span>" in body
        assert 'max="1000000" value="200000" aria-hidden="true" data-hq-sp-meter-committed' in body
        assert 'max="1000000" value="150000" aria-label=' in body
        assert "data-hq-sp-goal>Toward the $10,000 event goal</span>" in body

    def it_counts_open_asks_in_the_plural(hub):
        SponsorFactory.create_batch(2, hub=hub, status=S.ASKED)

        body = Client().get(_page(hub)).content.decode()

        assert "data-hq-sp-helper>2 open asks</span>" in body


def describe_follow_up():
    def it_lists_open_asks_with_a_date_soonest_first_at_most_eight(hub):
        today = date(2026, 11, 5)
        later = SponsorFactory(hub=hub, organization="Later", status=S.ASKED, follow_up_by=today + timedelta(days=9))
        soon = SponsorFactory(hub=hub, organization="Soon", follow_up_by=today - timedelta(days=2))
        SponsorFactory(hub=hub, organization="Paid", status=S.RECEIVED, follow_up_by=today)
        SponsorFactory(hub=hub, organization="No", status=S.DECLINED, follow_up_by=today)
        SponsorFactory(hub=hub, organization="Undated", status=S.ASKED)
        filler = [
            SponsorFactory(
                hub=hub, organization=f"Filler {n}", status=S.COMMITTED, follow_up_by=today + timedelta(days=1)
            )
            for n in range(7)
        ]

        result = services.follow_ups(hub, today=today)

        assert result.count == 9
        assert [row.sponsor for row in result.rows] == [soon, *filler]
        assert later not in [row.sponsor for row in result.rows]

    def it_marks_overdue_and_today_but_not_later(hub):
        today = date(2026, 11, 5)
        SponsorFactory(hub=hub, organization="A", follow_up_by=today - timedelta(days=1))
        SponsorFactory(hub=hub, organization="B", follow_up_by=today)
        SponsorFactory(hub=hub, organization="C", follow_up_by=today + timedelta(days=1))

        rows = services.follow_ups(hub, today=today).rows

        assert [(row.prefix, row.is_due) for row in rows] == [("Overdue ", True), ("Today ", True), ("", False)]

    def it_reads_today_by_the_portland_date_not_utc(hub, admin_client, monkeypatch):
        # 06:30 UTC on Nov 3 is still 10:30 PM on Nov 2 in Portland.
        monkeypatch.setattr(timezone, "now", lambda: datetime(2026, 11, 3, 6, 30, tzinfo=UTC))
        SponsorFactory(hub=hub, organization="Due Portland Today", follow_up_by=date(2026, 11, 2))
        SponsorFactory(hub=hub, organization="Due Tomorrow", follow_up_by=date(2026, 11, 3))

        body = admin_client.get(_page(hub)).content.decode()

        assert 'class="pl-hq-sp__due" data-hq-sp-follow-up-date>Today Nov 2</span>' in body
        assert 'class="pl-hq-sp__muted" data-hq-sp-follow-up-date>Nov 3</span>' in body
        assert "Overdue" not in body

    def it_shows_the_org_the_next_step_or_follow_up_and_the_asker(hub, admin_client):
        when = timezone.localdate() + timedelta(days=3)
        SponsorFactory(hub=hub, organization="Spec Bank", next_step="Send sponsor deck", owner="Lee", follow_up_by=when)
        SponsorFactory(hub=hub, organization="Spec Brewery", follow_up_by=when)

        body = admin_client.get(_page(hub)).content.decode()

        assert (
            '<span><strong>Spec Bank</strong> · Send sponsor deck <span class="pl-hq-sp__muted">(Lee)</span></span>'
            in body
        )
        assert "<span><strong>Spec Brewery</strong> · Follow up</span>" in body

    def it_says_how_to_schedule_one_when_there_are_none(hub, admin_client):
        body = admin_client.get(_page(hub)).content.decode()

        assert 'data-hq-sp-due-empty>Nothing scheduled. Add a "follow up by" date to any sponsor.</p>' in body


def describe_adding_a_sponsor():
    def it_saves_a_prospect_over_htmx_with_its_first_log_line_and_a_toast(hub, admin, admin_client):
        response = admin_client.post(_add(hub), _add_post(), **HTMX)

        sponsor = Sponsor.objects.get()
        body = response.content.decode()
        assert response.status_code == 200
        assert _toast(response) == "Sponsor added."
        assert (sponsor.hub, sponsor.organization, sponsor.tier, sponsor.status) == (
            hub,
            "OnPoint Community Credit Union",
            Sponsor.Tier.GOLD,
            S.PROSPECT,
        )
        assert (sponsor.ask_cents, sponsor.amount_cents, sponsor.email, sponsor.created_by) == (
            250_000,
            0,
            "dana@onpoint.example",
            admin,
        )
        assert (sponsor.owner, sponsor.contact_name, sponsor.contact_role, sponsor.phone) == (
            "Augustin",
            "Dana Reyes",
            "Branch manager",
            "503-555-0100",
        )
        assert (sponsor.next_step, sponsor.follow_up_by, sponsor.fit_note) == (
            "Send sponsor deck",
            date(2026, 11, 10),
            "PLM and 16 guild accounts bank with them",
        )
        assert [(entry.text, entry.by, entry.date) for entry in sponsor.log_entries.all()] == [
            ("Added to tracker", "Augustin", timezone.localdate())
        ]
        assert "data-hq-sp-board" in body
        assert "data-hq-sp-subline" not in body
        assert "open data-hq-sp-add" in body
        assert 'name="organization" placeholder="e.g. OnPoint Community Credit Union"' in body
        assert 'value="OnPoint Community Credit Union"' in body  # the new row's edit form, not the add form

    def it_lands_a_plain_post_back_on_the_page_keeping_the_filter(hub, admin_client):
        response = admin_client.post(_add(hub), _add_post(filter="asked"), follow=True)

        assert response.redirect_chain == [(f"{_page(hub)}?filter=asked", 302)]
        assert "Sponsor added." in [str(message) for message in response.context["messages"]]

    def it_stores_a_blank_ask_as_zero_and_an_unset_tier_as_blank(hub, admin_client):
        admin_client.post(_add(hub), _add_post(ask="", tier="", follow_up_by="", owner=""))

        sponsor = Sponsor.objects.get()
        assert (sponsor.ask_cents, sponsor.tier, sponsor.follow_up_by) == (0, "", None)
        assert sponsor.log_entries.get().by == ""

    def it_refuses_a_sponsor_with_no_name_a_bad_email_or_a_negative_ask(hub, admin_client):
        for fields in ({"organization": "  "}, {"email": "not an email"}, {"ask": "-5"}, {"ask": "12.5"}):
            response = admin_client.post(_add(hub), _add_post(**fields), **HTMX)

            body = response.content.decode()
            assert response.status_code == 200
            assert 'class="pl-field-errors"' in body
            assert "open data-hq-sp-add" in body
            assert "HX-Trigger" not in response
        assert not Sponsor.objects.exists()

    def it_refuses_an_ask_past_a_million_dollars_as_a_form_error_not_a_server_error(hub, admin_client):
        # 30 million dollars is 3 billion cents, past the integer column's 2,147,483,647.
        response = admin_client.post(_add(hub), _add_post(ask="30000000"), **HTMX)

        body = response.content.decode()
        assert response.status_code == 200
        assert "Ensure this value is less than or equal to 1000000." in body
        assert 'max="1000000"' in body
        assert not Sponsor.objects.exists()

        admin_client.post(_add(hub), _add_post(ask="1000000"))

        assert Sponsor.objects.get().ask_cents == 100_000_000

    def it_renders_the_whole_page_again_for_a_plain_post_that_fails(hub, admin_client):
        response = admin_client.post(_add(hub), _add_post(organization=""))

        assert response.status_code == 200
        assert "data-hq-sp-subline" in response.content.decode()
        assert not Sponsor.objects.exists()

    def it_offers_one_tier_list_and_suggests_the_cards_named_members(card, admin_client, make_member):
        card.named_members.set([make_member(preferred_name="Dixie"), make_member(preferred_name="Augustin")])

        body = admin_client.get(_page(card.hub)).content.decode()

        assert (
            '<datalist id="hq-sponsor-team" data-hq-sp-team><option value="Augustin"></option>'
            '<option value="Dixie"></option></datalist>'
        ) in body
        assert body.count('list="hq-sponsor-team"') == 1
        assert [label for _, label in SponsorForm().fields["tier"].choices] == [  # type: ignore[attr-defined]
            "Not set",
            "Presenting ($5,000)",
            "Gold ($2,500)",
            "Silver ($1,000)",
            "Bar sponsor ($900)",
            "Community ($500)",
            "Friend (under $500)",
            "In-kind",
        ]
        for placeholder in ("e.g. Augustin", "e.g. Branch manager", "e.g. Send sponsor deck"):
            assert f'placeholder="{placeholder}"' in body
        assert ">Add sponsor</button>" in body
        assert "+ Add a sponsor</span>" in body


def describe_the_pipeline():
    def it_orders_committed_asked_prospect_received_declined_then_by_name(hub):
        declined = SponsorFactory(hub=hub, organization="Aardvark", status=S.DECLINED)
        received = SponsorFactory(hub=hub, organization="Bee", status=S.RECEIVED)
        prospect_b = SponsorFactory(hub=hub, organization="zebra", status=S.PROSPECT)
        prospect_a = SponsorFactory(hub=hub, organization="Yak", status=S.PROSPECT)
        asked = SponsorFactory(hub=hub, organization="Cat", status=S.ASKED)
        committed = SponsorFactory(hub=hub, organization="Dog", status=S.COMMITTED)

        assert list(hub.sponsors.all()) == [committed, asked, prospect_a, prospect_b, received, declined]

    @pytest.mark.parametrize(
        ("show", "expected"),
        [
            ("all", {"Prospect Co", "Asked Co", "Committed Co", "Received Co", "Declined Co"}),
            ("prospect", {"Prospect Co"}),
            ("asked", {"Asked Co"}),
            ("committed", {"Committed Co"}),
            ("received", {"Received Co"}),
            ("declined", {"Declined Co"}),
            ("donor2025", {"Asked Co"}),
        ],
    )
    def it_filters_by_status_or_last_years_gift(hub, show, expected):
        for status in S:
            SponsorFactory(hub=hub, organization=f"{status.label} Co", status=status, history="")
        hub.sponsors.filter(status=S.ASKED).update(history="Raffle prize, 2025")

        assert {sponsor.organization for sponsor in services.filtered_sponsors(hub, show)} == expected

    def it_shows_every_sponsor_for_a_filter_it_does_not_know(hub, admin_client):
        SponsorFactory(hub=hub, status=S.DECLINED, organization="Spec Declined")

        body = admin_client.get(f"{_page(hub)}?filter=nonsense").content.decode()

        assert services.sponsor_filter("nonsense") == "all"
        assert services.sponsor_filter("asked") == "asked"
        assert "Spec Declined" in body
        assert 'data-hq-sp-filter="all" aria-current="true"' in body

    def it_marks_the_filter_in_view_and_links_each_one(hub, admin_client):
        body = admin_client.get(f"{_page(hub)}?filter=committed").content.decode()

        assert f'href="{_page(hub)}" class="pl-hq-sp__filter" data-hq-sp-filter="all">All</a>' in body
        assert (
            f'href="{_page(hub)}?filter=committed" class="pl-hq-sp__filter" data-hq-sp-filter="committed" '
            'aria-current="true">Committed</a>'
        ) in body
        assert 'data-hq-sp-filter="donor2025">2025 donors</a>' in body

    def it_says_so_when_the_tracker_or_the_group_is_empty(hub, admin_client):
        assert "No sponsors yet. Add your first one above." in admin_client.get(_page(hub)).content.decode()

        SponsorFactory(hub=hub, status=S.PROSPECT)

        body = admin_client.get(f"{_page(hub)}?filter=received").content.decode()
        assert "data-hq-sp-empty>No sponsors in this group.</p>" in body

    def it_summarises_each_row_for_an_organiser(hub, admin_client):
        when = timezone.localdate() - timedelta(days=1)
        SponsorFactory(
            hub=hub,
            organization="Spec Bank",
            tier=Sponsor.Tier.GOLD,
            contact_name="Dana",
            contact_role="Branch manager",
            owner="Augustin",
            status=S.COMMITTED,
            amount_cents=250_000,
            next_step="Invoice",
            follow_up_by=when,
            history="$300 in 2025",
        )

        body = admin_client.get(_page(hub)).content.decode()

        assert "data-hq-sp-line>Gold ($2,500) · Dana, Branch manager · Asking: Augustin</small>" in body
        assert "data-hq-sp-donor>2025 donor</span>" in body
        assert 'data-hq-sp-status="committed">Committed</span>' in body
        assert f'class="pl-hq-sp__due" data-hq-sp-row-due>by {when:%b} {when.day}</small>' in body
        assert "data-hq-sp-amount>$2,500<small>committed</small>" in body
        assert "<strong>Last year (2025, as a donation):</strong> $300 in 2025</p>" in body

    def it_runs_a_fixed_number_of_queries_however_many_sponsors(hub, admin_client):
        def _count() -> int:
            with CaptureQueriesContext(connection) as queries:
                assert admin_client.get(_page(hub)).status_code == 200
            return len(queries)

        SponsorLogEntryFactory(sponsor=SponsorFactory(hub=hub))
        _count()  # the first request warms the session and the caches
        few = _count()
        for _ in range(4):
            SponsorLogEntryFactory(sponsor=SponsorFactory(hub=hub, follow_up_by=timezone.localdate()))

        assert _count() == few


def describe_editing_a_sponsor():
    def it_saves_the_row_over_htmx_and_brings_it_back_open(hub, admin_client):
        sponsor = SponsorFactory(hub=hub, organization="Spec Bank", ask_cents=100_000, email="old@bank.example")

        response = admin_client.post(
            _edit(sponsor),
            _edit_post(
                sponsor,
                amount="750",
                ask="1000",
                email="New@Bank.example",
                thanked="on",
                benefits="Logo on flyer",
                note="Prefers email",
                history="A $200 gift",
                tier=Sponsor.Tier.IN_KIND,
                filter="prospect",
            ),
            **HTMX,
        )

        sponsor.refresh_from_db()
        body = response.content.decode()
        assert _toast(response) == "Saved."
        assert (sponsor.ask_cents, sponsor.amount_cents, sponsor.email, sponsor.thanked) == (
            100_000,
            75_000,
            "new@bank.example",
            True,
        )
        assert (sponsor.benefits, sponsor.note, sponsor.history, sponsor.tier) == (
            "Logo on flyer",
            "Prefers email",
            "A $200 gift",
            Sponsor.Tier.IN_KIND,
        )
        assert body.lstrip().startswith(f'<details class="pl-hq-sp__row" data-hq-sp-row="{sponsor.pk}" open>')
        assert 'name="filter" value="prospect"' in body
        assert 'hx-target="closest [data-hq-sp-row]" hx-swap="outerHTML"' in body
        assert 'id="hq-sp-band" aria-label="Progress" data-hq-sp-band hx-swap-oob="true">' in body
        assert 'id="hq-sp-due" aria-labelledby="hq-sp-due-title" data-hq-sp-due hx-swap-oob="true">' in body
        for elsewhere in ("data-hq-sp-board", "data-hq-sp-add", "data-hq-sp-filters"):
            assert elsewhere not in body, elsewhere
        assert not sponsor.log_entries.exists()

    def it_moves_the_band_and_follow_up_with_the_saved_row(hub, admin_client):
        sponsor = SponsorFactory(hub=hub, organization="Spec Bank", status=S.ASKED, ask_cents=50_000)

        response = admin_client.post(
            _edit(sponsor),
            _edit_post(sponsor, status=S.RECEIVED, amount="400", follow_up_by=timezone.localdate().isoformat()),
            **HTMX,
        )

        body = response.content.decode()
        assert 'data-hq-sp-tile="received">' in body
        assert "data-hq-sp-figure>$400</strong>" in body
        assert "data-hq-sp-due-empty>" in body  # received closes the ask, so it leaves Follow up

    def it_keeps_a_row_whose_new_status_leaves_the_filter_in_view(hub, admin_client):
        sponsor = SponsorFactory(hub=hub, organization="Spec Bank", status=S.ASKED)
        other = SponsorFactory(hub=hub, status=S.ASKED)

        response = admin_client.post(
            _edit(sponsor), _edit_post(sponsor, status=S.COMMITTED, amount="900", filter="asked"), **HTMX
        )

        body = response.content.decode()
        assert _toast(response) == "Saved."
        assert f'data-hq-sp-row="{sponsor.pk}" open>' in body
        assert 'data-hq-sp-status="committed">Committed</span>' in body
        assert 'name="filter" value="asked"' in body
        assert f'data-hq-sp-row="{other.pk}"' not in body

    def it_logs_a_status_change_in_lees_words(hub, admin_client):
        sponsor = SponsorFactory(hub=hub, status=S.ASKED)

        admin_client.post(_edit(sponsor), _edit_post(sponsor, status=S.COMMITTED, amount="900"), **HTMX)
        admin_client.post(_edit(sponsor), _edit_post(sponsor, status=S.COMMITTED, next_step="Invoice"), **HTMX)

        assert [entry.text for entry in sponsor.log_entries.all()] == ["Status changed to Committed"]
        assert sponsor.log_entries.get().by == ""

    def it_brings_back_a_refused_edit_open_with_its_error_and_saves_nothing(hub, admin_client):
        sponsor = SponsorFactory(hub=hub, email="ok@bank.example")
        other = SponsorFactory(hub=hub)

        response = admin_client.post(_edit(sponsor), _edit_post(sponsor, email="nope", status=S.RECEIVED), **HTMX)

        body = response.content.decode()
        sponsor.refresh_from_db()
        assert response.status_code == 200
        assert "HX-Trigger" not in response
        assert (sponsor.email, sponsor.status) == ("ok@bank.example", S.PROSPECT)
        assert f'data-hq-sp-row="{sponsor.pk}" open>' in body
        assert f'data-hq-sp-row="{other.pk}"' not in body  # the other rows keep what is typed in them
        assert 'class="pl-field-errors"' in body
        assert 'value="nope"' in body
        assert 'hx-swap-oob="true"' in body

    def it_brings_back_a_refused_edit_on_a_row_outside_the_filter(hub, admin_client):
        sponsor = SponsorFactory(hub=hub, organization="Spec Outside", status=S.PROSPECT)
        inside = SponsorFactory(hub=hub, organization="Spec Inside", status=S.ASKED)
        outside = SponsorFactory(hub=hub, organization="Spec Also Outside", status=S.DECLINED)
        data = _edit_post(sponsor, email="nope", filter="asked")

        fragment = admin_client.post(_edit(sponsor), data, **HTMX).content.decode()
        page = admin_client.post(_edit(sponsor), data).content.decode()

        for body in (fragment, page):
            assert f'data-hq-sp-row="{sponsor.pk}" open>' in body
            assert 'class="pl-field-errors"' in body
            assert 'value="nope"' in body
        assert "data-hq-sp-subline" in page
        assert 'data-hq-sp-filter="asked" aria-current="true"' in page
        assert f'data-hq-sp-row="{inside.pk}">' in page
        assert f'data-hq-sp-row="{outside.pk}"' not in page
        assert page.index("Spec Inside") < page.index("Spec Outside")  # pipeline order: asked before prospect

    def it_refuses_an_amount_past_a_million_dollars_as_a_form_error(hub, admin_client):
        sponsor = SponsorFactory(hub=hub)

        response = admin_client.post(_edit(sponsor), _edit_post(sponsor, amount="30000000", status=S.RECEIVED), **HTMX)

        sponsor.refresh_from_db()
        assert response.status_code == 200
        assert "Ensure this value is less than or equal to 1000000." in response.content.decode()
        assert (sponsor.amount_cents, sponsor.status) == (0, S.PROSPECT)

    def it_lands_a_plain_post_back_on_the_page_with_a_message(hub, admin_client):
        sponsor = SponsorFactory(hub=hub, organization="Spec Bank")

        response = admin_client.post(_edit(sponsor), _edit_post(sponsor, filter="declined"), follow=True)

        assert response.redirect_chain == [(f"{_page(hub)}?filter=declined", 302)]
        assert "Spec Bank saved." in [str(message) for message in response.context["messages"]]

    def it_is_a_404_for_another_hubs_sponsor(hub, admin_client):
        elsewhere = SponsorFactory()

        url = reverse("hq:sponsor_edit", args=[hub.slug, elsewhere.pk])

        assert admin_client.post(url, _edit_post(elsewhere)).status_code == 404

    def it_starts_the_row_on_whole_dollars_and_blanks_for_none():
        sponsor = Sponsor(ask_cents=250_000, amount_cents=0, status=S.ASKED, thanked=True)

        initial = SponsorEditForm.initial_for(sponsor)

        assert (initial["ask"], initial["amount"], initial["status"], initial["thanked"]) == (2500, None, S.ASKED, True)

    def it_labels_the_row_in_lees_words(hub, admin_client):
        SponsorFactory(hub=hub)

        body = admin_client.get(_page(hub)).content.decode()

        for label in ("Status", "Ask ($)", "Committed or received ($)", "Thank-you sent", "Benefits promised", "Notes"):
            assert label in body, label
        assert 'placeholder="e.g. Logo on flyer, 4 drink tickets"' in body
        assert ">Save</button>" in body


def describe_the_contact_log():
    def it_adds_a_line_over_htmx_newest_first(hub, admin_client):
        sponsor = SponsorFactory(hub=hub)
        SponsorLogEntryFactory(sponsor=sponsor, text="Older line")

        response = admin_client.post(
            _log(sponsor), {f"log-{sponsor.pk}-text": " Called, left voicemail ", f"log-{sponsor.pk}-by": "Lee"}, **HTMX
        )

        body = response.content.decode()
        assert _toast(response) == "Added to the log."
        assert [(entry.text, entry.by) for entry in sponsor.log_entries.all()] == [
            ("Called, left voicemail", "Lee"),
            ("Older line", ""),
        ]
        assert body.index("Called, left voicemail") < body.index("Older line")
        assert '<span class="pl-hq-sp__muted">(Lee)</span>' in body
        assert body.lstrip().startswith(f'<details class="pl-hq-sp__row" data-hq-sp-row="{sponsor.pk}" open>')
        assert 'data-hq-sp-band hx-swap-oob="true">' in body
        assert 'data-hq-sp-due hx-swap-oob="true">' in body
        assert "data-hq-sp-board" not in body

    def it_refuses_a_blank_line(hub, admin_client):
        sponsor = SponsorFactory(hub=hub)

        response = admin_client.post(
            _log(sponsor), {f"log-{sponsor.pk}-text": "  ", f"log-{sponsor.pk}-by": "Spec Half Typed"}, **HTMX
        )

        body = response.content.decode()
        assert "HX-Trigger" not in response
        assert 'class="pl-field-errors"' in body
        assert f'data-hq-sp-row="{sponsor.pk}" open>' in body
        assert 'value="Spec Half Typed"' in body
        assert not sponsor.log_entries.exists()

    def it_renders_the_whole_page_with_the_row_open_for_a_plain_post_that_fails(hub, admin_client):
        sponsor = SponsorFactory(hub=hub, status=S.DECLINED)

        response = admin_client.post(_log(sponsor), {f"log-{sponsor.pk}-text": "", "filter": "asked"})

        body = response.content.decode()
        assert "data-hq-sp-subline" in body
        assert f'data-hq-sp-row="{sponsor.pk}" open>' in body
        assert 'class="pl-field-errors"' in body

    def it_is_a_404_for_another_hubs_sponsor(hub, admin_client):
        elsewhere = SponsorFactory()

        url = reverse("hq:sponsor_log", args=[hub.slug, elsewhere.pk])

        assert admin_client.post(url, {f"log-{elsewhere.pk}-text": "Called"}).status_code == 404
        assert not SponsorLogEntry.objects.exists()

    def it_lands_a_plain_post_back_on_the_page(hub, admin_client):
        sponsor = SponsorFactory(hub=hub)

        response = admin_client.post(_log(sponsor), {f"log-{sponsor.pk}-text": "Emailed"}, follow=True)

        assert response.redirect_chain == [(_page(hub), 302)]
        assert "Added to the log." in [str(message) for message in response.context["messages"]]

    def it_says_so_when_nothing_is_logged(hub, admin_client):
        SponsorFactory(hub=hub)

        body = admin_client.get(_page(hub)).content.decode()

        assert "data-hq-sp-log-empty>No contact logged yet.</p>" in body
        assert 'placeholder="e.g. Called, left voicemail"' in body


def describe_deleting_a_sponsor():
    def it_takes_the_sponsor_and_its_log_off_for_an_organiser(card, member):
        card.named_members.set([member])
        sponsor = SponsorFactory(hub=card.hub, organization="Spec Bank")
        SponsorLogEntryFactory(sponsor=sponsor)

        response = signed_in(member).post(_delete(sponsor), {"filter": "asked"}, follow=True)

        assert response.redirect_chain == [(f"{_page(card.hub)}?filter=asked", 302)]
        assert "Spec Bank is off the tracker." in [str(message) for message in response.context["messages"]]
        assert not Sponsor.objects.exists()
        assert not SponsorLogEntry.objects.exists()

    def it_asks_first_through_the_confirm_modal(hub, admin_client):
        sponsor = SponsorFactory(hub=hub)

        body = admin_client.get(_page(hub)).content.decode()

        assert f"$dispatch('open-confirm', 'delete-sponsor-{sponsor.pk}')" in body
        assert f'action="{_delete(sponsor)}"' in body

    def it_is_a_404_for_another_hubs_sponsor(hub, admin_client):
        elsewhere = SponsorFactory()

        response = admin_client.post(reverse("hq:sponsor_delete", args=[hub.slug, elsewhere.pk]))

        assert response.status_code == 404
        assert Sponsor.objects.filter(pk=elsewhere.pk).exists()


def describe_the_csv():
    def it_streams_one_row_per_sponsor_in_pipeline_order(hub, admin_client):
        SponsorFactory(
            hub=hub,
            organization="Spec Bank",
            tier=Sponsor.Tier.GOLD,
            status=S.RECEIVED,
            ask_cents=250_000,
            amount_cents=250_050,
            email="dana@bank.example",
            follow_up_by=date(2026, 11, 10),
            thanked=True,
            note="=HYPERLINK(1)",
        )
        SponsorFactory(hub=hub, organization="Spec Cafe", status=S.COMMITTED)
        SponsorFactory(organization="Elsewhere")

        response = admin_client.get(_csv(hub))

        rows = list(csv.reader(io.StringIO(b"".join(response.streaming_content).decode())))
        assert response["Content-Type"] == "text/csv"
        assert response["Content-Disposition"].startswith(f'attachment; filename="{hub.slug}-sponsors-')
        assert rows[0] == SPONSORS_CSV_HEADERS
        assert [row[0] for row in rows[1:]] == ["Spec Cafe", "Spec Bank"]
        bank = dict(zip(SPONSORS_CSV_HEADERS, rows[2], strict=True))
        assert (bank["Tier"], bank["Status"], bank["Ask"], bank["Committed or received"]) == (
            "Gold ($2,500)",
            "Received",
            "2500.00",
            "2500.50",
        )
        assert (bank["Email"], bank["Follow up by"], bank["Thank-you sent"], bank["Notes"]) == (
            "dana@bank.example",
            "2026-11-10",
            "yes",
            "'=HYPERLINK(1)",
        )
        cafe = dict(zip(SPONSORS_CSV_HEADERS, rows[1], strict=True))
        assert (cafe["Tier"], cafe["Follow up by"], cafe["Thank-you sent"]) == ("", "", "no")

    def it_links_the_download_out_of_hx_boost(hub, admin_client):
        body = admin_client.get(_page(hub)).content.decode()

        assert f'href="{_csv(hub)}" class="pl-hq-sp__csv" hx-boost="false" data-pl-download' in body


def describe_the_tiers():
    def it_shows_lees_six_draft_tiers_to_everyone_the_card_is_open_to(hub):
        body = Client().get(_page(hub)).content.decode()

        assert [tier.name for tier in SPONSOR_TIERS] == [
            "Presenting",
            "Gold",
            "Silver",
            "Bar sponsor",
            "Community",
            "In-kind",
        ]
        assert body.count("data-hq-sp-tier=") == 6
        assert "Sponsor tiers (draft): what they get</span>" in body
        assert (
            "<td><strong>Presenting</strong></td>\n            <td>$5,000</td>\n            <td>1</td>\n            "
            "<td>&quot;Holiday Bizarre presented by&quot; on the flyer, adventure map, signage and website · Top logo "
            "placement · Private team-building workshop at PLM for up to 12 · 10 wine wall tickets · Instagram reel "
            "and member newsletter feature · Thank-you from the stage</td>"
        ) in body
        assert (
            "Wine, kegs, gift cards and products · Name on the sponsor board · Wine counts toward the wine wall</td>"
            in body
        )

    def it_links_the_wine_line_to_the_wine_wall_when_that_door_is_open_to_the_viewer(hub):
        wine = HubCardFactory(hub=hub, kind=HubCard.Kind.WINE_WALL, audience=HubCard.Audience.ADMINS)

        assert "data-hq-sp-wine-link" not in Client().get(_page(hub)).content.decode()

        wine.audience = HubCard.Audience.PUBLIC
        wine.save(update_fields=["audience"])
        body = Client().get(_page(hub)).content.decode()

        assert f'<a href="{wine.url}" data-hq-sp-wine-link>{WINE_WALL_BENEFIT}</a>' in body


def describe_adopting_the_placeholder():
    def it_turns_the_sponsorship_placeholder_into_the_live_door_in_place(open_hub, member):
        placeholder = HubCardFactory(
            hub=open_hub,
            kind=HubCard.Kind.PLACEHOLDER,
            title="Sponsorship Tracker",
            bullets="Who we asked and where it stands\nLevels and what each sponsor gets\nLogos for the flyer and the wall",
            button_text="Track sponsors",
            audience=HubCard.Audience.MEMBERS,
            named=[member],
        )

        result = open_hub.seed_default_cards()

        card = HubCard.objects.get(pk=placeholder.pk)
        assert "Sponsorship Tracker" in result.adopted
        assert (card.kind, card.audience, card.button_text) == (
            HubCard.Kind.SPONSORSHIP,
            HubCard.Audience.MEMBERS,
            "Enter sponsorships",
        )
        assert list(card.named_members.all()) == [member]
        assert card.url == _page(open_hub)


def describe_the_model():
    def it_reads_as_its_organization_and_lowercases_the_email(hub):
        sponsor = SponsorFactory(hub=hub, organization="Spec Bank", email="  Dana@Bank.Example ")
        entry = SponsorLogEntryFactory(sponsor=sponsor, text="Called")

        assert str(sponsor) == "Spec Bank"
        assert sponsor.email == "dana@bank.example"
        assert str(entry) == "Spec Bank: Called"
        assert entry.date == timezone.localdate()

    @pytest.mark.parametrize(
        ("status", "follow_up_by", "expected"),
        [
            (S.PROSPECT, date(2026, 11, 1), True),
            (S.ASKED, date(2026, 11, 1), True),
            (S.COMMITTED, date(2026, 11, 1), True),
            (S.RECEIVED, date(2026, 11, 1), False),
            (S.DECLINED, date(2026, 11, 1), False),
            (S.ASKED, None, False),
        ],
    )
    def it_follows_up_only_while_the_ask_is_open_and_dated(status, follow_up_by, expected):
        assert Sponsor(status=status, follow_up_by=follow_up_by).is_following_up is expected

    @pytest.mark.parametrize(
        ("status", "ask", "amount", "figure", "label"),
        [
            (S.RECEIVED, 100_000, 90_000, "$900", "received"),
            (S.COMMITTED, 100_000, 90_000, "$900", "committed"),
            (S.ASKED, 100_000, 90_000, "$900", "committed"),
            (S.ASKED, 100_050, 0, "$1,000.50", "ask"),
            (S.PROSPECT, 0, 0, "–", ""),
        ],
    )
    def it_shows_the_amount_else_the_ask_else_a_dash(status, ask, amount, figure, label):
        sponsor = Sponsor(status=status, ask_cents=ask, amount_cents=amount)

        assert (sponsor.figure_display, sponsor.figure_label) == (figure, label)

    @pytest.mark.parametrize(
        ("fields", "line"),
        [
            ({}, ""),
            ({"tier": Sponsor.Tier.FRIEND}, "Friend (under $500)"),
            ({"contact_name": "Dana"}, "Dana"),
            ({"contact_role": "Owner"}, ""),
            ({"contact_name": "Dana", "contact_role": "Owner", "owner": "Lee"}, "Dana, Owner · Asking: Lee"),
        ],
    )
    def it_puts_the_tier_contact_and_asker_on_the_organisers_line(fields, line):
        assert Sponsor(**fields).organiser_line == line
