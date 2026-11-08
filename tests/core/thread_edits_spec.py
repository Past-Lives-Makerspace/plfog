"""The model side of changing a posted thread message (#1140): the empty rule, the kinds, the form and the menu's gate."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.http import QueryDict
from django.template.loader import render_to_string
from django.utils import timezone

from core.models import FeedbackComment
from core.threads import EmptyMessageError, ThreadOpening, thread_message_model
from hub.forms import ThreadMessageEditForm
from tests.core.factories import FeedbackRequestFactory

pytestmark = pytest.mark.django_db


def describe_thread_message_model():
    def it_finds_each_thread_by_its_kind():
        assert thread_message_model("feedback") is FeedbackComment
        assert {thread_message_model(k).__name__ for k in ("class", "booking", "reservation")} == {
            "ClassMessage",
            "OrientationBookingMessage",
            "EquipmentReservationMessage",
        }

    def it_raises_for_an_unknown_kind():
        with pytest.raises(LookupError):
            thread_message_model("zznothing")


def describe_edit():
    def it_refuses_to_leave_a_message_with_no_text_and_no_photo():
        request = FeedbackRequestFactory()
        comment = FeedbackComment.write(request=request, author=request.user, body="Zzwords")

        with pytest.raises(EmptyMessageError):
            comment.edit(body="  ")

        comment.refresh_from_db()
        assert comment.body == "Zzwords" and comment.edited_at is None

    def it_raises_when_the_message_was_deleted_meanwhile():
        request = FeedbackRequestFactory()
        comment = FeedbackComment.write(request=request, author=request.user, body="Zzwords")
        FeedbackComment.objects.filter(pk=comment.pk).delete()

        with pytest.raises(FeedbackComment.DoesNotExist):
            comment.edit(body="Zznew")

    def it_reads_the_photos_again_so_one_removed_meanwhile_counts():
        request = FeedbackRequestFactory()
        comment = FeedbackComment.write(request=request, author=request.user, body="Zzwords")
        comment.photos.create(image="feedback/zz.png")
        loaded = FeedbackComment.objects.prefetch_related("photos").get(pk=comment.pk)
        assert len(loaded.thread_photos) == 1
        comment.photos.all().delete()

        with pytest.raises(EmptyMessageError):
            loaded.edit(body="")

    def it_ignores_a_photo_pk_that_is_not_the_messages():
        request = FeedbackRequestFactory()
        comment = FeedbackComment.write(request=request, author=request.user, body="Zzwords")

        assert comment.edit(body="Zzwords", remove_photo_ids=[999999]) is False


def describe_the_edit_form():
    def it_refuses_a_photo_the_message_does_not_have():
        form = ThreadMessageEditForm(QueryDict("body=Zzhi&remove_photos=7"), photo_ids=[3])

        assert not form.is_valid() and "remove_photos" in form.errors

    def it_has_nothing_marked_before_it_is_posted_and_keeps_the_marks_after():
        assert ThreadMessageEditForm(photo_ids=[3]).removing_ids == set()
        assert ThreadMessageEditForm(QueryDict("body=&remove_photos=3"), photo_ids=[3]).removing_ids == {3}

    def it_caps_the_text_only_when_asked():
        long = QueryDict(f"body={'x' * 2001}")

        assert ThreadMessageEditForm(long, photo_ids=[]).is_valid()
        assert not ThreadMessageEditForm(long, photo_ids=[], body_max_length=2000).is_valid()


def describe_the_menu_gate():
    def _render(request: SimpleNamespace) -> str:
        message = SimpleNamespace(
            pk=4,
            author_id=1,
            author_user_id=1,
            author_name="Zzwriter",
            is_from_staff=False,
            shows_staff_tag=False,
            staff_signature="",
            body="Zzbody",
            created_at=timezone.now(),
            edited_at=None,
            thread_photos=[],
        )
        context = {
            "request": request,
            "thread_messages": [message],
            "viewer_is_staff": False,
            "thread_key": "k",
            "thread_empty": "",
        }
        return render_to_string("components/thread.html", context)

    def it_shows_no_menu_to_a_signed_out_reader():
        signed_out = SimpleNamespace(user=SimpleNamespace(pk=None, is_authenticated=False))

        html = _render(signed_out)

        assert "Zzbody" in html and "data-thread-edit-form" not in html

    def it_never_offers_to_change_a_requests_opening():
        opening = ThreadOpening(
            author_id=1, name=lambda: "Zzsender", body="Zzfirst", created_at=timezone.now(), photos=[]
        )

        assert opening.edited_at is None and opening.pk is None


def describe_rewrite_notices():
    def it_touches_nothing_without_a_period():
        from core.models import Notification
        from core.threads import rewrite_notices

        request = FeedbackRequestFactory()
        row = Notification.objects.create(user=request.user, trigger="zz", title="Zz", body="Zzwords")

        assert rewrite_notices("", "Zzwords", None) == 0
        row.refresh_from_db()
        assert row.body == "Zzwords"

    def it_clips_a_rewritten_row_to_the_bell_column():
        from core.models import Notification
        from core.threads import rewrite_notices

        request = FeedbackRequestFactory()
        row = Notification.objects.create(user=request.user, trigger="zz", title="Zz", body="Zzshort", period="zz-p")

        assert rewrite_notices("zz-p", "Zzshort", "y" * 600) == 1
        row.refresh_from_db()
        assert len(row.body) == 500 and row.body.endswith("\u2026")

    def it_swaps_only_the_words_after_a_kiln_author_name_that_contains_them():
        from core.models import Notification
        from core.threads import DELETED_NOTICE, rewrite_notices

        request = FeedbackRequestFactory()
        row = Notification.objects.create(
            user=request.user, trigger="zz", title="Zz", body="Yesenia Park: Yes", period="reply-zz"
        )

        assert rewrite_notices("reply-zz", "Yes", None) == 1
        row.refresh_from_db()
        assert row.body == f"Yesenia Park: {DELETED_NOTICE}"

    def it_edits_the_tail_and_keeps_the_kiln_author_name():
        from core.models import Notification
        from core.threads import rewrite_notices

        request = FeedbackRequestFactory()
        row = Notification.objects.create(
            user=request.user, trigger="zz", title="Zz", body="Yesenia Park: Yes", period="reply-zy"
        )

        rewrite_notices("reply-zy", "Yes", "Yes, Tuesday works")
        row.refresh_from_db()
        assert row.body == "Yesenia Park: Yes, Tuesday works"
