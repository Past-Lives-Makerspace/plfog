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
import urllib.error
import urllib.request
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / ".github" / "scripts" / "check_changelog_fragment.py"

_BASE = "a" * 40
_HEAD = "b" * 40

#: A member-facing fragment up to the two #1007 keys a spec appends.
_FRAGMENT_PREFIX = 'bump = "minor"\ndate = "2026-10-29"\ntitle = "A guided thing"\nchanges = ["It does the thing."]\n'
#: One ``[[shots]]`` block (#1050): a fragment at ``shots`` or ``tour`` must list at least one.
_SHOT = '\n[[shots]]\ncaption = "Open it."\n'
#: A ``shots`` fragment with two pictures, the shape the HEAD check walks.
_TWO_SHOTS = (
    _FRAGMENT_PREFIX + 'feature = "home.whats-new"\nguidance = "shots"\n'
    '\n[[shots]]\ncaption = "One."\n\n[[shots]]\ncaption = "Two."\n'
)


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


def _fake_diff(monkeypatch, result: _Completed) -> list[list[str]]:
    """Replace ``subprocess.run`` with a fake, recording the argv it was called with.

    Patches ``subprocess.run`` where the script imported it, so it needs no handle on the
    loaded module.
    """
    calls: list[list[str]] = []

    def _run(argv: list[str], **kwargs: object) -> _Completed:
        calls.append(argv)
        return result

    monkeypatch.setattr(subprocess, "run", _run)
    return calls


def _env(monkeypatch, *, labels: list[str] | None = None, public_url: str | None = None, ci: bool = False) -> None:
    monkeypatch.setenv("BASE_SHA", _BASE)
    monkeypatch.setenv("HEAD_SHA", _HEAD)
    monkeypatch.setenv("PR_NUMBER", "394")
    monkeypatch.setenv("TODAY", "2026-09-13")
    # Unset unless a spec hands one over: the HEAD step then skips, as on a local run. The suite
    # itself runs in Actions, so GITHUB_ACTIONS is cleared unless a spec asks for CI's branch.
    if public_url is None:
        monkeypatch.delenv("R2_PUBLIC_URL", raising=False)
    else:
        monkeypatch.setenv("R2_PUBLIC_URL", public_url)
    if ci:
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
    else:
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
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

    def it_hands_the_check_the_buckets_public_url_from_a_repository_variable():
        # A public address, so a variable rather than a secret (#1050); a fork has none and the check says so.
        assert "R2_PUBLIC_URL: ${{ vars.R2_PUBLIC_URL }}" in _workflow()


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
        calls = _fake_diff(monkeypatch, _Completed(0, ""))
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
        _fake_diff(monkeypatch, _Completed(0, "changelog.d/394-a.toml\nchangelog.d/395-b.toml\n"))
        assert module._changed_fragments(_BASE, _HEAD) == [
            "changelog.d/394-a.toml",
            "changelog.d/395-b.toml",
        ]

    def it_returns_nothing_when_the_branch_touched_none(monkeypatch):
        module = _load_script()
        _fake_diff(monkeypatch, _Completed(0, "\n"))
        assert module._changed_fragments(_BASE, _HEAD) == []

    def it_exits_when_git_fails(monkeypatch):
        # Treating a failed diff as "no fragments" would fail every PR; treating it as "fine"
        # would pass every PR. Neither is an answer, so it exits.
        module = _load_script()
        _fake_diff(monkeypatch, _Completed(128, "", "fatal: bad object"))
        with pytest.raises(SystemExit, match="Could not diff"):
            module._changed_fragments(_BASE, _HEAD)


