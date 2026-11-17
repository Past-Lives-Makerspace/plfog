"""The kiln crew's Reports tab (#1234): the period, the figures, the CSV and the print page."""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.test import Client
from django.urls import reverse

from kiln.forms import ReportPeriodForm
from kiln.models import KilnFiring, KilnTicket
from kiln.services import KilnUseReport, kiln_use_period, kiln_use_report, report_months
from membership.models import Member
from membership.wiki_guild import previous_month_window
from tests.kiln.conftest import signed_in
from tests.kiln.factories import ClayOptionFactory, KilnTicketFactory
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db

PACIFIC = ZoneInfo("America/Los_Angeles")
REPORTS = reverse("kiln:reports")
CSV = reverse("kiln:reports_csv")
PRINT = reverse("kiln:reports_print")
# "Today" for every spec here: 2 March 2026 in Portland, so the tab opens on February 2026.
TODAY = datetime(2026, 3, 2, 12, 0, tzinfo=PACIFIC)

_next_number = iter(range(1, 10_000))


@pytest.fixture(autouse=True)
def _today(monkeypatch) -> None:
    monkeypatch.setattr("django.utils.timezone.now", lambda: TODAY.astimezone(UTC))


def _firing(firing_type: str = "bisque", *, unloaded_at: datetime) -> KilnFiring:
    loader = MemberFactory()
    return KilnFiring.objects.create(
        firing_type=firing_type,
        number=next(_next_number),
        loaded_by=loader,
        unloaded_by=loader,
        unloaded_at=unloaded_at,
    )


def _fired(firing: KilnFiring, *, maker: Member | None = None, quantity: int = 1, **answers: Any) -> KilnTicket:
    return KilnTicketFactory(
        maker=maker or MemberFactory(),
        status=KilnTicket.Status.FIRED,
        firing_type=firing.firing_type,
        firing=firing,
        fired_at=firing.unloaded_at,
        quantity=quantity,
        **{"height_in": Decimal("2"), "width_in": Decimal("3"), "length_in": Decimal("4"), **answers},
    )


def _at(day: date, hour: int = 12) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=PACIFIC)


def _csv_rows(response) -> list[list[str]]:
    return list(csv.reader(io.StringIO(response.content.decode())))


def describe_the_gate():
    @pytest.mark.parametrize("url", [REPORTS, CSV, PRINT])
    def it_refuses_a_maker_who_is_not_crew(maker_client, kiln_guild, url):
        assert maker_client.get(url).status_code == 403

    @pytest.mark.parametrize("url", [REPORTS, CSV, PRINT])
    def it_admits_the_crew(crew_client, url):
        assert crew_client.get(url).status_code == 200

    @pytest.mark.parametrize("url", [REPORTS, CSV, PRINT])
    def it_admits_a_site_admin(make_member, kiln_guild, url):
        admin = make_member(fog_role=Member.FogRole.ADMIN)
        assert signed_in(admin).get(url).status_code == 200

    def it_sends_a_signed_out_visitor_to_sign_in(kiln_guild):
        assert Client().get(REPORTS).status_code == 302


def describe_the_tab_and_the_button():
    def it_shows_the_crew_a_Reports_tab(crew_client):
        html = crew_client.get(reverse("kiln:log")).content.decode()
        assert f'href="{REPORTS}"' in html
        assert 'data-kiln-tab="reports"' in html

    def it_marks_Reports_as_the_current_tab(crew_client):
        html = crew_client.get(REPORTS).content.decode()
        assert 'aria-current="page" data-kiln-tab="reports">Reports</a>' in html

    def it_puts_a_Reports_button_on_the_guild_Kiln_Tickets_tab_for_the_crew(crew_client, kiln_guild):
        html = crew_client.get(reverse("hub_guild_detail", args=[kiln_guild.slug]) + "?tab=kiln").content.decode()
        assert f'href="{REPORTS}"' in html

    def it_shows_a_maker_no_Reports_button(maker_client, kiln_guild):
        html = maker_client.get(reverse("hub_guild_detail", args=[kiln_guild.slug]) + "?tab=kiln").content.decode()
        assert f'href="{REPORTS}"' not in html


