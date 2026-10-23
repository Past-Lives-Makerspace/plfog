"""BDD specs for the 8 AM staff digest (#894 part 1): class review requests wait for one morning email.

An instructor submits classes; the reviewers' bell rings at once, and their email waits for
8:00 Pacific, when each reviewer gets one email listing what is still open. A request decided
first drops out of the digest and its bell entry is marked read.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.core import mail
from django.core.management import call_command
from django.db import IntegrityError
from django.test import Client
from django.urls import reverse

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassApproval, ClassOffering
from core.events import resolvers
from core.events.channels import DigestAdapter, Message, digest_adapter, digest_context
from core.events.emit import emit
from core.events.registry import Channel, EmailDelivery, all_events
from core.events.scheduler import flush_digests
from core.models import EventDelivery, Notification, NotificationPreference, TransactionalEmailLog
from membership.models import GuildStaffMembership, Member
from tests.core.events.digest_support import next_8am
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory

pytestmark = pytest.mark.django_db

PACIFIC = ZoneInfo("America/Los_Angeles")
DIGEST_EVENTS = {"class_review_requested", "class_validation_requested"}


@pytest.fixture(autouse=True)
def _outbox() -> None:
    mail.outbox = []


def _person(slug: str, first: str, last: str) -> Member:
    """A member with a login and an email, named ``first last``."""
    user = UserFactory(username=f"{slug}@example.com", email=f"{slug}@example.com", first_name=first, last_name=last)
    member = Member.objects.get(user=user)
    member.full_legal_name = f"{first} {last}"
    member.save(update_fields=["full_legal_name"])
    return member


class _Woodshop:
    """The incident's shape: a Woodworking lead, one Class Reviewer, and Alex teaching."""

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


_next_8am = next_8am


def _digests(member: Member) -> list:
    return [m for m in mail.outbox if m.to == [member.user.email] and "waiting on you" in m.subject]


def _review_emails(member: Member) -> list:
    return [m for m in mail.outbox if m.to == [member.user.email] and m.subject.startswith("Review request:")]


def _bell(member: Member, gate: ClassApproval) -> Notification:
    return Notification.objects.get(user=member.user, url=gate.review_path)


def _admin_approves(gate: ClassApproval) -> None:
    """An admin gives final approval, which closes the lead's open gate for them."""
    admin = UserFactory(username="lee@example.com", email="lee@example.com", is_superuser=True)
    admin_row = ClassApproval.objects.create(class_offering=gate.class_offering, role=ClassApproval.Role.ADMIN)
    admin_row.decide(ClassApproval.Decision.APPROVED, user=admin)


def _email_off(member: Member) -> None:
    NotificationPreference.objects.create(
        user=member.user, event_key="class_review_requested", channel="email", enabled=False
    )


def _queued_at(when: datetime) -> None:
    """Backdate every queued digest line, standing in for a request filed at ``when``."""
    EventDelivery.objects.filter(channel=Channel.DIGEST.value).update(created_at=when)


def describe_email_delivery():
    def it_keeps_every_event_with_no_declaration_instant():
        for event in all_events():
            expected = EmailDelivery.DIGEST if event.key in DIGEST_EVENTS else EmailDelivery.INSTANT
            assert event.email_delivery is expected, event.key

    def it_sends_an_instant_event_email_at_once():
        user = UserFactory(username="ines@example.com", email="ines@example.com")
        emit("event.approved", context={"user": user}, title="Approved", body="Your event", period="p")
        assert [m.to for m in mail.outbox] == [["ines@example.com"]]
        assert not EventDelivery.objects.filter(channel=Channel.DIGEST.value).exists()

    def it_registers_a_still_open_check_for_every_digest_event():
        for event in all_events():
            if event.email_delivery is EmailDelivery.DIGEST:
                assert resolvers.has_open_check(event.key), event.key


