"""Manage All Classes > Registrations (#911): the class date range, the Payment Report button,
the totals line and the toolbar layout."""

from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.forms import RegistrationDateRangeForm
from classes.models import ClassOffering, Registration

pytestmark = pytest.mark.django_db

PACIFIC = ZoneInfo("America/Los_Angeles")
FLEXIBLE = ClassOffering.SchedulingModel.FLEXIBLE


def _at(year: int, month: int, day: int, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, tzinfo=PACIFIC)


def _scheduled(email: str, *starts: datetime) -> Registration:
    offering = ClassOfferingFactory()
    for starts_at in starts:
        ClassSessionFactory(class_offering=offering, starts_at=starts_at)
    return RegistrationFactory(class_offering=offering, email=email)


def _flexible(email: str, starts_on: date | None, ends_on: date | None) -> Registration:
    offering = ClassOfferingFactory(scheduling_model=FLEXIBLE, flexible_starts_on=starts_on, flexible_ends_on=ends_on)
    return RegistrationFactory(class_offering=offering, email=email)


def _no_window_day(reg: Registration, registered: datetime, confirmed: datetime | None = None) -> Registration:
    """Backdate ``reg``; ``registered_at`` is auto_now_add, so it moves only through an update."""
    Registration.objects.filter(pk=reg.pk).update(registered_at=registered, confirmed_at=confirmed)
    return reg


def _emails(qs) -> set[str]:
    return set(qs.values_list("email", flat=True))


def _in(start: date | None, end: date | None) -> set[str]:
    return _emails(Registration.objects.with_class_dates_in(start, end))


def _body(response) -> str:
    return b"".join(response.streaming_content).decode()


def _toolbar(html: str) -> str:
    return re.search(r'<form method="get" class="pl-reg-toolbar".*?</form>', html, re.S).group(0)


NOV_1, NOV_30 = date(2026, 11, 1), date(2026, 11, 30)


