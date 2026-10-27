"""BDD specs for the admin Feedback inbox and a request's admin page (#693)."""

from __future__ import annotations

from io import BytesIO

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from core.events.registry import FEEDBACK_COMMENT_ADDED, FEEDBACK_REQUEST_UPDATED
from core.models import FeedbackComment, FeedbackRequest, Notification
from membership.models import Member
from tests.core.factories import FeedbackRequestFactory

pytestmark = pytest.mark.django_db

Status = FeedbackRequest.Status
INBOX = reverse("hub_admin_feedback_inbox")


def _photo(name: str = "shot.png") -> SimpleUploadedFile:
    buf = BytesIO()
    Image.new("RGB", (1, 1)).save(buf, format="PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def _admin(client: Client, username: str = "inboxer") -> User:
    user = User.objects.create_superuser(username=username, password="pass", email=f"{username}@example.com")
    client.login(username=username, password="pass")
    return user


def _member(client: Client, username: str = "plainmember") -> User:
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    client.login(username=username, password="pass")
    return user


def _inbox_queries(client: Client) -> int:
    with CaptureQueriesContext(connection) as captured:
        client.get(INBOX)
    return len(captured)


def _page(pk: int) -> str:
    return reverse("hub_admin_feedback_request", args=[pk])


def _comment_url(pk: int) -> str:
    return reverse("hub_admin_feedback_comment", args=[pk])


def _post(client: Client, request: FeedbackRequest, *, status: str, note: str = "", github: str = "", query: str = ""):
    url = _page(request.pk) + (f"?{query}" if query else "")
    return client.post(url, {"status": status, "comment": note, "github_issue_url": github})


def describe_permissions():
    @pytest.mark.parametrize("view", ["inbox", "page", "mark_live", "comment", "not_a_bug", "close"])
    def it_refuses_a_non_admin(client: Client, view: str):
        _member(client)
        request = FeedbackRequestFactory()
        url = {
            "inbox": INBOX,
            "page": _page(request.pk),
            "mark_live": reverse("hub_admin_feedback_mark_live", args=[request.pk]),
            "comment": _comment_url(request.pk),
            "not_a_bug": reverse("hub_admin_feedback_not_a_bug", args=[request.pk]),
            "close": reverse("hub_admin_feedback_close", args=[request.pk]),
        }[view]

        posts = {"mark_live", "comment", "not_a_bug", "close"}
        response = client.post(url, {"body": "Zzsneaky", "comment": "Zzsneaky"}) if view in posts else client.get(url)

        assert response.status_code == 403
        request.refresh_from_db()
        assert request.status == Status.RECEIVED
        assert request.comments.count() == 0
        assert request.staff_seen_at is None

    def it_sends_an_anonymous_visitor_to_log_in(client: Client):
        response = client.get(INBOX)

        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def it_only_marks_live_on_a_post(client: Client):
        _admin(client)
        request = FeedbackRequestFactory()

        response = client.get(reverse("hub_admin_feedback_mark_live", args=[request.pk]))

        assert response.status_code == 405

    def it_answers_404_for_an_unknown_request(client: Client):
        _admin(client)

        assert client.get(_page(999999)).status_code == 404


def describe_admin_tools_card():
    def it_links_the_inbox_with_the_count_of_new_requests(client: Client):
        _admin(client)
        FeedbackRequestFactory.create_batch(2)
        FeedbackRequestFactory(status=Status.PLANNED)

        body = client.get(reverse("hub_admin_tools")).content.decode()

        assert f'href="{INBOX}" data-tool-feedback' in body
        assert "data-feedback-received-count>2 new<" in body

    def it_shows_no_count_when_nothing_is_new(client: Client):
        _admin(client)

        body = client.get(reverse("hub_admin_tools")).content.decode()

        assert "data-tool-feedback" in body
        assert "data-feedback-received-count" not in body

    def it_hides_the_card_from_a_non_admin(client: Client):
        user = _member(client, "lead")
        user.member.fog_role = Member.FogRole.GUILD_OFFICER  # type: ignore[attr-defined]
        user.member.save()  # type: ignore[attr-defined]

        body = client.get(reverse("hub_admin_tools")).content.decode()

        assert "data-tool-feedback" not in body


def describe_inbox():
    def it_lists_every_request_newest_first_with_its_sender(client: Client):
        _admin(client)
        older = FeedbackRequestFactory(user__first_name="Ada", user__last_name="Older")
        newer = FeedbackRequestFactory()
        FeedbackRequest.objects.filter(pk=older.pk).update(created_at=older.created_at.replace(year=2025))

        response = client.get(INBOX)

        assert list(response.context["page"].object_list) == [newer, older]
        assert "Ada Older" in response.content.decode()

    def it_says_so_when_there_is_no_feedback(client: Client):
        _admin(client)

        body = client.get(INBOX).content.decode()

        assert "data-feedback-inbox-empty>Nothing open right now. Pick All statuses to see closed requests.<" in body

    def it_filters_by_category_and_status(client: Client):
        _admin(client)
        match = FeedbackRequestFactory(category="bug", status=Status.PLANNED)
        FeedbackRequestFactory(category="bug", status=Status.RECEIVED)
        FeedbackRequestFactory(category="feature", status=Status.PLANNED)

        response = client.get(INBOX, {"category": "bug", "status": "planned"})

        assert list(response.context["page"].object_list) == [match]

    def it_says_so_when_nothing_matches_the_filters(client: Client):
        _admin(client)
        FeedbackRequestFactory(category="bug")

        body = client.get(INBOX, {"category": "feature"}).content.decode()

        assert "Nothing matches these filters." in body

    def it_ignores_an_unknown_filter_value(client: Client):
        _admin(client)
        FeedbackRequestFactory()

        response = client.get(INBOX, {"category": "nope", "status": "nope"})

        assert len(response.context["page"].object_list) == 1

    def it_carries_the_filters_to_each_request_page(client: Client):
        _admin(client)
        request = FeedbackRequestFactory(category="bug")

        body = client.get(INBOX, {"category": "bug"}).content.decode()

        assert f'href="{_page(request.pk)}?category=bug"' in body

    def it_loads_every_sender_without_a_query_per_row(client: Client):
        _admin(client)
        FeedbackRequestFactory.create_batch(2)
        client.get(INBOX)  # warm the per-process caches the first request fills
        few = _inbox_queries(client)
        FeedbackRequestFactory.create_batch(5)

        assert _inbox_queries(client) == few


def describe_request_page():
    def it_shows_the_message_sender_and_github_link(client: Client):
        _admin(client)
        request = FeedbackRequestFactory(
            message="Zzthe full message", github_issue_url="https://github.com/o/r/issues/42"
        )

        body = client.get(_page(request.pk)).content.decode()

        assert "Zzthe full message" in body
        assert request.user.email in body
        assert "https://github.com/o/r/issues/42" in body

    def it_links_a_member_sender_to_their_member_page(client: Client):
        _admin(client)
        sender = User.objects.create_user(username="withmember", password="pass", email="withmember@example.com")
        request = FeedbackRequestFactory(user=sender)

        body = client.get(_page(request.pk)).content.decode()

        assert reverse("hub_admin_member_edit", args=[sender.member.pk]) in body  # type: ignore[attr-defined]

    def it_marks_a_sender_with_no_membership(client: Client):
        _admin(client)
        sender = User.objects.create_user(username="loose", password="pass", email="loose@example.com")
        Member.objects.filter(user=sender).delete()
        request = FeedbackRequestFactory(user=sender)

        body = client.get(_page(request.pk)).content.decode()

        assert "No membership" in body

    def it_keeps_the_inbox_filters_on_the_back_link(client: Client):
        _admin(client)
        request = FeedbackRequestFactory()

        body = client.get(_page(request.pk), {"status": "received"}).content.decode()

        assert f'href="{INBOX}?status=received"' in body

    def it_saves_and_says_who_was_told(client: Client):
        admin = _admin(client)
        request = FeedbackRequestFactory(user__first_name="Robin", user__last_name="Vale")

        response = _post(
            client,
            request,
            status="planned",
            note="Next up.",
            github="https://github.com/o/r/issues/7",
            query="category=feature",
        )

        assert response.status_code == 302
        assert response["Location"] == f"{_page(request.pk)}?category=feature"
        request.refresh_from_db()
        assert (request.status, request.github_issue_url) == ("planned", "https://github.com/o/r/issues/7")
        assert [comment.body for comment in request.comments.all()] == ["Next up."]
        assert Notification.objects.filter(user=request.user, trigger=FEEDBACK_REQUEST_UPDATED).count() == 1
        follow = client.get(response["Location"])
        assert [str(m) for m in follow.context["messages"]] == ["Saved. Robin Vale was notified."]
        assert admin != request.user

    def it_says_saved_when_nobody_needed_telling(client: Client):
        _admin(client)
        request = FeedbackRequestFactory()

        response = _post(client, request, status="received", github="https://github.com/o/r/issues/8")

        follow = client.get(response["Location"])
        assert [str(m) for m in follow.context["messages"]] == ["Saved."]
        assert mail.outbox == []

    def it_refuses_not_planned_without_a_reason(client: Client):
        _admin(client)
        request = FeedbackRequestFactory()

        response = _post(client, request, status="not_planned", note="")

        assert response.status_code == 200
        assert response.context["form"].errors["comment"] == [FeedbackRequest.NOT_PLANNED_NEEDS_REASON]
        assert FeedbackRequest.NOT_PLANNED_NEEDS_REASON in response.content.decode()
        request.refresh_from_db()
        assert request.status == Status.RECEIVED
        assert response.context["feedback_request"].status == Status.RECEIVED
        assert not Notification.objects.exists()

    def it_refuses_an_unknown_status(client: Client):
        _admin(client)
        request = FeedbackRequestFactory()

        response = _post(client, request, status="shipped")

        assert response.status_code == 200
        assert "status" in response.context["form"].errors

    def it_does_not_notify_when_the_same_status_is_saved_twice(client: Client):
        _admin(client)
        request = FeedbackRequestFactory()

        _post(client, request, status="building")
        _post(client, request, status="building")

        assert Notification.objects.filter(trigger=FEEDBACK_REQUEST_UPDATED).count() == 1
        assert len(mail.outbox) == 1


def describe_mark_live():
    def it_marks_the_request_live_and_tells_the_sender(client: Client):
        _admin(client)
        request = FeedbackRequestFactory(status=Status.BUILDING)

        response = client.post(reverse("hub_admin_feedback_mark_live", args=[request.pk]) + "?status=building")

        assert response["Location"] == f"{_page(request.pk)}?status=building"
        request.refresh_from_db()
        assert request.status == Status.LIVE
        assert request.live_notified_at is not None
        assert "is live" in mail.outbox[0].subject

    def it_offers_mark_fixed_for_a_bug_and_hides_once_live(client: Client):
        _admin(client)
        bug = FeedbackRequestFactory(category="bug")
        live = FeedbackRequestFactory(status=Status.LIVE)

        assert "Mark Fixed" in client.get(_page(bug.pk)).content.decode()
        assert "data-feedback-mark-live" not in client.get(_page(live.pk)).content.decode()


def describe_general_feedback_page():
    def it_offers_only_received_and_closed_as_the_status(client: Client):
        _admin(client)
        request = FeedbackRequestFactory(category="feedback")

        response = client.get(_page(request.pk))

        assert response.context["form"].fields["status"].choices == [("received", "Received"), ("closed", "Closed")]
        assert "General feedback is Received or Closed." in response.content.decode()

    def it_hides_mark_live(client: Client):
        _admin(client)
        request = FeedbackRequestFactory(category="feedback")

        assert "data-feedback-mark-live" not in client.get(_page(request.pk)).content.decode()

    def it_refuses_a_posted_status_change(client: Client):
        _admin(client)
        request = FeedbackRequestFactory(category="feedback")

        response = _post(client, request, status="planned", note="Hi")

        assert response.status_code == 200
        assert "status" in response.context["form"].errors
        request.refresh_from_db()
        assert request.status == Status.RECEIVED

    def it_refuses_mark_live_with_a_message(client: Client):
        _admin(client)
        request = FeedbackRequestFactory(category="feedback")

        response = client.post(reverse("hub_admin_feedback_mark_live", args=[request.pk]))

        follow = client.get(response["Location"])
        assert [str(m) for m in follow.context["messages"]] == [FeedbackRequest.FEEDBACK_STAYS_RECEIVED]
        request.refresh_from_db()
        assert request.status == Status.RECEIVED
        assert mail.outbox == []

    def it_saves_and_sends_a_comment_only_reply(client: Client):
        _admin(client)
        request = FeedbackRequestFactory(category="feedback")

        _post(client, request, status="received", note="Thanks for this.")

        assert "Thanks for this." in mail.outbox[0].body


def describe_conversation():
    def it_shows_the_original_message_first_then_every_comment(client: Client):
        admin = _admin(client)
        request = FeedbackRequestFactory(message="Zzthe original", user__first_name="Robin", user__last_name="Vale")
        request.add_comment(author=request.user, body="Zzfrom the sender", photos=[])
        FeedbackComment.objects.create(request=request, author=None, body="Zzcarried over note")
        request.add_comment(author=admin, body="Zzfrom an admin", photos=[])

        body = client.get(_page(request.pk)).content.decode()

        assert body.index("Zzthe original") < body.index("Zzfrom the sender") < body.index("Zzcarried over note")
        assert body.index("Zzcarried over note") < body.index("Zzfrom an admin")
        assert "data-thread-opening" in body
        assert body.count('data-from="staff"') == 2
        assert 'data-from="member"' in body
        assert "<strong>Past Lives</strong>" in body

    def it_names_who_hears_the_comment_on_its_button(client: Client):
        _admin(client)
        request = FeedbackRequestFactory(user__first_name="Robin", user__last_name="Vale")

        body = client.get(_page(request.pk)).content.decode()

        assert "data-thread-send data-pl-busy>Send to Robin Vale<" in body
        assert "data-thread-reply data-pl-busy-submit" in body
        assert "data-thread-photos>" in body
        assert 'class="pl-feedback-admin__status"' in body
        assert 'class="pl-feedback-admin__link"' in body
        assert 'enctype="multipart/form-data"' in body

    def it_loads_the_thread_without_a_query_per_comment(client: Client):
        admin = _admin(client)
        request = FeedbackRequestFactory()
        request.add_comment(author=admin, body="One", photos=[])
        client.get(_page(request.pk))
        with CaptureQueriesContext(connection) as few:
            client.get(_page(request.pk))
        for n in range(4):
            request.add_comment(author=request.user if n % 2 else admin, body=f"More {n}", photos=[])

        with CaptureQueriesContext(connection) as many:
            client.get(_page(request.pk))

        assert len(many) == len(few)


def describe_posting_a_comment():
    def it_posts_it_tells_the_sender_and_says_so(client: Client):
        admin = _admin(client)
        request = FeedbackRequestFactory(user__first_name="Robin", user__last_name="Vale")

        response = client.post(
            _comment_url(request.pk) + "?status=received",
            {"body": "Zzwhich phone?", "photos": _photo()},
        )

        assert response["Location"] == f"{_page(request.pk)}?status=received"
        [comment] = request.comments.all()
        assert (comment.author, comment.body, comment.photos.count()) == (admin, "Zzwhich phone?", 1)
        assert Notification.objects.filter(user=request.user, trigger=FEEDBACK_COMMENT_ADDED).count() == 1
        follow = client.get(response["Location"])
        assert [str(m) for m in follow.context["messages"]] == ["Sent. Robin Vale was notified."]

    def it_refuses_an_empty_comment_and_keeps_the_page(client: Client):
        _admin(client)
        request = FeedbackRequestFactory()

        response = client.post(_comment_url(request.pk), {"body": "  "})

        assert response.status_code == 200
        assert "body" in response.context["comment_form"].errors
        assert request.comments.count() == 0
        assert mail.outbox == []

    def it_refuses_too_many_photos(client: Client):
        _admin(client)
        request = FeedbackRequestFactory()

        response = client.post(
            _comment_url(request.pk), {"body": "Hi", "photos": [_photo(f"p{n}.png") for n in range(6)]}
        )

        assert response.status_code == 200
        assert "photos" in response.context["comment_form"].errors
        assert request.comments.count() == 0

    def it_only_posts(client: Client):
        _admin(client)
        request = FeedbackRequestFactory()

        assert client.get(_comment_url(request.pk)).status_code == 405

    def it_answers_404_for_an_unknown_request(client: Client):
        _admin(client)

        assert client.post(_comment_url(999999), {"body": "Hi"}).status_code == 404


def describe_member_replied():
    def it_pills_a_request_the_sender_wrote_on_since_an_admin_opened_it(
        client: Client, django_capture_on_commit_callbacks
    ):
        _admin(client)
        replied = FeedbackRequestFactory()
        with django_capture_on_commit_callbacks(execute=True):
            replied.add_comment(author=replied.user, body="More", photos=[])
        quiet = FeedbackRequestFactory()

        body = client.get(INBOX).content.decode()

        row = body[body.index(f'data-feedback-row="{replied.pk}"') :]
        assert "data-feedback-replied>Member replied<" in row[: row.index("</tr>")]
        quiet_row = body[body.index(f'data-feedback-row="{quiet.pk}"') :]
        assert "data-feedback-replied" not in quiet_row[: quiet_row.index("</tr>")]

    def it_filters_the_inbox_to_member_replied(client: Client, django_capture_on_commit_callbacks):
        _admin(client)
        replied = FeedbackRequestFactory()
        with django_capture_on_commit_callbacks(execute=True):
            replied.add_comment(author=replied.user, body="More", photos=[])
        FeedbackRequestFactory()

        response = client.get(INBOX, {"replied": "1"})

        assert list(response.context["page"].object_list) == [replied]
        assert response.context["is_filtered"] is True
        assert '<option value="1" selected>Member replied</option>' in response.content.decode()

    def it_clears_the_pill_once_an_admin_opens_the_request(client: Client, django_capture_on_commit_callbacks):
        _admin(client)
        request = FeedbackRequestFactory()
        with django_capture_on_commit_callbacks(execute=True):
            request.add_comment(author=request.user, body="More", photos=[])

        client.get(_page(request.pk))

        assert list(client.get(INBOX, {"replied": "1"}).context["page"].object_list) == []


def describe_an_admin_who_sent_the_request():
    def _webmaster(username: str) -> User:
        from membership.models import AdminCapability

        user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
        user.member.admin_capabilities.create(capability=AdminCapability.Capability.WEBMASTER)  # type: ignore[attr-defined]
        return user

    def it_says_only_sent_when_their_comment_reached_nobody(client: Client, django_capture_on_commit_callbacks):
        admin = _admin(client)
        request = FeedbackRequestFactory(user=admin)

        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(_comment_url(request.pk), {"body": "Mine"})

        follow = client.get(response["Location"])
        assert [str(m) for m in follow.context["messages"]] == ["Sent."]
        assert not Notification.objects.exists()

    def it_names_the_webmasters_when_their_comment_reached_them(client: Client, django_capture_on_commit_callbacks):
        admin = _admin(client)
        _webmaster("otherwm")
        request = FeedbackRequestFactory(user=admin)

        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(_comment_url(request.pk), {"body": "Mine"})

        follow = client.get(response["Location"])
        assert [str(m) for m in follow.context["messages"]] == ["Sent. The Webmasters were notified."]

    def it_says_only_saved_for_their_own_status_move(client: Client, django_capture_on_commit_callbacks):
        admin = _admin(client)
        request = FeedbackRequestFactory(user=admin)

        with django_capture_on_commit_callbacks(execute=True):
            response = _post(client, request, status="planned")

        follow = client.get(response["Location"])
        assert [str(m) for m in follow.context["messages"]] == ["Saved."]
        assert not Notification.objects.exists()

    def it_names_the_webmasters_for_a_comment_with_their_status_move(
        client: Client, django_capture_on_commit_callbacks
    ):
        admin = _admin(client)
        _webmaster("otherwm2")
        request = FeedbackRequestFactory(user=admin)

        with django_capture_on_commit_callbacks(execute=True):
            response = _post(client, request, status="planned", note="Mine")

        follow = client.get(response["Location"])
        assert [str(m) for m in follow.context["messages"]] == ["Saved. The Webmasters were notified."]


def describe_resolving():
    def _not_a_bug(pk: int) -> str:
        return reverse("hub_admin_feedback_not_a_bug", args=[pk])

    def _close(pk: int) -> str:
        return reverse("hub_admin_feedback_close", args=[pk])

    def it_marks_a_bug_not_a_bug_with_its_comment_and_says_who_heard(client: Client):
        _admin(client)
        request = FeedbackRequestFactory(category="bug", user__first_name="Robin", user__last_name="Vale")

        response = client.post(_not_a_bug(request.pk) + "?status=all", {"comment": "Zzuse the Book button"})

        assert response["Location"] == f"{_page(request.pk)}?status=all"
        request.refresh_from_db()
        assert request.status == Status.NOT_A_BUG
        assert [comment.body for comment in request.comments.all()] == ["Zzuse the Book button"]
        follow = client.get(response["Location"])
        assert [str(m) for m in follow.context["messages"]] == ["Saved. Robin Vale was notified."]

    def it_refuses_not_a_bug_without_a_comment_in_the_models_words(client: Client):
        _admin(client)
        request = FeedbackRequestFactory(category="bug")

        response = client.post(_not_a_bug(request.pk), {"comment": "  "})

        follow = client.get(response["Location"])
        assert [str(m) for m in follow.context["messages"]] == [FeedbackRequest.NOT_A_BUG_NEEDS_HOW]
        request.refresh_from_db()
        assert request.status == Status.RECEIVED
        assert mail.outbox == []

    def it_refuses_not_a_bug_when_the_opener_was_sent_unedited(client: Client):
        _admin(client)
        request = FeedbackRequestFactory(category="bug")

        response = client.post(_not_a_bug(request.pk), {"comment": FeedbackRequest.NOT_A_BUG_OPENER})

        follow = client.get(response["Location"])
        assert [str(m) for m in follow.context["messages"]] == [FeedbackRequest.NOT_A_BUG_NEEDS_HOW]
        request.refresh_from_db()
        assert request.status == Status.RECEIVED
        assert request.comments.count() == 0
        assert mail.outbox == []

    def it_closes_general_feedback_with_no_comment(client: Client):
        _admin(client)
        request = FeedbackRequestFactory(category="feedback")

        client.post(_close(request.pk))

        request.refresh_from_db()
        assert request.status == Status.CLOSED
        assert len(mail.outbox) == 1

    @pytest.mark.parametrize("view", ["not_a_bug", "close"])
    def it_only_posts_and_answers_404_for_an_unknown_request(client: Client, view: str):
        _admin(client)
        url = _not_a_bug if view == "not_a_bug" else _close
        request = FeedbackRequestFactory(category="bug")

        assert client.get(url(request.pk)).status_code == 405
        assert client.post(url(999999)).status_code == 404

    def describe_the_resolve_card():
        def it_offers_mark_fixed_not_a_bug_and_close_on_an_open_bug_report(client: Client):
            _admin(client)
            request = FeedbackRequestFactory(category="bug")

            body = client.get(_page(request.pk)).content.decode()

            assert "data-feedback-mark-live" in body
            assert "data-feedback-not-a-bug" in body
            assert "data-feedback-close" in body
            assert f'action="{_not_a_bug(request.pk)}"' in body
            assert f'action="{_close(request.pk)}"' in body
            assert "Thanks for reporting this. It is working as intended. Here is how to do it: </textarea>" in body
            assert 'rows="5" required id="id_not_a_bug_comment"' in body
            assert 'data-feedback-resolve-form="close" data-pl-busy-submit' in body

        def it_offers_no_not_a_bug_on_a_feature_request(client: Client):
            _admin(client)
            request = FeedbackRequestFactory(category="feature")

            body = client.get(_page(request.pk)).content.decode()

            assert "data-feedback-not-a-bug" not in body
            assert "data-feedback-close" in body
            assert ("not_a_bug", "Not a bug") not in client.get(_page(request.pk)).context["form"].fields[
                "status"
            ].choices

        def it_offers_only_close_on_general_feedback(client: Client):
            _admin(client)
            request = FeedbackRequestFactory(category="feedback")

            body = client.get(_page(request.pk)).content.decode()

            assert "data-feedback-close" in body
            assert "data-feedback-mark-live" not in body
            assert "data-feedback-not-a-bug" not in body

        def it_hides_the_card_on_closed_general_feedback(client: Client):
            _admin(client)
            request = FeedbackRequestFactory(category="feedback", status=Status.CLOSED)

            assert "data-feedback-resolve" not in client.get(_page(request.pk)).content.decode()

        def it_keeps_mark_fixed_and_not_a_bug_on_a_closed_bug_report(client: Client):
            _admin(client)
            request = FeedbackRequestFactory(category="bug", status=Status.CLOSED)

            body = client.get(_page(request.pk)).content.decode()

            assert "data-feedback-mark-live" in body
            assert "data-feedback-not-a-bug" in body
            assert "data-feedback-close" not in body

        def it_hides_not_a_bug_once_it_is_not_a_bug(client: Client):
            _admin(client)
            request = FeedbackRequestFactory(category="bug", status=Status.NOT_A_BUG)

            body = client.get(_page(request.pk)).content.decode()

            assert "data-feedback-not-a-bug" not in body
            assert "data-feedback-close" in body


def describe_open_closed_all():
    def it_opens_on_open_requests_and_a_closed_one_the_sender_replied_to(
        client: Client, django_capture_on_commit_callbacks
    ):
        _admin(client)
        waiting = FeedbackRequestFactory()
        FeedbackRequestFactory(status=Status.CLOSED)
        replied = FeedbackRequestFactory(status=Status.NOT_A_BUG, category="bug")
        with django_capture_on_commit_callbacks(execute=True):
            replied.add_comment(author=replied.user, body="Still stuck", photos=[])

        response = client.get(INBOX)

        assert set(response.context["page"].object_list) == {waiting, replied}
        assert response.context["is_filtered"] is False
        assert '<option value="" selected>Open</option>' in response.content.decode()

    def it_shows_every_closed_request_under_all_closed(client: Client):
        _admin(client)
        FeedbackRequestFactory()
        closed = {FeedbackRequestFactory(status=status) for status in FeedbackRequest.CLOSED_STATUSES}

        response = client.get(INBOX, {"status": "closed_all"})

        assert set(response.context["page"].object_list) == closed

    def it_shows_everything_under_all_statuses(client: Client):
        _admin(client)
        rows = {FeedbackRequestFactory(), FeedbackRequestFactory(status=Status.CLOSED)}

        assert set(client.get(INBOX, {"status": "all"}).context["page"].object_list) == rows

    def it_filters_to_one_closed_status(client: Client):
        _admin(client)
        closed = FeedbackRequestFactory(status=Status.CLOSED)
        FeedbackRequestFactory(status=Status.NOT_A_BUG, category="bug")

        assert list(client.get(INBOX, {"status": "closed"}).context["page"].object_list) == [closed]

    def it_falls_back_to_open_for_an_unknown_value(client: Client):
        _admin(client)
        waiting = FeedbackRequestFactory()
        FeedbackRequestFactory(status=Status.LIVE)

        response = client.get(INBOX, {"status": "nope"})

        assert list(response.context["page"].object_list) == [waiting]
        assert response.context["filters"]["status"] == ""