def describe_submitting_a_class():
    def it_rings_the_bell_at_once_and_emails_no_reviewer(shop: _Woodshop):
        gate = shop.submit("Cutting Board Basics")
        assert _bell(shop.lead, gate).is_unread
        assert _bell(shop.reviewer, gate).is_unread
        assert _review_emails(shop.lead) == []
        assert _review_emails(shop.reviewer) == []
        assert len(DigestAdapter.pending_for(shop.lead.user)) == 1

    def it_still_emails_the_instructor_explainer_at_once(shop: _Woodshop):
        shop.submit("Cutting Board Basics")
        explainers = [m for m in mail.outbox if m.to == ["alex@example.com"]]
        assert [m.subject for m in explainers] == ["Your class 'Cutting Board Basics' is in review"]


def describe_the_8am_digest():
    def it_sends_each_reviewer_one_email_listing_all_three(shop: _Woodshop):
        for title in ("Board One", "Board Two", "Board Three"):
            shop.submit(title)
        flush_digests(_next_8am())
        for member in (shop.lead, shop.reviewer):
            (digest,) = _digests(member)
            assert digest.subject == "3 things are waiting on you at Past Lives"
            for title in ("Board One", "Board Two", "Board Three"):
                assert f"Review request: {title}" in digest.body
            assert _review_emails(member) == []

    def it_links_each_line_to_its_review_page(shop: _Woodshop):
        gate = shop.submit("Board One")
        flush_digests(_next_8am())
        (digest,) = _digests(shop.lead)
        assert f"/classes/review/{gate.token}/" in digest.body
        assert f"/classes/review/{gate.token}/" in digest.alternatives[0][0]

    def it_writes_one_email_log_row_per_digest_under_its_own_trigger_kind(shop: _Woodshop):
        shop.submit("Board One")
        shop.submit("Board Two")
        flush_digests(_next_8am())
        logs = TransactionalEmailLog.objects.filter(trigger_kind="staff_digest")
        assert sorted(logs.values_list("to_email", flat=True)) == ["chris@example.com", "rae@example.com"]

    def it_sends_nothing_before_8am(shop: _Woodshop):
        shop.submit("Board One")
        assert flush_digests(_next_8am() - timedelta(minutes=15)) == {}
        assert _digests(shop.lead) == []

    def it_leaves_a_request_filed_after_8am_for_the_next_morning(shop: _Woodshop):
        shop.submit("Board One")
        eight = _next_8am()
        _queued_at(eight + timedelta(minutes=5))
        flush_digests(eight + timedelta(minutes=15))
        assert _digests(shop.lead) == []
        flush_digests(eight + timedelta(days=1))
        assert len(_digests(shop.lead)) == 1

    def it_names_the_event_label_over_its_lines(shop: _Woodshop):
        shop.submit("Board One")
        flush_digests(_next_8am())
        (digest,) = _digests(shop.lead)
        assert "CLASS REVIEW REQUEST" in digest.body


def describe_a_request_decided_before_8am():
    def it_leaves_an_admin_approved_class_out_and_marks_its_bell_read(shop: _Woodshop):
        first = shop.submit("Board One")
        second = shop.submit("Board Two")
        _admin_approves(first)
        flush_digests(_next_8am())
        (digest,) = _digests(shop.lead)
        assert "Board One" not in digest.body
        assert "Board Two" in digest.body
        assert digest.subject == "1 thing is waiting on you at Past Lives"
        assert _bell(shop.lead, first).is_unread is False
        assert _bell(shop.reviewer, first).is_unread is False
        assert _bell(shop.lead, second).is_unread

    def it_sends_no_digest_when_all_three_are_decided(shop: _Woodshop):
        gates = [shop.submit(title) for title in ("Board One", "Board Two", "Board Three")]
        for gate in gates:
            gate.decide(ClassApproval.Decision.APPROVED, user=shop.lead.user)
        assert flush_digests(_next_8am()) == {}
        assert _digests(shop.lead) == []
        assert _digests(shop.reviewer) == []
        assert not TransactionalEmailLog.objects.filter(trigger_kind="staff_digest").exists()

    def it_marks_every_reviewers_bell_read_when_the_lead_decides(shop: _Woodshop):
        gate = shop.submit("Board One")
        gate.decide(ClassApproval.Decision.CHANGES_REQUESTED, user=shop.lead.user, notes="Add a photo.")
        assert _bell(shop.lead, gate).is_unread is False
        assert _bell(shop.reviewer, gate).is_unread is False

    def it_drops_a_line_whose_gate_was_deleted(shop: _Woodshop):
        gate = shop.submit("Board One")
        gate.delete()
        assert flush_digests(_next_8am()) == {}
        assert not EventDelivery.objects.filter(channel=Channel.DIGEST.value).exists()


