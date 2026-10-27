"""BDD specs for the shared thread's base (core.threads): the contract a concrete thread fills in."""

from __future__ import annotations

import pytest
from django.utils import timezone

from core.threads import ThreadMessage, ThreadOpening
from membership.models import OrientationBookingMessage


def describe_the_contract():
    @pytest.mark.parametrize(
        "member",
        ["member_user_id", "member_side_name"],
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
        opening = ThreadOpening(name=name, body="Zzfirst words", created_at=now, photos=[])

        assert (opening.is_from_staff, opening.shows_staff_tag, opening.pk) == (False, False, None)
        assert (opening.body, opening.created_at, opening.thread_photos) == ("Zzfirst words", now, [])
        assert asked == []
        assert opening.author_name == "Zzsender"
        assert asked == ["asked"]
