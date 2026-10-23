"""BDD specs for the Email Queue tab under Manage > Site Activity (#894 part 2).

Read only: what waits for the next 8 AM email and in what state, the timed emails the cron
sends in the next 24 hours, the staff emails sent this week, and the Instant or 8 AM digest
pill on Site Settings > Emails.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from classes.emails import send_guild_lead_review_reminder
from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    ClassSessionFactory,
    InstructorFactory,
    RegistrationFactory,
    UserFactory,
)
from classes.models import ClassApproval, ClassOffering, ClassSession, Registration
from core.events.email_catalogue import build_email_catalogue
from core.events.scheduler import ScheduledOccurrence, flush_digests
from core.events.scheduling import send_tick
from core.models import EventDelivery, NotificationPreference
from core.services.email_queue import DigestQueue, _subject_key, next_digest_at, sent_digests, upcoming_timed_emails
from membership.models import GuildStaffMembership, Member
from tests.core.events.digest_support import PACIFIC, next_8am
from tests.membership.factories import (
    CommunityEventFactory,
    GuildFactory,
    GuildMembershipFactory,
    GuildStaffMembershipFactory,
)

pytestmark = pytest.mark.django_db

QUEUE_URL = reverse("manage_activity")
UPCOMING_URL = reverse("manage_activity_upcoming")


def _person(slug: str, first: str, last: str) -> Member:
    user = UserFactory(username=f"{slug}@example.com", email=f"{slug}@example.com", first_name=first, last_name=last)
    member = Member.objects.get(user=user)
    member.full_legal_name = f"{first} {last}"
    member.save(update_fields=["full_legal_name"])
    return member


class _Woodshop:
    """A Woodworking lead, one Class Reviewer, and Alex teaching."""

    def __init__(self) -> None:
        self.lead = _person("chris", "Chris", "Ellwanger")
        self.reviewer = _person("rae", "Rae", "Okafor")
        self.guild = GuildFactory(name="Woodworking", guild_lead=self.lead)
        GuildStaffMembershipFactory(
            guild=self.guild, member=self.reviewer, role=GuildStaffMembership.Role.SECRETARY, can_review_classes=True
        )
        alex = UserFactory(username="alex@example.com", email="alex@example.com", first_name="Alex", last_name="Eason")
        self.instructor = InstructorFactory(user=alex, full_legal_name="Alex Eason", instructor_slug="alex-eason")
        self.category = CategoryFactory(guild=self.guild)

    def submit(self, title: str) -> ClassApproval:
        offering = ClassOfferingFactory(
            ready=True,
            title=title,
            category=self.category,
            instructor=self.instructor,
            status=ClassOffering.Status.DRAFT,
        )
        (gate,) = offering.submit_for_review()
        return gate


@pytest.fixture
def shop() -> _Woodshop:
    return _Woodshop()


@pytest.fixture
def staff_client(client: Client) -> Client:
    client.force_login(UserFactory(username="lee@example.com", email="lee@example.com", is_staff=True))
    return client


def _queue_html(staff_client: Client) -> str:
    response = staff_client.get(QUEUE_URL, {"tab": "queue"})
    assert response.status_code == 200
    return response.content.decode()


def _opted_in_guild_member(guild: object, slug: str, first: str, last: str) -> Member:
    """A guild member who switched event reminder email on (it is off by default)."""
    member = _person(slug, first, last)
    GuildMembershipFactory(guild=guild, member=member)
    NotificationPreference.objects.create(user=member.user, event_key="event.reminder", channel="email", enabled=True)
    return member


def _class_reminder(*, title: str, hours_from_now: float) -> Registration:
    """A confirmed registrant on a live class whose 24 hour reminder is due ``hours_from_now``."""
    offering = ClassOfferingFactory(title=title, status=ClassOffering.Status.PUBLISHED)
    session = ClassSessionFactory(
        class_offering=offering, starts_at=timezone.now() + timedelta(hours=24 + hours_from_now)
    )
    registration = RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
    registration.session = session  # type: ignore[attr-defined]
    return registration


def _move_session(registration: Registration, starts_at: datetime) -> None:
    """Move a reminder spec's session to ``starts_at``, keeping its two hours."""
    session = registration.session  # type: ignore[attr-defined]
    ClassSession.objects.filter(pk=session.pk).update(starts_at=starts_at, ends_at=starts_at + timedelta(hours=2))