def describe_with_class_dates_in():
    def describe_a_scheduled_class():
        def it_counts_a_session_on_the_first_and_the_last_day(db):
            _scheduled("first@example.com", _at(2026, 11, 1, 0))
            _scheduled("last@example.com", _at(2026, 11, 30, 23))  # 11 PM Pacific is Dec 1 in UTC
            assert _in(NOV_1, NOV_30) == {"first@example.com", "last@example.com"}

        def it_leaves_out_sessions_on_the_day_before_and_the_day_after(db):
            _scheduled("before@example.com", _at(2026, 10, 31, 23))
            _scheduled("after@example.com", _at(2026, 12, 1, 0))
            assert _in(NOV_1, NOV_30) == set()

        def it_counts_a_class_once_any_session_lands_in_range(db):
            _scheduled("span@example.com", _at(2026, 10, 20), _at(2026, 11, 3), _at(2026, 11, 10))
            assert list(Registration.objects.with_class_dates_in(NOV_1, NOV_30).values_list("email", flat=True)) == [
                "span@example.com"
            ]

        def it_leaves_out_a_class_with_no_sessions(db):
            RegistrationFactory(email="unscheduled@example.com")
            assert _in(NOV_1, NOV_30) == set()

        def it_takes_a_from_alone(db):
            _scheduled("before@example.com", _at(2026, 10, 31))
            _scheduled("later@example.com", _at(2027, 3, 1))
            assert _in(NOV_1, None) == {"later@example.com"}

        def it_takes_a_to_alone(db):
            _scheduled("earlier@example.com", _at(2025, 3, 1))
            _scheduled("after@example.com", _at(2026, 12, 1))
            assert _in(None, NOV_30) == {"earlier@example.com"}

        def it_returns_every_row_with_no_range(db):
            _scheduled("one@example.com", _at(2026, 11, 3))
            RegistrationFactory(email="unscheduled@example.com")
            assert _in(None, None) == {"one@example.com", "unscheduled@example.com"}

    def describe_a_flexible_class():
        def it_counts_a_window_that_overlaps_the_range(db):
            _flexible("inside@example.com", date(2026, 11, 5), date(2026, 11, 20))
            _flexible("straddles-start@example.com", date(2026, 10, 15), NOV_1)
            _flexible("straddles-end@example.com", NOV_30, date(2026, 12, 15))
            _flexible("covers@example.com", date(2026, 10, 1), date(2027, 1, 1))
            assert _in(NOV_1, NOV_30) == {
                "inside@example.com",
                "straddles-start@example.com",
                "straddles-end@example.com",
                "covers@example.com",
            }

        def it_leaves_out_a_window_wholly_before_or_after(db):
            _flexible("before@example.com", date(2026, 10, 1), date(2026, 10, 31))
            _flexible("after@example.com", date(2026, 12, 1), date(2026, 12, 31))
            assert _in(NOV_1, NOV_30) == set()

        def it_treats_a_blank_end_of_the_window_as_open(db):
            _flexible("no-first-day@example.com", None, date(2026, 11, 2))
            _flexible("no-last-day@example.com", date(2026, 11, 29), None)
            _flexible("no-first-day-too-early@example.com", None, date(2026, 10, 31))
            _flexible("no-last-day-too-late@example.com", date(2026, 12, 1), None)
            assert _in(NOV_1, NOV_30) == {"no-first-day@example.com", "no-last-day@example.com"}

        def it_goes_by_the_window_not_by_the_registration_day_when_there_is_a_window(db):
            _no_window_day(
                _flexible("dec-window@example.com", date(2026, 12, 1), date(2026, 12, 31)), _at(2026, 11, 10)
            )
            assert _in(NOV_1, NOV_30) == set()

        def it_goes_by_the_window_not_by_a_stray_session(db):
            reg = _flexible("flex@example.com", date(2026, 12, 1), date(2026, 12, 31))
            ClassSessionFactory(class_offering=reg.class_offering, starts_at=_at(2026, 11, 10))
            assert _in(NOV_1, NOV_30) == set()

        def it_takes_one_bound_alone(db):
            _flexible("dec@example.com", date(2026, 12, 1), date(2026, 12, 31))
            assert _in(NOV_1, None) == {"dec@example.com"}
            assert _in(None, NOV_30) == set()

    def describe_a_flexible_class_with_no_window():
        def it_does_not_match_every_range(db):
            _no_window_day(_flexible("october@example.com", None, None), _at(2026, 10, 10))
            assert _in(NOV_1, NOV_30) == set()

        def it_counts_by_the_day_payment_confirmed(db):
            _no_window_day(
                _flexible("confirmed-nov@example.com", None, None), _at(2026, 10, 10), confirmed=_at(2026, 11, 2)
            )
            _no_window_day(
                _flexible("confirmed-dec@example.com", None, None), _at(2026, 11, 10), confirmed=_at(2026, 12, 2)
            )
            assert _in(NOV_1, NOV_30) == {"confirmed-nov@example.com"}

        def it_falls_back_to_the_day_it_was_made_when_unconfirmed(db):
            _no_window_day(_flexible("made-nov@example.com", None, None), _at(2026, 11, 10))
            _no_window_day(_flexible("made-dec@example.com", None, None), _at(2026, 12, 10))
            assert _in(NOV_1, NOV_30) == {"made-nov@example.com"}

        def it_reads_pacific_days_at_both_edges(db):
            _no_window_day(_flexible("first-moment@example.com", None, None), _at(2026, 11, 1, 0))
            _no_window_day(_flexible("last-evening@example.com", None, None), _at(2026, 11, 30, 23))
            _no_window_day(_flexible("eve-before@example.com", None, None), _at(2026, 10, 31, 23))
            _no_window_day(_flexible("day-after@example.com", None, None), _at(2026, 12, 1, 0))
            assert _in(NOV_1, NOV_30) == {"first-moment@example.com", "last-evening@example.com"}

        def it_takes_one_bound_alone(db):
            _no_window_day(_flexible("dec@example.com", None, None), _at(2026, 12, 10))
            assert _in(NOV_1, None) == {"dec@example.com"}
            assert _in(None, NOV_30) == set()

        def it_leaves_a_scheduled_class_to_its_sessions(db):
            # Only a windowless flexible class falls back to the registration's day.
            _no_window_day(RegistrationFactory(email="unscheduled@example.com"), _at(2026, 11, 10))
            assert _in(NOV_1, NOV_30) == set()


