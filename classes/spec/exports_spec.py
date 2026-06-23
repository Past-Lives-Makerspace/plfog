"""BDD specs for the per-class registration CSV exporter."""

from __future__ import annotations

import csv
import io

import pytest

from classes.exports import CSV_HEADERS, stream_registrations_csv
from classes.factories import ClassOfferingFactory, RegistrationFactory
from classes.models import Registration

pytestmark = pytest.mark.django_db


def _read_csv(response) -> list[list[str]]:
    body = b"".join(response.streaming_content).decode()
    return list(csv.reader(io.StringIO(body)))


def describe_stream_registrations_csv():
    def it_emits_the_spec_columns_in_order():
        assert CSV_HEADERS[:5] == [
            "First Name",
            "Last Name",
            "Email Address",
            "Registration Date",
            "Payment Status",
        ]

    def it_writes_a_header_row_first():
        offering = ClassOfferingFactory(slug="csv-class")
        rows = _read_csv(stream_registrations_csv(offering))
        assert rows[0] == CSV_HEADERS

    def it_writes_one_row_per_registration():
        offering = ClassOfferingFactory()
        RegistrationFactory(
            class_offering=offering,
            first_name="Ada",
            last_name="Lovelace",
            email="ada@example.com",
            phone="503-555-0100",
            status=Registration.Status.CONFIRMED,
            amount_paid_cents=2500,
        )
        rows = _read_csv(stream_registrations_csv(offering))
        assert len(rows) == 2  # header + 1
        data = rows[1]
        assert data[0] == "Ada"
        assert data[1] == "Lovelace"
        assert data[2] == "ada@example.com"
        assert data[4] == "Confirmed"  # human label, not "confirmed"
        assert data[5] == "503-555-0100"
        assert data[6] == "25.00"  # amount_paid_cents formatted as dollars

    def it_includes_all_statuses_including_cancelled():
        offering = ClassOfferingFactory()
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        RegistrationFactory(class_offering=offering, status=Registration.Status.CANCELLED)
        rows = _read_csv(stream_registrations_csv(offering))
        assert len(rows) == 3  # header + 2

    def it_only_includes_rows_for_this_offering():
        mine = ClassOfferingFactory(slug="mine")
        other = ClassOfferingFactory(slug="other")
        RegistrationFactory(class_offering=mine, email="in@example.com")
        RegistrationFactory(class_offering=other, email="out@example.com")
        body = b"".join(stream_registrations_csv(mine).streaming_content).decode()
        assert "in@example.com" in body
        assert "out@example.com" not in body

    def it_sets_a_csv_attachment_disposition_with_slug_and_date():
        offering = ClassOfferingFactory(slug="pottery-101")
        response = stream_registrations_csv(offering)
        assert response["Content-Type"] == "text/csv"
        disp = response["Content-Disposition"]
        assert "attachment" in disp
        assert "participants-pottery-101-" in disp
        assert disp.endswith('.csv"')
