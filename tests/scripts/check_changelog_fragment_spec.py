"""BDD specs for ``.github/scripts/check_changelog_fragment.py``.

This is the gate the whole fragment design rests on: it is the only thing standing between a
PR with no fragment and a release nobody hears about, and it is what the repo accepted in
exchange for deleting ``release_guard.py`` and its 527-line spec. So it gets the same
treatment ``release_plan_spec.py`` gives the planner.

``_fake_diff`` asserts the exact argv git is called with and raises on anything else. The
argv *is* the check: ``{base}...{head}`` rather than ``{base}..{head}`` is what makes the
comparison against the merge base instead of against whatever else landed on main; without
``--diff-filter=AM`` a PR that only DELETED a fragment would satisfy the gate; and without the
pathspec any file at all would. A fake that ignored its arguments would pass against all three
of those mistakes.

The script lives under ``.github/scripts`` (not a Python package), so it is loaded from its
file path via ``importlib``, matching the other script specs.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / ".github" / "scripts" / "check_changelog_fragment.py"

_BASE = "a" * 40
_HEAD = "b" * 40


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_changelog_fragment", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Completed:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_diff(module: ModuleType, monkeypatch, result: _Completed) -> list[list[str]]:
    """Replace ``subprocess.run`` with a fake, recording the argv it was called with."""
    calls: list[list[str]] = []

    def _run(argv: list[str], **kwargs: object) -> _Completed:
        calls.append(argv)
        return result

    monkeypatch.setattr(subprocess, "run", _run)
    return calls


def _env(monkeypatch, *, labels: list[str] | None = None) -> None:
    monkeypatch.setenv("BASE_SHA", _BASE)
    monkeypatch.setenv("HEAD_SHA", _HEAD)
    monkeypatch.setenv("PR_NUMBER", "394")
    monkeypatch.setenv("TODAY", "2026-09-13")
    if labels is None:
        monkeypatch.delenv("PR_LABELS", raising=False)
    else:
        monkeypatch.setenv("PR_LABELS", json.dumps(labels))


def _workflow() -> str:
    """``.github/workflows/changelog.yml``, with whole-line comments stripped.

    The comment block above the trigger talks ABOUT `labeled` and about `no-changelog` at
    length, so an unfiltered substring check passes with the trigger itself deleted — the same
    trap ``release_guard_spec.py`` documented for ``fetch-depth``.
    """
    text = (pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "changelog.yml").read_text()
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def describe_the_workflow_wiring():
    """Where this particular gap lives: the script was right and nothing ran it.

    The script is thoroughly specced below, and was, while the `no-changelog` label it tells
    authors to reach for could not actually trigger it. ``ci.yml``'s bare ``pull_request:``
    defaults to ``[opened, synchronize, reopened]``; adding a label fires none of them, and
    re-running the failed job by hand replays the original payload, which still has no label.
    The only way past the gate was to push a commit — which is precisely what the README-typo
    PR the label exists for has nothing left to push.
    """

    def it_runs_the_check():
        assert "python .github/scripts/check_changelog_fragment.py" in _workflow()

    def it_fires_on_labeled_so_the_escape_hatch_works():
        assert "labeled" in _workflow()

    def it_fires_on_unlabeled_so_removing_the_label_restores_the_gate():
        # Otherwise a PR could take the label to go green, drop it, and merge unchecked.
        assert "unlabeled" in _workflow()

    def it_still_fires_on_the_ordinary_pull_request_events():
        workflow = _workflow()
        for event in ("opened", "synchronize", "reopened"):
            assert event in workflow, event

    def it_forwards_the_label_names_the_script_reads():
        assert "PR_LABELS:" in _workflow()

    def it_checks_out_enough_history_to_find_the_merge_base():
        # A shallow clone makes `{base}...{head}` unresolvable, and the diff would come back
        # empty — indistinguishable from "this PR added no fragment", failing every PR.
        assert "fetch-depth: 0" in _workflow()

    def it_does_not_live_in_ci_yml_where_a_label_cannot_reach_it():
        ci = (pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml").read_text()
        assert "check_changelog_fragment" not in ci


def describe_labels():
    def it_reads_the_label_names_the_workflow_forwards(monkeypatch):
        module = _load_script()
        monkeypatch.setenv("PR_LABELS", json.dumps(["bot-review", "no-changelog"]))
        assert module._labels() == {"bot-review", "no-changelog"}

    def it_is_empty_when_the_payload_carries_no_labels(monkeypatch):
        module = _load_script()
        monkeypatch.delenv("PR_LABELS", raising=False)
        assert module._labels() == set()

    def it_is_empty_for_an_empty_list(monkeypatch):
        module = _load_script()
        monkeypatch.setenv("PR_LABELS", "[]")
        assert module._labels() == set()

    def it_lowercases_and_trims(monkeypatch):
        # A label typed "No-Changelog" in the GitHub UI must still open the gate; an
        # exact-match check would silently fail the PR of whoever capitalised it.
        module = _load_script()
        monkeypatch.setenv("PR_LABELS", json.dumps([" No-Changelog "]))
        assert module._labels() == {"no-changelog"}


def describe_changed_fragments():
    def it_asks_git_for_the_merge_base_range_and_the_fragment_pathspec(monkeypatch):
        module = _load_script()
        calls = _fake_diff(module, monkeypatch, _Completed(0, ""))
        module._changed_fragments(_BASE, _HEAD)
        assert calls == [
            [
                "git",
                "diff",
                "--diff-filter=AM",
                "--name-only",
                f"{_BASE}...{_HEAD}",
                "--",
                module._PATHSPEC,
            ]
        ]

    def it_lists_the_fragments_the_branch_touched(monkeypatch):
        module = _load_script()
        _fake_diff(module, monkeypatch, _Completed(0, "changelog.d/394-a.toml\nchangelog.d/395-b.toml\n"))
        assert module._changed_fragments(_BASE, _HEAD) == [
            "changelog.d/394-a.toml",
            "changelog.d/395-b.toml",
        ]

    def it_returns_nothing_when_the_branch_touched_none(monkeypatch):
        module = _load_script()
        _fake_diff(module, monkeypatch, _Completed(0, "\n"))
        assert module._changed_fragments(_BASE, _HEAD) == []

    def it_exits_when_git_fails(monkeypatch):
        # Treating a failed diff as "no fragments" would fail every PR; treating it as "fine"
        # would pass every PR. Neither is an answer, so it exits.
        module = _load_script()
        _fake_diff(module, monkeypatch, _Completed(128, "", "fatal: bad object"))
        with pytest.raises(SystemExit, match="Could not diff"):
            module._changed_fragments(_BASE, _HEAD)


def describe_main():
    def it_passes_when_the_pr_adds_a_fragment(monkeypatch, capsys):
        module = _load_script()
        _env(monkeypatch, labels=[])
        _fake_diff(module, monkeypatch, _Completed(0, "changelog.d/394-a.toml\n"))
        module.main()
        assert "changelog.d/394-a.toml" in capsys.readouterr().out

    def it_reports_the_version_the_tree_folds_to(monkeypatch, capsys):
        module = _load_script()
        _env(monkeypatch, labels=[])
        _fake_diff(module, monkeypatch, _Completed(0, "changelog.d/394-a.toml\n"))
        module.main()
        assert f"folding to v{module.VERSION}" in capsys.readouterr().out

    def describe_when_the_pr_adds_nothing():
        def it_fails(monkeypatch):
            module = _load_script()
            _env(monkeypatch, labels=[])
            _fake_diff(module, monkeypatch, _Completed(0, ""))
            with pytest.raises(SystemExit) as exit_info:
                module.main()
            assert "No changelog fragment in this pull request" in str(exit_info.value)

        def it_shows_a_fragment_the_author_can_copy(monkeypatch):
            # The failure a contributor sees has to contain the fix, because the whole
            # convention is new and the error is most people's first encounter with it.
            module = _load_script()
            _env(monkeypatch, labels=[])
            _fake_diff(module, monkeypatch, _Completed(0, ""))
            with pytest.raises(SystemExit) as exit_info:
                module.main()
            message = str(exit_info.value)
            assert "changelog.d/394-short-slug.toml" in message
            assert 'date = "2026-09-13"' in message
            assert 'audience = "internal"' in message
            assert "no-changelog" in message

        def it_passes_when_the_no_changelog_label_is_present(monkeypatch, capsys):
            module = _load_script()
            _env(monkeypatch, labels=["no-changelog"])
            # No diff is faked: reaching git at all would mean the label short-circuit did
            # not happen, and subprocess.run would run a real command against the worktree.
            monkeypatch.setattr(
                subprocess, "run", lambda *a, **k: pytest.fail("git must not be consulted once the label is present")
            )
            module.main()
            assert "no-changelog' label present" in capsys.readouterr().out

    def describe_when_a_fragment_is_malformed():
        def it_fails_with_the_parse_error_and_points_at_the_readme(monkeypatch, tmp_path):
            # The fold happens at import of plfog.version on main, so a broken fragment that
            # slipped through would stop the app booting, not just the release workflow.
            module = _load_script()
            _env(monkeypatch, labels=[])
            (tmp_path / "1-broken.toml").write_text('bump = "sideways"\n')
            monkeypatch.setattr(module, "FRAGMENTS_PATH", tmp_path)
            with pytest.raises(SystemExit) as exit_info:
                module.main()
            message = str(exit_info.value)
            assert "Invalid changelog fragment" in message
            assert "'bump' must be one of" in message
            assert "changelog.d/README.md" in message
