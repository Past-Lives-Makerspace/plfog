"""BDD specs for ``.github/scripts/release_guard.py``.

The script lives under ``.github/scripts`` (not a Python package), so it is loaded from its
file path via ``importlib``, matching ``discord_release_notify_spec.py``.

``_fake_git`` asserts the exact ref of every git question. That strictness is the point: the
base the script asks about is the whole of what this change did, so a fake that ignored its
arguments would pass just as happily against ``_previous_version(after)``, against
``_version_py_changed(before, before)``, or against the ``HEAD^`` comparison this replaced.

The historical fixtures below are the four commits issue #364 names, plus the second real
fire. They carry the ``VERSION`` values as *data* rather than reading them back out of git:
CI checks the repo out shallow (``actions/checkout`` with the default ``fetch-depth: 1``),
so ``git show 11a1693d:plfog/version.py`` has nothing to read there. Re-derive any row with:

    git show <sha>:plfog/version.py | grep -E '^VERSION = '
    git diff --name-only <sha>^ <sha> -- plfog/version.py
"""

from __future__ import annotations

import importlib.util
import pathlib
from collections.abc import Callable
from types import ModuleType

import pytest

from plfog.version import VERSION

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / ".github" / "scripts" / "release_guard.py"

# Two shas that are not each other, so a spec can tell "the base" from "the tip". The fake
# raises on any ref it was not told to expect, so asking about the wrong one is a test failure.
_BASE = "a" * 40
_HEAD = "b" * 40
_NULL_SHA = "0" * 40


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_guard", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _version_py(version: str) -> str:
    return f'"""App version and changelog."""\n\nVERSION = "{version}"\n\nCHANGELOG = []\n'


def _squash(text: str) -> str:
    """Collapse all whitespace, so an assertion pins the words and not the line wrapping.

    Asserting a sentence with its hard newline in place makes a pure reflow — the same words,
    wrapped one word earlier — a test failure, while a maintainer reading the CI log sees no
    difference at all.
    """
    return " ".join(text.split())


def _uncommented(text: str) -> str:
    """Drop whole-line ``#`` comments, so a YAML assertion cannot be satisfied by prose.

    ``discord-notify.yml`` carries a long comment block that talks ABOUT ``fetch-depth`` and
    the ``paths:`` filter directly above the settings themselves, so an unfiltered substring
    check passes with the real setting deleted.
    """
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _fake_git(
    *,
    base: str = _BASE,
    base_readable: bool = True,
    base_source: str | None = None,
    diff_output: str = "",
) -> Callable[..., str | None]:
    """Stand in for ``_git``, answering only the exact questions the script should be asking.

    Anything else raises, which is what kills a mutation that swaps the base for the tip.
    """

    def fake(*args: str) -> str | None:
        if args == ("cat-file", "-e", f"{base}^{{commit}}"):
            return "" if base_readable else None
        if args == ("show", f"{base}:plfog/version.py"):
            return base_source
        if args == ("diff", "--name-only", base, _HEAD, "--", "plfog/version.py"):
            return diff_output
        raise AssertionError(f"git was asked the wrong question: {args}")

    return fake


