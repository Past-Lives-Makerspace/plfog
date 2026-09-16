"""BDD specs for ``plfog.changelog`` — the fold that replaced the hand-edited VERSION literal.

Everything here is filesystem-and-string work with no Django in it, so no spec below takes
``db``. Fragments are written into ``tmp_path`` rather than read out of the real
``changelog.d/``: a spec that asserted against the repo's own fragments would start failing
the day someone shipped a feature, which is the opposite of what these are for.

The fold specs carry the property that the whole design rests on. ``fold_version`` must not
care what order fragments arrive in, because that independence is exactly what makes a rebase
safe — a PR that merges late cannot renumber a release that already shipped. ``it_does_not
_care_about_order`` is the spec that fails if someone "simplifies" the counting back into a
sequential replay.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from plfog.changelog import (
    Fragment,
    FragmentError,
    compose_changelog,
    fold_version,
    load_base,
    load_fragment,
    load_fragments,
    load_history,
    parse_fragment,
    parse_version,
)

_PATH = pathlib.Path("changelog.d/1-example.toml")


def _toml(**fields: object) -> str:
    """A fragment's TOML text, with sensible member-facing defaults for anything unstated."""
    data: dict[str, object] = {
        "bump": "minor",
        "date": "2026-09-13",
        "title": "A thing members can see",
        "changes": ["It does the thing now."],
    }
    data.update(fields)
    lines = []
    for key, value in data.items():
        if isinstance(value, list):
            body = ", ".join(json.dumps(item) for item in value)
            lines.append(f"{key} = [{body}]")
        else:
            lines.append(f"{key} = {json.dumps(value)}")
    return "\n".join(lines) + "\n"


def _fragment(
    bump: str = "minor", *, audience: str = "members", date: str = "2026-09-13", name: str = "1-a"
) -> Fragment:
    return parse_fragment(_toml(bump=bump, audience=audience, date=date), pathlib.Path(f"changelog.d/{name}.toml"))


def describe_parse_version():
    def it_splits_a_semver_string():
        assert parse_version("1.62.1") == (1, 62, 1)

    def it_tolerates_surrounding_whitespace():
        assert parse_version("  2.0.0\n") == (2, 0, 0)

    @pytest.mark.parametrize("bad", ["1.62", "1.62.1.4", "v1.62.1", "", "1.62.x"])
    def it_refuses_anything_that_is_not_major_minor_patch(bad: str):
        # Strict on purpose: a base that does not parse would let the fold invent a number,
        # and an invented number is a mis-stamped release.
        with pytest.raises(ValueError, match="Not a MAJOR.MINOR.PATCH version"):
            parse_version(bad)


