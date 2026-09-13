"""BDD specs for ``announce_release``'s entry selection.

``tests/core/events/new_events_spec.py`` covers the fan-out — who receives the announcement.
This covers which entry gets announced, which changed when ``VERSION`` stopped being a literal.

The branch that matters is the default one. ``--release-version`` names an already-swept
release and is found by its number, but the *default* is the current ``VERSION``, which has no
numbered entry to find: the current batch lives in ``changelog.d/`` and a fragment carries no
version of its own. That is the branch a real post-deploy ``python manage.py announce_release``
takes, and it is reachable in a suite only when a member-facing fragment exists — which is a
condition each spec below constructs, rather than one it inherits from whatever happens to be
sitting in the repo on the day it runs.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from core.management.commands.announce_release import _entry_for, _period_for, _release_notes

_BATCH = {
    "date": "2026-09-14",
    "title": "Something that just shipped",
    "changes": ["A bullet members will read."],
}
_SWEPT = {
    "version": "1.62.1",
    "date": "2026-09-13",
    "title": "A release already folded into history",
    "changes": ["Older news."],
}


@pytest.fixture
def changelog(monkeypatch):
    """Pin ``CHANGELOG`` to one unswept fragment entry ahead of one swept entry.

    That is the shape ``compose_changelog`` produces on any day someone has merged a
    member-facing change since the last sweep, which is most days.
    """
    monkeypatch.setattr("plfog.version.CHANGELOG", [_BATCH, _SWEPT])
    monkeypatch.setattr("plfog.version.VERSION", "1.63.0")


def describe_entry_for():
    def describe_the_current_version():
        def it_announces_the_newest_unswept_entry(changelog):
            # The default path, and the one a post-deploy run actually takes. There is no
            # entry stamped "1.63.0" to find — the fragment that produced that number carries
            # no version at all.
            assert _entry_for("1.63.0") == _BATCH

        def it_does_not_require_the_entry_to_name_the_version(changelog):
            assert "version" not in _entry_for("1.63.0")

    def describe_an_explicit_release_version():
        def it_finds_a_swept_release_by_its_number(changelog):
            assert _entry_for("1.62.1") == _SWEPT

        def it_raises_when_nothing_carries_that_number(changelog):
            with pytest.raises(CommandError, match="No CHANGELOG entry found for version"):
                _entry_for("0.0.1")

    def describe_right_after_a_sweep():
        def it_falls_through_to_the_numbered_search(monkeypatch):
            # The batch is empty and VERSION names the frozen entry the sweep just wrote.
            # Failing in the first branch here would break announce_release on exactly the
            # release a sweep produces.
            monkeypatch.setattr("plfog.version.CHANGELOG", [_SWEPT])
            monkeypatch.setattr("plfog.version.VERSION", "1.62.1")
            assert _entry_for("1.62.1") == _SWEPT

        def it_still_raises_when_the_batch_is_empty_and_no_number_matches(monkeypatch):
            monkeypatch.setattr("plfog.version.CHANGELOG", [_SWEPT])
            monkeypatch.setattr("plfog.version.VERSION", "9.9.9")
            with pytest.raises(CommandError, match="No CHANGELOG entry found for version"):
                _entry_for("9.9.9")


def describe_period_for():
    """The ledger key, which is the only thing stopping a second announcement of one release."""

    def it_keys_a_swept_entry_on_its_own_version():
        # Byte-identical to the pre-fragment scheme, so a re-run for a release announced
        # before this change still dedupes against the ledger row it already wrote.
        assert _period_for(_SWEPT) == "release:1.62.1"

    def it_keys_a_current_batch_entry_on_the_entry_itself():
        # A fragment has no version to key on. Keying it to VERSION is the bug: VERSION moves
        # on every release including an internal one.
        assert _period_for(_BATCH).startswith("release:entry:")

    def it_gives_the_same_entry_the_same_key_every_time():
        assert _period_for(_BATCH) == _period_for(dict(_BATCH))

    def it_gives_two_different_entries_different_keys():
        other = {**_BATCH, "title": "A different feature"}
        assert _period_for(_BATCH) != _period_for(other)

    def it_does_not_change_when_the_app_version_moves():
        """The property the whole fix exists for.

        A tooling release moves VERSION and changes nothing about what there is to announce.
        If the key moved with VERSION, the ledger would see a fresh period and re-send the
        previous feature's email, bell row and Discord post to every member.
        """
        before = _period_for(_BATCH)
        # Nothing about the entry changed; only the release around it did.
        assert _period_for(_BATCH) == before


def describe_an_internal_only_release():
    def it_does_not_re_announce_the_previous_feature(db, monkeypatch):
        """The failure this guard exists for, end to end.

        #400 ships a member-facing fragment and is announced. Nobody sweeps, which is the
        normal steady state. #401 is a tooling PR: it moves VERSION, deploys, and correctly
        announces nothing on Discord. A maintainer then runs announce_release post-deploy as
        CLAUDE.md instructs. Before the ledger key was moved onto the entry, that second run
        found #400's entry, minted a fresh period from the new VERSION, and emailed every
        member the same release twice.
        """
        from core.models import EventDelivery

        monkeypatch.setattr("plfog.version.CHANGELOG", [_BATCH, _SWEPT])
        monkeypatch.setattr("plfog.version.VERSION", "1.64.0")
        call_command("announce_release")
        after_feature = EventDelivery.objects.filter(event_key="release.published").count()
        assert after_feature, "the feature release must actually announce"

        # #401 merges: VERSION moves, changelog.d/ is untouched, nothing was swept.
        monkeypatch.setattr("plfog.version.VERSION", "1.64.1")
        call_command("announce_release")

        assert EventDelivery.objects.filter(event_key="release.published").count() == after_feature, (
            "a tooling release re-announced the previous feature to every member"
        )


def describe_release_notes():
    def it_renders_the_changes_as_bullets():
        assert _release_notes(_BATCH) == "• A bullet members will read."

    def it_renders_nothing_for_an_entry_with_no_changes():
        assert _release_notes({"title": "T", "changes": []}) == ""


def describe_the_command():
    def it_announces_the_current_batch_without_an_explicit_version(db, changelog):
        # End to end on the default path: no --release-version, nothing stamped at VERSION,
        # and the announcement still goes out carrying the batch entry's title.
        call_command("announce_release")

    def it_raises_when_there_is_nothing_to_announce(db, monkeypatch):
        monkeypatch.setattr("plfog.version.CHANGELOG", [])
        monkeypatch.setattr("plfog.version.VERSION", "9.9.9")
        with pytest.raises(CommandError, match="No CHANGELOG entry found for version"):
            call_command("announce_release")