def describe_once_a_day():
    def it_sends_once_when_the_tick_runs_twice_in_the_same_window(shop: _Woodshop):
        shop.submit("Board One")
        eight = _next_8am()
        flush_digests(eight)
        flush_digests(eight)
        flush_digests(eight + timedelta(minutes=15))
        assert len(_digests(shop.lead)) == 1

    def it_sends_at_8am_pacific_on_both_sides_of_the_daylight_saving_change(shop: _Woodshop):
        # 2026-11-01 is the fall-back date: 8:00 is 15:00 UTC the day before, 16:00 UTC on it.
        shop.submit("Board One")
        _queued_at(datetime(2026, 10, 30, 20, 0, tzinfo=UTC))
        assert flush_digests(datetime(2026, 10, 31, 14, 45, tzinfo=UTC)) == {}
        assert set(flush_digests(datetime(2026, 10, 31, 15, 0, tzinfo=UTC))) == {
            f"user:{shop.lead.user.pk}",
            f"user:{shop.reviewer.user.pk}",
        }
        shop.submit("Board Two")
        EventDelivery.objects.filter(channel=Channel.DIGEST.value, status=EventDelivery.Status.PENDING).update(
            created_at=datetime(2026, 10, 31, 20, 0, tzinfo=UTC)
        )
        assert flush_digests(datetime(2026, 11, 1, 15, 0, tzinfo=UTC)) == {}
        assert len(flush_digests(datetime(2026, 11, 1, 16, 0, tzinfo=UTC))) == 2
        assert [m.subject for m in _digests(shop.lead)] == [
            "1 thing is waiting on you at Past Lives",
            "1 thing is waiting on you at Past Lives",
        ]

    def it_never_sends_a_second_digest_on_the_same_pacific_date(shop: _Woodshop):
        shop.submit("Board One")
        eight = _next_8am()
        flush_digests(eight)
        shop.submit("Board Two")
        _queued_at(eight - timedelta(hours=1))
        EventDelivery.objects.filter(channel=Channel.DIGEST.value).update(status=EventDelivery.Status.PENDING)
        flush_digests(eight + timedelta(hours=2))
        assert len(_digests(shop.lead)) == 1


def describe_the_email_switch():
    def it_keeps_the_request_out_of_their_digest_but_rings_their_bell(shop: _Woodshop):
        _email_off(shop.lead)
        gate = shop.submit("Board One")
        flush_digests(_next_8am())
        assert _digests(shop.lead) == []
        assert _bell(shop.lead, gate).is_unread
        assert len(_digests(shop.reviewer)) == 1

    def it_drops_a_queued_line_when_the_switch_goes_off_before_8am(shop: _Woodshop):
        shop.submit("Board One")
        _email_off(shop.lead)
        flush_digests(_next_8am())
        assert _digests(shop.lead) == []
        assert DigestAdapter.pending_for(shop.lead.user) == []


