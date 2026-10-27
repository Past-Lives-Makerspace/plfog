"""BDD specs for the migrations that carry FeedbackRequest.staff_note into the comment thread.

0129 copied each note into a comment with no author; 0130 copies any note left over (one old code
could write during part 1's deploy window), skipping one already carried, then takes the field out of
the model while the column stays, with a database default, for the release still serving.
Each spec steps core back to 0129 with the real migration executor and forward again, so the
functions run against the schema they were written for.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from typing import Any

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = ("core", "0129_feedback_notes_to_comments")
AFTER = ("core", "0130_retire_feedbackrequest_staff_note")

pytestmark = pytest.mark.django_db(transaction=True)


def _migrate(target: tuple[str, str]) -> Any:
    executor = MigrationExecutor(connection)
    executor.migrate([target])
    executor.loader.build_graph()
    return executor.loader.project_state([target]).apps


@pytest.fixture
def old_apps() -> Iterator[Any]:
    """The app registry at 0129, with ``staff_note`` still a column; core goes forward again after."""
    try:
        yield _migrate(BEFORE)
    finally:
        _migrate(AFTER)


def _request(apps: Any, username: str, note: str) -> Any:
    user = apps.get_model("auth", "User").objects.create(username=username, email=f"{username}@example.com")
    return apps.get_model("core", "FeedbackRequest").objects.create(
        user=user, category="bug", subject="s", message="m", staff_note=note
    )


def describe_0129_reverse():
    def it_puts_back_only_the_oldest_comment_with_no_author_on_each_request(old_apps: Any):
        Comment = old_apps.get_model("core", "FeedbackComment")
        request = _request(old_apps, "rev", "")
        carried = Comment.objects.create(request=request, author=None, body="Zzcarried note")
        later = Comment.objects.create(request=request, author=None, body="Zzlater no author")
        staff = Comment.objects.create(request=request, author=request.user, body="Zzfrom the sender")
        untouched = _request(old_apps, "untouched", "")
        migration = importlib.import_module("core.migrations.0129_feedback_notes_to_comments")

        migration.comments_to_notes(old_apps, None)

        request.refresh_from_db()
        assert request.staff_note == "Zzcarried note"
        assert set(Comment.objects.values_list("pk", flat=True)) == {later.pk, staff.pk}
        assert not Comment.objects.filter(pk=carried.pk).exists()
        untouched.refresh_from_db()
        assert untouched.staff_note == ""

    def it_copies_each_note_forward_as_an_authorless_comment(old_apps: Any):
        Comment = old_apps.get_model("core", "FeedbackComment")
        noted = _request(old_apps, "fwd", "  Zzold note  ")
        _request(old_apps, "blank", "   ")
        migration = importlib.import_module("core.migrations.0129_feedback_notes_to_comments")

        migration.notes_to_comments(old_apps, None)

        [comment] = Comment.objects.all()
        assert (comment.request_id, comment.author_id, comment.body) == (noted.pk, None, "Zzold note")
        assert comment.created_at == noted.status_changed_at


def describe_0130():
    def it_copies_only_leftover_notes_and_keeps_the_column_for_the_old_release(old_apps: Any):
        Comment = old_apps.get_model("core", "FeedbackComment")
        carried = _request(old_apps, "carried", "Zzalready in the thread")
        Comment.objects.create(request=carried, author=None, body="Zzalready in the thread")
        leftover = _request(old_apps, "leftover", "  Zzwritten during the deploy  ")
        _request(old_apps, "none", "")

        new_apps = _migrate(AFTER)

        NewComment = new_apps.get_model("core", "FeedbackComment")
        assert list(NewComment.objects.filter(request_id=carried.pk).values_list("body", flat=True)) == [
            "Zzalready in the thread"
        ]
        [copied] = NewComment.objects.filter(request_id=leftover.pk)
        assert (copied.author_id, copied.body) == (None, "Zzwritten during the deploy")
        assert NewComment.objects.count() == 2
        NewRequest = new_apps.get_model("core", "FeedbackRequest")
        assert "staff_note" not in {field.name for field in NewRequest._meta.get_fields()}
        columns = [
            column.name
            for column in connection.introspection.get_table_description(connection.cursor(), "core_feedbackrequest")
        ]
        assert "staff_note" in columns  # the part 1 release still reads and writes it during the deploy

    def it_gives_a_new_request_a_blank_note_from_the_database_default():
        new_apps = _migrate(AFTER)
        user = new_apps.get_model("auth", "User").objects.create(username="fresh", email="fresh@example.com")

        request = new_apps.get_model("core", "FeedbackRequest").objects.create(
            user=user, category="bug", subject="s", message="m"
        )

        with connection.cursor() as cursor:
            cursor.execute("SELECT staff_note FROM core_feedbackrequest WHERE id = %s", [request.pk])
            assert cursor.fetchone() == ("",)

    def it_restores_the_column_from_the_oldest_authorless_comment_on_the_way_back():
        new_apps = _migrate(AFTER)
        user = new_apps.get_model("auth", "User").objects.create(username="back", email="back@example.com")
        Request = new_apps.get_model("core", "FeedbackRequest")
        Comment = new_apps.get_model("core", "FeedbackComment")
        request = Request.objects.create(user=user, category="bug", subject="s", message="m")
        Comment.objects.create(request=request, author=None, body="Zzfirst note")
        Comment.objects.create(request=request, author=None, body="Zzsecond note")
        bare = Request.objects.create(user=user, category="bug", subject="s2", message="m")

        try:
            old_apps = _migrate(BEFORE)
            OldRequest = old_apps.get_model("core", "FeedbackRequest")
            assert OldRequest.objects.get(pk=request.pk).staff_note == "Zzfirst note"
            assert OldRequest.objects.get(pk=bare.pk).staff_note == ""
            assert old_apps.get_model("core", "FeedbackComment").objects.count() == 2
        finally:
            _migrate(AFTER)