def describe_the_tab():
    def it_is_staff_only(client: Client):
        client.force_login(UserFactory(username="member@example.com", email="member@example.com"))
        assert client.get(QUEUE_URL).status_code == 302
        assert client.get(UPCOMING_URL).status_code == 302

    def it_sits_after_email_log_inside_the_section_picker(staff_client: Client):
        html = _queue_html(staff_client)
        assert html.index("Activity Feed") < html.index("Email Log") < html.index("Email Queue")
        assert 'aria-label="Site Activity"' in html
        assert "data-tab-picker" in html or "pl-tab-picker" in html

    def it_has_no_add_delete_or_save_control(shop: _Woodshop):
        shop.submit("Board One")
        now = timezone.now()
        html = render_to_string(
            "hub/admin/_activity_queue.html",
            {"digest_queue": DigestQueue.load(now), "sent_digests": sent_digests(now)},
        )
        assert "<form" not in html
        assert "<button" not in html

    def it_spaces_every_card_and_row_through_the_email_queue_stylesheet(staff_client: Client, shop: _Woodshop):
        # FRONTEND.md rule 26: the four cards clear each other by 1.5rem through a class, never inline.
        shop.submit("Board One")
        html = _queue_html(staff_client)
        assert "css/email-queue.css" in html
        assert html.count('class="hub-card pl-email-queue__section"') == 4
        assert html.count('class="pl-email-queue__rows"') == 3
        assert 'class="pl-email-queue__rows" hx-get=' in html
        assert "pl-email-queue__person" in html
        assert 'class="hub-member-row pl-email-queue__row" data-queue-item' in html
        queue_pane = html[html.index("pl-email-queue") :]
        assert 'style="' not in queue_pane[: queue_pane.index("</section>\n</div>")]

    def it_loads_coming_up_only_when_the_tab_opens(staff_client: Client):
        html = _queue_html(staff_client)
        assert f'hx-get="{UPCOMING_URL}" hx-trigger="intersect once"' in html


def describe_the_top_card():
    def it_names_the_next_8am_and_counts_people_items_and_handled(staff_client: Client, shop: _Woodshop):
        first = shop.submit("Board One")
        shop.submit("Board Two")
        first.decide(ClassApproval.Decision.APPROVED, user=shop.lead.user)
        html = _queue_html(staff_client)
        when = next_digest_at(timezone.now())
        assert f"Next digest: {when:%A, %B} {when.day} at 8:00 AM" in html
        assert "2 people · 2 items waiting · 2 already handled (will be left out)" in html
        assert "?tab=automations" in html

    def it_is_todays_8am_before_it_and_tomorrows_after():
        morning = timezone.now().astimezone(PACIFIC).replace(hour=7, minute=0, second=0, microsecond=0)
        assert next_digest_at(morning).date() == morning.date()
        assert next_digest_at(morning + timedelta(hours=2)).date() == morning.date() + timedelta(days=1)


def describe_waiting_for_the_next_digest():
    def it_says_so_when_nothing_is_waiting(staff_client: Client):
        assert "Nothing is waiting for the next digest." in _queue_html(staff_client)

    def it_lists_one_row_per_person_with_their_email_and_item_count(staff_client: Client, shop: _Woodshop):
        shop.submit("Board One")
        shop.submit("Board Two")
        html = _queue_html(staff_client)
        assert html.count("data-queue-person") == 2
        assert "chris@example.com · 2 items" in html
        assert "rae@example.com · 2 items" in html
        assert "Review request: Board One" in html
        assert "Still waiting" in html

    def it_marks_a_request_decided_since_as_handled(shop: _Woodshop):
        gate = shop.submit("Board One")
        gate.decide(ClassApproval.Decision.APPROVED, user=shop.lead.user)
        (lead,) = [person for person in DigestQueue.load(timezone.now()).people if person.email == "chris@example.com"]
        assert [item.pill_label for item in lead.items] == ["Handled, will be left out"]
        assert lead.items[0].pill_tone == "neutral"

    def it_shows_who_pressed_remind_lead_once_per_request(shop: _Woodshop):
        gate = shop.submit("Board One")
        send_guild_lead_review_reminder(gate, reminded_by=shop.instructor.user)
        (lead,) = [person for person in DigestQueue.load(timezone.now()).people if person.email == "chris@example.com"]
        assert [(item.pill_label, item.pill_tone) for item in lead.items] == [("Reminded by Alex Eason", "warn")]

    def it_marks_a_line_the_send_will_skip_because_the_email_switch_is_off(shop: _Woodshop):
        shop.submit("Board One")
        NotificationPreference.objects.create(
            user=shop.lead.user, event_key="class_review_requested", channel="email", enabled=False
        )
        queue = DigestQueue.load(timezone.now())
        (lead,) = [person for person in queue.people if person.name == "Chris Ellwanger"]
        assert [(item.pill_label, item.pill_tone) for item in lead.items] == [("Will be skipped: email off", "neutral")]
        assert (queue.waiting_count, queue.handled_count, queue.skipped_count) == (1, 0, 1)

    def it_marks_a_line_the_send_will_skip_for_want_of_an_address(staff_client: Client, shop: _Woodshop):
        shop.submit("Board One")
        User.objects.filter(pk=shop.lead.user.pk).update(email="")
        (lead,) = [p for p in DigestQueue.load(timezone.now()).people if p.name == "Chris Ellwanger"]
        assert lead.email == "no email address"
        assert [item.pill_label for item in lead.items] == ["Will be skipped: no email address"]
        assert "1 item waiting · 0 already handled (will be left out) · 1 will be skipped" in _queue_html(staff_client)

    def it_names_an_account_with_no_member_by_its_email_and_skips_one_that_is_gone(shop: _Woodshop):
        shop.submit("Board One")
        Member.objects.filter(user=shop.reviewer.user).update(user=None)
        lead_pk = shop.lead.user.pk
        User.objects.filter(pk=lead_pk).delete()
        people = DigestQueue.load(timezone.now()).people
        assert [(person.name, person.email) for person in people] == [("Rae Okafor", "rae@example.com")]