def describe_parse_fragment():
    def it_reads_a_member_facing_fragment():
        fragment = parse_fragment(_toml(screenshot="home"), _PATH)
        assert fragment.bump == "minor"
        assert fragment.audience == "members"
        assert fragment.date == "2026-09-13"
        assert fragment.title == "A thing members can see"
        assert fragment.changes == ("It does the thing now.",)
        assert fragment.screenshot == "home"
        assert fragment.is_member_facing

    def it_defaults_the_audience_to_members():
        assert parse_fragment(_toml(), _PATH).audience == "members"

    def it_strips_surrounding_whitespace_from_prose():
        fragment = parse_fragment(_toml(title="  Spaced  ", changes=["  bullet  "]), _PATH)
        assert fragment.title == "Spaced"
        assert fragment.changes == ("bullet",)

    def describe_an_internal_fragment():
        def it_needs_only_a_bump():
            fragment = parse_fragment('bump = "patch"\naudience = "internal"\n', _PATH)
            assert fragment.bump == "patch"
            assert not fragment.is_member_facing
            assert fragment.title == ""
            assert fragment.changes == ()

        def it_does_not_demand_a_date_or_bullets():
            # Demanding prose for a release published nowhere is how entries end up stamped
            # at versions Discord never posts.
            parse_fragment('bump = "minor"\naudience = "internal"\n', _PATH)

    def describe_rejections():
        def it_names_the_file_in_every_message():
            with pytest.raises(FragmentError, match=r"changelog\.d/1-example\.toml"):
                parse_fragment('bump = "sideways"\n', _PATH)

        def it_refuses_invalid_toml():
            with pytest.raises(FragmentError, match="not valid TOML"):
                parse_fragment("bump = \n", _PATH)

        @pytest.mark.parametrize("bad", ["sideways", "", "MINOR", "1"])
        def it_refuses_an_unknown_bump(bad: str):
            with pytest.raises(FragmentError, match="'bump' must be one of"):
                parse_fragment(_toml(bump=bad), _PATH)

        def it_refuses_a_missing_bump():
            with pytest.raises(FragmentError, match="'bump' must be one of"):
                parse_fragment('date = "2026-09-13"\n', _PATH)

        def it_refuses_an_unknown_audience():
            with pytest.raises(FragmentError, match="'audience' must be one of"):
                parse_fragment(_toml(audience="everyone"), _PATH)

        def it_refuses_an_unknown_key():
            # A misspelled key is a bullet that silently never reaches members.
            with pytest.raises(FragmentError, match="unknown key\\(s\\) titel"):
                parse_fragment(_toml(titel="oops"), _PATH)

        def it_lists_several_unknown_keys_alphabetically():
            with pytest.raises(FragmentError, match="unknown key\\(s\\) aaa, zzz"):
                parse_fragment(_toml(zzz=1, aaa=2), _PATH)

        @pytest.mark.parametrize("bad", ["2026-9-13", "13-09-2026", "2026/09/13", "soon"])
        def it_refuses_a_date_that_is_not_iso(bad: str):
            with pytest.raises(FragmentError, match="'date' must be YYYY-MM-DD"):
                parse_fragment(_toml(date=bad), _PATH)

        def it_refuses_a_missing_date():
            with pytest.raises(FragmentError, match="'date' must be a non-empty string"):
                parse_fragment('bump = "minor"\ntitle = "t"\nchanges = ["c"]\n', _PATH)

        def it_refuses_a_blank_date():
            with pytest.raises(FragmentError, match="'date' must be a non-empty string"):
                parse_fragment(_toml(date="   "), _PATH)

        def it_refuses_a_non_string_title():
            with pytest.raises(FragmentError, match="'title' must be a non-empty string"):
                parse_fragment(_toml(title=7), _PATH)

        def it_refuses_a_blank_title():
            with pytest.raises(FragmentError, match="'title' must be a non-empty string"):
                parse_fragment(_toml(title="  "), _PATH)

        def it_refuses_an_empty_changes_list():
            with pytest.raises(FragmentError, match="'changes' must be a non-empty list"):
                parse_fragment(_toml(changes=[]), _PATH)

        def it_refuses_changes_that_is_not_a_list():
            with pytest.raises(FragmentError, match="'changes' must be a non-empty list"):
                parse_fragment(_toml(changes="one bullet"), _PATH)

        def it_refuses_a_blank_bullet():
            with pytest.raises(FragmentError, match="every entry in 'changes'"):
                parse_fragment(_toml(changes=["fine", "  "]), _PATH)

        def it_refuses_a_non_string_bullet():
            with pytest.raises(FragmentError, match="every entry in 'changes'"):
                parse_fragment(_toml(changes=[3]), _PATH)

        def it_refuses_a_non_string_screenshot():
            with pytest.raises(FragmentError, match="'screenshot' must be a string"):
                parse_fragment(_toml(screenshot=5), _PATH)


def describe_as_entry():
    def it_shapes_a_fragment_like_a_frozen_changelog_entry():
        assert parse_fragment(_toml(), _PATH).as_entry() == {
            "date": "2026-09-13",
            "title": "A thing members can see",
            "changes": ["It does the thing now."],
        }

    def it_carries_no_version_key():
        # A fragment cannot know its own release number without merge order, which the tree
        # does not record. The changelog modal renders the badge only when one is present.
        assert "version" not in parse_fragment(_toml(), _PATH).as_entry()

    def it_includes_a_screenshot_slug_when_set():
        assert parse_fragment(_toml(screenshot="home"), _PATH).as_entry()["screenshot"] == "home"

    def it_omits_the_screenshot_key_when_unset():
        # So the entry matches a legacy entry that never had one, and build_release_cards
        # keeps treating it as genuinely optional.
        assert "screenshot" not in parse_fragment(_toml(), _PATH).as_entry()


