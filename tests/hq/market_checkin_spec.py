"""Market Check-in (Track D, part 4): the door, its members-only gate, the hero with Lee's words and figures, the
artist's market report (one per member, private), the volunteer hours log with own-entry delete, the thank-you
board, the organisers' reports and hours pages with their CSVs, the confirmed vendor table beside a report, the
roster's Hours logged column, and the seed's adoption of the placeholder."""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from hq import services
from hq.exports import MARKET_REPORTS_CSV_HEADERS, VOLUNTEER_HOURS_CSV_HEADERS
from hq.forms import HOURS_ERROR, HOURS_STEP_ERROR, REPORT_ITEMS_MAX, MarketReportForm, VolunteerHoursForm
from hq.holiday_bizarre import CHECKIN_FOOTER, CHECKIN_THANKS
from hq.models import BUILT_DOORS, HubCard, MarketReport, VendorTable, VolunteerHours, format_hours
from hub.view_as import ROLE_GUEST, ROLE_MEMBER
from membership.models import Member
from tests.hq.conftest import signed_in
from tests.hq.factories import HubCardFactory, ShiftSignupFactory, VendorTableFactory, VolunteerShiftFactory
from tests.membership.factories import CommunityEventFactory, GuildFactory, GuildMembershipFactory, MemberFactory

pytestmark = pytest.mark.django_db

User = get_user_model()
HTMX = {"HTTP_HX_REQUEST": "true"}
T = VolunteerHours.Task


def _page(hub: Any) -> str:
    return reverse("hq:checkin", args=[hub.slug])


def _report(hub: Any) -> str:
    return reverse("hq:checkin_report", args=[hub.slug])


def _log(hub: Any) -> str:
    return reverse("hq:checkin_hours_log", args=[hub.slug])


def _delete(entry: VolunteerHours) -> str:
    return reverse("hq:checkin_hours_delete", args=[entry.hub.slug, entry.pk])


def _reports_page(hub: Any) -> str:
    return reverse("hq:checkin_reports", args=[hub.slug])


def _hours_page(hub: Any) -> str:
    return reverse("hq:checkin_hours", args=[hub.slug])


def _reports_csv(hub: Any) -> str:
    return reverse("hq:checkin_reports_csv", args=[hub.slug])


def _hours_csv(hub: Any) -> str:
    return reverse("hq:checkin_hours_csv", args=[hub.slug])


def _report_post(**fields: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"name": "Riverbend Ceramics", "guild": "", "items_sold": "", "sales": "", "experience": ""}
    data.update(fields)
    return data


def _hours_post(**fields: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"date": "2026-11-03", "hours": "2.5", "task": T.FLYERING, "note": ""}
    data.update(fields)
    return data


def _messages(response: Any) -> list[str]:
    return [str(message) for message in response.context["messages"]]


def _hours(hub: Any, member: Member, hours: str = "1", **fields: Any) -> VolunteerHours:
    return VolunteerHours.objects.create(hub=hub, member=member, hours=Decimal(hours), **fields)


def _preview(client: Client, role: str) -> None:
    session = client.session
    session["view_as_role"] = role
    session.save()


@pytest.fixture
def lee(make_member) -> Member:
    return make_member(preferred_name="Lee Organiser")


@pytest.fixture
def card(open_hub, lee) -> HubCard:
    """The Market Check-in door on the open hub, open to everyone, with Lee named on it."""
    return HubCardFactory(
        hub=open_hub,
        kind=HubCard.Kind.MARKET_CHECKIN,
        title="Market Check-in",
        audience=HubCard.Audience.PUBLIC,
        named=[lee],
    )


@pytest.fixture
def hub(card):
    return card.hub


@pytest.fixture
def lee_client(lee) -> Client:
    return signed_in(lee)


@pytest.fixture
def artist(make_member) -> Member:
    return make_member(preferred_name="Rae Artist", _pre_signup_email="rae@studio.example")


@pytest.fixture
def artist_client(artist) -> Client:
    return signed_in(artist)


def describe_the_door():
    def it_is_a_built_door_that_opens_the_page_from_the_hq_page(card):
        body = Client().get(card.hub.get_absolute_url()).content.decode()

        assert HubCard.Kind.MARKET_CHECKIN in BUILT_DOORS
        assert card.url == _page(card.hub) == f"/hq/{card.hub.slug}/check-in/"
        assert f'href="{_page(card.hub)}" class="pl-hq-card" data-hq-card-link' in body
        assert "M8 9l1.5 1.5L12 8" in body  # the checked list icon

    def it_carries_lees_hero_with_the_events_day_and_the_figures(hub, artist, lee):
        starts = timezone.now() + timedelta(days=12)
        hub.event = CommunityEventFactory(community=True, starts_at=starts, ends_at=starts + timedelta(hours=5))
        hub.save(update_fields=["event"])
        _hours(hub, artist, "2.5")
        _hours(hub, artist, "1")
        _hours(hub, lee, "0.25")
        day = timezone.localdate(starts)

        body = Client().get(_page(hub)).content.decode().replace("&#x27;", "'")

        assert f"data-hq-ci-eyebrow>Past Lives Makerspace · {hub.title}</p>" in body
        assert 'id="hq-ci-title">Market Check-in</h1>' in body
        thanks = CHECKIN_THANKS.format(on_date=f" on {day:%B} {day.day}")
        assert f"data-hq-ci-thanks>{thanks}</p>" in body
        assert thanks.startswith("Thank you for being part of the 2nd Annual Holiday Bizarre on ")
        assert thanks.endswith(
            "Every entry helps us show what's possible when a community works together toward one goal!"
        )
        countdown = body.split('data-hq-ci-stat="countdown"')[1].split("</div>")[0]
        assert ">12</strong>" in countdown and ">days to go</span>" in countdown
        hours = body.split('data-hq-ci-stat="hours"')[1].split("</div>")[0]
        assert "data-hq-ci-figure>3.75</strong>" in hours and "volunteer hours so far" in hours
        people = body.split('data-hq-ci-stat="volunteers"')[1].split("</div>")[0]
        assert "data-hq-ci-figure>2</strong>" in people and ">volunteers</span>" in people
        assert f"data-hq-ci-footer>{CHECKIN_FOOTER}</p>" in body
        assert CHECKIN_FOOTER == "Questions? Email Lee at lee@pastlives.space."
        assert f'href="{hub.get_absolute_url()}" class="pl-hq-ci__back" data-hq-ci-back>&larr; HQ</a>' in body

    def it_leaves_out_the_day_and_the_countdown_without_an_event(hub):
        body = Client().get(_page(hub)).content.decode().replace("&#x27;", "'")

        assert f"data-hq-ci-thanks>{CHECKIN_THANKS.format(on_date='')}</p>" in body
        assert "2nd Annual Holiday Bizarre. Artists" in body
        assert 'data-hq-ci-stat="countdown"' not in body
        assert "data-hq-ci-figure>0</strong>" in body


