"""BDD specs for #1186: instructors change the dates of their own live class and take it down."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from html import unescape

import pytest
from django.contrib.messages import get_messages
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.forms import LIVE_NO_SESSIONS_MESSAGE, LIVE_PAST_SESSION_MESSAGE
from classes.models import ClassOffering, ClassSession, Registration
from classes.spec.live_edit import live_facts
from classes.tasks import send_new_dates_notices
from core.models import Notification
from membership.models import AdminCapability, Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory

pytestmark = pytest.mark.django_db

Status = ClassOffering.Status
RegStatus = Registration.Status


@pytest.fixture(autouse=True)
def _outbox():
    mail.outbox = []


@pytest.fixture
def teacher(db) -> Member:
    user = UserFactory(username="dates-teacher@example.com", email="dates-teacher@example.com")
    return InstructorFactory(user=user, full_legal_name="Dana Dates", instructor_slug="dana-dates")


@pytest.fixture
def lead(db) -> Member:
    user = UserFactory(username="dates-lead@example.com", email="dates-lead@example.com")
    return Member.objects.get(user=user)


def _local(day_offset: int, hour: int) -> datetime:
    """A wall clock time ``day_offset`` days from now, in the site's timezone, on the hour."""
    day = timezone.localdate() + timedelta(days=day_offset)
    return timezone.make_aware(datetime(day.year, day.month, day.day, hour, 0))


def _live(teacher: Member, *, guild_lead: Member | None = None, **kwargs) -> ClassOffering:
    guild = GuildFactory(name="Dates Guild", guild_lead=guild_lead)
    offering = ClassOfferingFactory(
        instructor=teacher,
        status=Status.PUBLISHED,
        published_at=timezone.now(),
        title="Zz Spoon Carving",
        capacity=6,
        category=CategoryFactory(guild=guild),
        **kwargs,
    )
    if not offering.is_flexible:
        ClassSessionFactory(class_offering=offering, starts_at=_local(5, 10), ends_at=_local(5, 12))
    return offering


def _sessions(offering: ClassOffering, *rows: tuple[str, str, str, bool]) -> dict[str, str]:
    """The scheduler's hidden inputs: (id, starts_at, ends_at, delete) per row, saved rows first."""
    initial = sum(1 for pk, *_ in rows if pk)
    data = {
        "sessions-TOTAL_FORMS": str(len(rows)),
        "sessions-INITIAL_FORMS": str(initial),
        "sessions-MIN_NUM_FORMS": "0",
        "sessions-MAX_NUM_FORMS": "1000",
    }
    for i, (pk, starts, ends, delete) in enumerate(rows):
        data |= {
            f"sessions-{i}-id": pk,
            f"sessions-{i}-starts_at": starts,
            f"sessions-{i}-ends_at": ends,
            f"sessions-{i}-DELETE": "on" if delete else "",
        }
    return data


def _keep(session: ClassSession) -> tuple[str, str, str, bool]:
    fmt = "%Y-%m-%dT%H:%M"
    return (
        str(session.pk),
        timezone.localtime(session.starts_at).strftime(fmt),
        timezone.localtime(session.ends_at).strftime(fmt),
        False,
    )


def _drop(session: ClassSession) -> tuple[str, str, str, bool]:
    return (*_keep(session)[:3], True)


def _new(day_offset: int, hour: int) -> tuple[str, str, str, bool]:
    fmt = "%Y-%m-%dT%H:%M"
    start = _local(day_offset, hour)
    return (
        "",
        timezone.localtime(start).strftime(fmt),
        timezone.localtime(start + timedelta(hours=2)).strftime(fmt),
        False,
    )


def _post(offering: ClassOffering, **extra: str) -> dict[str, str]:
    return {
        **live_facts(offering),
        "description": offering.description,
        "faq-TOTAL_FORMS": "0",
        "faq-INITIAL_FORMS": "0",
        "faq-MIN_NUM_FORMS": "0",
        "faq-MAX_NUM_FORMS": "1000",
        **extra,
    }


def _edit(offering: ClassOffering) -> str:
    return reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})


def _messages(response) -> list[str]:
    return [m.message for m in get_messages(response.wsgi_request)]


def _scheduler_rows(html: str) -> list[dict]:
    """The rows the page hands the session scheduler (``sessionCalendar(<json>, <initial>)``)."""
    call = html.split('x-data="sessionCalendar(')[1].split(')"')[0]
    return json.loads(unescape(call.rsplit(", ", 1)[0]))


def _to(address: str) -> list:
    return [m for m in mail.outbox if m.to == [address]]