def describe_remind_lead():
    def _remind(shop: _Woodshop, gate: ClassApproval) -> str:
        client = Client()
        client.force_login(shop.instructor.user)
        response = client.post(reverse("classes:admin_class_remind_lead", kwargs={"pk": gate.class_offering_id}))
        assert response.status_code == 204
        return str(response["HX-Trigger"])

    def it_sends_no_email_and_names_the_8am_email_in_the_confirmation(shop: _Woodshop):
        gate = shop.submit("Board One")
        mail.outbox = []
        toast = _remind(shop, gate)
        assert mail.outbox == []
        assert "Reminder sent to Chris Ellwanger and Rae Okafor." in toast
        assert "They will see it in their 8 AM email." in toast

    def it_shows_reminded_by_on_the_gates_line_in_the_next_digest(shop: _Woodshop):
        gate = shop.submit("Board One")
        _remind(shop, gate)
        flush_digests(_next_8am())
        (digest,) = _digests(shop.lead)
        assert digest.subject == "1 thing is waiting on you at Past Lives"
        assert "Reminded by Alex Eason" in digest.body

    def it_names_the_instructors_when_the_account_has_no_member():
        from classes.emails import send_guild_lead_review_reminder

        shop = _Woodshop()
        gate = shop.submit("Board One")
        nobody = UserFactory(username="root@example.com", email="root@example.com")
        Member.objects.filter(user=nobody).delete()
        nobody.refresh_from_db()
        send_guild_lead_review_reminder(gate, reminded_by=nobody)
        lines = digest_context(DigestAdapter.pending_for(shop.lead.user))["sections"]
        assert lines[0]["lines"][0]["body"] == "Reminded by Alex Eason"  # type: ignore[index]


def describe_admin_sign_off_request():
    def it_waits_for_the_digest_too(shop: _Woodshop):
        from membership.models import AdminCapability

        signer = _person("lee", "Lee", "Admin")
        signer.admin_capabilities.create(capability=AdminCapability.Capability.CLASS_APPROVER)
        gate = shop.submit("Board One")
        gate.decide(ClassApproval.Decision.APPROVED, user=shop.lead.user)
        assert [m for m in mail.outbox if m.to == ["lee@example.com"]] == []
        flush_digests(_next_8am())
        (digest,) = _digests(signer)
        assert "Admin sign-off needed: Board One" in digest.body


def describe_clear_decided_review_bells():
    def _run() -> str:
        out = StringIO()
        call_command("clear_decided_review_bells", stdout=out)
        return out.getvalue()

    def it_marks_read_the_unread_bells_whose_gate_is_decided_and_reports_the_count(shop: _Woodshop):
        decided = shop.submit("Board One")
        still_open = shop.submit("Board Two")
        # The pre-#894 shape: a decision recorded without clearing anyone's bell.
        ClassApproval.objects.filter(pk=decided.pk).update(decision=ClassApproval.Decision.APPROVED)
        Notification.objects.create(user=shop.lead.user, trigger="x", title="Other", body="", url="/somewhere/")
        assert "Marked 2 review bell entries read." in _run()
        assert _bell(shop.lead, decided).is_unread is False
        assert _bell(shop.reviewer, decided).is_unread is False
        assert _bell(shop.lead, still_open).is_unread
        assert Notification.objects.get(url="/somewhere/").is_unread

    def it_reports_one_entry_in_the_singular(shop: _Woodshop):
        decided = shop.submit("Board One")
        ClassApproval.objects.filter(pk=decided.pk).update(decision=ClassApproval.Decision.APPROVED)
        _bell(shop.reviewer, decided).mark_read()
        assert "Marked 1 review bell entry read." in _run()

    def it_ignores_a_review_like_link_that_is_not_a_review_page(shop: _Woodshop):
        Notification.objects.create(user=shop.lead.user, trigger="x", title="T", body="", url="/guilds/review/")
        assert "Marked 0 review bell entries read." in _run()


def describe_send_staff_digests_command():
    def it_reports_how_many_digests_it_sent(shop: _Woodshop):
        shop.submit("Board One")
        out = StringIO()
        with patch("core.events.channels.timezone.now", return_value=_next_8am()):
            call_command("send_staff_digests", stdout=out)
        assert "Sent 2 staff digest(s)." in out.getvalue()


