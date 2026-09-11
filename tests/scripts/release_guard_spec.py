"""BDD specs for ``.github/scripts/release_guard.py``.

The script lives under ``.github/scripts`` (not a Python package), so it is loaded from its
file path via ``importlib``, matching ``discord_release_notify_spec.py``.

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
import subprocess
from collections.abc import Callable
from types import ModuleType

import pytest

from plfog.version import VERSION

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / ".github" / "scripts" / "release_guard.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_guard", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_git(*, previous_source: str | None, changed: bool) -> Callable[..., str | None]:
    """Stand in for ``_git``, so ``_previous_version`` and ``_version_py_changed`` run for real."""

    def fake(*args: str) -> str | None:
        if args[0] == "show":
            return previous_source
        if args[0] == "diff":
            return "plfog/version.py\n" if changed else ""
        raise AssertionError(f"unexpected git call: {args}")

    return fake


def _version_py(version: str) -> str:
    return f'"""App version and changelog."""\n\nVERSION = "{version}"\n\nCHANGELOG = []\n'


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

        def describe_when_the_previous_version_is_unknown():
            def it_passes_rather_than_blocking_a_release_it_cannot_judge():
                module = _load_script()
                assert module.should_fail(version_py_changed=True, previous=None, current="1.54.1") is False

    def describe_against_real_pushes_to_main():
        # sha, what it was, version.py touched, VERSION before, VERSION after, must fail
        @pytest.mark.parametrize(
            ("sha", "what", "changed", "previous", "current", "expected"),
            [
                ("11a1693d", "#348 — the lobby slideshow, announced to nobody", True, "1.49.0", "1.49.0", True),
                ("920fab64", "#160 — the hand-written 0.23.39 changelog repair", True, "0.23.39", "0.23.39", True),
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
            assert module._git("cat-file", "-p", "0000000000000000000000000000000000000000") is None

        def it_returns_none_when_git_cannot_be_run(monkeypatch):
            module = _load_script()

            def boom(*args, **kwargs):
                raise OSError("no git on PATH")

            monkeypatch.setattr(subprocess, "run", boom)
            assert module._git("--version") is None

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

    def describe_previous_version():
        def it_parses_the_blob_git_hands_back(monkeypatch):
            module = _load_script()
            monkeypatch.setattr(module, "_git", _fake_git(previous_source=_version_py("1.53.9"), changed=True))
            assert module._previous_version("abc1234") == "1.53.9"

        def it_returns_none_when_the_blob_is_unreadable(monkeypatch):
            module = _load_script()
            monkeypatch.setattr(module, "_git", _fake_git(previous_source=None, changed=True))
            assert module._previous_version("0000000") is None

    def describe_version_py_changed():
        def it_is_true_when_git_names_the_file(monkeypatch):
            module = _load_script()
            monkeypatch.setattr(module, "_git", _fake_git(previous_source=None, changed=True))
            assert module._version_py_changed("abc", "def") is True

        def it_is_false_when_the_diff_is_empty(monkeypatch):
            module = _load_script()
            monkeypatch.setattr(module, "_git", _fake_git(previous_source=None, changed=False))
            assert module._version_py_changed("abc", "def") is False

        def it_is_false_when_git_cannot_answer(monkeypatch):
            module = _load_script()
            monkeypatch.setattr(module, "_git", lambda *args: None)
            assert module._version_py_changed("abc", "def") is False

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
                monkeypatch.setenv("BEFORE_SHA", "f75b4c25")
                monkeypatch.setenv("AFTER_SHA", "2bdcf2ff")
                monkeypatch.setattr(module, "_current_version", lambda: "1.54.1")
                monkeypatch.setattr(module, "_git", _fake_git(previous_source=_version_py("1.54.0"), changed=True))
                module.main()
                assert output.read_text() == "should_post=true\n"

        def describe_on_a_push_that_left_the_version_alone():
            @pytest.fixture
            def failure(monkeypatch, output):
                module = _load_script()
                monkeypatch.setenv("EVENT_NAME", "push")
                monkeypatch.setenv("BEFORE_SHA", "d4ee1e73b095ee04f395ddd972af02a9a30c107c")
                monkeypatch.setenv("AFTER_SHA", "11a1693d")
                monkeypatch.setattr(module, "_current_version", lambda: "1.49.0")
                monkeypatch.setattr(module, "_git", _fake_git(previous_source=_version_py("1.49.0"), changed=True))
                with pytest.raises(SystemExit) as exit_info:
                    module.main()
                return str(exit_info.value), output

            def it_fails_the_run(failure):
                message, _ = failure
                assert message  # sys.exit with a message is a non-zero exit
                assert "Release guard" in message

            def it_names_the_commit_and_both_versions(failure):
                message, _ = failure
                assert "11a1693d" in message
                assert "it was 1.49.0 before this push" in message
                assert "left VERSION at 1.49.0" in message

            def it_names_both_recovery_commands(failure):
                message, _ = failure
                assert "gh workflow run discord-notify.yml" in message
                assert "announce_release" in message

            def it_says_how_to_clear_the_announce_release_dedupe_row(failure):
                message, _ = failure
                assert "EventDelivery" in message
                assert 'period="release:1.49.0"' in message

            def it_does_not_arm_the_post_step(failure):
                _, output = failure
                assert output.read_text() == ""

        def describe_when_the_push_base_is_unreachable():
            def it_announces_rather_than_failing_on_a_question_it_cannot_answer(monkeypatch, output):
                module = _load_script()
                monkeypatch.setenv("EVENT_NAME", "push")
                monkeypatch.setenv("BEFORE_SHA", "0000000000000000000000000000000000000000")
                monkeypatch.setenv("AFTER_SHA", "2bdcf2ff")
                monkeypatch.setattr(module, "_current_version", lambda: "1.54.1")
                monkeypatch.setattr(module, "_git", _fake_git(previous_source=None, changed=False))
                module.main()
                assert output.read_text() == "should_post=true\n"