def describe_who_may_check_in():
    def it_shows_a_signed_out_visitor_the_hero_and_board_with_a_sign_in_link(hub, artist):
        _hours(hub, artist, "3")

        body = Client().get(_page(hub)).content.decode()

        assert "data-hq-ci-hero" in body and "data-hq-ci-board" in body
        assert "Rae Artist" in body
        assert f'href="/accounts/login/?next=%2Fhq%2F{hub.slug}%2Fcheck-in%2F"' in body
        assert ">Sign in to add your report or log hours</a>" in body
        assert "data-hq-ci-report-form" not in body
        assert "data-hq-ci-hours-form" not in body
        assert "data-hq-ci-members-only" not in body

    # A FORMER or SUSPENDED account never reaches the page: the lockout middleware turns it away first.
    @pytest.mark.parametrize("status", [Member.Status.GUEST, Member.Status.INVITED])
    def it_shows_a_signed_in_account_that_is_not_active_the_page_without_the_forms(hub, make_member, status):
        client = signed_in(make_member(status=status))

        response = client.get(_page(hub))

        body = response.content.decode()
        assert response.status_code == 200
        assert "data-hq-ci-members-only>Market reports and volunteer hours are for Past Lives members.</p>" in body
        assert "data-hq-ci-sign-in" not in body
        assert "data-hq-ci-report-form" not in body and "data-hq-ci-hours-form" not in body

    def it_gives_an_active_member_both_forms(hub, artist_client):
        body = artist_client.get(_page(hub)).content.decode()

        assert "data-hq-ci-report-form" in body and "data-hq-ci-hours-form" in body
        assert "data-hq-ci-sign-in" not in body and "data-hq-ci-members-only" not in body
        assert "data-hq-ci-staff" not in body

    def it_gives_a_named_member_who_is_not_active_the_forms_and_the_organisers_links(card, make_member):
        named = make_member(status=Member.Status.INVITED)
        card.named_members.add(named)

        body = signed_in(named).get(_page(card.hub)).content.decode()

        assert "data-hq-ci-report-form" in body
        assert f'href="{_reports_page(card.hub)}"' in body and f'href="{_hours_page(card.hub)}"' in body

    def it_renders_a_previewing_admin_as_the_viewer_but_lets_them_act(hub, admin, admin_client):
        _preview(admin_client, ROLE_GUEST)

        body = admin_client.get(_page(hub)).content.decode()

        assert "data-hq-ci-members-only" in body
        assert "data-hq-ci-report-form" not in body and "data-hq-ci-staff" not in body
        assert admin_client.post(_report(hub), _report_post()).status_code == 302
        assert admin_client.post(_log(hub), _hours_post()).status_code == 302
        assert MarketReport.objects.get().member == admin
        assert VolunteerHours.objects.get().member == admin
        _preview(admin_client, ROLE_MEMBER)
        body = admin_client.get(_page(hub)).content.decode()
        assert "data-hq-ci-report-form" in body and "data-hq-ci-staff" not in body

    def it_keeps_a_previewing_admins_errors_on_a_refused_plain_post(hub, admin_client):
        _preview(admin_client, ROLE_GUEST)

        hours = admin_client.post(_log(hub), _hours_post(hours="1.3"), follow=True)
        report = admin_client.post(_report(hub), _report_post(name=""), follow=True)

        assert hours.redirect_chain == [(_page(hub), 302)]
        assert HOURS_STEP_ERROR in _messages(hours)
        assert report.redirect_chain == [(_page(hub), 302)]
        assert "This field is required." in _messages(report)
        assert not VolunteerHours.objects.exists() and not MarketReport.objects.exists()

    def it_renders_a_members_refused_plain_post_on_the_page_with_its_form(hub, artist_client):
        response = artist_client.post(_log(hub), _hours_post(hours="1.3"))

        assert response.status_code == 200
        assert HOURS_STEP_ERROR in response.content.decode()

    def it_sends_a_signed_out_visitor_through_login_on_a_members_card(card):
        card.audience = HubCard.Audience.MEMBERS
        card.save(update_fields=["audience"])

        response = Client().get(_page(card.hub))

        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def it_is_a_404_when_hidden_for_all_but_admins_and_refuses_their_posts(card, artist, artist_client, admin_client):
        entry = _hours(card.hub, artist)
        card.is_hidden = True
        card.save(update_fields=["is_hidden"])

        assert artist_client.get(_page(card.hub)).status_code == 404
        assert artist_client.post(_report(card.hub), _report_post()).status_code == 404
        assert artist_client.post(_log(card.hub), _hours_post()).status_code == 404
        assert artist_client.post(_delete(entry)).status_code == 404
        assert admin_client.get(_page(card.hub)).status_code == 200
        assert not MarketReport.objects.exists()
        assert VolunteerHours.objects.count() == 1

    def it_refuses_an_active_member_on_an_admins_card_but_admits_a_named_one(card, artist_client, lee_client):
        card.audience = HubCard.Audience.ADMINS
        card.save(update_fields=["audience"])

        assert artist_client.get(_page(card.hub)).status_code == 404
        assert artist_client.post(_report(card.hub), _report_post()).status_code == 403
        assert lee_client.post(_report(card.hub), _report_post()).status_code == 302

    def it_answers_every_post_with_a_403_for_anyone_who_may_not_check_in(hub, artist, make_member):
        entry = _hours(hub, artist)
        for client in (Client(), signed_in(make_member(status=Member.Status.GUEST))):
            assert client.post(_report(hub), _report_post()).status_code == 403
            assert client.post(_log(hub), _hours_post()).status_code == 403
            assert client.post(_delete(entry)).status_code == 403
        assert not MarketReport.objects.exists()
        assert list(VolunteerHours.objects.all()) == [entry]

    def it_takes_posts_only(hub, artist, artist_client):
        entry = _hours(hub, artist)

        for url in (_report(hub), _log(hub), _delete(entry)):
            assert artist_client.get(url).status_code == 405, url

    def it_is_a_404_for_another_hubs_entry_and_a_hub_with_no_check_in_door(hub, artist, artist_client):
        elsewhere = _hours(
            HubCardFactory(kind=HubCard.Kind.MARKET_CHECKIN, audience=HubCard.Audience.PUBLIC).hub, artist
        )

        assert artist_client.post(reverse("hq:checkin_hours_delete", args=[hub.slug, elsewhere.pk])).status_code == 404
        assert VolunteerHours.objects.filter(pk=elsewhere.pk).exists()
        hub.cards.all().delete()
        assert artist_client.get(_page(hub)).status_code == 404
        assert artist_client.post(_report(hub), _report_post()).status_code == 404
        assert artist_client.post(_log(hub), _hours_post()).status_code == 404


