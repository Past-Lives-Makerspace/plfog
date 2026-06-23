"""BDD specs for the cross-class registrations CSV exporter."""

from __future__ import annotations

import csv
import io

import pytest

from classes.exports import REGISTRATIONS_CSV_HEADERS, stream_registrations_query_csv
from classes.factories import ClassOfferingFactory, RegistrationFactory
from classes.models import Registration

pytestmark = pytest.mark.django_db


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
        assert data[2] == "Ada"
        assert data[4] == "ada@example.com"
        assert data[6] == "Confirmed"  # human label
        assert data[8] == "25.00"  # cents → dollars

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
