"""BDD specs for the Instructor Inquiries Board Report (#690): per month and the funnel."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.inquiries import BASELINE, PLOT_TOP, board_report
from classes.models import ClassOffering
from membership.models import Member
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db

PORTLAND = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=PORTLAND)


def _inquiry(applied: str, **fields: object) -> Member:
    stamp = datetime.fromisoformat(applied).replace(tzinfo=PORTLAND)
    return MemberFactory(teaching_applied_at=stamp, **fields)


def _past_class(instructor: Member, status: str, *, published: bool = True) -> None:
    """A class taught by ``instructor`` whose only session started three days before NOW."""
    offering = ClassOfferingFactory(
        instructor=instructor, status=status, published_at=NOW - timedelta(days=30) if published else None
    )
    ClassSessionFactory(class_offering=offering, starts_at=NOW - timedelta(days=3))


def _report(applied_from: date | None = None, applied_to: date | None = None):
    inquiries = Member.objects.teaching_inquiries(applied_from=applied_from, applied_to=applied_to)
    return board_report(inquiries, applied_from=applied_from, applied_to=applied_to, now=NOW)


def describe_inquiries_per_month():
    def it_counts_every_month_in_the_range_including_empty_ones():
        _inquiry("2026-09-02T10:00")
        _inquiry("2026-09-30T23:30")  # late evening in Portland is still September
        _inquiry("2026-11-01T00:30")
        report = _report(date(2026, 9, 1), date(2026, 11, 30))
        assert [(b.label, b.sublabel, b.count) for b in report.by_month] == [
            ("Sep", "2026", 2),
            ("Oct", "", 0),
            ("Nov", "", 1),
        ]

    def it_runs_from_the_earliest_inquiry_to_today_with_no_range_and_marks_each_new_year():
        _inquiry("2025-12-10T10:00")
        report = _report()
        assert [(b.label, b.sublabel) for b in report.by_month][:3] == [("Dec", "2025"), ("Jan", "2026"), ("Feb", "")]
        assert report.by_month[-1].label == "Nov"
        assert len(report.by_month) == 12

    def it_draws_no_months_when_the_range_starts_after_today():
        assert _report(date(2027, 1, 1)).by_month == []

    def it_scales_bars_to_the_tallest():
        _inquiry("2026-09-02T10:00")
        _inquiry("2026-09-03T10:00")
        _inquiry("2026-10-03T10:00")
        sep, oct_, nov = _report(date(2026, 9, 1), date(2026, 11, 30)).by_month
        assert sep.height == BASELINE - PLOT_TOP
        assert sep.y == PLOT_TOP
        assert oct_.height == (BASELINE - PLOT_TOP) / 2
        assert (nov.height, nov.y) == (0, BASELINE)
        assert sep.value_y == PLOT_TOP - 6
        assert sep.x < sep.center < oct_.x


def describe_the_funnel():
    def it_counts_inquired_approved_and_first_class_run_whatever_the_status():
        ran = _inquiry("2026-09-02T10:00", instructor_oriented_at=NOW)
        _past_class(ran, ClassOffering.Status.PUBLISHED)
        upcoming = _inquiry("2026-09-03T10:00", instructor_oriented_at=NOW)
        ClassSessionFactory(
            class_offering=ClassOfferingFactory(
                instructor=upcoming, status=ClassOffering.Status.PUBLISHED, published_at=NOW - timedelta(days=30)
            ),
            starts_at=NOW + timedelta(days=3),
        )
        _inquiry("2026-09-04T10:00")
        _inquiry("2026-08-04T10:00", instructor_oriented_at=NOW)  # outside the range
        report = _report(date(2026, 9, 1), date(2026, 9, 30))
        assert [(b.label, b.count) for b in report.funnel] == [
            ("Inquired", 3),
            ("Approved", 2),
            ("First class run", 1),
        ]
        assert report.is_empty is False

    def it_counts_a_run_only_on_a_class_that_went_live():
        """A past session on a cancelled, draft, pending or unpublished class is not a class run."""
        cancelled = _inquiry("2026-09-05T10:00", instructor_oriented_at=NOW)
        _past_class(cancelled, ClassOffering.Status.CANCELLED)
        never_live = _inquiry("2026-09-06T10:00", instructor_oriented_at=NOW)
        _past_class(never_live, ClassOffering.Status.DRAFT, published=False)
        _past_class(never_live, ClassOffering.Status.PENDING, published=False)
        _past_class(never_live, ClassOffering.Status.ARCHIVED, published=False)
        archived = _inquiry("2026-09-07T10:00", instructor_oriented_at=NOW)
        _past_class(archived, ClassOffering.Status.ARCHIVED)
        report = _report(date(2026, 9, 1), date(2026, 9, 30))
        assert [(b.label, b.count) for b in report.funnel] == [
            ("Inquired", 3),
            ("Approved", 3),
            ("First class run", 1),
        ]

    def it_is_empty_when_no_one_asked():
        report = _report(date(2026, 9, 1), date(2026, 9, 30))
        assert [b.count for b in report.funnel] == [0, 0, 0]
        assert {b.height for b in report.funnel} == {0}
        assert report.is_empty is True


def describe_the_range_label():
    @pytest.mark.parametrize(
        ("applied_from", "applied_to", "label"),
        [
            (date(2026, 9, 1), date(2026, 9, 30), "Sep 1, 2026 to Sep 30, 2026"),
            (date(2026, 9, 1), None, "Since Sep 1, 2026"),
            (None, date(2026, 9, 30), "Through Sep 30, 2026"),
            (None, None, "All time"),
        ],
    )
    def it_says_what_the_charts_cover(applied_from, applied_to, label):
        assert _report(applied_from, applied_to).range_label == label