def describe_the_period():
    def it_opens_on_last_month_in_Portland_time(crew_client):
        _fired(_firing(unloaded_at=_at(date(2026, 2, 10))), quantity=3)
        _fired(_firing(unloaded_at=_at(date(2026, 3, 1))), quantity=7)
        response = crew_client.get(REPORTS)
        report = response.context["report"]
        assert (report.period_label, report.pieces_fired) == ("February 2026", 3)
        assert '<option value="2026-02" selected>February 2026</option>' in response.content.decode()

    def it_matches_the_monthly_email_for_a_whole_month(crew_client):
        clay = ClayOptionFactory(name="Buff")
        _fired(_firing("glaze", unloaded_at=_at(date(2026, 1, 5))), quantity=2, clay=clay)
        _fired(_firing(unloaded_at=_at(date(2026, 1, 20))), quantity=4)
        page = crew_client.get(REPORTS, {"month": "2026-01"}).context["report"]
        assert page == kiln_use_report(*previous_month_window(date(2026, 2, 1)))

    def it_shows_a_From_and_To_range_including_the_whole_last_day(crew_client):
        _fired(_firing(unloaded_at=_at(date(2026, 1, 10))), quantity=1)
        _fired(_firing(unloaded_at=_at(date(2026, 2, 14), hour=23)), quantity=5)
        _fired(_firing(unloaded_at=_at(date(2026, 2, 15), hour=0)), quantity=9)
        response = crew_client.get(REPORTS, {"start": "2026-01-10", "end": "2026-02-14"})
        report = response.context["report"]
        assert (report.bisque_runs, report.pieces_fired) == (2, 6)
        assert report.period_label == "January 10 to February 14, 2026"
        assert "January 10 to February 14, 2026" in response.content.decode()

    def it_names_both_years_for_a_range_across_new_year(crew_client):
        _fired(_firing(unloaded_at=_at(date(2025, 12, 20))))
        report = crew_client.get(REPORTS, {"start": "2025-12-15", "end": "2026-01-10"}).context["report"]
        assert report.period_label == "December 15, 2025 to January 10, 2026"
        assert report.file_stamp == "2025-12-15-to-2026-01-10"

    def it_calls_a_range_that_is_exactly_a_month_by_the_month(crew_client):
        report = crew_client.get(REPORTS, {"start": "2026-01-01", "end": "2026-01-31"}).context["report"]
        assert (report.period_label, report.file_stamp) == ("January 2026", "2026-01")

    def it_handles_December(crew_client):
        report = crew_client.get(REPORTS, {"month": "2025-12"}).context["report"]
        assert report.dates_label == "December 1 to December 31, 2025"
        assert report.is_whole_month

    def it_refuses_From_after_To_with_a_message(crew_client):
        response = crew_client.get(REPORTS, {"start": "2026-02-10", "end": "2026-02-01"})
        assert response.status_code == 200
        assert response.context["report"] is None
        assert "From must be on or before To." in response.content.decode()

    def it_asks_for_both_dates(crew_client):
        response = crew_client.get(REPORTS, {"start": "2026-02-10"})
        assert "Pick both a From and a To date." in response.content.decode()

    def it_refuses_a_date_it_cannot_read(crew_client):
        response = crew_client.get(REPORTS, {"start": "soon", "end": "2026-02-01"})
        assert response.context["report"] is None
        assert "data-kiln-period-error" in response.content.decode()

    def it_refuses_a_month_not_on_the_list(crew_client):
        response = crew_client.get(REPORTS, {"month": "1999-01"})
        assert response.context["report"] is None

    @pytest.mark.parametrize("url", [CSV, PRINT])
    def it_sends_a_bad_range_on_the_CSV_or_print_link_back_to_the_tab(crew_client, url):
        response = crew_client.get(url, {"start": "2026-02-10", "end": "2026-02-01"})
        assert response.status_code == 302
        assert response["Location"] == f"{REPORTS}?start=2026-02-10&end=2026-02-01"


def describe_report_months():
    def it_offers_twelve_months_newest_first_when_nothing_was_fired_earlier():
        months = report_months(date(2026, 3, 2))
        assert (months[0], months[1], months[-1], len(months)) == (
            date(2026, 3, 1),
            date(2026, 2, 1),
            date(2025, 4, 1),
            12,
        )

    def it_goes_back_to_the_first_unloaded_firing():
        _firing(unloaded_at=_at(date(2024, 11, 3)))
        months = report_months(date(2026, 3, 2))
        assert months[-1] == date(2024, 11, 1)
        assert len(months) == 17


def describe_the_form():
    def it_reads_no_query_as_last_month_across_new_year():
        form = ReportPeriodForm({}, months=report_months(date(2026, 1, 9)), today=date(2026, 1, 9))
        assert form.is_valid()
        assert form.window == (_at(date(2025, 12, 1), hour=0), _at(date(2026, 1, 1), hour=0))
        assert form.month_value == "2025-12"


def describe_an_empty_period():
    def it_says_no_firings_were_unloaded(crew_client):
        html = crew_client.get(REPORTS).content.decode()
        assert "No firings were unloaded in February 2026." in html
        assert "data-kiln-report-totals" not in html

    def it_still_downloads_a_CSV_with_the_totals_at_zero(crew_client):
        rows = _csv_rows(crew_client.get(CSV))
        assert rows == [
            ["Section", "Name", "Count", "Pieces", "Cubic inches", "Share"],
            ["Totals", "Bisque runs", "0", "", "", ""],
            ["Totals", "Glaze runs", "0", "", "", ""],
            ["Totals", "Pieces fired", "0", "", "", ""],
            ["Totals", "Kiln use", "", "", "0", ""],
            ["Totals", "Tickets with a size missing", "0", "", "", ""],
        ]

    def it_returns_the_same_report_as_the_email_service_when_there_were_firings():
        _firing(unloaded_at=_at(date(2026, 2, 3)))
        window = previous_month_window(date(2026, 3, 1))
        assert kiln_use_period(*window) == kiln_use_report(*window)

    def it_is_all_zeros_when_the_email_service_has_nothing():
        report = kiln_use_period(*previous_month_window(date(2026, 3, 1)))
        assert isinstance(report, KilnUseReport)
        assert not report.has_firings
        assert (report.makers, report.clays, report.glazes, report.pieces_fired) == ((), (), (), 0)