def describe_registration_date_range_form():
    def it_is_valid_and_empty_with_no_dates():
        form = RegistrationDateRangeForm({})
        assert form.is_valid()
        assert form.cleaned_data == {"start": None, "end": None}

    def it_refuses_a_to_before_the_from():
        form = RegistrationDateRangeForm({"start": "2026-11-30", "end": "2026-11-01"})
        assert not form.is_valid()
        assert form.errors["end"] == ["To is before From. Pick a later date."]

    def it_accepts_the_same_day_for_both():
        assert RegistrationDateRangeForm({"start": "2026-11-05", "end": "2026-11-05"}).is_valid()

    def it_refuses_a_date_that_does_not_parse():
        assert "start" in RegistrationDateRangeForm({"start": "soon"}).errors

    def it_leaves_the_rows_alone_when_the_range_has_an_error(db):
        _scheduled("nov@example.com", _at(2026, 11, 10))
        RegistrationFactory(email="unscheduled@example.com")
        form = RegistrationDateRangeForm({"start": "2026-11-30", "end": "2026-11-01"})
        assert _emails(form.apply(Registration.objects.all())) == {"nov@example.com", "unscheduled@example.com"}

    def it_narrows_the_rows_when_the_range_is_valid(db):
        _scheduled("nov@example.com", _at(2026, 11, 10))
        RegistrationFactory(email="unscheduled@example.com")
        form = RegistrationDateRangeForm({"start": "2026-11-01", "end": "2026-11-30"})
        assert _emails(form.apply(Registration.objects.all())) == {"nov@example.com"}

    def describe_payment_report_window():
        today = date(2026, 10, 23)

        def it_is_the_chosen_range():
            form = RegistrationDateRangeForm({"start": "2026-09-01", "end": "2026-09-30"})
            assert form.payment_report_window(today) == (date(2026, 9, 1), date(2026, 9, 30))

        def it_is_this_month_through_today_with_no_range():
            assert RegistrationDateRangeForm({}).payment_report_window(today) == (date(2026, 10, 1), today)

        def it_is_this_month_through_today_when_the_range_has_an_error():
            form = RegistrationDateRangeForm({"start": "2026-09-30", "end": "2026-09-01"})
            assert form.payment_report_window(today) == (date(2026, 10, 1), today)

        def it_runs_a_past_from_through_today():
            form = RegistrationDateRangeForm({"start": "2026-08-15"})
            assert form.payment_report_window(today) == (date(2026, 8, 15), today)

        def it_ends_a_future_from_on_itself():
            form = RegistrationDateRangeForm({"start": "2026-12-05"})
            assert form.payment_report_window(today) == (date(2026, 12, 5), date(2026, 12, 5))

        def it_starts_a_lone_to_on_the_first_of_its_month():
            form = RegistrationDateRangeForm({"end": "2026-08-20"})
            assert form.payment_report_window(today) == (date(2026, 8, 1), date(2026, 8, 20))