def describe_load_fragments():
    def it_returns_nothing_when_the_directory_is_absent(tmp_path: pathlib.Path):
        # The state right after a sweep, not an error.
        assert load_fragments(tmp_path / "nope") == []

    def it_returns_nothing_when_the_directory_is_empty(tmp_path: pathlib.Path):
        assert load_fragments(tmp_path) == []

    def it_reads_every_toml_file(tmp_path: pathlib.Path):
        (tmp_path / "1-a.toml").write_text(_toml(title="One"))
        (tmp_path / "2-b.toml").write_text(_toml(title="Two"))
        assert {f.title for f in load_fragments(tmp_path)} == {"One", "Two"}

    def it_ignores_the_readme(tmp_path: pathlib.Path):
        (tmp_path / "1-a.toml").write_text(_toml())
        (tmp_path / "README.md").write_text("not a fragment")
        assert len(load_fragments(tmp_path)) == 1

    def it_orders_newest_date_first(tmp_path: pathlib.Path):
        (tmp_path / "1-old.toml").write_text(_toml(date="2026-01-01", title="Old"))
        (tmp_path / "2-new.toml").write_text(_toml(date="2026-09-13", title="New"))
        assert [f.title for f in load_fragments(tmp_path)] == ["New", "Old"]

    def it_breaks_a_same_day_tie_on_filename_descending(tmp_path: pathlib.Path):
        # Path.glob order is filesystem order. A changelog that reshuffles between a laptop
        # and Render is a changelog nobody can review, so the tie-break is explicit.
        (tmp_path / "391-a.toml").write_text(_toml(title="Earlier PR"))
        (tmp_path / "392-b.toml").write_text(_toml(title="Later PR"))
        assert [f.title for f in load_fragments(tmp_path)] == ["Later PR", "Earlier PR"]

    def it_raises_on_a_malformed_fragment_rather_than_skipping_it(tmp_path: pathlib.Path):
        # A fragment silently dropped for a typo is a release that ships and announces
        # nothing — the #348 failure this whole line of work exists to close.
        (tmp_path / "1-broken.toml").write_text('bump = "sideways"\n')
        with pytest.raises(FragmentError):
            load_fragments(tmp_path)


def describe_load_fragment():
    def it_reads_one_file_from_disk(tmp_path: pathlib.Path):
        path = tmp_path / "1-a.toml"
        path.write_text(_toml(title="From disk"))
        assert load_fragment(path).title == "From disk"


def describe_fold_version():
    def it_returns_the_base_when_nothing_is_pending():
        assert fold_version("1.62.1", []) == "1.62.1"

    def it_bumps_the_patch_for_a_patch():
        assert fold_version("1.62.1", [_fragment("patch")]) == "1.62.2"

    def it_bumps_the_minor_and_resets_the_patch_for_a_minor():
        assert fold_version("1.62.1", [_fragment("minor")]) == "1.63.0"

    def it_bumps_the_major_and_resets_the_rest_for_a_major():
        assert fold_version("1.62.1", [_fragment("major")]) == "2.0.0"

    def it_counts_several_of_the_same_level():
        assert fold_version("1.62.1", [_fragment("minor"), _fragment("minor")]) == "1.64.0"

    def it_lets_patches_ride_on_top_of_a_minor():
        # Two minor releases, then a patch on the second: 1.63.0, 1.64.0, 1.64.1.
        fragments = [_fragment("minor"), _fragment("minor"), _fragment("patch")]
        assert fold_version("1.62.1", fragments) == "1.64.1"

    def it_lets_minors_and_patches_ride_on_top_of_a_major():
        fragments = [_fragment("major"), _fragment("minor"), _fragment("patch")]
        assert fold_version("1.62.1", fragments) == "2.1.1"

    def it_does_not_care_about_order():
        # THE property the whole design rests on. A merge only ever adds to the set, so a
        # fragment landing today cannot renumber one that shipped last week — which is what
        # makes a rebase safe and what a sequential replay would quietly destroy.
        fragments = [_fragment("patch"), _fragment("major"), _fragment("minor"), _fragment("patch")]
        assert fold_version("1.62.1", fragments) == fold_version("1.62.1", list(reversed(fragments)))

    def it_ignores_internal_fragments_being_internal():
        # Audience decides what gets announced, never what the version does. A tooling
        # release still moves the number.
        assert fold_version("1.62.1", [_fragment("minor", audience="internal")]) == "1.63.0"

    def it_counts_two_majors_rather_than_raising():
        # This runs at import of plfog.version, so raising here means the app does not boot —
        # and two PRs each declaring `major` both pass the PR check alone and collide only
        # once both are on main. A slightly imprecise number beats a production outage.
        assert fold_version("1.62.1", [_fragment("major"), _fragment("major")]) == "3.0.0"

    def it_stays_monotonic_as_majors_accumulate():
        one = fold_version("1.62.1", [_fragment("major")])
        two = fold_version("1.62.1", [_fragment("major"), _fragment("major")])
        assert parse_version(two) > parse_version(one)

    def it_keeps_minors_and_patches_after_several_majors():
        fragments = [_fragment("major"), _fragment("major"), _fragment("minor"), _fragment("patch")]
        assert fold_version("1.62.1", fragments) == "3.1.1"

    def it_refuses_a_base_that_is_not_a_version():
        with pytest.raises(ValueError, match="Not a MAJOR.MINOR.PATCH version"):
            fold_version("nope", [])