def describe_release_guard():
    def describe_extract_version():
        def it_reads_the_version_literal():
            module = _load_script()
            assert module.extract_version(_version_py("1.54.1")) == "1.54.1"

        def it_returns_none_when_the_file_has_no_version_literal():
            module = _load_script()
            assert module.extract_version('"""No version here."""\n\nCHANGELOG = []\n') is None

        def it_ignores_a_version_that_is_not_at_the_start_of_a_line():
            # The decoys sit BELOW the real literal deliberately. Since extract_version takes
            # the LAST match, decoys above it would be shadowed by the real one and the spec
            # would pass with the ^ anchor dropped — which is the whole thing being tested.
            module = _load_script()
            source = 'VERSION = "1.0.0"\n# VERSION = "9.9.9" in a comment\n    VERSION = "8.8.8"\n'
            assert module.extract_version(source) == "1.0.0"

        def describe_when_two_top_level_assignments_are_present():
            def it_takes_the_last_one_as_python_would():
                # A stray duplicate above the real literal — a bad rebase resolution, a
                # copy-paste. discord_release_notify.py gets VERSION by importing the module,
                # so Python's binding is the only reading that keeps the guard and the
                # announcement talking about the same release.
                module = _load_script()
                source = 'VERSION = "9.9.9"\nCHANGELOG = []\nVERSION = "1.54.2"\n'
                assert module.extract_version(source) == "1.54.2"

    def describe_should_fail():
        def it_fires_when_version_py_moved_but_version_did_not():
            module = _load_script()
            assert module.should_fail(version_py_changed=True, previous="1.49.0", current="1.49.0") is True

        def it_passes_when_the_version_moved():
            module = _load_script()
            assert module.should_fail(version_py_changed=True, previous="1.54.0", current="1.54.1") is False

        def describe_when_version_py_was_not_touched():
            def it_passes_even_though_the_version_is_identical():
                # The workflow's paths: filter already means it does not run. The broader
                # predicate would have fired 41 times in the same history, almost all of
                # them deliberate batched releases.
                module = _load_script()
                assert module.should_fail(version_py_changed=False, previous="1.34.2", current="1.34.2") is False

        def describe_when_version_py_did_not_exist_at_the_base():
            def it_passes_rather_than_blocking_a_release_it_cannot_judge():
                module = _load_script()
                assert module.should_fail(version_py_changed=True, previous=None, current="1.54.1") is False

    def describe_against_real_pushes_to_main():
        # sha, what it was, version.py touched, VERSION before, VERSION after, must fail
        @pytest.mark.parametrize(
            ("sha", "what", "changed", "previous", "current", "expected"),
            [
                ("11a1693d", "#348 — the lobby slideshow, announced to nobody", True, "1.49.0", "1.49.0", True),
                ("920fab64", "#160 — the handwritten 0.23.39 changelog repair", True, "0.23.39", "0.23.39", True),
                ("2bdcf2ff", "#363 — a healthy release", True, "1.54.0", "1.54.1", False),
                ("f19bb6f8", "#322 — moved nothing, carried no entry", False, "1.34.2", "1.34.2", False),
                ("fc329683", "#349 — the cross-line re-stamp", True, "1.50.0", "1.51.0", False),
            ],
        )
        def it_matches_what_actually_happened(sha, what, changed, previous, current, expected):
            module = _load_script()
            actual = module.should_fail(version_py_changed=changed, previous=previous, current=current)
            assert actual is expected, f"{sha} ({what})"

    def describe_git():
        def it_returns_stdout_when_the_command_succeeds():
            module = _load_script()
            assert "git version" in (module._git("--version") or "")

        def it_returns_none_when_git_exits_non_zero():
            module = _load_script()
            assert module._git("cat-file", "-e", f"{_NULL_SHA}^{{commit}}") is None

        def it_runs_in_the_repo_root_not_the_working_directory(monkeypatch, tmp_path):
            # tmp_path is not a git repository, so an unpinned cwd would make every git call
            # fail — which is precisely the state in which the guard silently passes. The chdir
            # MUST precede the load: _REPO_ROOT binds at module exec, so loading first would
            # let a cwd-derived implementation pass.
            monkeypatch.chdir(tmp_path)
            module = _load_script()
            assert module._git("rev-parse", "--show-toplevel") is not None

        def it_does_not_swallow_a_missing_git(monkeypatch):
            # A broken guard must crash the step, not degrade into a pass.
            module = _load_script()

            def boom(*args, **kwargs):
                raise OSError("no git on PATH")

            monkeypatch.setattr(module.subprocess, "run", boom)
            with pytest.raises(OSError):
                module._git("--version")

    def describe_current_version():
        def it_agrees_with_the_version_the_app_imports():
            module = _load_script()
            assert module._current_version() == VERSION

        def it_resolves_from_the_script_location_not_the_working_directory(monkeypatch, tmp_path):
            # chdir before the load, for the reason given in describe_git above.
            monkeypatch.chdir(tmp_path)
            module = _load_script()
            assert module._current_version() == VERSION

        def it_exits_when_the_version_literal_cannot_be_read(monkeypatch):
            module = _load_script()
            monkeypatch.setattr(module, "extract_version", lambda source: None)
            with pytest.raises(SystemExit) as exit_info:
                module._current_version()
            assert "Could not read VERSION" in str(exit_info.value)

    def describe_names_a_commit():
        def it_accepts_a_full_sha():
            module = _load_script()
            assert module._names_a_commit(_BASE) is True

        def it_rejects_the_all_zero_sha_a_branch_creation_pushes():
            module = _load_script()
            assert module._names_a_commit(_NULL_SHA) is False

        def it_rejects_an_empty_base():
            module = _load_script()
            assert module._names_a_commit("") is False

        def it_rejects_forty_characters_that_are_not_hex():
            module = _load_script()
            assert module._names_a_commit("z" * 40) is False

        def it_rejects_an_uppercase_sha():
            # git never hands one back, but accepting it here would mean accepting anything.
            module = _load_script()
            assert module._names_a_commit("A" * 40) is False

        def it_rejects_a_sha_with_trailing_characters():
            # fullmatch, so a longer string cannot match on its first 40 characters.
            module = _load_script()
            assert module._names_a_commit(_BASE + "extra") is False

        def it_rejects_a_sha_with_leading_characters():
            # The other half of fullmatch. Under the old anchored-match pair, `^` and .match
            # each covered for the other, so neither could be killed alone and dropping both
            # accepted this string.
            module = _load_script()
            assert module._names_a_commit("junk" + _BASE) is False

        def it_rejects_an_abbreviated_sha():
            # An abbreviated ref would make `git show :plfog/version.py` read the INDEX when
            # the base is empty, reporting a fabricated previous version.
            module = _load_script()
            assert module._names_a_commit("11a1693d") is False

    def describe_require_readable_base():
        def it_says_nothing_when_the_base_is_in_the_clone(monkeypatch):
            module = _load_script()
            monkeypatch.setattr(module, "_git", _fake_git())
            assert module._require_readable_base(_BASE) is None

        def describe_when_the_base_is_missing_from_the_clone():
            @pytest.fixture
            def failure(monkeypatch):
                module = _load_script()
                monkeypatch.setattr(module, "_git", _fake_git(base_readable=False))
                with pytest.raises(SystemExit) as exit_info:
                    module._require_readable_base(_BASE)
                return str(exit_info.value)

            def it_fails_the_run_rather_than_passing_blind(failure):
                # The hole that would otherwise swallow the guard: an unreachable base makes
                # previous unknown AND changed false, so #348 would sail through green.
                assert "is not in this clone" in failure

            def it_names_the_base_and_the_setting_that_went_wrong(failure):
                assert _BASE in failure
                assert "fetch-depth: 0" in failure

            def it_names_the_force_push_cause_too(failure):
                # On that path fetch-depth is already correct, so sending the maintainer to
                # audit it wastes the one moment they are paying attention.
                assert "force-pushed" in failure
                assert "no checkout depth can reach" in _squash(failure)

            def it_does_not_promise_that_a_manual_run_decides_anything(failure):
                # It does not. With FORCE_ANNOUNCE a manual run posts the entries at the
                # current VERSION, or the NEWEST entry when there are none, either way
                # unconditionally. Telling the maintainer it "will announce it if it needs
                # announcing" invents a safety check and re-announces a shipped release.
                assert "Do NOT reach straight for" in failure
                assert "does not decide whether an announcement is owed" in _squash(failure)
                assert "if it needs announcing" not in failure

    def describe_previous_version():
        def it_reads_the_blob_at_the_base_commit(monkeypatch):
            module = _load_script()
            monkeypatch.setattr(module, "_git", _fake_git(base_source=_version_py("1.53.9")))
            assert module._previous_version(_BASE) == "1.53.9"

        def it_returns_none_when_the_file_did_not_exist_there(monkeypatch):
            module = _load_script()
            monkeypatch.setattr(module, "_git", _fake_git(base_source=None))
            assert module._previous_version(_BASE) is None

    def describe_version_py_changed():
        def it_is_true_when_git_names_the_file(monkeypatch):
            module = _load_script()
            monkeypatch.setattr(module, "_git", _fake_git(diff_output="plfog/version.py\n"))
            assert module._version_py_changed(_BASE, _HEAD) is True

        def it_is_false_when_the_diff_is_empty(monkeypatch):
            module = _load_script()
            monkeypatch.setattr(module, "_git", _fake_git(diff_output=""))
            assert module._version_py_changed(_BASE, _HEAD) is False

        def it_is_false_when_the_diff_is_only_whitespace(monkeypatch):
            # git prints a bare newline often enough that a truthiness test on raw stdout
            # would read "no files changed" as "changed".
            module = _load_script()
            monkeypatch.setattr(module, "_git", _fake_git(diff_output="  \n"))
            assert module._version_py_changed(_BASE, _HEAD) is False

        def it_fails_the_run_when_git_cannot_diff(monkeypatch):
            module = _load_script()
            monkeypatch.setattr(module, "_git", lambda *args: None)
            with pytest.raises(SystemExit) as exit_info:
                module._version_py_changed(_BASE, _HEAD)
            assert "could not diff" in str(exit_info.value)

    def describe_write_output():
        def it_appends_a_step_output(monkeypatch, tmp_path):
            module = _load_script()
            output = tmp_path / "github_output"
            monkeypatch.setenv("GITHUB_OUTPUT", str(output))
            module._write_output("should_post", "true")
            module._write_output("other", "value")
            assert output.read_text() == "should_post=true\nother=value\n"

    def describe_the_workflow_that_runs_it():
        # _require_readable_base exists because these settings can silently regress, and its
        # error text names one of them. Asserted on the raw text rather than parsed: PyYAML is
        # not a declared dependency of this repo, only a transitive one.
        #
        # Every assertion here runs against the COMMENT-STRIPPED text. The file's own comment
        # block discusses fetch-depth and the paths: filter directly above the settings, so a
        # whole-file substring check passes with the setting itself deleted.
        @pytest.fixture
        def workflow() -> str:
            path = _SCRIPT.parents[1] / "workflows" / "discord-notify.yml"
            return _uncommented(path.read_text(encoding="utf-8"))

        def it_checks_out_the_full_history(workflow):
            # Anything shallower and _require_readable_base fires on every rebase-merge push,
            # turning healthy releases red and blaming a setting nothing had checked.
            assert "fetch-depth: 0" in workflow

        def it_only_runs_on_pushes_to_main(workflow):
            assert "branches: [main]" in workflow

        def it_runs_this_guard_as_its_own_step(workflow):
            # The whole line, not a prefix: `|| true` appended to it would swallow the exit
            # code, and a red X on the Actions tab is documented as the entire alert.
            assert "\n        run: python .github/scripts/release_guard.py\n" in workflow

        def it_does_not_let_the_guard_fail_softly(workflow):
            assert "continue-on-error" not in workflow
            assert "if: false" not in workflow

        def it_still_filters_on_version_py(workflow):
            # The paths: filter is what keeps the guard off the 41 commits it must not judge.
            # Asserted with its key attached: `paths-ignore:` leaves the list item untouched
            # and inverts the trigger, so the guard would never judge a release again.
            assert 'paths:\n      - "plfog/version.py"' in workflow
            assert "paths-ignore" not in workflow

        def it_arms_the_post_step_from_the_guard_output(workflow):
            # should_post is the only thing connecting release_guard.py to any observable
            # behaviour. A typo in this expression silently stops every announcement.
            assert "if: steps.version_changed.outputs.should_post == 'true'" in workflow
            assert "id: version_changed" in workflow

        def it_hands_the_guard_the_push_base_and_not_a_pull_request_base(workflow):
            # github.event.before is the tip of main before the push. A pull_request
            # expression is EMPTY on a push event, and the script then reports "no push base"
            # and announces, which is #348 restored behind a guard that still looks present.
            assert "BEFORE_SHA: ${{ github.event.before }}" in workflow
            assert "AFTER_SHA: ${{ github.sha }}" in workflow
            assert "EVENT_NAME: ${{ github.event_name }}" in workflow

    def describe_main():
        @pytest.fixture
        def output(monkeypatch, tmp_path):
            path = tmp_path / "github_output"
            path.touch()
            monkeypatch.setenv("GITHUB_OUTPUT", str(path))
            return path

        def describe_on_a_manual_run():
            def it_posts_without_consulting_git(monkeypatch, output):
                # workflow_dispatch is the documented recovery lever for a missed
                # announcement. The guard must never stand in its way.
                module = _load_script()
                monkeypatch.setenv("EVENT_NAME", "workflow_dispatch")
                monkeypatch.setattr(module, "_git", lambda *args: pytest.fail("git must not run on a manual run"))
                module.main()
                assert output.read_text() == "should_post=true\n"

        def describe_on_a_push_that_moved_the_version():
            def it_lets_the_announcement_through(monkeypatch, output):
                module = _load_script()
                monkeypatch.setenv("EVENT_NAME", "push")
                monkeypatch.setenv("BEFORE_SHA", _BASE)
                monkeypatch.setenv("AFTER_SHA", _HEAD)
                monkeypatch.setattr(module, "_current_version", lambda: "1.54.1")
                monkeypatch.setattr(
                    module,
                    "_git",
                    _fake_git(base_source=_version_py("1.54.0"), diff_output="plfog/version.py\n"),
                )
                module.main()
                assert output.read_text() == "should_post=true\n"

        def describe_on_a_push_carrying_several_commits():
            def it_compares_against_the_push_base_not_the_tips_parent(monkeypatch, output):
                # The rebase-merge shape this change exists for. HEAD^ is a commit from
                # inside the PR that already carries the bump, so comparing against it would
                # see previous == current and red-X a perfectly good release. The fake
                # raises on any ref but _BASE, so "it did not ask about HEAD^" is actually asserted.
                module = _load_script()
                monkeypatch.setenv("EVENT_NAME", "push")
                monkeypatch.setenv("BEFORE_SHA", _BASE)
                monkeypatch.setenv("AFTER_SHA", _HEAD)
                monkeypatch.setattr(module, "_current_version", lambda: "1.55.0")
                monkeypatch.setattr(
                    module,
                    "_git",
                    _fake_git(base_source=_version_py("1.54.2"), diff_output="plfog/version.py\n"),
                )
                module.main()
                assert output.read_text() == "should_post=true\n"

        def describe_on_a_push_that_left_the_version_alone():
            @pytest.fixture
            def failure(monkeypatch, output, capsys):
                module = _load_script()
                monkeypatch.setenv("EVENT_NAME", "push")
                monkeypatch.setenv("BEFORE_SHA", _BASE)
                monkeypatch.setenv("AFTER_SHA", _HEAD)
                monkeypatch.setattr(module, "_current_version", lambda: "1.49.0")
                monkeypatch.setattr(
                    module,
                    "_git",
                    _fake_git(base_source=_version_py("1.49.0"), diff_output="plfog/version.py\n"),
                )
                with pytest.raises(SystemExit) as exit_info:
                    module.main()
                return str(exit_info.value), output, capsys.readouterr().out

            def it_fails_the_run(failure):
                message, _, _ = failure
                assert "Release guard" in message

            def it_names_the_commit_and_both_versions(failure):
                message, _, _ = failure
                assert _HEAD in message
                assert "it was 1.49.0 before this push" in message
                assert "left VERSION at 1.49.0" in message

            def it_asks_whether_the_entry_EXISTED_rather_than_who_touched_it(failure):
                # Two weaker questions were tried and both re-post an announced release.
                # "Does the entry read well" is satisfied by the PREVIOUS release's entry.
                # "Did this push write it" is satisfied by a typo fix to that same entry,
                # which touched it without making it new. Only existence at the base sorts
                # the shapes, so that is the command the message hands over.
                message, _, _ = failure
                assert f"git show {_BASE}:plfog/version.py" in message
                assert 'grep \'"version": "1.49.0"\'' in message

            def it_gives_the_one_command_when_nothing_was_there_before(failure):
                message, _, _ = failure
                assert "NO MATCH means this push wrote that entry" in message
                assert "gh workflow run discord-notify.yml" in message

            def it_warns_that_re_posting_an_older_entry_announces_the_wrong_release(failure):
                message, _, _ = failure
                assert "belongs to the release before this one" in message
                assert "wrong release" in message

            def it_says_a_rewording_does_not_make_the_entry_new(failure):
                # The defect this replaced: "ADDED or REWROTE" routed a typo fix on an
                # already-announced entry into the unconditional re-announce branch.
                message, _, _ = failure
                assert "whatever this push did to its wording" in _squash(message)

            def it_offers_the_no_entry_answer_for_a_release_members_never_saw(failure):
                # The guard fires on ANY version.py edit that leaves VERSION alone, including
                # a comment-block edit. Ordering an entry there would invent one for a release
                # members never saw, which the repo's own changelog rule forbids.
                message, _, _ = failure
                assert "shipped nothing a member would notice, nothing is owed" in _squash(message)
                assert "with no entry" in _squash(message)

            def it_says_the_new_entry_goes_at_the_new_version(failure):
                # Bumping VERSION but stamping the entry at the old number announces nothing
                # and goes green, which is the miss this guard cannot see. Squashed, because
                # the assertion is about the words, not where the paragraph happens to wrap.
                message, _, _ = failure
                assert "stamps the new entry at the NEW number" in _squash(message)
                assert "Stamping it at 1.49.0 instead" in _squash(message)

            def it_warns_that_announce_release_would_double_post(failure):
                # announce_release emits release.published, which is registered on the
                # Discord channel as well as email, so running both announces twice.
                message, _, _ = failure
                assert "announce_release" in message
                assert "twice" in message

            def it_does_not_arm_the_post_step(failure):
                _, output, _ = failure
                assert output.read_text() == ""

            def it_emits_a_github_error_annotation(failure):
                # The annotation is what surfaces the reason on the Actions summary rather
                # than only inside the step log.
                _, _, stdout = failure
                assert "::error title=Release shipped without an announcement::" in stdout

        def describe_when_the_push_created_the_branch():
            def it_announces_because_there_is_no_previous_release(monkeypatch, output):
                module = _load_script()
                monkeypatch.setenv("EVENT_NAME", "push")
                monkeypatch.setenv("BEFORE_SHA", _NULL_SHA)
                monkeypatch.setenv("AFTER_SHA", _HEAD)
                monkeypatch.setattr(module, "_current_version", lambda: "1.54.1")
                monkeypatch.setattr(module, "_git", lambda *args: pytest.fail("git must not run with no base"))
                module.main()
                assert output.read_text() == "should_post=true\n"

        def describe_when_the_push_base_is_missing_from_the_clone():
            def it_fails_the_run_instead_of_passing_blind(monkeypatch, output):
                # The regression this guards against: a shallower checkout makes every git
                # question unanswerable, and the pre-fix code answered "all clear".
                module = _load_script()
                monkeypatch.setenv("EVENT_NAME", "push")
                monkeypatch.setenv("BEFORE_SHA", _BASE)
                monkeypatch.setenv("AFTER_SHA", _HEAD)
                monkeypatch.setattr(module, "_current_version", lambda: "1.49.0")
                monkeypatch.setattr(module, "_git", _fake_git(base_readable=False))
                with pytest.raises(SystemExit) as exit_info:
                    module.main()
                assert "is not in this clone" in str(exit_info.value)
                assert output.read_text() == ""