def describe_registrations_page():
    def describe_the_date_range():
        def it_filters_the_list(admin_user, client):
            _scheduled("nov@example.com", _at(2026, 11, 10))
            _scheduled("dec@example.com", _at(2026, 12, 10))
            client.force_login(admin_user)
            html = client.get(
                reverse("classes:admin_registrations"), {"start": "2026-11-01", "end": "2026-11-30"}
            ).content.decode()
            assert "nov@example.com" in html
            assert "dec@example.com" not in html

        def it_shows_the_error_and_filters_nothing_for_a_to_before_the_from(admin_user, client):
            _scheduled("nov@example.com", _at(2026, 11, 10))
            _scheduled("dec@example.com", _at(2026, 12, 10))
            client.force_login(admin_user)
            html = client.get(
                reverse("classes:admin_registrations"), {"start": "2026-11-30", "end": "2026-11-01"}
            ).content.decode()
            assert "To is before From. Pick a later date." in html
            assert "nov@example.com" in html
            assert "dec@example.com" in html

        def it_keeps_the_chosen_dates_in_the_inputs(admin_user, client):
            client.force_login(admin_user)
            html = client.get(
                reverse("classes:admin_registrations"), {"start": "2026-11-01", "end": "2026-11-30"}
            ).content.decode()
            assert 'name="start" value="2026-11-01"' in html
            assert 'name="end" value="2026-11-30"' in html

        def it_composes_with_the_mine_toggle_and_the_status_filter(admin_user, client):
            me = admin_user.member
            mine = ClassOfferingFactory(instructor=me)
            ClassSessionFactory(class_offering=mine, starts_at=_at(2026, 11, 10))
            RegistrationFactory(
                class_offering=mine, email="mine-conf@example.com", status=Registration.Status.CONFIRMED
            )
            RegistrationFactory(class_offering=mine, email="mine-pend@example.com", status=Registration.Status.PENDING)
            _scheduled("theirs@example.com", _at(2026, 11, 10))
            client.force_login(admin_user)
            html = client.get(
                reverse("classes:admin_registrations"),
                {"start": "2026-11-01", "end": "2026-11-30", "mine": "1", "status": Registration.Status.CONFIRMED},
            ).content.decode()
            assert "mine-conf@example.com" in html
            assert "mine-pend@example.com" not in html
            assert "theirs@example.com" not in html

        def it_carries_the_range_on_the_my_classes_toggle_and_the_export(admin_user, client):
            client.force_login(admin_user)
            response = client.get(reverse("classes:admin_registrations"), {"start": "2026-11-01", "end": "2026-11-30"})
            assert "start=2026-11-01" in response.context["mine_toggle_url"]
            assert "end=2026-11-30" in response.context["mine_toggle_url"]
            export = reverse("classes:admin_registrations_export")
            assert re.search(rf'href="{export}\?[^"]*start=2026-11-01', response.content.decode())

    def describe_the_export():
        def it_follows_the_range(admin_user, client):
            _scheduled("nov@example.com", _at(2026, 11, 10))
            _scheduled("dec@example.com", _at(2026, 12, 10))
            client.force_login(admin_user)
            body = _body(
                client.get(reverse("classes:admin_registrations_export"), {"start": "2026-11-01", "end": "2026-11-30"})
            )
            assert "nov@example.com" in body
            assert "dec@example.com" not in body

        def it_follows_the_search_box(admin_user, client):
            RegistrationFactory(email="ana-search@example.com", first_name="Ana")
            RegistrationFactory(email="bo-search@example.com", first_name="Bo")
            client.force_login(admin_user)
            body = _body(client.get(reverse("classes:admin_registrations_export"), {"q": " ana "}))
            assert "ana-search@example.com" in body
            assert "bo-search@example.com" not in body

        def it_searches_the_same_fields_as_the_page(admin_user, client):
            from classes import views

            RegistrationFactory(class_offering=ClassOfferingFactory(title="Kiln Basics"), email="kiln@example.com")
            RegistrationFactory(email="other@example.com")
            client.force_login(admin_user)
            page = client.get(reverse("classes:admin_registrations"), {"q": "kiln"}).content.decode()
            body = _body(client.get(reverse("classes:admin_registrations_export"), {"q": "kiln"}))
            assert "kiln@example.com" in page and "kiln@example.com" in body
            assert "other@example.com" not in page and "other@example.com" not in body
            assert views.REGISTRATION_SEARCH_FIELDS == ["first_name", "last_name", "email", "class_offering__title"]

        def it_filters_nothing_by_date_when_the_range_has_an_error(admin_user, client):
            _scheduled("nov@example.com", _at(2026, 11, 10))
            _scheduled("dec@example.com", _at(2026, 12, 10))
            client.force_login(admin_user)
            body = _body(
                client.get(reverse("classes:admin_registrations_export"), {"start": "2026-11-30", "end": "2026-11-01"})
            )
            assert "nov@example.com" in body
            assert "dec@example.com" in body

    def describe_the_payment_report_button():
        def it_opens_reconciliation_on_the_chosen_range(admin_user, client):
            client.force_login(admin_user)
            html = client.get(
                reverse("classes:admin_registrations"), {"start": "2026-09-01", "end": "2026-09-30"}
            ).content.decode()
            url = f"{reverse('billing_admin_dashboard')}?tab=reconciliation&amp;start=2026-09-01&amp;end=2026-09-30"
            link = re.search(r"<a [^>]*data-reg-payment-report[^>]*>", html).group(0)
            assert f'href="{url}"' in link
            assert "target=" not in link

        def it_opens_this_month_through_today_with_no_range(admin_user, client):
            today = timezone.localdate()
            client.force_login(admin_user)
            response = client.get(reverse("classes:admin_registrations"))
            assert response.context["payment_report_url"] == (
                f"{reverse('billing_admin_dashboard')}?tab=reconciliation"
                f"&start={today.replace(day=1).isoformat()}&end={today.isoformat()}"
            )

        def it_sits_beside_export_csv_for_an_admin(admin_user, client):
            client.force_login(admin_user)
            html = client.get(reverse("classes:admin_registrations")).content.decode()
            actions = re.search(r'<div class="pl-reg-toolbar__group pl-reg-toolbar__actions">(.*?)</div>', html, re.S)
            assert "data-reg-payment-report" in actions.group(1)
            assert "Export CSV" in actions.group(1)

        def it_explains_that_reconciliation_groups_by_payment_day(admin_user, client):
            client.force_login(admin_user)
            html = client.get(reverse("classes:admin_registrations")).content.decode()
            report = re.search(r'<span class="pl-reg-toolbar__report">(.*?)</span>\s*</span>\s*</span>', html, re.S)
            assert "data-reg-payment-report" in report.group(1)
            assert (
                "Opens Reconciliation for these dates. It groups payments by the day they were paid, not the class date."
                in report.group(1)
            )

        def it_is_hidden_from_an_instructor(client):
            user = UserFactory(username="reg-range-teacher@example.com")
            ClassOfferingFactory(instructor=InstructorFactory(user=user))
            client.force_login(user)
            response = client.get(reverse("classes:admin_registrations"))
            assert response.status_code == 200
            assert "data-reg-payment-report" not in response.content.decode()

    def describe_the_totals_line():
        def it_counts_and_sums_every_filtered_row_across_pages(admin_user, client):
            offering = ClassOfferingFactory()
            for _ in range(27):
                RegistrationFactory(class_offering=offering, amount_paid_cents=1050)
            RegistrationFactory(amount_paid_cents=99999, status=Registration.Status.PENDING)
            client.force_login(admin_user)
            response = client.get(reverse("classes:admin_registrations"), {"class": offering.pk})
            assert response.context["totals"] == {"count": 27, "paid_cents": 28350}
            totals = re.search(r"<p class=\"pl-reg-totals\" data-reg-totals>(.*?)</p>", response.content.decode())
            text = re.sub(r"<[^>]+>", "", totals.group(1))
            assert text == "27 registrations·$283.50 paid before refunds"

        def it_follows_the_search_and_the_range(admin_user, client):
            ana = _scheduled("ana@example.com", _at(2026, 11, 10))
            Registration.objects.filter(pk=ana.pk).update(amount_paid_cents=2500)
            _scheduled("bo@example.com", _at(2026, 11, 10))
            _scheduled("ana-dec@example.com", _at(2026, 12, 10))
            client.force_login(admin_user)
            response = client.get(
                reverse("classes:admin_registrations"), {"q": "ana", "start": "2026-11-01", "end": "2026-11-30"}
            )
            assert response.context["totals"] == {"count": 1, "paid_cents": 2500}

        def it_reads_one_registration_and_zero_dollars(admin_user, client):
            RegistrationFactory()
            client.force_login(admin_user)
            html = client.get(reverse("classes:admin_registrations")).content.decode()
            assert re.search(r"<strong>1</strong> registration<span", html)
            assert "<strong>$0.00</strong> paid before refunds" in html

        def it_is_zero_on_an_empty_list(admin_user, client):
            client.force_login(admin_user)
            response = client.get(reverse("classes:admin_registrations"))
            assert response.context["totals"] == {"count": 0, "paid_cents": 0}

    def describe_the_toolbar():
        def it_has_no_inline_styles(admin_user, client):
            client.force_login(admin_user)
            html = client.get(reverse("classes:admin_registrations")).content.decode()
            assert "style=" not in _toolbar(html)

        def it_puts_the_help_directly_beside_my_classes(admin_user, client):
            client.force_login(admin_user)
            html = client.get(reverse("classes:admin_registrations")).content.decode()
            mine = re.search(r'<span class="pl-reg-toolbar__mine">(.*?)</span>\s*</div>', _toolbar(html), re.S)
            assert "data-reg-mine" in mine.group(1)
            assert 'class="pl-help"' in mine.group(1)

        def it_labels_the_date_inputs_from_and_to(admin_user, client):
            client.force_login(admin_user)
            dates = re.search(
                r"data-reg-dates>(.*?)</div>\s*</div>\s*</div>",
                client.get(reverse("classes:admin_registrations")).content.decode(),
                re.S,
            ).group(1)
            assert re.search(r'<label for="id_start" class="pl-form-label">\s*From\s*</label>', dates)
            assert re.search(r'<label for="id_end" class="pl-form-label">\s*To\s*</label>', dates)
            assert 'type="date"' in dates

        def it_loads_its_stylesheet(admin_user, client):
            client.force_login(admin_user)
            html = client.get(reverse("classes:admin_registrations")).content.decode()
            assert "css/registrations-admin" in html


def describe_registrations_page_across_days():
    def it_matches_the_class_date_column_for_a_late_evening_session(admin_user, client):
        # A session at 11 PM Pacific on Nov 30 is Dec 1 in UTC; the range reads Pacific days.
        _scheduled("late@example.com", _at(2026, 11, 30, 23))
        client.force_login(admin_user)
        on_day = client.get(reverse("classes:admin_registrations"), {"start": "2026-11-30", "end": "2026-11-30"})
        next_day = client.get(reverse("classes:admin_registrations"), {"start": "2026-12-01", "end": "2026-12-01"})
        assert "late@example.com" in on_day.content.decode()
        assert "late@example.com" not in next_day.content.decode()