def describe_the_market_report():
    def it_saves_a_first_report_over_htmx_with_lees_message(hub, artist, artist_client):
        guild = GuildFactory(name="Ceramics Guild")

        response = artist_client.post(
            _report(hub), _report_post(guild=str(guild.pk), experience=" Busy night "), **HTMX
        )

        report = MarketReport.objects.get()
        body = response.content.decode()
        assert response.status_code == 200
        assert (report.hub, report.member, report.name, report.guild) == (hub, artist, "Riverbend Ceramics", guild)
        assert (report.items_sold, report.sales_cents, report.experience) == (None, 0, "Busy night")
        assert body.lstrip().startswith('<section class="pl-hq-ci__card" aria-labelledby="hq-ci-report-title"')
        assert "data-hq-ci-saved>Saved</span>" in body
        assert "data-hq-ci-report-submit>Update my report</button>" in body
        assert "data-hq-ci-report-msg>Saved! Come back after the market to add your sales.</span>" in body
        assert "data-hq-ci-hero" not in body

    def it_congratulates_on_the_sales_and_keeps_the_cents(hub, artist_client):
        response = artist_client.post(_report(hub), _report_post(items_sold="14", sales="1234.5"), **HTMX)

        report = MarketReport.objects.get()
        assert (report.items_sold, report.sales_cents, report.sales_display) == (14, 123450, "$1,234.50")
        assert "data-hq-ci-report-msg>Saved! $1,234.50 in sales. Congratulations!</span>" in response.content.decode()

    def it_updates_the_one_report_the_second_time(hub, artist, artist_client):
        artist_client.post(_report(hub), _report_post(), **HTMX)
        first = MarketReport.objects.get()

        artist_client.post(_report(hub), _report_post(name="Riverbend", items_sold="3", sales="40"), **HTMX)

        report = MarketReport.objects.get()
        assert report.pk == first.pk
        assert (report.name, report.items_sold, report.sales_cents) == ("Riverbend", 3, 4000)
        assert report.updated_at >= first.updated_at

    def it_starts_a_new_report_with_save_and_no_chip(hub, artist_client):
        body = artist_client.get(_page(hub)).content.decode()

        assert "data-hq-ci-report-submit>Save my report</button>" in body
        assert "data-hq-ci-saved" not in body
        assert "Only you and Past Lives staff can see your report." in body
        for placeholder in (
            "e.g. Riverbend Ceramics",
            "After the market",
            "e.g. Steady crowd all night, and two people asked about classes!",
        ):
            assert f'placeholder="{placeholder}"' in body

    def it_renders_a_saved_report_back_into_the_form(hub, artist, artist_client):
        guild = GuildFactory(name="Glass Guild")
        MarketReport.objects.create(
            hub=hub,
            member=artist,
            name="Spec Glassworks",
            guild=guild,
            items_sold=9,
            sales_cents=42050,
            experience="Fun",
        )

        body = artist_client.get(_page(hub)).content.decode()

        assert 'value="Spec Glassworks"' in body
        assert f'<option value="{guild.pk}" selected>Glass Guild</option>' in body
        assert 'value="9"' in body and 'value="420.50"' in body and 'value="Fun"' in body
        assert "data-hq-ci-saved>Saved</span>" in body

    def it_prefills_the_members_name_and_their_one_active_guild(hub, artist, artist_client):
        guild = GuildFactory(name="Woodshop Guild")
        GuildMembershipFactory(member=artist, guild=guild)
        GuildMembershipFactory(member=artist, guild=GuildFactory(name="Spec Sleeping Guild", is_active=False))

        body = artist_client.get(_page(hub)).content.decode()

        assert 'value="Rae Artist"' in body
        assert f'<option value="{guild.pk}" selected>Woodshop Guild</option>' in body

    def it_leaves_the_guild_unpicked_for_a_member_of_two(hub, artist):
        GuildMembershipFactory(member=artist, guild=GuildFactory(name="Metal Guild"))
        GuildMembershipFactory(member=artist, guild=GuildFactory(name="Leather Guild"))

        assert services.report_initial(artist, None) == {"name": "Rae Artist", "guild": None}

    def it_offers_only_the_active_guilds_and_not_in_a_guild(hub, artist_client):
        GuildFactory(name="Woodshop Guild")
        GuildFactory(name="Ceramics Guild")
        sleeping = GuildFactory(name="Spec Sleeping Guild", is_active=False)

        form = MarketReportForm(guilds=services.active_guilds())

        labels = [label for _, label in form.fields["guild"].choices]  # type: ignore[attr-defined]
        assert labels == ["Not in a guild", "Ceramics Guild", "Woodshop Guild"]
        response = artist_client.post(_report(hub), _report_post(guild=str(sleeping.pk)), **HTMX)
        assert 'class="pl-field-errors"' in response.content.decode()
        assert not MarketReport.objects.exists()

    @pytest.mark.parametrize(
        "fields",
        [
            {"name": "  "},
            {"sales": "-1"},
            {"sales": "1000000.01"},
            {"sales": "12.345"},
            {"items_sold": "-1"},
            {"items_sold": str(REPORT_ITEMS_MAX + 1)},
            {"items_sold": "2.5"},
            {"guild": "999999"},
            {"experience": "x" * 281},
        ],
    )
    def it_refuses_a_bad_report_and_saves_nothing(hub, artist_client, fields):
        response = artist_client.post(_report(hub), _report_post(**fields), **HTMX)

        body = response.content.decode()
        assert response.status_code == 200
        assert 'class="pl-field-errors"' in body
        assert "data-hq-ci-report-msg" not in body
        assert "data-hq-ci-saved" not in body
        assert not MarketReport.objects.exists()

    def it_takes_the_largest_figures_the_form_allows(hub, artist_client):
        artist_client.post(_report(hub), _report_post(items_sold=str(REPORT_ITEMS_MAX), sales="1000000"), **HTMX)

        report = MarketReport.objects.get()
        assert (report.items_sold, report.sales_cents) == (REPORT_ITEMS_MAX, 100_000_000)

    def it_lands_a_plain_post_back_on_the_page_with_the_message(hub, artist_client):
        response = artist_client.post(_report(hub), _report_post(sales="20"), follow=True)

        assert response.redirect_chain == [(_page(hub), 302)]
        assert _messages(response) == ["Saved! $20 in sales. Congratulations!"]

    def it_renders_the_whole_page_with_the_errors_for_a_plain_post_that_fails(hub, artist_client):
        response = artist_client.post(_report(hub), _report_post(name=""))

        body = response.content.decode()
        assert response.status_code == 200
        assert "data-hq-ci-hero" in body and 'class="pl-field-errors"' in body
        assert not MarketReport.objects.exists()

    def it_never_shows_one_members_report_to_another(hub, artist, artist_client, make_member):
        other = make_member(preferred_name="Spec Other Maker")
        MarketReport.objects.create(
            hub=hub,
            member=other,
            name="Spec Secret Shop",
            # Inactive, so the guild select does not offer it and only a leak could print its name.
            guild=GuildFactory(name="Spec Secret Guild", is_active=False),
            items_sold=48613,
            sales_cents=987654,
            experience="Spec secret experience",
        )
        MarketReport.objects.create(hub=hub, member=artist, name="Rae Mine")

        bodies = [artist_client.get(_page(hub)).content.decode(), Client().get(_page(hub)).content.decode()]

        for body in bodies:
            for private in (
                "Spec Secret Shop",
                "Spec Secret Guild",
                "48613",
                "48,613",
                "9,876.54",
                "9876.54",
                "Spec secret experience",
            ):
                assert private not in body, private
        assert 'value="Rae Mine"' in bodies[0]
        assert "Rae Mine" not in bodies[1]