def _move_the_session(client, offering: ClassOffering, *, day_offset: int = 9, hour: int = 14):
    (session,) = offering.sessions.all()
    return client.post(_edit(offering), _post(offering, **_sessions(offering, _drop(session), _new(day_offset, hour))))


def describe_an_instructor_moves_a_session():
    def it_keeps_the_class_live_and_emails_each_seat_holder_once(
        teacher, lead, client, django_capture_on_commit_callbacks
    ):
        offering = _live(teacher, guild_lead=lead)
        for status, email in (
            (RegStatus.CONFIRMED, "kept@example.com"),
            (RegStatus.PENDING, "paying@example.com"),
            (RegStatus.WAITLISTED, "waiting@example.com"),
            (RegStatus.CANCELLED, "gone@example.com"),
        ):
            RegistrationFactory(class_offering=offering, status=status, email=email, member=None)
        before = offering.date_lines
        client.force_login(teacher.user)

        with django_capture_on_commit_callbacks(execute=True):
            resp = _move_the_session(client, offering)

        assert resp.status_code == 302
        assert "Class updated. Everyone signed up is getting the new dates by email." in _messages(resp)
        offering.refresh_from_db()
        assert offering.status == Status.PUBLISHED
        assert offering.approvals.count() == 0
        after = offering.date_lines
        assert after != before and len(after) == 1
        for address in ("kept@example.com", "paying@example.com", "waiting@example.com"):
            (sent,) = _to(address)
            assert sent.subject == "Zz Spoon Carving has new dates"
            assert before[0] in sent.body and after[0] in sent.body
        assert _to("gone@example.com") == []

    def it_tells_the_guild_lead_once_and_not_the_instructor(teacher, lead, client, django_capture_on_commit_callbacks):
        offering = _live(teacher, guild_lead=lead)
        client.force_login(teacher.user)
        with django_capture_on_commit_callbacks(execute=True):
            _move_the_session(client, offering)
        (notice,) = _to("dates-lead@example.com")
        assert notice.subject == "Zz Spoon Carving has new dates"
        assert Notification.objects.filter(trigger="class_rescheduled_lead_notice", user=lead.user).count() == 1
        assert not Notification.objects.filter(trigger="class_rescheduled_lead_notice", user=teacher.user).exists()

    def it_tells_the_cms_administrators_for_a_class_with_no_guild_lead(
        teacher, client, django_capture_on_commit_callbacks
    ):
        holder = UserFactory(username="dates-cms@example.com", email="dates-cms@example.com")
        Member.objects.get(user=holder).admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
        offering = _live(teacher)
        client.force_login(teacher.user)
        with django_capture_on_commit_callbacks(execute=True):
            _move_the_session(client, offering)
        assert len(_to("dates-cms@example.com")) == 1

    def it_leaves_the_lead_out_when_the_lead_moved_their_own_class(lead, client, django_capture_on_commit_callbacks):
        InstructorFactory(user=lead.user, full_legal_name="Lee Lead", instructor_slug="lee-lead")
        offering = _live(lead, guild_lead=lead)
        client.force_login(lead.user)
        with django_capture_on_commit_callbacks(execute=True):
            _move_the_session(client, offering)
        assert not Notification.objects.filter(trigger="class_rescheduled_lead_notice").exists()

    def it_adds_and_removes_dates_in_one_save(teacher, client, django_capture_on_commit_callbacks):
        offering = _live(teacher)
        RegistrationFactory(class_offering=offering, status=RegStatus.CONFIRMED, email="series@example.com")
        (session,) = offering.sessions.all()
        client.force_login(teacher.user)
        with django_capture_on_commit_callbacks(execute=True):
            resp = client.post(
                _edit(offering), _post(offering, **_sessions(offering, _keep(session), _new(12, 10), _new(19, 10)))
            )
        assert resp.status_code == 302
        assert offering.sessions.count() == 3
        (sent,) = _to("series@example.com")
        assert sent.body.count("at 10:00 AM to 12:00 PM") == 4  # one before, three after

    def it_sends_nothing_when_no_date_changed(teacher, lead, client, django_capture_on_commit_callbacks):
        offering = _live(teacher, guild_lead=lead)
        RegistrationFactory(class_offering=offering, status=RegStatus.CONFIRMED, email="calm@example.com")
        (session,) = offering.sessions.all()
        client.force_login(teacher.user)
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            resp = client.post(
                _edit(offering), _post(offering, title="Zz Spoon Carving Two", **_sessions(offering, _keep(session)))
            )
        assert resp.status_code == 302
        assert "Class updated." in _messages(resp)
        assert callbacks == []
        assert mail.outbox == []
        assert not Notification.objects.filter(trigger__startswith="class_rescheduled").exists()

    def it_reads_the_scheduler_fields_only_when_they_are_posted(teacher, client):
        offering = _live(teacher)
        client.force_login(teacher.user)
        resp = client.post(_edit(offering), _post(offering))
        assert resp.status_code == 302
        assert offering.sessions.count() == 1