def describe_digest_adapter():
    def it_queues_one_line_per_request_so_a_second_never_overwrites_the_first():
        user = UserFactory(username="q@example.com", email="q@example.com")
        adapter = digest_adapter()
        adapter.enqueue(user, Message(title="A", body="a", url="/a/"), event_key="class_review_requested", period="1")
        adapter.enqueue(user, Message(title="B", body="b", url="/b/"), event_key="class_review_requested", period="2")
        assert [row.title for row in DigestAdapter.pending_for(user)] == ["A", "B"]

    def it_queues_nothing_for_a_user_with_no_address():
        user = UserFactory(username="noaddr", email="")
        message = Message(title="A", body="a", url="/a/")
        assert digest_adapter().enqueue(user, message, event_key="class_review_requested", period="1") is False
        assert not EventDelivery.objects.exists()

    def it_keys_a_direct_delivery_on_its_link():
        user = UserFactory(username="d@example.com", email="d@example.com")
        digest_adapter().deliver(user, Message(title="A", body="a", url="/a/", trigger_kind="class_review_requested"))
        assert DigestAdapter.pending_for(user)[0].period == "/a/"

    def it_drops_lines_queued_for_a_user_who_is_gone(shop: _Woodshop):
        shop.submit("Board One")
        lead_pk = shop.lead.user.pk
        shop.lead.user.delete()
        sent = flush_digests(_next_8am())
        assert f"user:{lead_pk}" not in sent
        assert not EventDelivery.objects.filter(target_ref=f"user:{lead_pk}", channel="digest").exists()

    def it_keeps_the_lines_when_the_address_is_gone_by_8am(shop: _Woodshop):
        shop.submit("Board One")
        type(shop.lead.user).objects.filter(pk=shop.lead.user.pk).update(email="")
        flush_digests(_next_8am())
        assert len(DigestAdapter.pending_for(shop.lead.user)) == 1

    def it_retries_on_the_next_tick_when_the_send_fails(shop: _Woodshop):
        shop.submit("Board One")
        eight = _next_8am()
        failed = TransactionalEmailLog(status=TransactionalEmailLog.Status.FAILED)
        with patch("core.events.channels.send_email", return_value=failed):
            assert flush_digests(eight) == {}
        assert len(DigestAdapter.pending_for(shop.lead.user)) == 1
        flush_digests(eight + timedelta(minutes=15))
        assert len(_digests(shop.lead)) == 1

    def it_treats_a_lost_race_for_the_days_slot_as_already_sent(shop: _Woodshop):
        shop.submit("Board One")
        with patch.object(EventDelivery.objects, "get_or_create", side_effect=IntegrityError):
            assert flush_digests(_next_8am()) == {}
        assert _digests(shop.lead) == []


def describe_a_queued_line():
    """What a part 2 queue view reads off a pending row, without re-rendering it."""

    def it_carries_the_recipient_event_request_queued_time_and_no_reminder(shop: _Woodshop):
        gate = shop.submit("Board One")
        (row,) = DigestAdapter.pending_for(shop.lead.user)
        assert row.recipient_user_id == shop.lead.user.pk
        assert row.event_key == "class_review_requested"
        assert row.request_ref == f"approval:{gate.pk}"
        assert row.created_at is not None
        assert row.reminded_by == ""

    def it_carries_who_pressed_remind_lead_under_the_same_request(shop: _Woodshop):
        from classes.emails import send_guild_lead_review_reminder

        gate = shop.submit("Board One")
        send_guild_lead_review_reminder(gate, reminded_by=shop.instructor.user)
        rows = DigestAdapter.pending_for(shop.lead.user)
        assert [row.reminded_by for row in rows] == ["", "Alex Eason"]
        assert {row.request_ref for row in rows} == {f"approval:{gate.pk}"}

    def it_refuses_a_recipient_for_a_row_that_is_not_per_person():
        with pytest.raises(ValueError):
            _ = EventDelivery(target_ref="broadcast").recipient_user_id


def describe_review_token_from_url():
    def it_reads_the_token_from_a_path_or_an_absolute_link():
        assert ClassApproval.review_token_from_url("/classes/review/abc/") == "abc"
        assert ClassApproval.review_token_from_url("https://classes.pastlives.space/classes/review/abc/") == "abc"

    def it_answers_none_for_any_other_link():
        assert ClassApproval.review_token_from_url("/classes/review/abc/preview/") is None
        assert ClassApproval.review_token_from_url("/no/such/page/") is None