def describe_the_vendor_table_link():
    def it_names_the_confirmed_table_and_starts_the_report_from_it(hub, artist, artist_client):
        guild = GuildFactory(name="Jewelry Guild")
        VendorTableFactory(
            hub=hub,
            name="Rae's Rings",
            guild=guild,
            email="RAE@studio.example",
            status=VendorTable.Status.CONFIRMED,
            table_no="7",
        )

        body = artist_client.get(_page(hub)).content.decode().replace("&#x27;", "'")

        assert "data-hq-ci-table>You're at table 7</p>" in body
        assert 'value="Rae\'s Rings"' in body
        assert f'<option value="{guild.pk}" selected>Jewelry Guild</option>' in body

    def it_says_the_table_is_confirmed_before_it_has_a_number(hub, artist_client):
        VendorTableFactory(hub=hub, email="rae@studio.example", status=VendorTable.Status.CONFIRMED)

        body = artist_client.get(_page(hub)).content.decode()

        assert "data-hq-ci-table>Your table is confirmed</p>" in body

    @pytest.mark.parametrize(
        "status", [VendorTable.Status.REQUESTED, VendorTable.Status.WAITLIST, VendorTable.Status.CANCELLED]
    )
    def it_says_nothing_for_a_request_that_is_not_confirmed(hub, artist_client, status):
        VendorTableFactory(hub=hub, name="Spec Waiting", email="rae@studio.example", status=status, table_no="3")

        body = artist_client.get(_page(hub)).content.decode()

        assert "data-hq-ci-table" not in body
        assert 'value="Rae Artist"' in body

    def it_ignores_another_hubs_table_and_another_address(hub, artist_client):
        VendorTableFactory(email="rae@studio.example", status=VendorTable.Status.CONFIRMED)
        VendorTableFactory(hub=hub, email="someone@else.example", status=VendorTable.Status.CONFIRMED)

        assert "data-hq-ci-table" not in artist_client.get(_page(hub)).content.decode()

    def it_keeps_a_saved_report_over_the_tables_words(hub, artist, artist_client):
        VendorTableFactory(
            hub=hub, name="Table Name", email="rae@studio.example", status=VendorTable.Status.CONFIRMED, table_no="2"
        )
        MarketReport.objects.create(hub=hub, member=artist, name="My Own Name")

        body = artist_client.get(_page(hub)).content.decode().replace("&#x27;", "'")

        assert 'value="My Own Name"' in body and 'value="Table Name"' not in body
        assert "You're at table 2" in body

    def it_finds_a_signed_in_members_table_in_two_queries_and_never_matches_a_blank_address(hub, make_member):
        signed = make_member(_pre_signup_email="solo@studio.example")
        assert EmailAddress.objects.filter(user=signed.user, primary=True, email="solo@studio.example").exists()
        table = VendorTableFactory(hub=hub, email="solo@studio.example", status=VendorTable.Status.CONFIRMED)
        VendorTableFactory(hub=hub, email="", status=VendorTable.Status.CONFIRMED)
        # As a request has it: the member read off the signed in user, so the user is already loaded.
        member = User.objects.get(pk=signed.user_id).member

        with CaptureQueriesContext(connection) as queries:
            assert services.confirmed_table(hub, member) == table
        # The member's primary EmailAddress, then the table.
        assert len(queries) == 2
        blank = make_member()
        blank.user.email = ""
        blank.user.save(update_fields=["email"])
        EmailAddress.objects.filter(user=blank.user).delete()
        blank = User.objects.get(pk=blank.user_id).member
        with CaptureQueriesContext(connection) as queries:
            assert services.confirmed_table(hub, blank) is None
        assert "vendortable" not in " ".join(query["sql"] for query in queries.captured_queries)


