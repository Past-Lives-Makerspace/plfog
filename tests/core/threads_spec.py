"""BDD specs for the shared thread's base (core.threads): the contract a concrete thread fills in."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import PropertyMock, patch

import pytest
from django.template.loader import render_to_string
from django.utils import timezone

from core.threads import ThreadMessage, ThreadOpening
from membership.models import OrientationBookingMessage


def describe_the_contract():
    @pytest.mark.parametrize(
        "member",
        ["member_user_ids", "member_side_name"],
    )
    def it_leaves_each_property_to_the_concrete_thread(member: str):
        with pytest.raises(NotImplementedError):
            getattr(ThreadMessage, member).fget(OrientationBookingMessage())

    @pytest.mark.parametrize("method", ["_tell_member_side", "_tell_staff_side", "told_sentence"])
    def it_leaves_each_method_to_the_concrete_thread(method: str):
        with pytest.raises(NotImplementedError):
            getattr(ThreadMessage, method)(OrientationBookingMessage())

    def it_signs_as_past_lives_and_does_no_bookkeeping_by_default():
        message = OrientationBookingMessage()
        assert ThreadMessage.staff_signature.fget(message) == "Past Lives"  # type: ignore[attr-defined]
        assert ThreadMessage.after_write(message) is None


def describe_an_opening():
    def it_reads_as_the_member_side_and_asks_for_the_name_only_when_shown():
        asked: list[str] = []

        def name() -> str:
            asked.append("asked")
            return "Zzsender"

        now = timezone.now()
        opening = ThreadOpening(author_id=7, name=name, body="Zzfirst words", created_at=now, photos=[])

        assert (opening.is_from_staff, opening.shows_staff_tag, opening.pk) == (False, False, None)
        assert (opening.body, opening.created_at, opening.thread_photos) == ("Zzfirst words", now, [])
        assert asked == []
        assert opening.author_name == "Zzsender"
        assert asked == ["asked"]


def describe_the_thread_template():
    def _render(viewer_pk: int, messages: list[Any]) -> str:
        # A plain context (no context processors): the template reads request.user.pk alone.
        viewer = SimpleNamespace(user=SimpleNamespace(pk=viewer_pk))
        return render_to_string(
            "components/thread.html",
            {
                "request": viewer,
                "thread_messages": messages,
                "viewer_is_staff": False,
                "thread_key": "k",
                "thread_empty": "",
            },
        )

    def _message(pk: int, author_id: int, name: str) -> SimpleNamespace:
        return SimpleNamespace(
            pk=pk,
            author_id=author_id,
            author_name=name,
            is_from_staff=False,
            shows_staff_tag=False,
            staff_signature="",
            body=f"Zzbody {pk}",
            created_at=timezone.now(),
            thread_photos=[],
        )

    def it_says_you_only_for_the_viewers_own_message_on_a_shared_member_side():
        html = _render(1, [_message(10, 1, "Zzfirst instructor"), _message(11, 2, "Zzsecond instructor")])
        assert "<strong>You</strong>" in html
        assert "<strong>Zzsecond instructor</strong>" in html
        assert "Zzfirst instructor" not in html

    def it_reads_a_message_from_any_member_side_account_as_the_member_side():
        message = OrientationBookingMessage(author_id=5)
        with patch.object(OrientationBookingMessage, "member_user_ids", new_callable=PropertyMock) as ids:
            ids.return_value = {4, 5}
            assert message.is_from_staff is False
            ids.return_value = {4}
            assert message.is_from_staff is True
