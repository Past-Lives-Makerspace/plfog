"""BDD specs for the Instructor Inquiries page and its CSV export (#690).

Every member who asked to teach, pending and decided, newest first; a date range and
status filter on GET; an empty state; To before From as a field error; and a CSV of
exactly the filtered rows. Both views are admin-only.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import Member
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db

PAGE = reverse("classes:admin_instructor_inquiries")
EXPORT = reverse("classes:admin_instructor_inquiries_export")
EMPTY = "No one has asked to teach in this range."


def _inquiry(name: str, applied: str, **fields: object) -> Member:
    stamp = datetime.fromisoformat(applied).replace(tzinfo=ZoneInfo("America/Los_Angeles"))
    defaults: dict[str, object] = {
        "full_legal_name": name,
        "preferred_name": "",
        "teaching_applied_at": stamp,
        "teaching_application_note": f"{name} wants to teach.",
        "teaching_contact_method": "text",
        "teaching_contact_detail": "503 555 0100",
    }
    return MemberFactory(**{**defaults, **fields})


@pytest.fixture
def three(db) -> dict[str, Member]:
    now = timezone.now()
    return {
        "approved": _inquiry(
            "Avery Approved",
            "2026-08-15T10:00",
            instructor_oriented_at=now,
            teaching_website="https://avery.example",
            teaching_socials="https://instagram.com/avery @averymakes",
            teaching_experience="experienced",
        ),
        "declined": _inquiry(
            "Dana Declined", "2026-09-10T10:00", teaching_decided_at=now, teaching_decline_reason="Not yet."
        ),
        "pending": _inquiry("Pat Pending", "2026-10-02T10:00", teaching_experience="first_time"),
    }


def _rows(content: str) -> list[str]:
    """The data-inquiry-row pks in page order."""
    return re.findall(r'data-inquiry-row="(\d+)"', content)


def describe_access():
    def it_turns_away_a_member_who_is_not_an_admin(client: Client):
        user = get_user_model().objects.create_user(username="plain", email="plain@example.com", password="pw")
        client.force_login(user)
        assert client.get(PAGE).status_code == 403
        assert client.get(EXPORT).status_code == 403


def describe_the_page():
    def it_lists_every_applicant_pending_and_decided_newest_first(admin_user, client, three):
        client.force_login(admin_user)
        content = client.get(PAGE).content.decode()
        assert _rows(content) == [str(three[k].pk) for k in ("pending", "declined", "approved")]

    def it_shows_every_column_for_a_row(admin_user, client, three):
        client.force_login(admin_user)
        content = client.get(PAGE).content.decode()
        avery = three["approved"]
        assert "Aug 15, 2026" in content
        assert f'<a href="{reverse("hub_admin_member_edit", args=[avery.pk])}">Avery Approved</a>' in content
        assert "Avery Approved wants to teach." in content
        assert '<a href="https://avery.example" target="_blank" rel="noopener">' in content
        assert '<a href="https://instagram.com/avery" rel="nofollow">' in content
        assert "Experienced instructor" in content
        assert "503 555 0100 (text message)" in content
        assert 'pl-inquiries__status--approved">Approved</span>' in content
        assert 'pl-inquiries__status--declined">Declined</span>' in content
        assert 'pl-inquiries__status--pending">Pending</span>' in content

    def it_offers_the_csv_with_the_same_filter_as_a_real_download(admin_user, client, three):
        client.force_login(admin_user)
        content = client.get(PAGE, {"status": "pending"}).content.decode()
        assert (
            f'href="{EXPORT}?status=pending" class="hub-btn hub-btn--sm hub-btn--ghost" hx-boost="false" data-pl-download'
            in content
        )


def describe_the_filters():
    def it_narrows_by_date_range(admin_user, client, three):
        client.force_login(admin_user)
        content = client.get(PAGE, {"date_from": "2026-09-01", "date_to": "2026-09-30"}).content.decode()
        assert _rows(content) == [str(three["declined"].pk)]

    @pytest.mark.parametrize("status", ["pending", "approved", "declined"])
    def it_narrows_by_status(admin_user, client, three, status):
        client.force_login(admin_user)
        content = client.get(PAGE, {"status": status}).content.decode()
        assert _rows(content) == [str(three[status].pk)]

    def it_says_so_when_nothing_matches(admin_user, client, three):
        client.force_login(admin_user)
        content = client.get(PAGE, {"date_from": "2025-01-01", "date_to": "2025-01-31"}).content.decode()
        assert _rows(content) == []
        assert EMPTY in content

    def it_makes_to_before_from_a_field_error_and_lists_nothing(admin_user, client, three):
        client.force_login(admin_user)
        content = client.get(PAGE, {"date_from": "2026-10-01", "date_to": "2026-09-01"}).content.decode()
        assert "Date to is before Date from. Pick a later date." in content
        assert _rows(content) == []
        assert EMPTY not in content
        assert EXPORT not in content


def _csv(response) -> list[list[str]]:
    body = b"".join(response.streaming_content).decode()
    return list(csv.reader(io.StringIO(body)))


def describe_the_csv():
    def it_downloads_exactly_the_filtered_rows_with_every_column(admin_user, client, three):
        client.force_login(admin_user)
        response = client.get(EXPORT, {"status": "approved"})
        assert response["Content-Disposition"].startswith('attachment; filename="instructor-inquiries-')
        assert _csv(response) == [
            [
                "Date Reached Out",
                "Name",
                "What They Want to Teach",
                "Website",
                "Socials",
                "Experience Level",
                "Contact",
                "Status",
            ],
            [
                "2026-08-15",
                "Avery Approved",
                "Avery Approved wants to teach.",
                "https://avery.example",
                "https://instagram.com/avery @averymakes",
                "Experienced instructor",
                "503 555 0100 (text message)",
                "Approved",
            ],
        ]

    def it_lists_every_applicant_newest_first_with_no_filter(admin_user, client, three):
        client.force_login(admin_user)
        names = [row[1] for row in _csv(client.get(EXPORT))[1:]]
        assert names == ["Pat Pending", "Dana Declined", "Avery Approved"]

    @pytest.mark.parametrize("lead", ["=", "+", "-", "@", "\t", "\r"])
    def it_neutralises_applicant_text_a_spreadsheet_would_run_as_a_formula(admin_user, client, lead):
        _inquiry(
            "Formula Fran",
            "2026-09-01T10:00",
            teaching_application_note=f'{lead}HYPERLINK("https://evil.example","x")',
            teaching_socials=f"{lead}cmd",
            teaching_contact_detail=f"{lead}1+1",
        )
        client.force_login(admin_user)
        row = _csv(client.get(EXPORT))[1]
        assert row[2] == f'\'{lead}HYPERLINK("https://evil.example","x")'
        assert row[4] == f"'{lead}cmd"
        assert row[6] == f"'{lead}1+1 (text message)"

    def it_leaves_ordinary_text_alone(admin_user, client, three):
        client.force_login(admin_user)
        row = _csv(client.get(EXPORT, {"status": "approved"}))[1]
        assert row[1] == "Avery Approved"
        assert row[4] == "https://instagram.com/avery @averymakes"

    def it_refuses_a_filter_with_an_error(admin_user, client, three):
        client.force_login(admin_user)
        response = client.get(EXPORT, {"date_from": "2026-10-01", "date_to": "2026-09-01"})
        assert response.status_code == 400


def describe_the_board_report():
    def it_draws_both_charts_with_a_download_button_each(admin_user, client, three):
        client.force_login(admin_user)
        content = client.get(PAGE).content.decode()
        assert "data-board-report" in content
        assert 'aria-label="Inquiries per Month, All time"' in content
        assert 'aria-label="Success Funnel, All time"' in content
        assert 'data-chart-download="instructor-inquiries-per-month.png" data-pl-download>Download</button>' in content
        assert 'data-chart-download="instructor-inquiries-funnel.png" data-pl-download>Download</button>' in content
        assert "js/chart_download" in content

    def it_counts_the_whole_date_range_even_when_the_list_is_filtered_by_status(admin_user, client, three):
        client.force_login(admin_user)
        content = client.get(PAGE, {"status": "pending"}).content.decode()
        assert _rows(content) == [str(three["pending"].pk)]
        funnel = content[content.index('aria-label="Success Funnel') :]
        assert re.findall(r'data-chart-bar="(\d+)"', funnel) == ["3", "1", "0"]

    def it_draws_nothing_when_no_one_asked_in_the_range(admin_user, client, three):
        client.force_login(admin_user)
        content = client.get(PAGE, {"date_from": "2025-01-01", "date_to": "2025-01-31"}).content.decode()
        assert "data-board-report" not in content

    def it_draws_nothing_for_a_filter_with_an_error(admin_user, client, three):
        client.force_login(admin_user)
        content = client.get(PAGE, {"date_from": "2026-10-01", "date_to": "2026-09-01"}).content.decode()
        assert "data-board-report" not in content