def describe_the_hours_log():
    def it_logs_hours_over_htmx_and_sends_the_figures_and_board_out_of_band(hub, artist, artist_client):
        response = artist_client.post(
            _log(hub), _hours_post(hours="2.5", task=T.WINE_WALL, note=" Picked up 3 bottles "), **HTMX
        )

        entry = VolunteerHours.objects.get()
        body = response.content.decode()
        assert (entry.hub, entry.member, entry.date, entry.hours) == (hub, artist, date(2026, 11, 3), Decimal("2.5"))
        assert (entry.task, entry.note) == (T.WINE_WALL, "Picked up 3 bottles")
        assert body.lstrip().startswith('<section class="pl-hq-ci__card" aria-labelledby="hq-ci-hours-title"')
        assert "data-hq-ci-hours-msg>Logged 2.5 hours. Thank you!</span>" in body
        assert 'id="hq-ci-stats" data-hq-ci-stats hx-swap-oob="true"' in body
        assert 'id="hq-ci-board" aria-labelledby="hq-ci-board-title" data-hq-ci-board hx-swap-oob="true"' in body
        assert "data-hq-ci-figure>2.5</strong>" in body
        assert "data-hq-ci-my-total>Your hours: 2.5</p>" in body
        assert "data-hq-ci-you>(you)</span>" in body
        # The next entry keeps the date and task; the hours and note clear.
        form = body.split("data-hq-ci-hours-form")[1].split("</form>")[0]
        assert 'value="2026-11-03"' in form
        assert '<option value="wine_wall" selected>Wine wall</option>' in form
        assert 'name="hours"' in form and 'name="hours" value=' not in form
        assert "Picked up" not in form

    def it_says_hour_for_exactly_one(hub, artist_client):
        response = artist_client.post(_log(hub), _hours_post(hours="1.00"), **HTMX)

        assert "data-hq-ci-hours-msg>Logged 1 hour. Thank you!</span>" in response.content.decode()

    @pytest.mark.parametrize("hours", ["0.25", "24", "1.75", "12.5"])
    def it_takes_quarter_hours_from_a_quarter_to_a_day(hub, artist_client, hours):
        artist_client.post(_log(hub), _hours_post(hours=hours), **HTMX)

        assert VolunteerHours.objects.get().hours == Decimal(hours)

    @pytest.mark.parametrize(
        ("hours", "error"),
        [
            ("0", HOURS_ERROR),
            ("0.2", HOURS_ERROR),
            ("24.25", HOURS_ERROR),
            ("-1", HOURS_ERROR),
            ("100", HOURS_ERROR),
            ("abc", HOURS_ERROR),
            ("", HOURS_ERROR),
            ("1.333", HOURS_STEP_ERROR),
            ("1.3", HOURS_STEP_ERROR),
        ],
    )
    def it_refuses_hours_out_of_range_or_not_in_quarters(hub, artist_client, hours, error):
        response = artist_client.post(_log(hub), _hours_post(hours=hours), **HTMX)

        body = response.content.decode()
        assert response.status_code == 200
        assert error in body
        assert "data-hq-ci-hours-msg" not in body
        assert not VolunteerHours.objects.exists()
        assert HOURS_ERROR == "Enter hours between 0.25 and 24."

    @pytest.mark.parametrize(("hours", "saved"), [("2.500", Decimal("2.50")), ("24.000", Decimal("24.00"))])
    def it_takes_a_quarter_hour_typed_with_trailing_zeros(hub, artist_client, hours, saved):
        response = artist_client.post(_log(hub), _hours_post(hours=hours), **HTMX)

        assert "data-hq-ci-hours-msg" in response.content.decode()
        assert VolunteerHours.objects.get().hours == saved
        assert VolunteerHoursForm(_hours_post(hours=hours)).is_valid()

    def it_refuses_a_task_not_on_lees_list(hub, artist_client):
        artist_client.post(_log(hub), _hours_post(task="napping"), **HTMX)

        assert not VolunteerHours.objects.exists()

    def it_offers_lees_tasks_and_starts_on_portland_today(hub, artist_client):
        form = VolunteerHoursForm()

        assert [label for _, label in form.fields["task"].choices] == [  # type: ignore[attr-defined]
            "Flyering",
            "Sponsor outreach",
            "Wine wall",
            "Guild outreach",
            "Setup and decor",
            "Admin and planning",
            "Social media",
            "Event night",
            "Teardown",
            "Other",
        ]
        body = artist_client.get(_page(hub)).content.decode()
        assert f'value="{timezone.localdate():%Y-%m-%d}"' in body
        assert 'placeholder="e.g. Dropped flyers on Division St"' in body
        assert "Flyering, outreach, setup, event night: it all counts." in body
        assert 'min="0.25" max="24" step="0.25"' in body
        assert "data-hq-ci-hours-submit>Log my hours</button>" in body

    def it_lists_the_members_own_entries_newest_date_first_with_the_total(hub, artist, artist_client, lee):
        older = _hours(hub, artist, "1.5", date=date(2026, 11, 1), task=T.FLYERING, note="Division St")
        newer = _hours(hub, artist, "2", date=date(2026, 11, 9), task=T.SETUP)
        _hours(hub, lee, "8", note="Spec someone else's note")

        body = artist_client.get(_page(hub)).content.decode().replace("&#x27;", "'")

        mine = body.split("data-hq-ci-mine")[1].split("</ul>")[0]
        assert "data-hq-ci-my-total>Your hours: 3.5</p>" in mine
        assert mine.index(f'data-hq-ci-entry="{newer.pk}"') < mine.index(f'data-hq-ci-entry="{older.pk}"')
        assert "Flyering · Division St" in mine and "Setup and decor" in mine
        assert mine.count("data-hq-ci-entry=") == 2
        assert "Spec someone else's note" not in body

    def it_lands_a_plain_post_back_on_the_page_with_the_message(hub, artist_client):
        response = artist_client.post(_log(hub), _hours_post(hours="3"), follow=True)

        assert response.redirect_chain == [(_page(hub), 302)]
        assert _messages(response) == ["Logged 3 hours. Thank you!"]

    def it_renders_the_whole_page_with_the_errors_for_a_plain_post_that_fails(hub, artist_client):
        response = artist_client.post(_log(hub), _hours_post(hours="30"))

        body = response.content.decode()
        assert response.status_code == 200
        assert "data-hq-ci-hero" in body and HOURS_ERROR in body


def describe_deleting_hours():
    def it_lets_a_member_delete_their_own_entry(hub, artist, artist_client):
        entry = _hours(hub, artist, "2.5", date=date(2026, 11, 3))

        response = artist_client.post(_delete(entry), follow=True)

        assert response.redirect_chain == [(_page(hub), 302)]
        assert _messages(response) == ["Deleted 2.5 h on Nov 3."]
        assert not VolunteerHours.objects.exists()

    def it_refuses_another_members_entry(hub, artist_client, make_member):
        entry = _hours(hub, make_member(), "4")

        assert artist_client.post(_delete(entry), {"back": "hours"}).status_code == 403
        assert VolunteerHours.objects.filter(pk=entry.pk).exists()

    def it_lets_an_organiser_delete_anyones_and_land_back_on_the_hours_page(hub, artist, lee_client, admin_client):
        first = _hours(hub, artist, "1")
        second = _hours(hub, artist, "2")

        response = lee_client.post(_delete(first), {"back": "hours"})
        assert response.status_code == 302 and response["Location"] == _hours_page(hub)
        response = admin_client.post(_delete(second))
        assert response.status_code == 302 and response["Location"] == _page(hub)
        assert not VolunteerHours.objects.exists()

    def it_sends_a_member_back_to_the_page_whatever_they_post(hub, artist, artist_client):
        entry = _hours(hub, artist)

        response = artist_client.post(_delete(entry), {"back": "hours"})

        assert response["Location"] == _page(hub)

    def it_asks_first_with_a_label_that_names_the_entry(hub, artist, artist_client):
        entry = _hours(hub, artist, "2.5", date=date(2026, 10, 3))

        body = artist_client.get(_page(hub)).content.decode()

        assert 'aria-label="Delete 2.5 h on Oct 3"' in body
        assert f"$dispatch('open-confirm', 'delete-hours-{entry.pk}')" in body
        assert f'action="{_delete(entry)}"' in body
        assert "Delete these hours?" in body


