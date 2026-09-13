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

from core.management.commands.announce_release import _entry_for, _release_notes

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