def describe_load_base():
    def it_reads_the_version(tmp_path: pathlib.Path):
        path = tmp_path / "base.json"
        path.write_text(json.dumps({"version": "1.62.1"}))
        assert load_base(path) == "1.62.1"

    def it_refuses_a_malformed_version(tmp_path: pathlib.Path):
        # Fail here, not eight lines later inside the fold.
        path = tmp_path / "base.json"
        path.write_text(json.dumps({"version": "1.62"}))
        with pytest.raises(ValueError, match="Not a MAJOR.MINOR.PATCH version"):
            load_base(path)

    def it_refuses_a_file_with_no_version_key(tmp_path: pathlib.Path):
        path = tmp_path / "base.json"
        path.write_text(json.dumps({}))
        with pytest.raises(KeyError):
            load_base(path)


def describe_load_history():
    def it_reads_the_frozen_entries(tmp_path: pathlib.Path):
        path = tmp_path / "history.json"
        path.write_text(json.dumps([{"version": "1.0.0", "date": "2026-01-01", "title": "T", "changes": []}]))
        assert load_history(path)[0]["version"] == "1.0.0"


def describe_compose_changelog():
    def it_puts_fragments_ahead_of_the_frozen_history():
        history = [{"version": "1.62.1", "date": "2026-09-13", "title": "Frozen", "changes": []}]
        composed = compose_changelog(history, [_fragment(date="2026-09-20")])
        assert composed[0]["title"] == "A thing members can see"
        assert composed[1]["title"] == "Frozen"

    def it_leaves_internal_fragments_out():
        # They moved the version and are not news.
        composed = compose_changelog([], [_fragment(audience="internal")])
        assert composed == []

    def it_keeps_the_frozen_history_when_nothing_is_pending():
        history = [{"version": "1.62.1", "date": "2026-09-13", "title": "Frozen", "changes": []}]
        assert compose_changelog(history, []) == history


def describe_the_repos_own_files():
    """The composed result the app actually imports, as opposed to a fixture."""

    def it_folds_to_the_version_the_app_reports():
        from plfog.version import BASE_VERSION, CHANGELOG, VERSION, FRAGMENTS_PATH

        assert VERSION == fold_version(BASE_VERSION, load_fragments(FRAGMENTS_PATH))
        assert CHANGELOG, "the composed changelog must never be empty"

    def it_keeps_the_history_newest_first():
        # CHANGELOG[0] is what the changelog modal shows first and what a forced Discord run
        # would re-post, so an out-of-order history is a wrong post to members.
        from plfog.version import HISTORY_PATH

        versions = [parse_version(str(entry["version"])) for entry in load_history(HISTORY_PATH)]
        assert versions == sorted(versions, reverse=True)

    def it_gives_every_frozen_entry_the_keys_the_renderers_read():
        from plfog.version import HISTORY_PATH

        for entry in load_history(HISTORY_PATH):
            assert {"version", "date", "title", "changes"} <= set(entry), entry

    def it_never_folds_below_a_release_that_already_shipped():
        """The invariant a stale snapshot breaks, and the only one that is not self-evident.

        ``changelog/history.json`` holds releases that are tagged, deployed and announced.
        If ``changelog/base.json`` is ever behind the newest of them — a branch cut before a
        merge, a sweep that moved one file and not the other — the fold produces a version
        BELOW what production is running: the footer, the changelog modal and the release
        email badge all drop under what members saw yesterday, and ``release_plan.py`` pushes
        a tag onto a commit newer than the tag above it. Nothing else in the machinery catches
        that; a tag collision is a no-op, not a regression.

        Caught for real on this PR's own rebase, when #392 shipped v1.63.0 while the branch
        was open and the snapshot still said 1.62.1.
        """
        from plfog.version import BASE_VERSION, HISTORY_PATH, VERSION

        newest_shipped = max(parse_version(str(e["version"])) for e in load_history(HISTORY_PATH))
        assert parse_version(BASE_VERSION) >= newest_shipped, (
            f"changelog/base.json is {BASE_VERSION}, behind the newest frozen release. "
            f"Rebase and move base.json forward."
        )
        assert parse_version(VERSION) >= newest_shipped

    def it_never_freezes_an_entry_at_a_version_above_the_base():
        # The other direction of the same mistake: a sweep that appended entries without
        # moving base.json leaves history claiming releases the fold cannot reach.
        from plfog.version import BASE_VERSION, HISTORY_PATH

        base = parse_version(BASE_VERSION)
        for entry in load_history(HISTORY_PATH):
            assert parse_version(str(entry["version"])) <= base, entry["version"]