def describe_the_thank_you_board():
    def it_ranks_by_total_hours_with_ties_to_who_logged_first(hub, make_member):
        names = ["Ann", "Bo", "Cy"]
        ann, bo, cy = (make_member(preferred_name=name) for name in names)
        _hours(hub, bo, "3")
        _hours(hub, ann, "2")
        _hours(hub, cy, "1")
        _hours(hub, ann, "1")
        _hours(HubCardFactory(kind=HubCard.Kind.MARKET_CHECKIN).hub, cy, "20")

        board = services.thank_you_board(hub)

        assert [(row.rank, row.name, row.hours_display) for row in board.rows] == [
            (1, "Bo", "3"),
            (2, "Ann", "3"),
            (3, "Cy", "1"),
        ]
        assert board.top == "3.00"

    def it_shows_the_top_fifteen_in_one_query(hub, make_member):
        for position in range(16):
            _hours(hub, make_member(preferred_name=f"Spec Helper {position:02d}"), str(position + 1))

        with CaptureQueriesContext(connection) as queries:
            board = services.thank_you_board(hub)

        assert len(queries) == 1
        assert len(board.rows) == services.BOARD_LIMIT == 15
        assert board.rows[0].name == "Spec Helper 15"
        assert "Spec Helper 00" not in [row.name for row in board.rows]

    def it_renders_ranks_bars_hours_and_you_on_the_viewers_row(hub, artist, artist_client, lee):
        _hours(hub, lee, "4")
        _hours(hub, artist, "1.5")

        body = artist_client.get(_page(hub)).content.decode()
        board = body.split("data-hq-ci-board")[1].split("</section>")[0]

        first = board.split('data-hq-ci-leader="1"')[1].split("</li>")[0]
        second = board.split('data-hq-ci-leader="2"')[1].split("</li>")[0]
        assert "data-hq-ci-leader-name>Lee Organiser</span>" in first
        assert '<progress class="pl-hq-ci__bar" max="4.00" value="4.00" aria-hidden="true">' in first
        assert "data-hq-ci-leader-hours>4 h</span>" in first
        assert "(you)" not in first
        assert 'class="pl-hq-ci__leader pl-hq-ci__leader--me"' in body
        assert 'Rae Artist <span class="pl-hq-ci__you" data-hq-ci-you>(you)</span>' in second
        assert 'max="4.00" value="1.50"' in second
        assert "style=" not in board

    def it_says_so_when_nobody_has_logged_hours(hub):
        body = Client().get(_page(hub)).content.decode()

        assert "data-hq-ci-board-empty>No hours yet. Be the first!</p>" in body
        assert services.thank_you_board(hub).top == "1"


def describe_the_band():
    def it_counts_hours_volunteers_and_the_reports_in_two_queries(hub, artist, lee, make_member):
        _hours(hub, artist, "2.5")
        _hours(hub, artist, "1")
        _hours(hub, lee, "0.25")
        _hours(HubCardFactory(kind=HubCard.Kind.MARKET_CHECKIN).hub, lee, "9")
        MarketReport.objects.create(hub=hub, member=artist, name="A", items_sold=4, sales_cents=10_000)
        MarketReport.objects.create(hub=hub, member=lee, name="B", items_sold=None, sales_cents=5_001)
        MarketReport.objects.create(hub=hub, member=make_member(), name="C")

        with CaptureQueriesContext(connection) as queries:
            band = services.checkin_band(hub)

        assert len(queries) == 2
        assert (band.hours, band.hours_display, band.volunteers) == (Decimal("3.75"), "3.75", 2)
        assert (band.reports, band.sales_cents, band.items, band.vendors_with_sales) == (3, 15_001, 4, 2)
        assert (band.sales_display, band.average_sale_cents, band.average_sale_display) == ("$150.01", 7_500, "$75")

    def it_reads_zero_on_an_empty_hub(hub):
        band = services.checkin_band(hub)

        assert (band.hours_display, band.volunteers, band.reports, band.sales_cents, band.items) == ("0", 0, 0, 0, 0)
        assert band.average_sale_display == "$0"

    @pytest.mark.parametrize(
        ("hours", "shown"),
        [("2.50", "2.5"), ("3.00", "3"), ("0.25", "0.25"), ("1234.75", "1,234.75"), ("0", "0"), ("10", "10")],
    )
    def it_writes_hours_as_lee_does(hours, shown):
        assert format_hours(Decimal(hours)) == shown


