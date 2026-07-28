"""Specs for the TEMPORARY copy-review comment model + manager (remove on/after 2026-08-10)."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from core.factories import CopyReviewCommentFactory
from core.models import CopyReviewComment


def describe_CopyReviewCommentManager():
    def describe_post():
        def it_generates_a_token_and_persists(db):
            comment = CopyReviewComment.objects.post(
                section_key="public--overview", author_name="Robin", body="Nice copy."
            )
            assert comment.pk is not None
            assert len(comment.edit_token) == 32  # secrets.token_hex(16) → 32 hex chars
            assert CopyReviewComment.objects.filter(pk=comment.pk).exists()

    def describe_grouped():
        def it_groups_active_comments_by_section_oldest_first(db):
            a1 = CopyReviewCommentFactory(section_key="s1")
            a2 = CopyReviewCommentFactory(section_key="s1")
            b1 = CopyReviewCommentFactory(section_key="s2")
            # Force a1 strictly older so oldest-first ordering is deterministic.
            CopyReviewComment.all_objects.filter(pk=a1.pk).update(created_at=timezone.now() - timedelta(hours=1))

            grouped = CopyReviewComment.objects.grouped()

            assert set(grouped) == {"s1", "s2"}
            assert [c.pk for c in grouped["s1"]] == [a1.pk, a2.pk]
            assert [c.pk for c in grouped["s2"]] == [b1.pk]

        def it_excludes_soft_deleted_comments(db):
            kept = CopyReviewCommentFactory(section_key="s1")
            gone = CopyReviewCommentFactory(section_key="s1")
            gone.soft_delete()

            grouped = CopyReviewComment.objects.grouped()

            assert [c.pk for c in grouped["s1"]] == [kept.pk]


def describe_CopyReviewComment():
    def describe_str():
        def it_names_author_section_and_date(db):
            comment = CopyReviewCommentFactory(section_key="public--overview", author_name="Robin")
            assert "Robin" in str(comment)
            assert "public--overview" in str(comment)
            assert f"{comment.created_at:%Y-%m-%d}" in str(comment)

    def describe_owned_by():
        def it_is_true_for_the_matching_token(db):
            comment = CopyReviewCommentFactory(edit_token="a" * 32)
            assert comment.owned_by("a" * 32) is True

        def it_is_false_for_a_wrong_token(db):
            comment = CopyReviewCommentFactory(edit_token="a" * 32)
            assert comment.owned_by("b" * 32) is False

        def it_is_false_for_an_empty_token(db):
            comment = CopyReviewCommentFactory(edit_token="a" * 32)
            assert comment.owned_by("") is False

    def describe_apply_edit():
        def it_updates_fields_and_bumps_updated_at(db):
            comment = CopyReviewCommentFactory(author_name="Old", body="Old body")
            CopyReviewComment.all_objects.filter(pk=comment.pk).update(updated_at=timezone.now() - timedelta(days=1))
            comment.refresh_from_db()
            before = comment.updated_at

            comment.apply_edit("New", "New body")
            comment.refresh_from_db()

            assert comment.author_name == "New"
            assert comment.body == "New body"
            assert comment.updated_at > before

    def describe_soft_delete():
        def it_hides_from_objects_but_not_all_objects(db):
            comment = CopyReviewCommentFactory()
            comment.soft_delete()

            assert not CopyReviewComment.objects.filter(pk=comment.pk).exists()
            assert CopyReviewComment.all_objects.filter(pk=comment.pk).exists()
            comment.refresh_from_db()
            assert comment.deleted_at is not None

    def describe_as_public_dict():
        def it_serializes_the_public_fields_without_the_edit_token(db):
            comment = CopyReviewCommentFactory(section_key="public--overview", author_name="Robin", body="Hi")

            payload = comment.as_public_dict()

            assert payload == {
                "id": comment.pk,
                "section_key": "public--overview",
                "author_name": "Robin",
                "body": "Hi",
                "created_at": comment.created_at.isoformat(),
                "updated_at": comment.updated_at.isoformat(),
            }
            assert "edit_token" not in payload
