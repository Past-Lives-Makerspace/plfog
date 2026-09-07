"""Specs for :class:`membership.models.CommunityEventDraft` — the ``/create`` preview payload.

The claim must be atomic (a double-confirm creates exactly one event) and the manager
must scope strictly to the author's own unconfirmed rows.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from membership.models import CommunityEventDraft
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def draft_for(django_user_model):
    def _make(username: str = "author", **overrides) -> CommunityEventDraft:
        user = django_user_model.objects.filter(username=username).first()
        if user is None:
            user = django_user_model.objects.create(username=username, email=f"{username}@example.com")
        now = timezone.now()
        fields = {
            "author": user,
            "title": "Potluck",
            "starts_at": now + timedelta(days=2),
            "ends_at": now + timedelta(days=2, hours=1),
        }
        fields.update(overrides)
        return CommunityEventDraft.objects.create(**fields)

    return _make


def describe_CommunityEventDraft():
    def describe_str():
        def it_names_the_title_author_and_claim_state(draft_for):
            draft = draft_for()
            assert "Potluck" in str(draft)
            assert "(unconfirmed)" in str(draft)
            draft.confirmed_at = timezone.now()
            assert "(confirmed)" in str(draft)

    def describe_claimable_for():
        def it_returns_the_authors_unconfirmed_drafts(draft_for):
            draft = draft_for()
            assert list(CommunityEventDraft.objects.claimable_for(draft.author)) == [draft]

        def it_excludes_confirmed_drafts(draft_for):
            draft = draft_for(confirmed_at=timezone.now())
            assert not CommunityEventDraft.objects.claimable_for(draft.author).exists()

        def it_excludes_other_authors_drafts(draft_for):
            mine = draft_for("me")
            draft_for("them")
            assert list(CommunityEventDraft.objects.claimable_for(mine.author)) == [mine]

    def describe_the_atomic_claim():
        def it_lets_exactly_one_conditional_update_win(draft_for):
            # The command's claim is `UPDATE ... WHERE confirmed_at IS NULL`; a simulated
            # double-confirm must see exactly one winner and one 0-row loser.
            draft = draft_for()
            base = CommunityEventDraft.objects.filter(pk=draft.pk, confirmed_at__isnull=True)
            first = base.update(confirmed_at=timezone.now())
            second = base.update(confirmed_at=timezone.now())
            assert (first, second) == (1, 0)

    def describe_guild_link():
        def it_carries_an_optional_guild(draft_for):
            guild = GuildFactory(name="Ceramics")
            draft = draft_for(guild=guild)
            assert CommunityEventDraft.objects.claimable_for(draft.author).get().guild == guild
