"""The Spotlight's read model (#708, hub.spotlight): who sees the version card and the newest update it names (#935)."""

from __future__ import annotations

import pytest

from hub.spotlight import LatestUpdate, sees_spotlight
from membership.models import Member
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db

ENTRY = {"title": "Zorblax update", "date": "2026-10-07", "changes": [], "slug": "701-zorblax"}


def describe_the_latest_update():
    def it_offers_the_newest_update_with_its_anchor(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("plfog.version.CHANGELOG", [ENTRY, {**ENTRY, "title": "Older", "slug": "older"}])

        update = LatestUpdate.newest()

        assert update is not None
        assert (update.title, update.date, update.anchor) == ("Zorblax update", "2026-10-07", "changelog-701-zorblax")
        assert update.short_date == "Oct 7"

    def it_gives_a_swept_entry_no_anchor(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "plfog.version.CHANGELOG", [{"version": "2.60.0", "title": "Old", "date": "2026-09-30", "changes": []}]
        )

        update = LatestUpdate.newest()

        assert update is not None and update.anchor == ""

    def it_has_none_with_an_empty_changelog(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("plfog.version.CHANGELOG", [])

        assert LatestUpdate.newest() is None


def describe_sees_spotlight():
    """Who sees the Spotlight: the polls app's can_vote rule (#709), moved here unchanged in #890."""

    @pytest.mark.parametrize(
        ("status", "allowed"),
        [
            (Member.Status.ACTIVE, True),
            (Member.Status.SUSPENDED, True),
            (Member.Status.GUEST, False),
            (Member.Status.FORMER, False),
        ],
    )
    def it_lets_members_in_but_not_guests_or_former_members(status: str, allowed: bool):
        assert sees_spotlight(MemberFactory(status=status)) is allowed

    def it_leaves_out_an_account_with_no_member():
        assert sees_spotlight(None) is False
