"""The Reports tab in the house report standard (#1234): the previous period, the deltas, the split and the ranked lists."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse

from kiln.models import KilnFiring, KilnTicket
from kiln.reports import Delta, delta, kiln_report_page, percent, previous_window
from kiln.services import kiln_use_period, kiln_use_report
from membership.models import Member
from tests.kiln.factories import ClayOptionFactory, GlazeOptionFactory, KilnTicketFactory
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db

PACIFIC = ZoneInfo("America/Los_Angeles")
REPORTS = reverse("kiln:reports")
TODAY = datetime(2026, 3, 2, 12, 0, tzinfo=PACIFIC)

_next_number = iter(range(1, 10_000))


@pytest.fixture(autouse=True)
def _today(monkeypatch) -> None:
    monkeypatch.setattr("django.utils.timezone.now", lambda: TODAY.astimezone(UTC))


def _midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=PACIFIC)


def _firing(firing_type: str = "bisque", *, on: date) -> KilnFiring:
    loader = MemberFactory()
    at = datetime(on.year, on.month, on.day, 12, 0, tzinfo=PACIFIC)
    return KilnFiring.objects.create(
        firing_type=firing_type, number=next(_next_number), loaded_by=loader, unloaded_by=loader, unloaded_at=at
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


def _month(year: int, month: int):
    first = date(year, month, 1)
    after = date(year + month // 12, month % 12 + 1, 1)
    return kiln_use_period(_midnight(first), _midnight(after))


def _range(first: date, last: date):
    from datetime import timedelta

    return kiln_use_period(_midnight(first), _midnight(last + timedelta(days=1)))


def describe_previous_window():
    def it_is_the_month_before_a_whole_month():
        assert previous_window(_month(2026, 2)) == (_midnight(date(2026, 1, 1)), _midnight(date(2026, 2, 1)))

    def it_crosses_new_year():
        assert previous_window(_month(2026, 1)) == (_midnight(date(2025, 12, 1)), _midnight(date(2026, 1, 1)))

    def it_is_the_same_number_of_days_just_before_a_range():
        report = _range(date(2026, 2, 10), date(2026, 2, 16))
        assert previous_window(report) == (_midnight(date(2026, 2, 3)), _midnight(date(2026, 2, 10)))


def describe_delta():
    def it_shows_a_small_change_alone():
        assert delta(7, 5, "September") == Delta("up", "▲ 2", "September")
        assert delta(5, 6, "September").text == "▼ 1 vs September"

    def it_adds_the_percent_from_one_hundred():
        assert delta(21860, 19420, "September").change == "▲ 2,440 (+13%)"
        assert delta(151, 184, "September").change == "▼ 33 (−18%)"

    def it_says_same_when_equal():
        assert delta(4, 4, "September").text == "Same as September"
        assert delta(4, 4, "September").direction == "same"

    def it_says_nothing_to_compare_when_the_previous_period_had_none():
        assert delta(4, 0, "September").text == "Nothing in September to compare"
        assert delta(0, 0, "September").direction == "none"


def describe_percent():
    def it_rounds_half_up_and_is_zero_of_nothing():
        assert percent(1, 8) == 13
        assert percent(5, 0) == 0


def describe_kiln_report_page():
    def it_compares_every_tile_with_the_previous_month_from_the_same_sum():
        _fired(_firing(on=date(2026, 1, 5)), quantity=2)
        _fired(_firing(on=date(2026, 2, 5)), quantity=3)
        _fired(_firing("glaze", on=date(2026, 2, 9)), quantity=1)
        page = kiln_report_page(_month(2026, 2))
        assert page.previous == kiln_use_report(_midnight(date(2026, 1, 1)), _midnight(date(2026, 2, 1)))
        assert (page.versus, page.compared_with) == ("January", "January 2026")
        texts = {tile.key: tile.delta.text for tile in page.tiles}
        assert texts == {
            "bisque": "Same as January",
            "glaze": "Nothing in January to compare",
            "pieces": "▲ 2 vs January",
            "kiln_use": "▲ 48 vs January",
        }

    def it_says_nothing_to_compare_on_every_tile_when_the_previous_period_is_empty():
        _fired(_firing(on=date(2026, 2, 5)))
        page = kiln_report_page(_month(2026, 2))
        assert not page.previous.has_firings
        assert {tile.delta.direction for tile in page.tiles} == {"none"}

    def it_names_a_range_by_its_days():
        _fired(_firing(on=date(2026, 2, 12)))
        page = kiln_report_page(_range(date(2026, 2, 10), date(2026, 2, 16)))
        assert page.versus == "the previous 7 days"
        assert page.compared_with == "February 3 to February 9, 2026"
        assert page.tiles[0].delta.text == "Nothing in the previous 7 days to compare"

    def it_says_day_for_a_one_day_range():
        page = kiln_report_page(_range(date(2026, 2, 10), date(2026, 2, 10)))
        assert page.versus == "the previous 1 day"

    def it_notes_tickets_with_no_size_on_the_kiln_use_tile():
        firing = _firing(on=date(2026, 2, 5))
        _fired(firing, height_in=None)
        assert kiln_report_page(_month(2026, 2)).tiles[3].note == "1 ticket had no size"

    def it_splits_runs_by_kind():
        _firing(on=date(2026, 2, 3))
        _firing(on=date(2026, 2, 4))
        _firing("glaze", on=date(2026, 2, 5))
        split = kiln_report_page(_month(2026, 2)).split
        assert [(p.name, p.runs, p.share) for p in split] == [("Bisque", 2, 67), ("Glaze", 1, 33)]

    def it_sizes_each_bar_against_the_top_row_and_shares_the_total():
        firing = _firing(on=date(2026, 2, 5))
        _fired(firing, maker=MemberFactory(full_legal_name="Big"), quantity=4, clay=ClayOptionFactory(name="Buff"))
        _fired(firing, maker=MemberFactory(full_legal_name="Small"), quantity=1, clay=ClayOptionFactory(name="Red"))
        page = kiln_report_page(_month(2026, 2))
        assert [(r.name, r.fill, r.share, r.pieces) for r in page.makers.rows] == [
            ("Big", "100.0", 80, 4),
            ("Small", "25.0", 20, 1),
        ]
        assert page.makers.rows[0].tip_more == "4 pieces · 80% of kiln use"
        assert page.clays.rows[1].tip_more == "20% of kiln use"
        assert page.kiln_use_label == "120"

    def it_gives_glazes_no_share_since_a_piece_counts_under_each():
        celadon, tenmoku = GlazeOptionFactory(name="Celadon"), GlazeOptionFactory(name="Tenmoku")
        ticket = _fired(_firing("glaze", on=date(2026, 2, 5)), glaze_studio=True)
        ticket.studio_glazes.set([celadon, tenmoku])
        glazes = kiln_report_page(_month(2026, 2)).glazes
        assert not glazes.shows_share
        assert [(r.share, r.tip_more) for r in glazes.rows] == [(None, "#1 of 2 glazes"), (None, "#2 of 2 glazes")]

    def it_ranks_a_catch_all_row_by_its_size_as_the_email_does():
        firing = _firing("glaze", on=date(2026, 2, 5))
        _fired(firing, quantity=5, glaze_commercial=True)
        small = _fired(firing, quantity=1, glaze_studio=True)
        small.studio_glazes.set([GlazeOptionFactory(name="Celadon")])
        report = _month(2026, 2)
        assert [row.name for row in report.glazes] == ["Commercial", "Celadon"]
        assert [row.name for row in kiln_report_page(report).glazes.rows] == ["Commercial", "Celadon"]

    def it_draws_empty_bars_when_no_ticket_had_a_size():
        _fired(_firing(on=date(2026, 2, 5)), height_in=None)
        row = kiln_report_page(_month(2026, 2)).makers.rows[0]
        assert (row.fill, row.share) == ("0.0", 0)


def describe_the_page():
    def it_shows_the_change_on_each_tile_and_what_it_is_compared_with(crew_client):
        _fired(_firing(on=date(2026, 1, 5)))
        _fired(_firing(on=date(2026, 2, 5)), quantity=3)
        html = crew_client.get(REPORTS).content.decode()
        assert "February 1 to February 28, 2026 · compared with January 2026" in html
        assert "<strong>▲ 2</strong> vs January" in html
        assert "Same as January" in html
        assert 'class="pl-report-tile__delta pl-report-delta pl-report-delta--up"' in html

    def it_steps_to_the_months_either_side(crew_client):
        html = crew_client.get(REPORTS, {"month": "2026-02"}).content.decode()
        assert 'href="?month=2026-01" aria-label="Previous month"' in html
        assert 'href="?month=2026-03" aria-label="Next month"' in html

    def it_disables_next_on_the_current_month(crew_client):
        html = crew_client.get(REPORTS, {"month": "2026-03"}).content.decode()
        assert 'aria-disabled="true" aria-label="Next month"' in html

    def it_disables_previous_on_the_oldest_month(crew_client):
        html = crew_client.get(REPORTS, {"month": "2025-04"}).content.decode()
        assert 'aria-disabled="true" aria-label="Previous month"' in html

    def it_opens_on_the_Dates_mode_for_a_range(crew_client):
        html = crew_client.get(REPORTS, {"start": "2026-02-01", "end": "2026-02-07"}).content.decode()
        assert "x-data=\"{ mode: 'dates' }\"" in html
        assert "compared with January 25 to January 31, 2026" in html

    def it_shows_the_empty_state_with_the_period_bar(crew_client):
        html = crew_client.get(REPORTS).content.decode()
        assert "No Firings in February 2026" in html
        assert "data-kiln-report-csv" in html
        assert "data-kiln-report-totals" not in html

    def it_says_no_pieces_when_the_firings_held_none(crew_client):
        _firing(on=date(2026, 2, 5))
        html = crew_client.get(REPORTS).content.decode()
        assert "No pieces came out of these firings." in html
        assert "None fired in this period." in html
