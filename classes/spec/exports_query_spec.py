"""BDD specs for the cross-class registrations CSV exporter."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from classes.exports import REGISTRATIONS_CSV_HEADERS, stream_registrations_query_csv
from classes.factories import ClassOfferingFactory, ClassSessionFactory, InstructorFactory, RegistrationFactory
from classes.models import Registration

pytestmark = pytest.mark.django_db

PACIFIC = ZoneInfo("America/Los_Angeles")


def _read(response) -> list[list[str]]:
    body = b"".join(response.streaming_content).decode()
    return list(csv.reader(io.StringIO(body)))


def describe_stream_registrations_query_csv():
    def it_writes_the_header_row_first():
        rows = _read(stream_registrations_query_csv(Registration.objects.all(), filename_stem="registrations"))
        assert rows[0] == REGISTRATIONS_CSV_HEADERS

    def it_includes_the_order_number_and_class_columns():
        offering = ClassOfferingFactory(title="Welding 101")
        RegistrationFactory(
            class_offering=offering,
            first_name="Ada",
            last_name="Lovelace",
            email="ada@example.com",
            phone="503-555-0100",
            status=Registration.Status.CONFIRMED,
            amount_paid_cents=2500,
        )
        rows = _read(stream_registrations_query_csv(Registration.objects.all(), filename_stem="registrations"))
        data = rows[1]
        assert data[0]  # order number present
        assert data[1] == "Welding 101"
        assert data[4] == "Ada"
        assert data[6] == "ada@example.com"
        assert data[8] == "Confirmed"  # human label
        assert data[10] == "25.00"  # cents → dollars

    def it_only_includes_rows_in_the_given_queryset():
        mine = ClassOfferingFactory(slug="q-mine")
        other = ClassOfferingFactory(slug="q-other")
        RegistrationFactory(class_offering=mine, email="in@example.com")
        RegistrationFactory(class_offering=other, email="out@example.com")
        body = b"".join(
            stream_registrations_query_csv(
                Registration.objects.filter(class_offering=mine), filename_stem="registrations"
            ).streaming_content
        ).decode()
        assert "in@example.com" in body
        assert "out@example.com" not in body

    def it_sets_a_csv_attachment_disposition_with_stem_and_date():
        response = stream_registrations_query_csv(Registration.objects.all(), filename_stem="registrations")
        assert response["Content-Type"] == "text/csv"
        disp = response["Content-Disposition"]
        assert "attachment" in disp
        assert "registrations-" in disp
        assert disp.endswith('.csv"')


def _row_for(offering) -> dict[str, str]:
    RegistrationFactory(class_offering=offering)
    rows = _read(stream_registrations_query_csv(Registration.objects.all(), filename_stem="registrations"))
    return dict(zip(rows[0], rows[1], strict=True))


def describe_class_date_and_instructor_columns():
    """#904: the treasurer pays instructors by class date, so each row carries both."""

    def it_puts_class_date_and_instructor_right_after_class():
        assert REGISTRATIONS_CSV_HEADERS[1:4] == ["Class", "Class Date", "Instructor"]

    def it_writes_a_one_date_class_as_its_pacific_day():
        offering = ClassOfferingFactory()
        # 5 PM Pacific on Oct 5 is already Oct 6 in UTC; the column must say the 5th.
        ClassSessionFactory(class_offering=offering, starts_at=datetime(2026, 10, 5, 17, tzinfo=PACIFIC))
        ClassSessionFactory(class_offering=offering, starts_at=datetime(2026, 10, 5, 20, tzinfo=PACIFIC))
        assert _row_for(offering)["Class Date"] == "2026-10-05"

    def it_writes_a_several_date_class_as_first_to_last():
        offering = ClassOfferingFactory()
        for day in (19, 5, 12):
            ClassSessionFactory(class_offering=offering, starts_at=datetime(2026, 10, day, 10, tzinfo=PACIFIC))
        assert _row_for(offering)["Class Date"] == "2026-10-05 to 2026-10-19"

    def it_writes_a_flexible_class_as_its_window():
        offering = ClassOfferingFactory(
            scheduling_model="flexible", flexible_starts_on=date(2026, 11, 2), flexible_ends_on=date(2026, 12, 1)
        )
        assert _row_for(offering)["Class Date"] == "Nov 2 to Dec 1, 2026"

    def it_leaves_the_date_empty_for_a_class_with_no_session():
        assert _row_for(ClassOfferingFactory())["Class Date"] == ""

    def it_names_every_instructor_in_order():
        ada = InstructorFactory(full_legal_name="Ada Lovelace")
        grace = InstructorFactory(full_legal_name="Grace Hopper")
        offering = ClassOfferingFactory(instructors=[grace, ada])
        assert _row_for(offering)["Instructor"] == f"{grace.display_name} and {ada.display_name}"

    def it_neutralises_an_instructor_name_that_reads_as_a_formula():
        offering = ClassOfferingFactory(instructor=InstructorFactory(preferred_name="=HYPERLINK(1)"))
        assert _row_for(offering)["Instructor"].startswith("'=")

    def it_costs_the_same_queries_for_one_class_or_many():
        def count() -> int:
            response = stream_registrations_query_csv(Registration.objects.all(), filename_stem="registrations")
            with CaptureQueriesContext(connection) as ctx:
                b"".join(response.streaming_content)
            return len(ctx.captured_queries)

        RegistrationFactory(class_offering=ClassOfferingFactory())
        one = count()
        for _ in range(4):
            offering = ClassOfferingFactory()
            ClassSessionFactory(class_offering=offering)
            RegistrationFactory(class_offering=offering)
        assert count() == one
