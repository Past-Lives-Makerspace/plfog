"""Class states (#1260, part 5): Scheduled, Between runs, Retired, No longer offered, and when they move."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering, ClassRecord, ClassSession, CmsActivity

pytestmark = pytest.mark.django_db

State = ClassRecord.State


def _run(slug: str, *, days: int, category=None, status=ClassOffering.Status.PUBLISHED, **extra) -> ClassOffering:
    extra.setdefault("title", "Picture Framing")
    run = ClassOfferingFactory(slug=slug, category=category or CategoryFactory(), status=status, **extra)
    start = timezone.now() + timedelta(days=days)
    ClassSessionFactory(class_offering=run, starts_at=start, ends_at=start + timedelta(hours=2))
    return run


def _record(run: ClassOffering) -> ClassRecord:
    return ClassRecord.objects.get(pk=run.class_record_id)


def describe_the_state_rule():
    def it_is_scheduled_with_a_date_to_come():
        assert _record(_run("framing-1", days=10)).state_now() == State.SCHEDULED

    def it_is_scheduled_with_an_open_flexible_window():
        run = ClassOfferingFactory(
            title="Flex Framing",
            status=ClassOffering.Status.PUBLISHED,
            scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
        )

        assert _record(run).state_now() == State.SCHEDULED

    def it_is_scheduled_with_a_published_run_not_dated_yet():
        run = ClassOfferingFactory(title="Undated Framing", status=ClassOffering.Status.PUBLISHED)

        assert _record(run).state_now() == State.SCHEDULED

    def it_is_between_runs_within_a_year_of_its_last_session():
        assert _record(_run("framing-1", days=-300)).state_now() == State.BETWEEN_RUNS

    def it_counts_an_archived_run():
        assert _record(_run("framing-1", days=-30, status=ClassOffering.Status.ARCHIVED)).state_now() == (
            State.BETWEEN_RUNS
        )

    def it_counts_a_flexible_window_that_closed_this_year():
        run = ClassOfferingFactory(
            title="Flex Framing",
            status=ClassOffering.Status.PUBLISHED,
            scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
            flexible_starts_on=timezone.localdate() - timedelta(days=90),
            flexible_ends_on=timezone.localdate() - timedelta(days=30),
        )

        assert _record(run).state_now() == State.BETWEEN_RUNS

    def it_is_retired_a_year_after_its_last_session():
        assert _record(_run("framing-1", days=-400)).state_now() == State.RETIRED

    def it_is_retired_with_no_public_run():
        assert _record(_run("framing-1", days=-30, status=ClassOffering.Status.CANCELLED)).state_now() == State.RETIRED


def describe_moving_state():
    def it_stores_a_move_and_logs_it_once():
        run = _run("framing-1", days=-30)
        record = _record(run)
        ClassRecord.objects.filter(pk=record.pk).update(state=State.SCHEDULED)
        CmsActivity.objects.filter(kind=CmsActivity.Kind.CLASS_STATE_CHANGED).delete()
        record.refresh_from_db()

        assert record.refresh_state() is True
        assert record.refresh_state() is False

        record.refresh_from_db()
        assert record.state == State.BETWEEN_RUNS
        assert record.state_changed_at is not None
        row = CmsActivity.objects.get(kind=CmsActivity.Kind.CLASS_STATE_CHANGED)
        assert row.class_offering == run
        assert row.payload == {"class": record.slug, "from": State.SCHEDULED, "to": State.BETWEEN_RUNS}

    def it_moves_when_a_run_is_published():
        past = _run("framing-1", days=-30)
        draft = _run("framing-2", days=20, category=past.category, status=ClassOffering.Status.DRAFT)
        assert _record(past).state == State.BETWEEN_RUNS

        draft.status = ClassOffering.Status.PUBLISHED
        draft.save()

        assert _record(past).state == State.SCHEDULED

    def it_moves_when_the_only_date_to_come_is_cancelled():
        past = _run("framing-1", days=-30)
        soon = _run("framing-2", days=20, category=past.category)
        assert _record(past).state == State.SCHEDULED

        soon.status = ClassOffering.Status.CANCELLED
        soon.save()

        assert _record(past).state == State.BETWEEN_RUNS

    def it_moves_when_a_run_is_re_dated():
        past = _run("framing-1", days=-30)
        soon = _run("framing-2", days=20, category=past.category)
        assert _record(past).state == State.SCHEDULED

        session = soon.sessions.get()
        session.starts_at = timezone.now() - timedelta(days=10)
        session.ends_at = session.starts_at + timedelta(hours=2)
        session.save()

        assert _record(past).state == State.BETWEEN_RUNS

    def it_moves_when_a_run_loses_its_last_date():
        dated = _run("framing-1", days=-400)
        soon = _run("framing-2", days=20, category=dated.category)
        ClassOffering.objects.filter(pk=soon.pk).update(status=ClassOffering.Status.CANCELLED)
        assert _record(dated).state == State.SCHEDULED

        ClassSession.objects.filter(class_offering=soon).get().delete()

        assert _record(dated).state == State.RETIRED

    def it_recomputes_once_a_deleted_run_is_gone():
        older = _run("framing-1", days=-400)
        recent = _run("framing-2", days=-30, category=older.category)
        record = _record(older)
        assert record.state == State.BETWEEN_RUNS

        ClassOffering.objects.get(pk=recent.pk).delete()

        record.refresh_from_db()
        assert record.state == State.RETIRED
        row = CmsActivity.objects.filter(kind=CmsActivity.Kind.CLASS_STATE_CHANGED).first()
        assert row is not None and row.class_offering == older

    def it_deletes_a_classs_only_run_with_its_dates():
        run = _run("framing-1", days=-30)

        ClassOffering.objects.get(pk=run.pk).delete()

        assert not ClassRecord.objects.exists()


def describe_no_longer_offered():
    def it_retires_a_class_between_runs_and_says_who():
        run = _run("framing-1", days=-30)
        record = _record(run)
        record.mark_no_longer_offered(None)

        record.refresh_from_db()
        assert (record.state, record.no_longer_offered) == (State.RETIRED, True)
        assert record.no_longer_offered_at is not None

    def it_retires_a_class_even_with_a_date_to_come_and_keeps_it_retired():
        record = _record(_run("framing-1", days=10))

        record.mark_no_longer_offered(None)
        record.refresh_state()

        record.refresh_from_db()
        assert (record.state, record.no_longer_offered) == (State.RETIRED, True)

    def it_is_cleared_when_a_new_run_is_published():
        past = _run("framing-1", days=-30)
        record = _record(past)
        record.mark_no_longer_offered(None)

        _run("framing-2", days=20, category=past.category)

        record.refresh_from_db()
        assert (record.state, record.no_longer_offered, record.no_longer_offered_at) == (State.SCHEDULED, False, None)

    def it_can_be_offered_again():
        record = _record(_run("framing-1", days=-30))
        record.mark_no_longer_offered(None)

        record.offer_again(None)

        record.refresh_from_db()
        assert (record.state, record.no_longer_offered) == (State.BETWEEN_RUNS, False)


def describe_the_admin_actions():
    def _act(client, action: str, record: ClassRecord):
        return client.post(
            reverse("admin:classes_classrecord_changelist"),
            {"action": action, "_selected_action": [record.pk]},
            follow=True,
        )

    def it_marks_and_clears_no_longer_offered(admin_client):
        record = _record(_run("framing-1", days=-30))

        _act(admin_client, "mark_no_longer_offered", record)
        record.refresh_from_db()
        assert record.state == State.RETIRED
        assert record.no_longer_offered_by is not None

        _act(admin_client, "offer_again", record)
        record.refresh_from_db()
        assert record.state == State.BETWEEN_RUNS

    def it_says_which_class_it_retired(admin_client):
        record = _record(_run("framing-1", days=10))

        body = _act(admin_client, "mark_no_longer_offered", record).content.decode()

        assert "Picture Framing is no longer offered." in body


def describe_the_daily_job():
    def it_moves_the_classes_the_clock_moved():
        run = _run("framing-1", days=-30)
        ClassRecord.objects.filter(pk=run.class_record_id).update(state=State.SCHEDULED)
        _run("pottery-1", days=5, title="Pottery")
        out = StringIO()

        call_command("refresh_class_states", stdout=out)

        assert _record(run).state == State.BETWEEN_RUNS
        assert "1 class(es) changed state." in out.getvalue()