def describe_the_organiser_pages():
    @pytest.fixture
    def filed(hub, artist, lee):
        jewelry = GuildFactory(name="Spec Jewelry Guild")
        MarketReport.objects.create(
            hub=hub,
            member=artist,
            name="=Spec Riverbend",
            guild=jewelry,
            items_sold=12,
            sales_cents=48_000,
            experience="+Spec busy night",
        )
        MarketReport.objects.create(hub=hub, member=lee, name="Lee's Prints", guild=jewelry, sales_cents=2_000)
        _hours(hub, artist, "2.5", date=date(2026, 11, 3), task=T.FLYERING, note="=spec cmd")
        _hours(hub, lee, "1", date=date(2026, 11, 9), task=T.SETUP)

    def it_shows_the_totals_sales_by_guild_and_every_report(hub, filed, lee_client, make_member):
        MarketReport.objects.create(hub=hub, member=make_member(), name="No Guild Co", sales_cents=0)

        body = lee_client.get(_reports_page(hub)).content.decode().replace("&#x27;", "'")

        assert "Market Reports</h1>" in body
        tiles = {
            key: body.split(f'data-hq-ci-tile="{key}"')[1].split("</div>")[0]
            for key in ("reports", "sales", "items", "average")
        }
        assert "data-hq-ci-tile-figure>3</strong>" in tiles["reports"]
        assert "data-hq-ci-tile-figure>$500</strong>" in tiles["sales"]
        assert "data-hq-ci-tile-figure>12</strong>" in tiles["items"]
        assert "data-hq-ci-tile-figure>$250</strong>" in tiles["average"] and "2 with sales" in tiles["average"]
        guilds = body.split("data-hq-ci-by-guild")[1].split("</section>")[0]
        assert guilds.index("Spec Jewelry Guild") < guilds.index("Not in a guild")
        assert body.count("data-hq-ci-report-row=") == 3
        assert "+Spec busy night" in body and "Rae Artist" in body and "Lee's Prints" in body
        assert f'href="{_reports_csv(hub)}" class="pl-hq-ci__csv" hx-boost="false" data-pl-download' in body
        assert services.sales_by_guild(hub)[0].sales_display == "$500"

    def it_shows_every_hours_entry_with_a_delete_that_comes_back_here(hub, filed, lee_client):
        body = lee_client.get(_hours_page(hub)).content.decode().replace("&#x27;", "'")

        assert "Volunteer Hours</h1>" in body
        assert body.count("data-hq-ci-hours-row=") == 2
        assert "data-hq-ci-tile-figure>3.5</strong>" in body
        assert 'aria-label="Delete Rae Artist\'s 2.5 h on Nov 3"' in body
        assert 'name="back" value="hours"' in body
        assert f'href="{_hours_csv(hub)}" class="pl-hq-ci__csv" hx-boost="false" data-pl-download' in body

    def it_links_both_pages_from_the_check_in_for_organisers_only(hub, lee_client, artist_client, admin, admin_client):
        body = lee_client.get(_page(hub)).content.decode()

        assert f'href="{_reports_page(hub)}" class="pl-hq-ci__staff-link"' in body
        assert f'href="{_hours_page(hub)}" class="pl-hq-ci__staff-link"' in body
        assert "data-hq-ci-staff" in admin_client.get(_page(hub)).content.decode()
        assert "data-hq-ci-staff" not in artist_client.get(_page(hub)).content.decode()

    def it_says_so_when_there_is_nothing_yet(hub, lee_client):
        assert "data-hq-ci-reports-empty" in lee_client.get(_reports_page(hub)).content.decode()
        assert "data-hq-ci-hours-empty" in lee_client.get(_hours_page(hub)).content.decode()

    def it_refuses_both_pages_and_both_csvs_to_anyone_but_an_organiser(hub, filed, artist_client, make_member):
        guest = signed_in(make_member(status=Member.Status.GUEST))
        for client in (artist_client, guest, Client()):
            for url in (_reports_page(hub), _hours_page(hub), _reports_csv(hub), _hours_csv(hub)):
                response = client.get(url)
                body = response.content.decode()
                assert response.status_code == 403, url
                for private in ("Spec Riverbend", "Spec Jewelry Guild", "Spec busy night", "spec cmd"):
                    assert private not in body, (url, private)

    def it_shows_a_previewing_admin_the_403_but_keeps_the_csvs(hub, admin_client):
        _preview(admin_client, ROLE_MEMBER)

        assert admin_client.get(_reports_page(hub)).status_code == 403
        assert admin_client.get(_hours_page(hub)).status_code == 403
        assert admin_client.get(_reports_csv(hub)).status_code == 200
        assert admin_client.get(_hours_csv(hub)).status_code == 200

    def it_is_a_404_when_the_card_is_hidden_from_a_named_member(card, lee_client):
        card.is_hidden = True
        card.save(update_fields=["is_hidden"])

        assert lee_client.get(_reports_page(card.hub)).status_code == 404

    def it_streams_the_reports_as_csv(hub, filed, lee_client):
        response = lee_client.get(_reports_csv(hub))

        rows = list(csv.reader(io.StringIO(b"".join(response.streaming_content).decode())))
        assert response["Content-Disposition"].startswith(f'attachment; filename="{hub.slug}-market-reports-')
        assert rows[0] == MARKET_REPORTS_CSV_HEADERS
        by_name = {row[0]: dict(zip(MARKET_REPORTS_CSV_HEADERS, row, strict=True)) for row in rows[1:]}
        assert set(by_name) == {"'=Spec Riverbend", "Lee's Prints"}
        first = by_name["'=Spec Riverbend"]
        assert first == {
            **first,
            "Member": "Rae Artist",
            "Guild": "Spec Jewelry Guild",
            "Items sold": "12",
            "Sales": "480.00",
            "Experience": "'+Spec busy night",
        }
        assert by_name["Lee's Prints"]["Items sold"] == ""

    def it_streams_the_hours_as_csv(hub, filed, lee_client):
        response = lee_client.get(_hours_csv(hub))

        rows = list(csv.reader(io.StringIO(b"".join(response.streaming_content).decode())))
        assert response["Content-Disposition"].startswith(f'attachment; filename="{hub.slug}-volunteer-hours-')
        assert rows[0] == VOLUNTEER_HOURS_CSV_HEADERS
        assert [row[:5] for row in rows[1:]] == [
            ["Lee Organiser", "2026-11-09", "1.00", "Setup and decor", ""],
            ["Rae Artist", "2026-11-03", "2.50", "Flyering", "'=spec cmd"],
        ]


def describe_the_volunteer_link():
    def it_shows_when_the_volunteer_card_is_open_to_the_viewer(hub, artist_client):
        volunteers = HubCardFactory(hub=hub, kind=HubCard.Kind.VOLUNTEERS, audience=HubCard.Audience.PUBLIC)

        body = Client().get(_page(hub)).content.decode()

        assert f'href="{volunteers.url}" class="pl-hq-ci__btn pl-hq-ci__btn--ghost" data-hq-ci-volunteer-link' in body
        volunteers.audience = HubCard.Audience.ADMINS
        volunteers.save(update_fields=["audience"])
        assert "data-hq-ci-volunteer" not in artist_client.get(_page(hub)).content.decode()

    def it_is_absent_without_a_volunteer_card(hub):
        assert "data-hq-ci-volunteer" not in Client().get(_page(hub)).content.decode()