def describe_sending_the_notices():
    def it_sends_each_change_once_however_often_the_task_runs(teacher, lead):
        offering = _live(teacher, guild_lead=lead)
        RegistrationFactory(class_offering=offering, status=RegStatus.CONFIRMED, email="twice@example.com", member=None)
        args = (offering.pk, ["Old date"], ["New date"], teacher.user.pk, "tok1")
        send_new_dates_notices.call(*args)
        send_new_dates_notices.call(*args)
        assert len(_to("twice@example.com")) == 1
        assert len(_to("dates-lead@example.com")) == 1
        # A later change is its own notice, even back to the old dates.
        send_new_dates_notices.call(offering.pk, ["New date"], ["Old date"], teacher.user.pk, "tok2")
        assert len(_to("twice@example.com")) == 2

    def it_gives_a_member_registrant_the_bell_row_and_the_email(teacher):
        offering = _live(teacher)
        member = Member.objects.get(user=UserFactory(username="bell@example.com", email="bell@example.com"))
        RegistrationFactory(
            class_offering=offering, status=RegStatus.CONFIRMED, email="bell@example.com", member=member
        )
        send_new_dates_notices.call(offering.pk, ["Old date"], ["New date"], None, "tok")
        bell = Notification.objects.get(trigger="class_rescheduled", user=member.user)
        assert bell.title == "Your class has new dates"
        assert len(_to("bell@example.com")) == 1

    def it_sends_nothing_for_a_class_no_longer_live(teacher, lead):
        offering = _live(teacher, guild_lead=lead)
        RegistrationFactory(class_offering=offering, status=RegStatus.CONFIRMED, email="late@example.com")
        ClassOffering.objects.filter(pk=offering.pk).update(status=Status.CANCELLED)
        send_new_dates_notices.call(offering.pk, ["Old date"], ["New date"], None, "tok")
        assert mail.outbox == []

    def it_links_the_class_page_and_the_registrants_own_page_and_uses_no_dashes(teacher):
        offering = _live(teacher)
        registration = RegistrationFactory(
            class_offering=offering, status=RegStatus.CONFIRMED, email="links@example.com"
        )
        send_new_dates_notices.call(offering.pk, ["Sat, Nov 22, 2026 at 10:00 AM to 12:00 PM"], ["Sun"], None, "tok")
        (sent,) = _to("links@example.com")
        html = sent.alternatives[0][0]
        for body in (sent.body, html):
            assert offering.public_path in body
            assert reverse("classes:my_registration", kwargs={"token": registration.self_serve_token}) in body
        assert "Can't make the new dates?" in sent.body
        for dash in ("—", "–", " - "):
            assert dash not in sent.body.split("Questions?")[0]


