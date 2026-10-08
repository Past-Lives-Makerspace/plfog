"""BDD specs for FeedbackRequest: saving a Feedback page send and telling the sender (#693)."""

from __future__ import annotations

from collections.abc import Iterator
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from django.contrib.auth.models import User

from core.events.registry import FEEDBACK_REQUEST_UPDATED, Channel
from core.models import (
    EventDelivery,
    FeedbackRequest,
    FeedbackRequestError,
    FeedbackRequestPhoto,
    Notification,
    NotificationPreference,
    PushSubscription,
)
from tests.core.factories import FeedbackRequestFactory
from tests.membership.factories import UserFactory

pytestmark = pytest.mark.django_db

Status = FeedbackRequest.Status
Category = FeedbackRequest.Category


def _photo(name: str = "shot.png") -> SimpleUploadedFile:
    buf = BytesIO()
    Image.new("RGB", (1, 1)).save(buf, format="PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


@pytest.fixture
def admin() -> User:
    return User.objects.create_superuser(username="inboxadmin", password="pass", email="inboxadmin@example.com")


@pytest.fixture
def pushed() -> Iterator[MagicMock]:
    with patch("core.events.channels.send_web_push") as send_web_push:
        yield send_web_push


def _update(request: FeedbackRequest, admin: User, *, status: str, note: str = "", github: str = "") -> bool:
    return request.apply_admin_update(status=status, staff_note=note, github_issue_url=github, actor=admin)


def _bells(request: FeedbackRequest) -> list[Notification]:
    return list(Notification.objects.filter(user=request.user, trigger=FEEDBACK_REQUEST_UPDATED))


def describe_submit():
    def it_saves_the_request_as_received_with_its_photos():
        user = UserFactory()
        first, second = _photo("first.png"), _photo("second.png")

        request = FeedbackRequest.submit(
            user=user, category=Category.BUG, subject="Broken", message="It broke", photos=[first, second]
        )

        request.refresh_from_db()
        assert (request.user, request.category, request.subject, request.message) == (
            user,
            Category.BUG,
            "Broken",
            "It broke",
        )
        assert request.status == Status.RECEIVED
        assert request.live_notified_at is None
        assert request.photos.count() == 2

    def it_leaves_each_upload_rewound_for_the_email_that_attaches_it():
        photo = _photo()

        FeedbackRequest.submit(user=UserFactory(), category=Category.BUG, subject="s", message="m", photos=[photo])

        assert photo.tell() == 0
        assert photo.read()

    def it_saves_a_request_with_no_photos():
        request = FeedbackRequest.submit(
            user=UserFactory(), category=Category.FEEDBACK, subject="s", message="m", photos=[]
        )

        assert request.photos.count() == 0


def describe_querysets():
    def it_lists_only_the_senders_requests_newest_first():
        user = UserFactory()
        older = FeedbackRequestFactory(user=user)
        newer = FeedbackRequestFactory(user=user)
        FeedbackRequestFactory()
        FeedbackRequest.objects.filter(pk=older.pk).update(created_at=timezone.now() - timezone.timedelta(days=2))

        assert list(FeedbackRequest.objects.sent_by(user)) == [newer, older]

    def it_counts_only_received_requests_as_new():
        received = FeedbackRequestFactory()
        FeedbackRequestFactory(status=Status.PLANNED)

        assert list(FeedbackRequest.objects.received()) == [received]


def describe_labels():
    @pytest.mark.parametrize(
        ("category", "status", "label", "phrase", "tone"),
        [
            (Category.FEATURE, Status.RECEIVED, "Received", "received", "neutral"),
            (Category.FEATURE, Status.PLANNED, "Planned", "planned", "primary"),
            (Category.FEATURE, Status.BUILDING, "Building", "being built", "primary"),
            (Category.FEATURE, Status.LIVE, "Live", "live", "ok"),
            (Category.BUG, Status.LIVE, "Fixed", "fixed", "ok"),
            (Category.FEEDBACK, Status.LIVE, "Live", "live", "ok"),
            (Category.BUG, Status.NOT_PLANNED, "Not planned", "not planned", "neutral"),
        ],
    )
    def it_words_each_status_for_the_member(category: str, status: str, label: str, phrase: str, tone: str):
        request = FeedbackRequestFactory.build(category=category, status=status)

        assert (request.status_label, request.status_phrase, request.status_tone) == (label, phrase, tone)

    def it_names_itself_by_number_subject_and_status():
        request = FeedbackRequestFactory(subject="Dark mode", status=Status.PLANNED)

        assert str(request) == f"#{request.pk} Dark mode (Planned)"

    def it_names_a_photo_by_its_request():
        request = FeedbackRequest.submit(
            user=UserFactory(), category=Category.BUG, subject="s", message="m", photos=[_photo()]
        )
        photo = FeedbackRequestPhoto.objects.get()

        assert str(photo) == f"Photo {photo.pk} on request #{request.pk}"

    def it_calls_the_sender_by_name_else_email():
        named = FeedbackRequestFactory(user=UserFactory(first_name="Robin", last_name="Vale"))
        unnamed = FeedbackRequestFactory(user=UserFactory(email="quiet@example.com"))

        assert named.sender_label == "Robin Vale"
        assert unnamed.sender_label == "quiet@example.com"

    def it_links_the_sender_to_the_open_row_and_the_admin_to_the_inbox(settings):
        settings.MEMBER_BASE_URL = "https://members.example"
        request = FeedbackRequestFactory()

        assert request.anchor == f"request-{request.pk}"
        assert request.member_url == f"https://members.example/feedback/#request-{request.pk}"
        assert request.inbox_url == f"https://members.example/manage/feedback/{request.pk}/"


def describe_apply_admin_update():
    @pytest.mark.parametrize("status", [Status.PLANNED, Status.BUILDING, Status.LIVE])
    def it_tells_the_sender_once_on_each_move_forward(admin: User, status: str):
        request = FeedbackRequestFactory()

        assert _update(request, admin, status=status) is True

        [bell] = _bells(request)
        assert bell.title == f"Your request is {request.status_phrase}"
        assert bell.body == request.subject
        [message] = mail.outbox
        assert message.to == [request.user.email]
        assert message.subject == f"Your request: {request.subject} is {request.status_phrase}"

    def it_tells_the_sender_not_planned_with_the_reason(admin: User):
        request = FeedbackRequestFactory()

        assert _update(request, admin, status=Status.NOT_PLANNED, note="Out of scope for this year.") is True

        [message] = mail.outbox
        assert "is not planned" in message.subject
        assert "Out of scope for this year." in message.body
        html = message.alternatives[0][0]
        assert "Out of scope for this year." in html
        assert request.member_url in html

    def it_refuses_not_planned_without_a_reason(admin: User):
        request = FeedbackRequestFactory()

        with pytest.raises(FeedbackRequestError, match="reason"):
            _update(request, admin, status=Status.NOT_PLANNED, note="   ")

        request.refresh_from_db()
        assert request.status == Status.RECEIVED
        assert _bells(request) == []

    def it_does_not_notify_twice_for_the_same_status(admin: User):
        request = FeedbackRequestFactory()
        _update(request, admin, status=Status.PLANNED)

        assert _update(request, admin, status=Status.PLANNED) is False

        assert len(_bells(request)) == 1
        assert len(mail.outbox) == 1

    def it_notifies_again_on_a_later_return_to_a_status(admin: User):
        request = FeedbackRequestFactory()
        _update(request, admin, status=Status.PLANNED)
        _update(request, admin, status=Status.BUILDING)

        _update(request, admin, status=Status.PLANNED)

        assert len(_bells(request)) == 3
        assert EventDelivery.objects.filter(event_key=FEEDBACK_REQUEST_UPDATED, channel=Channel.EMAIL).count() == 3

    def it_stays_quiet_on_a_move_back_to_received(admin: User):
        request = FeedbackRequestFactory(status=Status.PLANNED)

        assert _update(request, admin, status=Status.RECEIVED) is False

        assert _bells(request) == []
        request.refresh_from_db()
        assert request.status == Status.RECEIVED

    def it_tells_the_sender_about_a_new_note_on_general_feedback(admin: User):
        request = FeedbackRequestFactory(category=Category.FEEDBACK)

        assert _update(request, admin, status=Status.RECEIVED, note="Thanks, passed it on.") is True

        [message] = mail.outbox
        assert "is received" in message.subject
        assert "Thanks, passed it on." in message.body

    def it_sends_one_notice_when_the_status_and_note_change_together(admin: User):
        request = FeedbackRequestFactory()

        _update(request, admin, status=Status.BUILDING, note="Started this week.")

        assert len(_bells(request)) == 1
        assert len(mail.outbox) == 1

    def it_stays_quiet_when_the_note_is_cleared_or_unchanged(admin: User):
        request = FeedbackRequestFactory(status=Status.PLANNED, staff_note="Soon.")

        assert _update(request, admin, status=Status.PLANNED, note="Soon.") is False
        assert _update(request, admin, status=Status.PLANNED, note="") is False

        assert _bells(request) == []

    def it_leaves_no_missing_marker_when_there_is_no_note(admin: User):
        request = FeedbackRequestFactory()

        _update(request, admin, status=Status.PLANNED)

        [message] = mail.outbox
        assert "missing" not in message.body
        assert "A note from Past Lives" not in message.body

    def it_escapes_the_note_in_the_html_email(admin: User):
        request = FeedbackRequestFactory()

        _update(request, admin, status=Status.PLANNED, note="<script>x</script>")

        html = mail.outbox[0].alternatives[0][0]
        assert "<script>x</script>" not in html
        assert "&lt;script&gt;x&lt;/script&gt;" in html

    def it_saves_the_github_link_without_telling_the_sender(admin: User):
        request = FeedbackRequestFactory()

        assert _update(request, admin, status=Status.RECEIVED, github="https://github.com/o/r/issues/1") is False

        request.refresh_from_db()
        assert request.github_issue_url == "https://github.com/o/r/issues/1"
        assert _bells(request) == []

    def it_stamps_the_status_date_only_when_the_status_changes(admin: User):
        request = FeedbackRequestFactory()
        first = request.status_changed_at
        _update(request, admin, status=Status.PLANNED)
        moved = request.status_changed_at

        _update(request, admin, status=Status.PLANNED, note="A note.")

        assert moved > first
        assert request.status_changed_at == moved

    def it_stamps_live_notified_when_the_sender_hears_it_is_live(admin: User):
        request = FeedbackRequestFactory()

        _update(request, admin, status=Status.LIVE)

        request.refresh_from_db()
        assert request.live_notified_at is not None

    def it_leaves_live_notified_unset_for_other_moves(admin: User):
        request = FeedbackRequestFactory()

        _update(request, admin, status=Status.BUILDING)

        assert request.live_notified_at is None

    def it_respects_the_senders_email_choice_and_still_rings_the_bell(admin: User):
        request = FeedbackRequestFactory()
        NotificationPreference.objects.create(
            user=request.user, event_key=FEEDBACK_REQUEST_UPDATED, channel=Channel.EMAIL.value, enabled=False
        )

        _update(request, admin, status=Status.PLANNED)

        assert len(_bells(request)) == 1
        assert mail.outbox == []

    def it_pushes_only_when_the_sender_turned_push_on(admin: User, pushed: MagicMock):
        quiet = FeedbackRequestFactory()
        PushSubscription.objects.create(user=quiet.user, endpoint="https://push/quiet", p256dh="k", auth="a")
        loud = FeedbackRequestFactory()
        PushSubscription.objects.create(user=loud.user, endpoint="https://push/loud", p256dh="k", auth="a")
        NotificationPreference.objects.create(
            user=loud.user, event_key=FEEDBACK_REQUEST_UPDATED, channel=Channel.PUSH.value, enabled=True
        )

        _update(quiet, admin, status=Status.PLANNED)
        assert not pushed.called
        _update(loud, admin, status=Status.PLANNED)

        pushed.assert_called_once()
        assert pushed.call_args.kwargs["body"] == loud.subject


def describe_mark_live():
    def it_moves_the_request_live_keeping_its_note_and_link(admin: User):
        request = FeedbackRequestFactory(
            status=Status.BUILDING, staff_note="Nearly there.", github_issue_url="https://github.com/o/r/issues/2"
        )

        assert request.mark_live(actor=admin) is True

        request.refresh_from_db()
        assert request.status == Status.LIVE
        assert (request.staff_note, request.github_issue_url) == ("Nearly there.", "https://github.com/o/r/issues/2")
        assert "is live" in mail.outbox[0].subject

    def it_says_fixed_for_a_bug_report(admin: User):
        request = FeedbackRequestFactory(category=Category.BUG)

        request.mark_live(actor=admin)

        assert mail.outbox[0].subject == f"Your request: {request.subject} is fixed"

    def it_does_nothing_for_a_request_already_live(admin: User):
        request = FeedbackRequestFactory()
        request.mark_live(actor=admin)

        assert request.mark_live(actor=admin) is False

        assert len(_bells(request)) == 1