def describe_main():
    def it_passes_when_the_pr_adds_a_fragment(monkeypatch, capsys):
        module = _load_script()
        _env(monkeypatch, labels=[])
        _fake_diff(monkeypatch, _Completed(0, "changelog.d/394-a.toml\n"))
        module.main()
        assert "changelog.d/394-a.toml" in capsys.readouterr().out

    def it_reports_the_version_the_tree_folds_to(monkeypatch, capsys):
        module = _load_script()
        _env(monkeypatch, labels=[])
        _fake_diff(monkeypatch, _Completed(0, "changelog.d/394-a.toml\n"))
        module.main()
        assert f"folding to v{module.VERSION}" in capsys.readouterr().out

    def describe_when_the_pr_adds_nothing():
        def it_fails(monkeypatch):
            module = _load_script()
            _env(monkeypatch, labels=[])
            _fake_diff(monkeypatch, _Completed(0, ""))
            with pytest.raises(SystemExit) as exit_info:
                module.main()
            assert "No changelog fragment in this pull request" in str(exit_info.value)

        def it_shows_a_fragment_the_author_can_copy(monkeypatch):
            # The failure a contributor sees has to contain the fix, because the whole
            # convention is new and the error is most people's first encounter with it.
            module = _load_script()
            _env(monkeypatch, labels=[])
            _fake_diff(monkeypatch, _Completed(0, ""))
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

    def describe_when_a_fragment_promises_materials_its_feature_lacks():
        """``guidance = "tour"`` needs a registered tour (#1007); ``clip`` is no level since #1050."""

        def it_fails_the_retired_clip_level_naming_shots(monkeypatch, tmp_path):
            # The clips of #1008 were never built; a fragment that still says clip is told the level is shots.
            module = _load_script()
            _env(monkeypatch, labels=[])
            (tmp_path / "1-guided.toml").write_text(_FRAGMENT_PREFIX + 'feature = "guild.cubbies"\nguidance = "clip"\n')
            monkeypatch.setattr(module, "FRAGMENTS_PATH", tmp_path)
            with pytest.raises(SystemExit) as exit_info:
                module.main()
            message = str(exit_info.value)
            assert "Invalid changelog fragment" in message
            assert "'guidance' must be one of none, link, shots, tour (got 'clip')" in message

        def it_fails_a_tour_on_a_feature_with_none(monkeypatch, tmp_path):
            module = _load_script()
            _env(monkeypatch, labels=[])
            _fake_diff(monkeypatch, _Completed(0, "changelog.d/1-guided.toml\n"))
            (tmp_path / "1-guided.toml").write_text(
                _FRAGMENT_PREFIX + 'feature = "guild.cubbies"\nguidance = "tour"\n' + _SHOT
            )
            monkeypatch.setattr(module, "FRAGMENTS_PATH", tmp_path)
            with pytest.raises(SystemExit) as exit_info:
                module.main()
            assert "'guild.cubbies' has no tour registered" in str(exit_info.value)

        def it_passes_a_tour_the_feature_registers(monkeypatch, tmp_path, capsys):
            module = _load_script()
            _env(monkeypatch, labels=[])
            _fake_diff(monkeypatch, _Completed(0, "changelog.d/1-guided.toml\n"))
            (tmp_path / "1-guided.toml").write_text(
                _FRAGMENT_PREFIX + 'feature = "classes.teach"\nguidance = "tour"\n' + _SHOT
            )
            monkeypatch.setattr(module, "FRAGMENTS_PATH", tmp_path)
            module.main()
            assert "changelog.d/1-guided.toml" in capsys.readouterr().out

        def it_passes_a_link_which_needs_no_materials(monkeypatch, tmp_path, capsys):
            module = _load_script()
            _env(monkeypatch, labels=[])
            _fake_diff(monkeypatch, _Completed(0, "changelog.d/1-guided.toml\n"))
            (tmp_path / "1-guided.toml").write_text(_FRAGMENT_PREFIX + 'feature = "guild.cubbies"\nguidance = "link"\n')
            monkeypatch.setattr(module, "FRAGMENTS_PATH", tmp_path)
            module.main()
            assert "folding to" in capsys.readouterr().out

        def it_names_every_offending_fragment_at_once(monkeypatch, tmp_path):
            module = _load_script()
            _env(monkeypatch, labels=[])
            _fake_diff(monkeypatch, _Completed(0, "changelog.d/1-a.toml\n"))
            (tmp_path / "1-a.toml").write_text(
                _FRAGMENT_PREFIX + 'feature = "guild.cubbies"\nguidance = "tour"\n' + _SHOT
            )
            (tmp_path / "2-b.toml").write_text(_FRAGMENT_PREFIX + 'feature = "guild.page"\nguidance = "tour"\n' + _SHOT)
            monkeypatch.setattr(module, "FRAGMENTS_PATH", tmp_path)
            with pytest.raises(SystemExit) as exit_info:
                module.main()
            message = str(exit_info.value)
            assert "changelog.d/1-a.toml" in message and "changelog.d/2-b.toml" in message

    def describe_when_a_fragment_lists_shots():
        """Every shot of a ``shots`` or ``tour`` fragment is HEADed on the bucket (#1050), once CI hands over the public URL."""

        _ONE = "https://pub.example/guidance/1050-guidance-shots/step-1.png"
        _TWO = "https://pub.example/guidance/1050-guidance-shots/step-2.png"

        def _heads(monkeypatch, statuses: dict[str, int]) -> list[str]:
            """Answer ``urllib.request.urlopen`` from ``statuses`` by URL, recording each HEAD; a 4xx is the HTTPError urllib raises."""
            seen: list[str] = []

            class _Response:
                def __init__(self, status: int) -> None:
                    self.status = status

                def __enter__(self) -> "_Response":
                    return self

                def __exit__(self, *exc: object) -> bool:
                    return False

            def _urlopen(request, timeout=None):
                assert request.get_method() == "HEAD"
                # Python's default agent is answered 403 by the bucket's bot filter; the check names itself.
                assert request.get_header("User-agent", "").startswith("Mozilla/5.0 (compatible; plfog-fragment-check")
                seen.append(request.full_url)
                status = statuses[request.full_url]
                if status >= 400:
                    raise urllib.error.HTTPError(request.full_url, status, "nope", {}, None)  # type: ignore[arg-type]
                return _Response(status)

            monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
            return seen

        def _tree(monkeypatch, tmp_path, text: str) -> ModuleType:
            module = _load_script()
            _fake_diff(monkeypatch, _Completed(0, "changelog.d/1050-guidance-shots.toml\n"))
            (tmp_path / "1050-guidance-shots.toml").write_text(text)
            monkeypatch.setattr(module, "FRAGMENTS_PATH", tmp_path)
            return module

        def it_fails_naming_the_first_picture_the_bucket_does_not_have(monkeypatch, tmp_path):
            module = _tree(monkeypatch, tmp_path, _TWO_SHOTS)
            _env(monkeypatch, labels=[], public_url="https://pub.example/")
            seen = _heads(monkeypatch, {_ONE: 200, _TWO: 404})
            with pytest.raises(SystemExit) as exit_info:
                module.main()
            message = str(exit_info.value)
            assert "A fragment promises shots that are not on the bucket" in message
            assert f"changelog.d/1050-guidance-shots.toml: {_TWO} answered 404" in message
            assert "--guidance 1050-guidance-shots" in message
            assert seen == [_ONE, _TWO]  # the trailing slash of the variable is trimmed, so the URL has one

        def it_passes_when_every_picture_answers_200(monkeypatch, tmp_path, capsys):
            module = _tree(monkeypatch, tmp_path, _TWO_SHOTS)
            _env(monkeypatch, labels=[], public_url="https://pub.example")
            seen = _heads(monkeypatch, {_ONE: 200, _TWO: 200})
            module.main()
            assert "changelog.d/1050-guidance-shots.toml" in capsys.readouterr().out
            assert seen == [_ONE, _TWO]

        def it_skips_the_bucket_check_out_loud_on_a_local_run_without_the_variable(monkeypatch, tmp_path, capsys):
            # A local run has no repository variable; the skip is said, never silent.
            module = _tree(monkeypatch, tmp_path, _TWO_SHOTS)
            _env(monkeypatch, labels=[])
            monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("no HEAD without the variable"))
            module.main()
            assert "R2_PUBLIC_URL is unset; not checking" in capsys.readouterr().out

        def it_fails_in_ci_without_the_variable_naming_it(monkeypatch, tmp_path):
            # A deleted repository variable must never turn a missing picture into a green run.
            module = _tree(monkeypatch, tmp_path, _TWO_SHOTS)
            _env(monkeypatch, labels=[], ci=True)
            monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("no HEAD without the variable"))
            with pytest.raises(SystemExit) as exit_info:
                module.main()
            message = str(exit_info.value)
            assert "R2_PUBLIC_URL is unset in CI" in message and "repository variable R2_PUBLIC_URL" in message

        def it_fails_in_ci_without_the_variable_even_when_no_fragment_lists_shots(monkeypatch, tmp_path):
            # The gate is the variable's presence, not this PR's fragment: the whole tree is checked every run.
            module = _tree(monkeypatch, tmp_path, _FRAGMENT_PREFIX + 'feature = "guild.cubbies"\nguidance = "link"\n')
            _env(monkeypatch, labels=[], ci=True)
            with pytest.raises(SystemExit, match="R2_PUBLIC_URL is unset in CI"):
                module.main()

        def it_heads_nothing_for_a_fragment_at_link(monkeypatch, tmp_path, capsys):
            module = _tree(monkeypatch, tmp_path, _FRAGMENT_PREFIX + 'feature = "guild.cubbies"\nguidance = "link"\n')
            _env(monkeypatch, labels=[], public_url="https://pub.example")
            monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("a link fragment has no shots"))
            module.main()
            assert "folding to" in capsys.readouterr().out

        def it_exits_naming_a_host_it_cannot_reach(monkeypatch):
            # Neither "missing" nor "fine" answers a network failure.
            module = _load_script()

            def _down(request, timeout=None):
                raise urllib.error.URLError("no route")

            monkeypatch.setattr(urllib.request, "urlopen", _down)
            with pytest.raises(SystemExit, match=f"Could not reach {_ONE}"):
                module._head(_ONE)

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