def describe_the_rules_on_a_live_classes_dates():
    def it_refuses_to_move_or_remove_a_date_that_already_happened(teacher, client):
        offering = _live(teacher)
        past = ClassSessionFactory(class_offering=offering, starts_at=_local(-3, 10), ends_at=_local(-3, 12))
        future = offering.sessions.exclude(pk=past.pk).get()
        client.force_login(teacher.user)
        for rows in ((_drop(past), _keep(future)), ((str(past.pk), *_new(-2, 10)[1:3], False), _keep(future))):
            resp = client.post(_edit(offering), _post(offering, **_sessions(offering, *rows)))
            assert resp.status_code == 200
            assert LIVE_PAST_SESSION_MESSAGE in resp.content.decode()
        assert ClassSession.objects.get(pk=past.pk).starts_at == past.starts_at

    def it_keeps_a_past_date_that_is_posted_unchanged(teacher, client):
        offering = _live(teacher)
        past = ClassSessionFactory(class_offering=offering, starts_at=_local(-3, 10), ends_at=_local(-3, 12))
        future = offering.sessions.exclude(pk=past.pk).get()
        client.force_login(teacher.user)
        resp = client.post(
            _edit(offering), _post(offering, **_sessions(offering, _keep(past), _drop(future), _new(8, 9)))
        )
        assert resp.status_code == 302

    def it_refuses_to_remove_every_date(teacher, client):
        offering = _live(teacher)
        (session,) = offering.sessions.all()
        client.force_login(teacher.user)
        resp = client.post(_edit(offering), _post(offering, **_sessions(offering, _drop(session))))
        assert resp.status_code == 200
        assert LIVE_NO_SESSIONS_MESSAGE in resp.content.decode()
        assert offering.sessions.count() == 1

    def it_lets_a_class_that_went_live_with_no_dates_save_with_none(teacher, client):
        offering = _live(teacher)
        offering.sessions.all().delete()
        client.force_login(teacher.user)
        resp = client.post(_edit(offering), _post(offering, **_sessions(offering)))
        assert resp.status_code == 302

    def it_locks_a_date_that_already_happened_in_the_scheduler(teacher, client):
        offering = _live(teacher)
        past = ClassSessionFactory(class_offering=offering, starts_at=_local(-3, 10), ends_at=_local(-3, 12))
        client.force_login(teacher.user)
        html = client.get(_edit(offering)).content.decode()
        rows = _scheduler_rows(html)
        assert {row["id"]: row["locked"] for row in rows} == {
            past.pk: True,
            offering.sessions.exclude(pk=past.pk).get().pk: False,
        }
        assert 'x-show="!s.locked"' in html

    def it_relocks_the_date_when_a_refused_save_comes_back(teacher, client):
        offering = _live(teacher)
        past = ClassSessionFactory(class_offering=offering, starts_at=_local(-3, 10), ends_at=_local(-3, 12))
        future = offering.sessions.exclude(pk=past.pk).get()
        client.force_login(teacher.user)
        html = client.post(_edit(offering), _post(offering, **_sessions(offering, _drop(past), _keep(future)))).content
        rows = _scheduler_rows(html.decode())
        assert {row["id"]: row["locked"] for row in rows} == {str(past.pk): True, str(future.pk): False}


def describe_who_changes_the_dates():
    def it_ignores_a_guild_staffers_crafted_post_of_the_dates(teacher, client, django_capture_on_commit_callbacks):
        staffer = InstructorFactory(user=UserFactory(username="staffer@example.com"), instructor_slug="staffer")
        offering = _live(teacher)
        GuildStaffMembershipFactory(guild=offering.category.guild, member=staffer)
        RegistrationFactory(class_offering=offering, status=RegStatus.CONFIRMED, email="safe@example.com")
        (session,) = offering.sessions.all()
        client.force_login(staffer.user)
        with django_capture_on_commit_callbacks(execute=True):
            resp = client.post(_edit(offering), _post(offering, **_sessions(offering, _drop(session), _new(9, 14))))
        assert resp.status_code == 302
        assert list(offering.sessions.values_list("pk", "starts_at")) == [(session.pk, session.starts_at)]
        assert mail.outbox == []

    def it_ignores_a_guild_staffers_crafted_window(teacher, client):
        staffer = InstructorFactory(user=UserFactory(username="staffer2@example.com"), instructor_slug="staffer2")
        offering = _live(
            teacher,
            scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
            flexible_starts_on=date(2031, 3, 1),
            flexible_ends_on=date(2031, 4, 1),
        )
        GuildStaffMembershipFactory(guild=offering.category.guild, member=staffer)
        client.force_login(staffer.user)
        resp = client.post(
            _edit(offering), _post(offering, flexible_starts_on="2031-05-01", flexible_ends_on="2031-06-01")
        )
        assert resp.status_code == 302
        offering.refresh_from_db()
        assert (offering.flexible_starts_on, offering.flexible_ends_on) == (date(2031, 3, 1), date(2031, 4, 1))