def describe_the_roster_hours():
    @pytest.fixture
    def roster_card(hub):
        return HubCardFactory(hub=hub, kind=HubCard.Kind.VOLUNTEERS, audience=HubCard.Audience.PUBLIC)

    def it_puts_the_members_hours_beside_the_sign_up_with_their_primary_email(hub, roster_card, artist, make_member):
        shift = VolunteerShiftFactory(hub=hub, role="Setup crew")
        ShiftSignupFactory(shift=shift, name="Rae", email="rae@studio.example")
        ShiftSignupFactory(shift=shift, name="Nobody", email="nobody@example.com")
        _hours(hub, artist, "2.5")
        _hours(hub, artist, "1")
        _hours(HubCardFactory(kind=HubCard.Kind.MARKET_CHECKIN).hub, artist, "9")
        # Another member holding the address only as a secondary email is not that person.
        other = make_member()
        EmailAddress.objects.create(user=other.user, email="nobody@example.com", primary=False, verified=True)
        _hours(hub, other, "5")

        rows = {row.email: row for row in services.roster_rows(hub)}

        assert (rows["rae@studio.example"].hours, rows["rae@studio.example"].hours_display) == (Decimal("3.5"), "3.5")
        assert (rows["nobody@example.com"].hours, rows["nobody@example.com"].hours_display) == (0, "–")

    def it_matches_the_address_whatever_its_case(hub, roster_card, make_member):
        member = make_member(_pre_signup_email="pat@example.com")
        EmailAddress.objects.filter(user=member.user).update(email="Pat@Example.com")
        ShiftSignupFactory(shift=VolunteerShiftFactory(hub=hub), email="pat@example.com")
        _hours(hub, member, "4")

        assert services.hours_by_email(hub) == {"pat@example.com": Decimal("4")}
        assert services.roster_rows(hub)[0].hours_display == "4"

    def it_shows_the_column_on_the_roster_and_in_its_csv(hub, roster_card, artist, admin_client):
        ShiftSignupFactory(shift=VolunteerShiftFactory(hub=hub, role="Door"), name="Rae", email="rae@studio.example")
        _hours(hub, artist, "6.25")

        body = admin_client.get(reverse("hq:volunteer_roster", args=[hub.slug])).content.decode()
        rows = b"".join(admin_client.get(reverse("hq:volunteer_roster_csv", args=[hub.slug])).streaming_content)

        assert "<th>Hours logged</th>" in body
        assert 'data-label="Hours logged" data-hq-roster-hours>6.25</td>' in body
        assert rows.decode().splitlines()[1].endswith(",6.25")

    def it_falls_back_to_the_users_email_without_a_primary_address_like_the_table_lookup(hub, roster_card, make_member):
        member = make_member(_pre_signup_email="kit@example.com")
        EmailAddress.objects.filter(user=member.user).delete()
        ShiftSignupFactory(shift=VolunteerShiftFactory(hub=hub), name="Kit", email="kit@example.com")
        _hours(hub, member, "3")
        VendorTableFactory(hub=hub, email="kit@example.com", status=VendorTable.Status.CONFIRMED)

        assert services.hours_by_email(hub) == {"kit@example.com": Decimal("3")}
        assert services.roster_rows(hub)[0].hours_display == "3"
        assert services.confirmed_table(hub, Member.objects.get(pk=member.pk)) is not None

    def it_reads_an_unlinked_members_stored_address_and_leaves_out_a_member_with_none(hub, make_member):
        unlinked = MemberFactory(_pre_signup_email="Una@Example.com")
        _hours(hub, unlinked, "1.5")
        blank = make_member()
        EmailAddress.objects.filter(user=blank.user).delete()
        blank.user.email = ""
        blank.user.save(update_fields=["email"])
        _hours(hub, blank, "2")

        assert services.hours_by_email(hub) == {"una@example.com": Decimal("1.5")}

    def it_keeps_the_roster_page_to_a_fixed_number_of_queries(hub, roster_card, admin_client, make_member):
        shift = VolunteerShiftFactory(hub=hub, need=20)

        def _helper(position: int) -> None:
            email = f"helper{position}@example.com"
            member = make_member(_pre_signup_email=email)
            assert EmailAddress.objects.filter(user=member.user, primary=True, email=email).exists()
            ShiftSignupFactory(shift=shift, email=email)
            _hours(hub, member, "2")

        def _count() -> int:
            with CaptureQueriesContext(connection) as queries:
                response = admin_client.get(reverse("hq:volunteer_roster", args=[hub.slug]))
            assert response.status_code == 200
            # Every helper's hours matched their sign-up.
            assert response.content.decode().count("data-hq-roster-hours>2</td>") == shift.signups.count()
            return len(queries)

        _helper(0)
        _count()
        few = _count()
        for position in range(1, 5):
            _helper(position)

        assert _count() == few


def describe_the_page_queries():
    def it_keeps_the_check_in_page_to_a_fixed_number_of_queries_as_reports_hours_and_the_board_grow(
        hub, artist, artist_client, make_member
    ):
        def _grow(position: int) -> None:
            helper = make_member(_pre_signup_email=f"grow{position}@example.com")
            MarketReport.objects.create(hub=hub, member=helper, name=f"Studio {position}", sales_cents=1000)
            _hours(hub, helper, "1.25")
            _hours(hub, helper, "2")

        def _count() -> int:
            with CaptureQueriesContext(connection) as queries:
                response = artist_client.get(_page(hub))
            assert response.status_code == 200
            assert "data-hq-ci-report-form" in response.content.decode()
            return len(queries)

        _grow(0)
        _hours(hub, artist, "1")
        _count()
        few = _count()
        for position in range(1, 6):
            _grow(position)
        _hours(hub, artist, "3")

        assert _count() == few
        assert len(services.thank_you_board(hub).rows) == 7


def describe_adopting_the_placeholder():
    def it_turns_the_market_check_in_placeholder_into_the_live_door_in_place(open_hub, member):
        placeholder = HubCardFactory(
            hub=open_hub,
            kind=HubCard.Kind.PLACEHOLDER,
            title="Market Check-in",
            bullets="Artists and vendors checking in on the day\nTable numbers and who has arrived\nVolunteers check in here too",
            button_text="Check in",
            audience=HubCard.Audience.MEMBERS,
            named=[member],
        )

        result = open_hub.seed_default_cards()

        card = HubCard.objects.get(pk=placeholder.pk)
        assert "Market Check-in" in result.adopted
        assert (card.kind, card.audience, card.button_text) == (
            HubCard.Kind.MARKET_CHECKIN,
            HubCard.Audience.MEMBERS,
            "Enter check-in",
        )
        assert card.bullet_lines == [
            "Artists: your market report",
            "Volunteers: log your prep hours",
            "Volunteer thank-you board",
        ]
        assert list(card.named_members.all()) == [member]
        assert card.url == _page(open_hub)


def describe_the_models():
    def it_reads_as_the_report_and_the_hours(hub, artist):
        report = MarketReport.objects.create(hub=hub, member=artist, name="Spec Studio")
        entry = _hours(hub, artist, "2.5", date=date(2026, 11, 3))

        assert str(report) == f"Spec Studio ({hub.title})"
        assert (report.guild_display, report.sales_display) == ("Not in a guild", "$0")
        assert str(entry) == "Rae Artist 2.5 h on 2026-11-03"
        assert entry.hours_display == "2.5"
        assert entry.task == T.FLYERING

    def it_holds_one_report_per_member_on_a_hub(hub, artist):
        MarketReport.objects.create(hub=hub, member=artist, name="One")

        with pytest.raises(IntegrityError), transaction.atomic():
            MarketReport.objects.create(hub=hub, member=artist, name="Two")
        MarketReport.objects.create(hub=HubCardFactory().hub, member=artist, name="Elsewhere")

    @pytest.mark.parametrize("hours", ["0.00", "24.25"])
    def it_refuses_hours_out_of_range_in_the_database(hub, artist, hours):
        with pytest.raises(IntegrityError), transaction.atomic():
            _hours(hub, artist, hours)

    def it_goes_with_the_member(hub, artist):
        MarketReport.objects.create(hub=hub, member=artist, name="Gone")
        _hours(hub, artist)

        artist.delete()

        assert not MarketReport.objects.exists() and not VolunteerHours.objects.exists()

    def it_drops_a_deleted_guild_from_the_report_but_keeps_the_report(hub, artist):
        guild = GuildFactory()
        report = MarketReport.objects.create(hub=hub, member=artist, name="Kept", guild=guild)

        type(guild).all_objects.filter(pk=guild.pk).delete()

        report.refresh_from_db()
        assert report.guild is None
