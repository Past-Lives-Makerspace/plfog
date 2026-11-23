"""BDD specs for ``/api/v1/feedback/``: the Feedback inbox through the FOG API (#1220).

Reads list and retrieve requests with their thread and photos; ``PATCH`` saves through
``FeedbackRequest.apply_admin_update`` and ``POST <pk>/comment/`` through ``add_comment``, so the
sender hears exactly what the inbox page would tell them. Fog admins only, reads included.
"""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from typing import cast

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from core.events.registry import FEEDBACK_COMMENT_ADDED, FEEDBACK_REQUEST_UPDATED
from core.models import FeedbackComment, FeedbackCommentPhoto, FeedbackRequest, FeedbackRequestPhoto, Notification
from tests.core.factories import FeedbackRequestFactory

pytestmark = pytest.mark.django_db

Status = FeedbackRequest.Status
Category = FeedbackRequest.Category
URL = "/api/v1/feedback/"


def _detail(request: FeedbackRequest) -> str:
    return f"{URL}{request.pk}/"


def _photo(name: str = "shot.png") -> SimpleUploadedFile:
    buf = BytesIO()
    Image.new("RGB", (1, 1)).save(buf, format="PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


def _bells(request: FeedbackRequest, trigger: str) -> list[Notification]:
    return list(Notification.objects.filter(user=request.user, trigger=trigger))


def _staff_comments(request: FeedbackRequest) -> list[str]:
    return [comment.body for comment in request.comments.all() if comment.is_from_staff]


def describe_permissions():
    @pytest.fixture
    def feedback_request() -> FeedbackRequest:
        return cast(FeedbackRequest, FeedbackRequestFactory())

    def _calls(feedback_request: FeedbackRequest) -> list[tuple[str, str, dict]]:
        return [
            ("get", URL, {}),
            ("get", _detail(feedback_request), {}),
            ("patch", _detail(feedback_request), {"status": Status.PLANNED}),
            ("post", f"{_detail(feedback_request)}comment/", {"body": "Hi"}),
        ]

    def it_refuses_a_member_on_every_route(member_client, feedback_request):
        for method, url, payload in _calls(feedback_request):
            response = getattr(member_client, method)(url, payload, format="json")
            assert response.status_code == status.HTTP_403_FORBIDDEN, (method, url)
        feedback_request.refresh_from_db()
        assert feedback_request.status == Status.RECEIVED
        assert not feedback_request.comments.exists()

    def it_refuses_an_unauthenticated_caller_on_every_route(api_client, feedback_request):
        for method, url, payload in _calls(feedback_request):
            response = getattr(api_client, method)(url, payload, format="json")
            assert response.status_code == status.HTTP_401_UNAUTHORIZED, (method, url)

    def it_lets_a_superuser_with_no_member_read(api_client):
        superuser = User.objects.create_superuser(username="apisuper", password="x", email="apisuper@example.com")
        token, _ = Token.objects.get_or_create(user=superuser)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

        assert client.get(URL).status_code == status.HTTP_200_OK

    def it_offers_no_put_or_delete(admin_client, feedback_request):
        assert admin_client.put(_detail(feedback_request), {}, format="json").status_code == 405
        assert admin_client.delete(_detail(feedback_request)).status_code == 405
        assert admin_client.post(URL, {}, format="json").status_code == 405


def describe_list():
    def it_lists_newest_first(admin_client):
        older = FeedbackRequestFactory()
        newer = FeedbackRequestFactory()
        FeedbackRequest.objects.filter(pk=older.pk).update(created_at=timezone.now() - timedelta(days=2))

        response = admin_client.get(URL)

        assert response.status_code == status.HTTP_200_OK
        assert [item["id"] for item in response.data["results"]] == [newer.pk, older.pk]

    def it_filters_to_the_inbox_open_view(admin_client):
        received = FeedbackRequestFactory()
        building = FeedbackRequestFactory(status=Status.BUILDING)
        FeedbackRequestFactory(status=Status.LIVE)
        replied = FeedbackRequestFactory(status=Status.CLOSED, member_replied_at=timezone.now())

        response = admin_client.get(URL, {"status": "open"})

        assert {item["id"] for item in response.data["results"]} == {received.pk, building.pk, replied.pk}
        assert {item["id"] for item in response.data["results"]} == set(
            FeedbackRequest.objects.open().values_list("pk", flat=True)
        )

    def it_filters_by_one_status(admin_client):
        FeedbackRequestFactory()
        live = FeedbackRequestFactory(status=Status.LIVE)

        response = admin_client.get(URL, {"status": Status.LIVE})

        assert [item["id"] for item in response.data["results"]] == [live.pk]

    def it_refuses_an_unknown_status_filter(admin_client):
        response = admin_client.get(URL, {"status": "pending"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "open" in str(response.data["status"])


def describe_retrieve():
    def it_returns_the_request_with_its_thread_and_photo_urls(admin_client, admin_member):
        request = FeedbackRequest.submit(
            user=FeedbackRequestFactory().user,
            category=Category.BUG,
            subject="Door sticks",
            message="The door sticks.",
            photos=[_photo()],
        )
        reply = request.add_comment(author=request.user, body="Still sticking.", photos=[_photo("again.png")])
        request.apply_admin_update(
            status=Status.PLANNED,
            comment="On the list.",
            github_issue_url="https://github.com/example/repo/issues/1",
            actor=admin_member.user,
        )

        response = admin_client.get(_detail(request))

        assert response.status_code == status.HTTP_200_OK
        data = response.data
        assert data["id"] == request.pk
        assert data["category"] == Category.BUG
        assert data["status"] == Status.PLANNED
        assert data["status_label"] == "Planned"
        assert data["subject"] == "Door sticks"
        assert data["message"] == "The door sticks."
        assert data["sender_name"] == request.sender_label
        assert data["github_issue_url"] == "https://github.com/example/repo/issues/1"
        assert data["created_at"] and data["status_changed_at"]
        photo = FeedbackRequestPhoto.objects.get(request=request)
        assert data["photo_urls"] == [f"http://testserver{photo.image.url}"]
        first, second = data["comments"]
        assert (first["id"], first["body"], first["from_staff"]) == (reply.pk, "Still sticking.", False)
        assert first["author"] == request.user.pk
        assert first["author_name"] == request.sender_label
        comment_photo = FeedbackCommentPhoto.objects.get(comment=reply)
        assert first["photo_urls"] == [f"http://testserver{comment_photo.image.url}"]
        assert (second["body"], second["from_staff"], second["photo_urls"]) == ("On the list.", True, [])

    def it_returns_a_closed_request_whatever_the_filter(admin_client):
        closed = FeedbackRequestFactory(status=Status.CLOSED)

        assert admin_client.get(_detail(closed), {"status": "open"}).status_code == status.HTTP_200_OK

    def it_does_not_mark_the_request_seen(admin_client):
        request = FeedbackRequestFactory(member_replied_at=timezone.now())

        admin_client.get(_detail(request))

        request.refresh_from_db()
        assert request.staff_seen_at is None


def describe_comment():
    def it_posts_as_the_token_user_and_tells_the_sender(admin_client, admin_member):
        request = FeedbackRequestFactory()

        response = admin_client.post(f"{_detail(request)}comment/", {"body": "Thanks, looking."}, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        comment = FeedbackComment.objects.get(request=request)
        assert (comment.author, comment.body) == (admin_member.user, "Thanks, looking.")
        assert [item["body"] for item in response.data["comments"]] == ["Thanks, looking."]
        assert len(_bells(request, FEEDBACK_COMMENT_ADDED)) == 1
        [message] = mail.outbox
        assert message.to == [request.user.email]
        assert message.subject == f"Past Lives wrote on your request: {request.subject}"
        assert "Thanks, looking." in message.body

    def it_refuses_a_blank_body(admin_client):
        request = FeedbackRequestFactory()

        response = admin_client.post(f"{_detail(request)}comment/", {"body": "  "}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not request.comments.exists()
        assert mail.outbox == []

    def it_404s_on_a_missing_request(admin_client):
        response = admin_client.post(f"{URL}999999/comment/", {"body": "Hi"}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND


def describe_partial_update():
    def it_moves_the_status_and_tells_the_sender_once(admin_client):
        request = FeedbackRequestFactory()

        response = admin_client.patch(_detail(request), {"status": Status.BUILDING}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == Status.BUILDING
        request.refresh_from_db()
        assert request.status == Status.BUILDING
        [bell] = _bells(request, FEEDBACK_REQUEST_UPDATED)
        assert bell.title == "Your request is being built"
        [message] = mail.outbox
        assert message.subject == f"Your request: {request.subject} is being built"

    def it_carries_the_comment_in_that_one_notice(admin_client, admin_member):
        request = FeedbackRequestFactory()

        response = admin_client.patch(
            _detail(request), {"status": Status.PLANNED, "comment": "Next month."}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert len(_bells(request, FEEDBACK_REQUEST_UPDATED)) == 1
        assert _bells(request, FEEDBACK_COMMENT_ADDED) == []
        [message] = mail.outbox
        assert "is planned" in message.subject
        assert "Next month." in message.body
        assert _staff_comments(request) == ["Next month."]
        assert request.comments.get().author == admin_member.user

    def it_posts_a_comment_alone_keeping_status_and_link(admin_client):
        request = FeedbackRequestFactory(status=Status.PLANNED, github_issue_url="https://github.com/x/y/issues/2")

        response = admin_client.patch(_detail(request), {"comment": "Still on it."}, format="json")

        assert response.status_code == status.HTTP_200_OK
        request.refresh_from_db()
        assert (request.status, request.github_issue_url) == (Status.PLANNED, "https://github.com/x/y/issues/2")
        assert _staff_comments(request) == ["Still on it."]
        assert len(_bells(request, FEEDBACK_COMMENT_ADDED)) == 1
        assert _bells(request, FEEDBACK_REQUEST_UPDATED) == []

    def it_sets_the_github_link_without_telling_anyone(admin_client):
        request = FeedbackRequestFactory()

        response = admin_client.patch(
            _detail(request), {"github_issue_url": "https://github.com/x/y/issues/3"}, format="json"
        )

        assert response.data["github_issue_url"] == "https://github.com/x/y/issues/3"
        request.refresh_from_db()
        assert (request.status, request.github_issue_url) == (Status.RECEIVED, "https://github.com/x/y/issues/3")
        assert mail.outbox == []

    @pytest.mark.parametrize(
        ("category", "payload", "message"),
        [
            (Category.FEATURE, {"status": Status.NOT_PLANNED}, FeedbackRequest.NOT_PLANNED_NEEDS_REASON),
            (Category.BUG, {"status": Status.NOT_A_BUG, "comment": " "}, FeedbackRequest.NOT_A_BUG_NEEDS_HOW),
            (Category.FEATURE, {"status": Status.NOT_A_BUG, "comment": "No."}, FeedbackRequest.NOT_A_BUG_BUGS_ONLY),
            (Category.FEEDBACK, {"status": Status.PLANNED}, FeedbackRequest.FEEDBACK_STAYS_RECEIVED),
            (Category.FEEDBACK, {"status": Status.LIVE}, FeedbackRequest.FEEDBACK_STAYS_RECEIVED),
        ],
    )
    def it_refuses_what_the_inbox_refuses_with_its_message(admin_client, category, payload, message):
        request = FeedbackRequestFactory(category=category)

        response = admin_client.patch(_detail(request), payload, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {"detail": message}
        request.refresh_from_db()
        assert request.status == Status.RECEIVED
        assert not request.comments.exists()
        assert mail.outbox == []

    def it_accepts_not_a_bug_with_a_comment_on_a_bug(admin_client):
        request = FeedbackRequestFactory(category=Category.BUG)

        response = admin_client.patch(
            _detail(request), {"status": Status.NOT_A_BUG, "comment": "Use the second door."}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] == Status.NOT_A_BUG
        assert len(mail.outbox) == 1

    def it_moves_a_request_to_backlog_with_no_comment_or_link(admin_client):
        request = FeedbackRequestFactory(category=Category.FEATURE)

        response = admin_client.patch(_detail(request), {"status": "backlog"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert (response.data["status"], response.data["status_label"]) == ("backlog", "Backlog")
        assert len(mail.outbox) == 1

    def it_refuses_a_bad_status_or_link(admin_client):
        request = FeedbackRequestFactory()

        bad_status = admin_client.patch(_detail(request), {"status": "done"}, format="json")
        bad_link = admin_client.patch(_detail(request), {"github_issue_url": "not a link"}, format="json")

        assert bad_status.status_code == status.HTTP_400_BAD_REQUEST
        assert "status" in bad_status.data
        assert bad_link.status_code == status.HTTP_400_BAD_REQUEST
        assert "github_issue_url" in bad_link.data
