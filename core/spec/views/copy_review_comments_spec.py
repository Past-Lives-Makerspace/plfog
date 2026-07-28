"""Specs for the TEMPORARY copy-review comment API views (remove on/after 2026-08-10)."""

from __future__ import annotations

import json

import pytest
from django.urls import reverse

from core import copy_review_views
from core.factories import CopyReviewCommentFactory
from core.models import CopyReviewComment

pytestmark = pytest.mark.django_db

ALLOWED_ORIGIN = "https://copy-review.pastlives.space"
DISALLOWED_ORIGIN = "https://evil.example"

LIST_URL = reverse("copy_review_comments")


def _edit_url(pk: int) -> str:
    return reverse("copy_review_comment_edit", args=[pk])


def _delete_url(pk: int) -> str:
    return reverse("copy_review_comment_delete", args=[pk])


def _post(client, path: str, payload: dict, origin: str | None = None):
    extra = {"content_type": "application/json"}
    if origin is not None:
        extra["HTTP_ORIGIN"] = origin
    return client.post(path, data=json.dumps(payload), **extra)


def _post_raw(client, path: str, raw: str):
    return client.post(path, data=raw, content_type="application/json")


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    """The per-IP throttle lives in a process-wide cache; isolate each test."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


def describe_list_comments():
    def it_groups_active_comments_by_section_and_omits_edit_token(client):
        c1 = CopyReviewCommentFactory(section_key="s1")
        CopyReviewCommentFactory(section_key="s2")
        deleted = CopyReviewCommentFactory(section_key="s1")
        deleted.soft_delete()

        resp = client.get(LIST_URL)

        assert resp.status_code == 200
        data = resp.json()
        assert set(data["sections"]) == {"s1", "s2"}
        s1_ids = [c["id"] for c in data["sections"]["s1"]]
        assert c1.pk in s1_ids
        assert deleted.pk not in s1_ids
        assert "edit_token" not in resp.content.decode()


def describe_cors():
    def it_reflects_an_allowed_origin_on_get(client):
        resp = client.get(LIST_URL, HTTP_ORIGIN=ALLOWED_ORIGIN)
        assert resp["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN
        assert resp["Access-Control-Allow-Methods"] == "GET, POST, OPTIONS"
        assert "Origin" in resp["Vary"]

    def it_omits_the_header_for_a_disallowed_origin(client):
        resp = client.get(LIST_URL, HTTP_ORIGIN=DISALLOWED_ORIGIN)
        assert not resp.has_header("Access-Control-Allow-Origin")

    def it_omits_the_header_when_no_origin_is_sent(client):
        resp = client.get(LIST_URL)
        assert not resp.has_header("Access-Control-Allow-Origin")

    def it_answers_options_preflight_with_204_and_cors_headers(client):
        resp = client.options(LIST_URL, HTTP_ORIGIN=ALLOWED_ORIGIN)
        assert resp.status_code == 204
        assert resp["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN
        assert resp["Access-Control-Allow-Methods"] == "GET, POST, OPTIONS"
        assert resp["Access-Control-Allow-Headers"] == "Content-Type"

    def it_reflects_an_allowed_origin_on_post(client):
        resp = _post(client, LIST_URL, {"section": "s1", "author_name": "Robin", "body": "Hi"}, origin=ALLOWED_ORIGIN)
        assert resp.status_code == 201
        assert resp["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN

    def it_returns_405_for_an_unsupported_method(client):
        resp = client.delete(LIST_URL)
        assert resp.status_code == 405


def describe_create_comment():
    def it_creates_a_comment_and_returns_the_edit_token(client):
        resp = _post(client, LIST_URL, {"section": "s1", "author_name": "Robin", "body": "Hi there"})

        assert resp.status_code == 201
        body = resp.json()
        assert len(body["edit_token"]) == 32
        assert body["comment"]["author_name"] == "Robin"
        assert "edit_token" not in body["comment"]
        assert CopyReviewComment.objects.filter(pk=body["comment"]["id"]).exists()

    def it_rejects_invalid_json(client):
        resp = _post_raw(client, LIST_URL, "{not valid json")
        assert resp.status_code == 400
        assert resp.json() == {"error": "Invalid JSON"}

    def it_rejects_a_non_object_json_body(client):
        resp = _post_raw(client, LIST_URL, "[1, 2, 3]")
        assert resp.status_code == 400
        assert resp.json() == {"error": "Invalid JSON"}

    def it_returns_400_with_form_errors_for_blank_fields(client):
        resp = _post(client, LIST_URL, {"section": "s1", "author_name": "Robin", "body": "   "})
        assert resp.status_code == 400
        assert "body" in resp.json()["errors"]

    def it_silently_no_ops_when_the_honeypot_is_filled(client):
        resp = _post(
            client,
            LIST_URL,
            {"section": "s1", "author_name": "Robin", "body": "Hi", "website": "http://spam.example"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert not CopyReviewComment.objects.exists()

    def describe_when_the_per_ip_cap_is_exceeded():
        def it_returns_429(client, monkeypatch):
            monkeypatch.setattr(copy_review_views, "_THROTTLE_MAX_POSTS", 2)
            payload = {"section": "s1", "author_name": "Robin", "body": "Hi"}
            assert _post(client, LIST_URL, payload).status_code == 201
            assert _post(client, LIST_URL, payload).status_code == 201
            assert _post(client, LIST_URL, payload).status_code == 429


def describe_edit_comment():
    def it_edits_the_comment_with_the_correct_token(client):
        comment = CopyReviewCommentFactory(edit_token="t" * 32, author_name="Old", body="Old body")

        resp = _post(client, _edit_url(comment.pk), {"edit_token": "t" * 32, "author_name": "New", "body": "New body"})

        assert resp.status_code == 200
        assert resp.json()["comment"]["author_name"] == "New"
        comment.refresh_from_db()
        assert comment.body == "New body"

    def it_rejects_a_wrong_token(client):
        comment = CopyReviewCommentFactory(edit_token="t" * 32)
        resp = _post(client, _edit_url(comment.pk), {"edit_token": "x" * 32, "author_name": "New", "body": "New"})
        assert resp.status_code == 403

    def it_returns_404_for_a_missing_comment(client):
        resp = _post(client, _edit_url(999999), {"edit_token": "t" * 32, "author_name": "New", "body": "New"})
        assert resp.status_code == 404

    def it_returns_400_with_form_errors(client):
        comment = CopyReviewCommentFactory(edit_token="t" * 32)
        resp = _post(client, _edit_url(comment.pk), {"edit_token": "t" * 32, "author_name": "New", "body": "   "})
        assert resp.status_code == 400
        assert "body" in resp.json()["errors"]

    def it_rejects_invalid_json(client):
        comment = CopyReviewCommentFactory(edit_token="t" * 32)
        resp = _post_raw(client, _edit_url(comment.pk), "{bad")
        assert resp.status_code == 400

    def it_rejects_a_non_object_json_body(client):
        comment = CopyReviewCommentFactory(edit_token="t" * 32)
        resp = _post_raw(client, _edit_url(comment.pk), '"just a string"')
        assert resp.status_code == 400


def describe_delete_comment():
    def it_soft_deletes_with_the_correct_token(client):
        comment = CopyReviewCommentFactory(edit_token="t" * 32)

        resp = _post(client, _delete_url(comment.pk), {"edit_token": "t" * 32})

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert not CopyReviewComment.objects.filter(pk=comment.pk).exists()
        assert CopyReviewComment.all_objects.filter(pk=comment.pk).exists()

    def it_rejects_a_wrong_token(client):
        comment = CopyReviewCommentFactory(edit_token="t" * 32)
        resp = _post(client, _delete_url(comment.pk), {"edit_token": "x" * 32})
        assert resp.status_code == 403
        assert CopyReviewComment.objects.filter(pk=comment.pk).exists()

    def it_returns_404_for_a_missing_comment(client):
        resp = _post(client, _delete_url(999999), {"edit_token": "t" * 32})
        assert resp.status_code == 404


def describe_public_book_surface():
    def it_resolves_unauthenticated_on_the_book_surface(client, settings):
        # SurfaceMiddleware must NOT gate /copy-review/ on the public surface: the
        # gallery JS calls this cross-origin from book.pastlives.space, anonymously.
        settings.PUBLIC_HOSTS = ["book.pastlives.space"]
        settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "book.pastlives.space"]
        CopyReviewCommentFactory(section_key="s1")

        resp = client.get(LIST_URL, HTTP_HOST="book.pastlives.space")

        assert resp.status_code == 200
        assert "sections" in resp.json()