def describe_sent_digests():
    def it_says_so_when_none_went_out(staff_client: Client):
        assert "No digests sent in the last 7 days." in _queue_html(staff_client)

    def it_lists_the_date_person_items_included_and_left_out(staff_client: Client, shop: _Woodshop):
        decided = shop.submit("Board One")
        shop.submit("Board Two")
        decided.decide(ClassApproval.Decision.APPROVED, user=shop.lead.user)
        eight = next_8am()
        flush_digests(eight)
        html = _queue_html(staff_client)
        assert f"{eight:%a, %b} {eight.day} · chris@example.com" in html
        assert "1 included" in html
        assert "1 left out" in html

    def it_skips_a_row_with_no_counts_and_a_person_who_is_gone(shop: _Woodshop):
        EventDelivery.objects.create(
            event_key="staff_digest",
            target_ref=f"user:{shop.lead.user.pk}",
            channel="email",
            period="digest:2026-10-23",
        )
        gone = EventDelivery.objects.create(
            event_key="staff_digest", target_ref="user:999999", channel="email", period="digest:2026-10-23"
        )
        gone.record_digest(subject="S", included=1, left_out=0)
        assert sent_digests(timezone.now()) == []

    def it_refuses_counts_on_a_row_that_recorded_none():
        with pytest.raises(ValueError):
            _ = EventDelivery(event_key="staff_digest").digest_counts


