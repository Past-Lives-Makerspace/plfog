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
            # The literal is anchored, so a mention inside a comment or a nested assignment
            # cannot be mistaken for the real one.
            module = _load_script()
            source = '# VERSION = "9.9.9" in a comment\n    VERSION = "8.8.8"\nVERSION = "1.0.0"\n'
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
            # fail — which is precisely the state in which the guard silently passes.
            module = _load_script()
            monkeypatch.chdir(tmp_path)
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
            module = _load_script()
            monkeypatch.chdir(tmp_path)
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
            def failure(monkeypatch, output):
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
                return str(exit_info.value), output

            def it_fails_the_run(failure):
                message, _ = failure
                assert "Release guard" in message

            def it_names_the_commit_and_both_versions(failure):
                message, _ = failure
                assert _HEAD in message
                assert "it was 1.49.0 before this push" in message
                assert "left VERSION at 1.49.0" in message

            def it_gives_the_one_command_that_is_the_whole_fix(failure):
                message, _ = failure
                assert "gh workflow run discord-notify.yml" in message

            def it_warns_that_editing_the_entry_without_bumping_fires_the_guard_again(failure):
                message, _ = failure
                assert "without bumping VERSION fails this guard again" in message

            def it_warns_that_announce_release_would_double_post(failure):
                # announce_release emits release.published, which is registered on the
                # Discord channel as well as email, so running both announces twice.
                message, _ = failure
                assert "announce_release" in message
                assert "twice" in message

            def it_does_not_arm_the_post_step(failure):
                _, output = failure
                assert output.read_text() == ""

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
