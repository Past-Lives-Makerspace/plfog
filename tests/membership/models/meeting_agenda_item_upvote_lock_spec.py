"""BDD specs for the upvote-blocked-on-locked-minutes fix (MeetingAgendaItem.toggle_upvote)."""

from __future__ import annotations

import pytest

from membership.models import MeetingLockedError
from tests.membership.factories import MeetingAgendaItemFactory, MeetingFactory, UserFactory

pytestmark = pytest.mark.django_db


def describe_toggle_upvote():
    def describe_when_the_meeting_is_approved():
        def it_raises_MeetingLockedError():
            item = MeetingAgendaItemFactory(meeting=MeetingFactory(approved=True))
            user = UserFactory()
            with pytest.raises(MeetingLockedError):
                item.toggle_upvote(user)

        def it_does_not_record_the_upvote():
            item = MeetingAgendaItemFactory(meeting=MeetingFactory(approved=True))
            user = UserFactory()
            with pytest.raises(MeetingLockedError):
                item.toggle_upvote(user)
            assert item.upvoters.count() == 0

    def describe_when_the_meeting_is_a_draft():
        def it_toggles_on_then_off():
            item = MeetingAgendaItemFactory(meeting=MeetingFactory())
            user = UserFactory()
            assert item.toggle_upvote(user) is True
            assert item.upvoters.filter(pk=user.pk).exists()
            assert item.toggle_upvote(user) is False
            assert not item.upvoters.filter(pk=user.pk).exists()

    def describe_when_the_meeting_is_published():
        def it_toggles_on_then_off():
            item = MeetingAgendaItemFactory(meeting=MeetingFactory(published=True))
            user = UserFactory()
            assert item.toggle_upvote(user) is True
            assert item.toggle_upvote(user) is False