def describe_the_page():
    def it_shows_every_section_in_house_tables(crew_client):
        clay = ClayOptionFactory(name="Buff")
        _fired(
            _firing(unloaded_at=_at(date(2026, 2, 3))),
            maker=MemberFactory(full_legal_name="Ana Potter"),
            quantity=2,
            clay=clay,
        )
        html = crew_client.get(REPORTS).content.decode()
        assert 'data-total="bisque">1<' in html
        assert 'data-total="pieces">2<' in html
        assert 'data-total="kiln_use">48<' in html
        assert '<table class="pl-report-rank" data-kiln-report-table="makers">' in html
        assert 'data-kiln-report-table="clays"' in html
        assert "data-kiln-report-split" in html
        assert 'data-tip-value="48 in³" data-tip-name="Ana Potter" data-tip-more="2 pieces · 100% of kiln use"' in html
        assert "Ana Potter" in html
        assert "Buff" in html

    def it_says_how_many_tickets_had_a_size_missing(crew_client):
        firing = _firing(unloaded_at=_at(date(2026, 2, 3)))
        _fired(firing, height_in=None)
        _fired(firing, height_in=None)
        assert "2 tickets had a size missing" in crew_client.get(REPORTS).content.decode()

    def it_links_the_CSV_and_print_page_for_the_same_period(crew_client):
        html = crew_client.get(REPORTS, {"month": "2026-01"}).content.decode()
        assert f'href="{CSV}?month=2026-01"' in html
        assert f'href="{PRINT}?month=2026-01"' in html


def describe_the_csv():
    def it_is_text_csv_with_a_dated_filename_for_a_month(crew_client):
        response = crew_client.get(CSV, {"month": "2026-01"})
        assert response["Content-Type"] == "text/csv"
        assert response["Content-Disposition"] == 'attachment; filename="kiln-use-2026-01.csv"'

    def it_names_a_range_by_its_first_and_last_day(crew_client):
        response = crew_client.get(CSV, {"start": "2025-09-01", "end": "2025-11-15"})
        assert response["Content-Disposition"] == 'attachment; filename="kiln-use-2025-09-01-to-2025-11-15.csv"'

    def it_carries_every_section_with_plain_numbers(crew_client):
        clay = ClayOptionFactory(name="Buff")
        glaze_firing = _firing("glaze", unloaded_at=_at(date(2026, 2, 3)))
        _fired(
            glaze_firing,
            maker=MemberFactory(full_legal_name="Ana Potter"),
            quantity=50,
            clay=clay,
            glaze_commercial=True,
        )
        rows = _csv_rows(crew_client.get(CSV))
        assert ["Totals", "Glaze runs", "1", "", "", ""] in rows
        assert ["Totals", "Pieces fired", "50", "", "", ""] in rows
        assert ["Totals", "Kiln use", "", "", "1200", ""] in rows
        assert ["Member", "Ana Potter", "", "50", "1200", "100%"] in rows
        assert ["Clay", "Buff", "", "", "1200", "100%"] in rows
        assert ["Glaze", "Commercial", "", "", "1200", ""] in rows

    @pytest.mark.parametrize("lead", ["=", "+", "-", "@"])
    def it_neutralises_a_name_a_spreadsheet_would_run(crew_client, lead):
        clay = ClayOptionFactory(name=f"{lead}Clay")
        _fired(_firing(unloaded_at=_at(date(2026, 2, 3))), maker=MemberFactory(full_legal_name=f"{lead}cmd"), clay=clay)
        rows = _csv_rows(crew_client.get(CSV))
        assert ["Member", f"'{lead}cmd", "", "1", "24", "100%"] in rows
        assert ["Clay", f"'{lead}Clay", "", "", "24", "100%"] in rows


def describe_the_print_page():
    def it_is_a_standalone_page_with_no_site_navigation(crew_client):
        _fired(_firing(unloaded_at=_at(date(2026, 2, 3))))
        response = crew_client.get(PRINT)
        html = response.content.decode()
        assert response.templates[0].name == "kiln/report_print.html"
        assert "hub/base.html" not in [t.name for t in response.templates]
        assert "Past Lives Makerspace · Ceramics Guild" in html
        assert "Kiln Use Report" in html
        assert "February 2026" in html
        assert "compared with January 2026" in html
        assert "data-kiln-report-totals" in html
        assert "css/reports" in html
        assert "window.print()" in html
        assert "data-tip-value" not in html
        assert "Printed March 2, 2026" in html

    def it_prints_a_range(crew_client):
        html = crew_client.get(PRINT, {"start": "2026-02-01", "end": "2026-02-14"}).content.decode()
        assert "February 1 to February 14, 2026" in html
        assert "No firings were unloaded in February 1 to February 14, 2026." in html
