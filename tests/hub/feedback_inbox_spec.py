"""BDD specs for the admin Feedback inbox and a request's admin page (#693)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.events.registry import FEEDBACK_REQUEST_UPDATED
from core.models import FeedbackRequest, Notification
from membership.models import Member
from tests.core.factories import FeedbackRequestFactory

pytestmark = pytest.mark.django_db

Status = FeedbackRequest.Status
INBOX = reverse("hub_admin_feedback_inbox")


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


def _post(client: Client, request: FeedbackRequest, *, status: str, note: str = "", github: str = "", query: str = ""):
    url = _page(request.pk) + (f"?{query}" if query else "")
    return client.post(url, {"status": status, "staff_note": note, "github_issue_url": github})


def describe_permissions():
    @pytest.mark.parametrize("view", ["inbox", "page", "mark_live"])
    def it_refuses_a_non_admin(client: Client, view: str):
        _member(client)
        request = FeedbackRequestFactory()
        url = {
            "inbox": INBOX,
            "page": _page(request.pk),
            "mark_live": reverse("hub_admin_feedback_mark_live", args=[request.pk]),
        }[view]

        response = client.post(url) if view == "mark_live" else client.get(url)

        assert response.status_code == 403
        request.refresh_from_db()
        assert request.status == Status.RECEIVED

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

        assert "data-feedback-inbox-empty>No feedback yet.<" in body

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
        assert (request.status, request.staff_note, request.github_issue_url) == (
            "planned",
            "Next up.",
            "https://github.com/o/r/issues/7",
        )
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
        assert response.context["form"].errors["staff_note"] == [FeedbackRequest.NOT_PLANNED_NEEDS_REASON]
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