def describe_coming_up_in_the_next_24_hours():
    def it_says_so_when_nothing_is_due(staff_client: Client):
        response = staff_client.get(UPCOMING_URL)
        assert "No timed emails in the next 24 hours." in response.content.decode()

    def it_lists_a_class_reminder_with_its_time_label_class_and_reach(staff_client: Client):
        registration = _class_reminder(title="Cutting Board Basics", hours_from_now=2)
        html = staff_client.get(UPCOMING_URL).content.decode()
        send_at = timezone.localtime(registration.session.starts_at - timedelta(hours=24))  # type: ignore[attr-defined]
        assert "Cutting Board Basics" in html
        assert f"{send_at:%a, %b} {send_at.day}, " in html
        assert "Class reminder" in html
        assert "1 person" in html

    def it_groups_everyone_on_one_session_into_one_row():
        registration = _class_reminder(title="Cutting Board Basics", hours_from_now=2)
        RegistrationFactory(class_offering=registration.class_offering, status=Registration.Status.CONFIRMED)
        (row,) = upcoming_timed_emails(timezone.now())
        assert row.reach == 2

    def it_leaves_out_a_reminder_the_ledger_already_holds():
        registration = _class_reminder(title="Cutting Board Basics", hours_from_now=2)
        EventDelivery.objects.create(
            event_key="class_reminder",
            target_ref=f"email:{registration.email.lower()}",
            channel="email",
            period=f"reg:{registration.pk}:reminder:{registration.session.pk}",  # type: ignore[attr-defined]
        )
        assert upcoming_timed_emails(timezone.now()) == []

    def it_counts_only_people_whose_email_is_on():
        guild = GuildFactory(name="Metal")
        _opted_in_guild_member(guild, "mo", "Mo", "Smith")
        # Event reminder email is off by default, so a member who never switched it on is not reached.
        GuildMembershipFactory(guild=guild, member=_person("quinn", "Quinn", "Quiet"))
        CommunityEventFactory(
            guild=guild, title="Open Shop Night", starts_at=timezone.now() + timedelta(days=1, hours=3), remind_1d=True
        )
        (row,) = [row for row in upcoming_timed_emails(timezone.now()) if row.about == "Open Shop Night"]
        assert row.reach == 1

    def it_shows_the_quarter_hour_tick_the_cron_sends_on_and_skips_one_already_past():
        now = send_tick(timezone.now()) + timedelta(minutes=5)
        sent_soon = now + timedelta(hours=2)
        fire = send_tick(sent_soon) + timedelta(minutes=7)
        registration = _class_reminder(title="Lathe", hours_from_now=0)
        _move_session(registration, fire + timedelta(hours=24))
        late = _class_reminder(title="Already Going", hours_from_now=0)
        _move_session(late, now + timedelta(minutes=2, hours=24))
        rows = upcoming_timed_emails(now)
        assert [(row.about, row.send_at) for row in rows] == [("Lathe", send_tick(sent_soon))]

    def it_leaves_out_one_beyond_24_hours():
        _class_reminder(title="Cutting Board Basics", hours_from_now=30)
        assert upcoming_timed_emails(timezone.now()) == []

    def it_lists_an_event_reminder_by_the_event_title():
        guild = GuildFactory(name="Metal")
        _opted_in_guild_member(guild, "mo", "Mo", "Smith")
        CommunityEventFactory(
            guild=guild, title="Open Shop Night", starts_at=timezone.now() + timedelta(days=1, hours=3), remind_1d=True
        )
        assert "Open Shop Night" in [row.about for row in upcoming_timed_emails(timezone.now())]

    def it_names_a_voting_reminder_by_its_cycle_and_refuses_an_unknown_target():
        voting = ScheduledOccurrence(
            event_key="voting.closing_soon", anchor=timezone.now(), offset=timedelta(0), context={"cycle_label": "June"}
        )
        assert _subject_key(voting) == ("cycle", "June")
        odd = ScheduledOccurrence(
            event_key="class_reminder", anchor=timezone.now(), offset=timedelta(0), target=CategoryFactory()
        )
        with pytest.raises(ValueError):
            _subject_key(odd)

    def it_runs_in_a_bounded_number_of_queries(staff_client: Client, django_assert_max_num_queries):
        _class_reminder(title="Cutting Board Basics", hours_from_now=2)
        CommunityEventFactory(
            title="Open Shop Night", starts_at=timezone.now() + timedelta(days=1, hours=3), remind_1d=True
        )
        with django_assert_max_num_queries(320):
            assert staff_client.get(UPCOMING_URL).status_code == 200

    def it_keeps_the_rest_of_the_page_off_that_walk(
        staff_client: Client, shop: _Woodshop, django_assert_max_num_queries
    ):
        shop.submit("Board One")
        _class_reminder(title="Cutting Board Basics", hours_from_now=2)
        with django_assert_max_num_queries(40):
            _queue_html(staff_client)


def describe_send_tick():
    def it_is_the_quarter_hour_at_or_before_the_fire_time():
        at = timezone.now().replace(hour=9, minute=7, second=30, microsecond=5)
        assert send_tick(at) == at.replace(minute=0, second=0, microsecond=0)
        assert send_tick(at.replace(minute=15, second=0, microsecond=0)) == at.replace(
            minute=15, second=0, microsecond=0
        )


def describe_the_emails_tab_pill():
    def it_says_8am_digest_for_a_digest_email_and_instant_for_the_rest():
        rows = {row.key: row for _category, group in build_email_catalogue() for row in group}
        assert rows["class_review_requested"].delivery_label == "8 AM digest"
        assert rows["registration_confirmed"].delivery_label == "Instant"

    def it_shows_the_pill_on_each_row():
        html = render_to_string("hub/admin/_emails_tab.html", {"email_catalogue": build_email_catalogue()})
        assert 'data-email-delivery="class_review_requested">8 AM digest<' in html
        assert 'data-email-delivery="registration_confirmed">Instant<' in html
