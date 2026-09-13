"""BDD specs for ``.github/scripts/release_plan.py``.

The script lives under ``.github/scripts`` (not a Python package), so it is loaded from its
file path via ``importlib``, matching ``discord_release_notify_spec.py``.

``_fake_git`` asserts the exact arguments of every git question and raises on any it was not
told to expect. That strictness is the point, and it is inherited from the spec of the release
guard this replaced: the *range* the planner asks about is the whole of what it does, so a
fake that ignored its arguments would pass just as happily against ``before..before``, against
a ``HEAD^`` comparison, or against a diff with ``--diff-filter=A`` dropped — and that last one
would re-announce every fragment an edit touched, to members, permanently.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / ".github" / "scripts" / "release_plan.py"

# Two shas that are not each other, so a spec can tell "the base" from "the tip".
_BASE = "a" * 40
_HEAD = "b" * 40
_NULL_SHA = "0" * 40

_FRAGMENT = 'bump = "minor"\ndate = "2026-09-13"\ntitle = "A visible thing"\nchanges = ["It happened."]\n'
_INTERNAL = 'bump = "patch"\naudience = "internal"\n'


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_plan", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_git(module: ModuleType, monkeypatch, answers: dict[tuple[str, ...], str | None]) -> list[tuple[str, ...]]:
    """Replace ``_git`` with a fake that answers only the exact calls in ``answers``.

    Returns the list of calls made, so a spec can assert the planner asked the question it
    was supposed to ask and not a looser one that happens to give the same answer.
    """
    asked: list[tuple[str, ...]] = []

    def _git(*args: str) -> str | None:
        asked.append(args)
        if args not in answers:
            raise AssertionError(f"unexpected git call: {args}")
        return answers[args]

    monkeypatch.setattr(module, "_git", _git)
    return asked


def describe_release_plan():
    def describe_names_a_commit():
        def it_accepts_a_full_sha():
            assert _load_script()._names_a_commit(_BASE)

        def it_rejects_the_null_sha_a_branch_creation_pushes():
            assert not _load_script()._names_a_commit(_NULL_SHA)

        def it_rejects_an_empty_base():
            assert not _load_script()._names_a_commit("")

        def it_rejects_a_sha_with_junk_in_front_of_it():
            # fullmatch, not an anchored match: a prefix-tolerant check would accept a value
            # git would then reject, and the planner would exit on a real push.
            assert not _load_script()._names_a_commit(f"xx{_BASE}")

    def describe_require_readable_base():
        def it_passes_when_the_base_is_in_the_clone(monkeypatch):
            module = _load_script()
            _fake_git(module, monkeypatch, {("cat-file", "-e", f"{_BASE}^{{commit}}"): ""})
            module._require_readable_base(_BASE)

        def it_exits_when_the_base_is_missing_from_the_clone(monkeypatch):
            # The hole that would otherwise swallow the whole planner: an unreachable base
            # makes the diff empty, which is indistinguishable from "added no fragments".
            module = _load_script()
            _fake_git(module, monkeypatch, {("cat-file", "-e", f"{_BASE}^{{commit}}"): None})
            with pytest.raises(SystemExit, match="not in this clone"):
                module._require_readable_base(_BASE)

    def describe_added_fragments():
        def _diff_call(module: ModuleType) -> tuple[str, ...]:
            return ("diff", "--diff-filter=A", "--name-only", _BASE, _HEAD, "--", module._PATHSPEC)

        def it_asks_only_for_files_the_push_added(monkeypatch):
            # --diff-filter=A is load-bearing. Without it, editing a fragment that already
            # shipped would re-announce it to members, and a Discord post cannot be unsent.
            module = _load_script()
            asked = _fake_git(module, monkeypatch, {_diff_call(module): ""})
            module.added_fragments(_BASE, _HEAD)
            assert asked == [_diff_call(module)]

        def it_loads_each_added_fragment(monkeypatch, tmp_path):
            module = _load_script()
            path = tmp_path / "394-a.toml"
            path.write_text(_FRAGMENT)
            _fake_git(module, monkeypatch, {_diff_call(module): "394-a.toml\n"})
            monkeypatch.setattr(module, "_REPO_ROOT", tmp_path)
            assert [f.title for f in module.added_fragments(_BASE, _HEAD)] == ["A visible thing"]

        def it_skips_a_path_that_no_longer_exists(monkeypatch, tmp_path):
            # A fragment added and then deleted inside one push range is named by the diff
            # but is not on disk. Reading it would crash the release of everything else.
            module = _load_script()
            _fake_git(module, monkeypatch, {_diff_call(module): "gone.toml\n"})
            monkeypatch.setattr(module, "_REPO_ROOT", tmp_path)
            assert module.added_fragments(_BASE, _HEAD) == []

        def it_returns_nothing_when_the_push_added_none(monkeypatch):
            module = _load_script()
            _fake_git(module, monkeypatch, {_diff_call(module): "\n"})
            assert module.added_fragments(_BASE, _HEAD) == []

        def it_exits_when_the_diff_fails(monkeypatch):
            module = _load_script()
            _fake_git(module, monkeypatch, {_diff_call(module): None})
            with pytest.raises(SystemExit, match="Refusing to guess what shipped"):
                module.added_fragments(_BASE, _HEAD)

        def it_orders_newest_first(monkeypatch, tmp_path):
            module = _load_script()
            (tmp_path / "1-old.toml").write_text(_FRAGMENT.replace("2026-09-13", "2026-01-01"))
            (tmp_path / "2-new.toml").write_text(_FRAGMENT.replace("A visible thing", "Newer"))
            _fake_git(module, monkeypatch, {_diff_call(module): "1-old.toml\n2-new.toml\n"})
            monkeypatch.setattr(module, "_REPO_ROOT", tmp_path)
            assert [f.title for f in module.added_fragments(_BASE, _HEAD)] == ["Newer", "A visible thing"]

    def describe_tag_exists():
        def it_is_true_when_origin_has_the_tag(monkeypatch):
            module = _load_script()
            call = ("ls-remote", "--tags", "origin", "refs/tags/v1.63.0")
            _fake_git(module, monkeypatch, {call: "abc123\trefs/tags/v1.63.0\n"})
            assert module._tag_exists("v1.63.0")

        def it_is_false_when_origin_does_not(monkeypatch):
            module = _load_script()
            call = ("ls-remote", "--tags", "origin", "refs/tags/v1.63.0")
            _fake_git(module, monkeypatch, {call: ""})
            assert not module._tag_exists("v1.63.0")

        def it_exits_when_origin_is_unreachable(monkeypatch):
            # Guessing "false" here would push a tag that already exists and fail the step
            # anyway; guessing "true" would silently skip tagging a real release.
            module = _load_script()
            call = ("ls-remote", "--tags", "origin", "refs/tags/v1.63.0")
            _fake_git(module, monkeypatch, {call: None})
            with pytest.raises(SystemExit, match="could not reach origin"):
                module._tag_exists("v1.63.0")

    def describe_plan_fragments():
        def it_announces_the_newest_member_facing_fragment_on_a_manual_run(monkeypatch, tmp_path):
            module = _load_script()
            (tmp_path / "1-a.toml").write_text(_FRAGMENT)
            monkeypatch.setenv("EVENT_NAME", "workflow_dispatch")
            monkeypatch.setattr(module, "FRAGMENTS_PATH", tmp_path)
            assert [f.title for f in module._plan_fragments()] == ["A visible thing"]

        def it_re_announces_only_one_fragment_on_a_manual_run(monkeypatch, tmp_path):
            # The lever exists to re-send a post that failed. Firing off every unswept
            # fragment would spam members with a season of releases.
            module = _load_script()
            (tmp_path / "1-a.toml").write_text(_FRAGMENT.replace("2026-09-13", "2026-01-01"))
            (tmp_path / "2-b.toml").write_text(_FRAGMENT.replace("A visible thing", "Newest"))
            monkeypatch.setenv("EVENT_NAME", "workflow_dispatch")
            monkeypatch.setattr(module, "FRAGMENTS_PATH", tmp_path)
            assert [f.title for f in module._plan_fragments()] == ["Newest"]

        def it_announces_nothing_on_a_manual_run_right_after_a_sweep(monkeypatch, tmp_path):
            module = _load_script()
            monkeypatch.setenv("EVENT_NAME", "workflow_dispatch")
            monkeypatch.setattr(module, "FRAGMENTS_PATH", tmp_path)
            assert module._plan_fragments() == []

        def it_announces_nothing_when_the_push_created_the_branch(monkeypatch, capsys):
            module = _load_script()
            monkeypatch.setenv("EVENT_NAME", "push")
            monkeypatch.setenv("BEFORE_SHA", _NULL_SHA)
            monkeypatch.setenv("AFTER_SHA", _HEAD)
            assert module._plan_fragments() == []
            assert "No push base" in capsys.readouterr().out

        def it_warns_but_does_not_fail_when_a_push_adds_no_fragment(monkeypatch, capsys):
            # A no-changelog-labelled PR legitimately adds none, and the pull-request check
            # is where a forgotten one is caught — before Render has deployed anything.
            module = _load_script()
            monkeypatch.setenv("EVENT_NAME", "push")
            monkeypatch.setenv("BEFORE_SHA", _BASE)
            monkeypatch.setenv("AFTER_SHA", _HEAD)
            monkeypatch.setattr(module, "_require_readable_base", lambda before: None)
            monkeypatch.setattr(module, "added_fragments", lambda before, after: [])
            assert module._plan_fragments() == []
            assert "::warning" in capsys.readouterr().out

    def describe_main():
        def _run(module: ModuleType, monkeypatch, tmp_path, fragments: list) -> dict[str, str]:
            output = tmp_path / "github_output"
            output.write_text("")
            monkeypatch.setenv("GITHUB_OUTPUT", str(output))
            monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
            monkeypatch.setattr(module, "_plan_fragments", lambda: fragments)
            monkeypatch.setattr(module, "_tag_exists", lambda tag: False)
            module.main()
            return dict(line.split("=", 1) for line in output.read_text().splitlines())

        def it_writes_the_folded_version_and_its_tag(monkeypatch, tmp_path):
            module = _load_script()
            outputs = _run(module, monkeypatch, tmp_path, [])
            assert outputs["version"] == module.VERSION
            assert outputs["tag"] == f"v{module.VERSION}"

        def it_arms_the_announcement_for_a_member_facing_fragment(monkeypatch, tmp_path):
            module = _load_script()
            path = tmp_path / "1-a.toml"
            path.write_text(_FRAGMENT)
            fragment = module.load_fragment(path)
            outputs = _run(module, monkeypatch, tmp_path, [fragment])
            assert outputs["announce"] == "true"
            payload = json.loads(pathlib.Path(outputs["announce_path"]).read_text())
            assert [e["title"] for e in payload] == ["A visible thing"]

        def it_withholds_the_announcement_for_an_internal_only_release(monkeypatch, tmp_path):
            # Tags, deploys, announces nothing — and nobody had to decide to withhold it.
            module = _load_script()
            path = tmp_path / "1-a.toml"
            path.write_text(_INTERNAL)
            fragment = module.load_fragment(path)
            outputs = _run(module, monkeypatch, tmp_path, [fragment])
            assert outputs["announce"] == "false"
            assert json.loads(pathlib.Path(outputs["announce_path"]).read_text()) == []

        def it_reports_the_tag_already_exists_so_a_rerun_does_not_fail(monkeypatch, tmp_path):
            module = _load_script()
            output = tmp_path / "github_output"
            output.write_text("")
            monkeypatch.setenv("GITHUB_OUTPUT", str(output))
            monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
            monkeypatch.setattr(module, "_plan_fragments", lambda: [])
            monkeypatch.setattr(module, "_tag_exists", lambda tag: True)
            module.main()
            outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
            assert outputs["tag_exists"] == "true"