def describe_a_flexible_classes_window():
    def it_emails_the_registrants_when_the_window_moves(teacher, client, django_capture_on_commit_callbacks):
        offering = _live(
            teacher,
            scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
            flexible_starts_on=date(2031, 3, 1),
            flexible_ends_on=date(2031, 4, 1),
        )
        RegistrationFactory(class_offering=offering, status=RegStatus.CONFIRMED, email="flex@example.com")
        client.force_login(teacher.user)
        with django_capture_on_commit_callbacks(execute=True):
            resp = client.post(
                _edit(offering), _post(offering, flexible_starts_on="2031-05-01", flexible_ends_on="2031-06-01")
            )
        assert resp.status_code == 302
        (sent,) = _to("flex@example.com")
        assert "Mar 1 to Apr 1, 2031" in sent.body and "May 1 to Jun 1, 2031" in sent.body

    def it_refuses_a_last_day_before_the_first(teacher, client):
        offering = _live(teacher, scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
        client.force_login(teacher.user)
        resp = client.post(
            _edit(offering), _post(offering, flexible_starts_on="2031-05-01", flexible_ends_on="2031-04-01")
        )
        assert resp.status_code == 200
        assert "The last day is before the first day." in resp.content.decode()

    def it_reads_any_time_for_a_window_with_no_days(teacher):
        offering = _live(teacher, scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
        assert offering.date_lines == [ClassOffering.ANY_TIME_LINE]


def describe_capacity_on_a_live_class():
    def it_refuses_fewer_seats_than_are_held_on_the_field(teacher, client):
        offering = _live(teacher)
        for status in (RegStatus.CONFIRMED, RegStatus.PENDING, RegStatus.WAITLISTED):
            RegistrationFactory(class_offering=offering, status=status)
        RegistrationFactory(class_offering=offering, status=RegStatus.CANCELLED)
        client.force_login(teacher.user)
        resp = client.post(_edit(offering), _post(offering, capacity="2"))
        assert resp.status_code == 200
        assert "3 people already hold a seat, so capacity cannot go below 3." in resp.content.decode()
        offering.refresh_from_db()
        assert offering.capacity == 6
        assert client.post(_edit(offering), _post(offering, capacity="3")).status_code == 302

    def it_says_one_person_for_a_single_held_seat_and_keeps_the_one_seat_floor(teacher, client):
        offering = _live(teacher)
        RegistrationFactory(class_offering=offering, status=RegStatus.CONFIRMED)
        client.force_login(teacher.user)
        assert client.post(_edit(offering), _post(offering, capacity="1")).status_code == 302
        offering.refresh_from_db()
        resp = client.post(_edit(offering), _post(offering, capacity="0"))
        assert "A class needs at least 1 seat." in resp.content.decode()
        Registration.objects.filter(class_offering=offering).update(status=RegStatus.CANCELLED)
        RegistrationFactory(class_offering=offering, status=RegStatus.CONFIRMED)
        RegistrationFactory(class_offering=offering, status=RegStatus.CONFIRMED)
        resp = client.post(_edit(offering), _post(offering, capacity="1"))
        assert "2 people already hold a seat, so capacity cannot go below 2." in resp.content.decode()


def describe_taking_a_class_out_of_the_catalog():
    def it_returns_a_class_nobody_signed_up_for_to_draft(teacher, client):
        offering = _live(teacher)
        client.force_login(teacher.user)
        resp = client.post(reverse("classes:teach_class_unpublish", kwargs={"pk": offering.pk}))
        assert resp.status_code == 302
        assert "Zz Spoon Carving is out of the catalog and back to draft. Submit it again to go live." in _messages(
            resp
        )
        offering.refresh_from_db()
        assert offering.status == Status.DRAFT
        assert offering.published_at is None
        assert mail.outbox == []

    def it_refuses_a_class_someone_holds_a_seat_in(teacher, client):
        offering = _live(teacher)
        RegistrationFactory(class_offering=offering, status=RegStatus.WAITLISTED)
        client.force_login(teacher.user)
        resp = client.post(reverse("classes:teach_class_unpublish", kwargs={"pk": offering.pk}))
        assert resp.status_code == 302
        assert ClassOffering.TAKE_DOWN_HAS_SIGNUPS in _messages(resp)
        offering.refresh_from_db()
        assert offering.status == Status.PUBLISHED

    def it_refuses_guild_staff_and_get(teacher, client):
        staffer = InstructorFactory(user=UserFactory(username="staffer3@example.com"), instructor_slug="staffer3")
        offering = _live(teacher)
        GuildStaffMembershipFactory(guild=offering.category.guild, member=staffer)
        url = reverse("classes:teach_class_unpublish", kwargs={"pk": offering.pk})
        client.force_login(staffer.user)
        assert client.post(url).status_code == 404
        client.force_login(teacher.user)
        assert client.get(url).status_code == 405
        offering.refresh_from_db()
        assert offering.status == Status.PUBLISHED

    def it_shows_the_button_with_no_signups_and_the_cancel_pointer_with_some(teacher, client):
        offering = _live(teacher)
        detail = reverse("classes:teach_class_detail", kwargs={"pk": offering.pk})
        url = reverse("classes:teach_class_unpublish", kwargs={"pk": offering.pk})
        client.force_login(teacher.user)
        html = client.get(detail).content.decode()
        assert url in html and "data-take-down-pointer" not in html
        RegistrationFactory(class_offering=offering, status=RegStatus.CONFIRMED)
        html = client.get(detail).content.decode()
        assert url not in html
        assert "People are signed up, so use Cancel class to take it down." in html
        assert "Cancel this class?" in html
