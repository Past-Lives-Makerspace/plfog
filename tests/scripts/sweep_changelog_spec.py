"""BDD specs for ``scripts/sweep_changelog.py``.

Loaded from its file path via ``importlib`` like the release scripts' specs. Git is faked
with exact-argument answers (the pattern of ``release_plan_spec.py``), because the sweep's
whole job is *which* commit it asks about and what it folds there: a fake that ignored its
arguments would stamp every entry with the same number and pass.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "sweep_changelog.py"

# Two commits that are not each other: the push that shipped the first fragment, and the
# later push that shipped the other two.
_FIRST_PUSH = "a" * 40
_SECOND_PUSH = "b" * 40

_MEMBER = 'bump = "minor"\ndate = "2026-09-13"\ntitle = "A visible thing"\nchanges = ["It happened."]\n'
_FIX = 'bump = "patch"\ndate = "2026-09-14"\ntitle = "A fix"\nchanges = ["It is fixed."]\n'
_INTERNAL = 'bump = "patch"\naudience = "internal"\n'
_BASE = '{"version": "1.62.1"}'

_LOG = (
    "log",
    "--first-parent",
    "--diff-filter=A",
    "--name-only",
    "--format=%H",
    "origin/main",
    "--",
    "changelog.d/*.toml",
)


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sweep_changelog", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_git(module: ModuleType, monkeypatch, answers: dict[tuple[str, ...], str]) -> list[tuple[str, ...]]:
    """Replace ``_git`` with a fake that answers only the exact calls in ``answers``."""
    asked: list[tuple[str, ...]] = []

    def _git(*args: str) -> str:
        asked.append(args)
        if args not in answers:
            raise AssertionError(f"unexpected git call: {args}")
        return answers[args]

    monkeypatch.setattr(module, "_git", _git)
    return asked


def _history_answers() -> dict[tuple[str, ...], str]:
    """What git says about a main where the first push added one fragment and the second two."""
    return {
        _LOG: (
            f"{_SECOND_PUSH}\nchangelog.d/11-fix.toml\nchangelog.d/12-tooling.toml\n\n"
            f"{_FIRST_PUSH}\nchangelog.d/10-first.toml\n"
        ),
        ("show", f"{_FIRST_PUSH}:changelog/base.json"): _BASE,
        ("ls-tree", "--name-only", _FIRST_PUSH, "changelog.d/"): "changelog.d/10-first.toml\nchangelog.d/README.md\n",
        ("show", f"{_FIRST_PUSH}:changelog.d/10-first.toml"): _MEMBER,
        ("show", f"{_SECOND_PUSH}:changelog/base.json"): _BASE,
        ("ls-tree", "--name-only", _SECOND_PUSH, "changelog.d/"): (
            "changelog.d/10-first.toml\nchangelog.d/11-fix.toml\nchangelog.d/12-tooling.toml\nchangelog.d/README.md\n"
        ),
        ("show", f"{_SECOND_PUSH}:changelog.d/10-first.toml"): _MEMBER,
        ("show", f"{_SECOND_PUSH}:changelog.d/11-fix.toml"): _FIX,
        ("show", f"{_SECOND_PUSH}:changelog.d/12-tooling.toml"): _INTERNAL,
    }


@pytest.fixture
def tree(tmp_path: pathlib.Path, monkeypatch) -> tuple[ModuleType, pathlib.Path]:
    """The script pointed at a scratch changelog tree with three unswept fragments."""
    module = _load_script()
    fragments = tmp_path / "changelog.d"
    fragments.mkdir()
    (fragments / "10-first.toml").write_text(_MEMBER, encoding="utf-8")
    (fragments / "11-fix.toml").write_text(_FIX, encoding="utf-8")
    (fragments / "12-tooling.toml").write_text(_INTERNAL, encoding="utf-8")
    (fragments / "README.md").write_text("# not a fragment\n", encoding="utf-8")
    history = tmp_path / "history.json"
    history.write_text(
        json.dumps([{"version": "1.62.1", "date": "2026-09-01", "title": "Frozen", "changes": ["Old."]}]),
        encoding="utf-8",
    )
    base = tmp_path / "base.json"
    base.write_text(_BASE, encoding="utf-8")
    monkeypatch.setattr(module, "FRAGMENTS_PATH", fragments)
    monkeypatch.setattr(module, "HISTORY_PATH", history)
    monkeypatch.setattr(module, "BASE_PATH", base)
    return module, tmp_path


def describe_git():
    def it_returns_stdout_when_git_answers():
        module = _load_script()
        assert module._git("rev-parse", "--is-inside-work-tree").strip() == "true"

    def it_exits_rather_than_guess_when_git_fails():
        module = _load_script()
        with pytest.raises(SystemExit, match="failed"):
            module._git("rev-parse", "--verify", "refs/heads/no-such-branch-for-the-sweep-spec")


def describe_adding_commits():
    def it_maps_each_fragment_to_the_first_parent_commit_that_added_it(monkeypatch):
        module = _load_script()
        _fake_git(module, monkeypatch, _history_answers())

        assert module.adding_commits("origin/main") == {
            "changelog.d/10-first.toml": _FIRST_PUSH,
            "changelog.d/11-fix.toml": _SECOND_PUSH,
            "changelog.d/12-tooling.toml": _SECOND_PUSH,
        }

    def it_keeps_the_newest_arrival_of_a_fragment_added_twice(monkeypatch):
        # Newest first in the log, so the first sighting is the latest push.
        module = _load_script()
        _fake_git(
            module,
            monkeypatch,
            {_LOG: f"{_SECOND_PUSH}\nchangelog.d/10-first.toml\n\n{_FIRST_PUSH}\nchangelog.d/10-first.toml\n"},
        )

        assert module.adding_commits("origin/main") == {"changelog.d/10-first.toml": _SECOND_PUSH}


def describe_version_at():
    def it_folds_the_fragments_in_that_commits_tree_over_its_base(monkeypatch):
        module = _load_script()
        _fake_git(module, monkeypatch, _history_answers())

        assert module.version_at(_FIRST_PUSH) == "1.63.0"
        assert module.version_at(_SECOND_PUSH) == "1.63.2"


def describe_plan():
    def it_stamps_each_member_facing_entry_with_the_version_its_push_folded_to(tree, monkeypatch):
        module, _ = tree
        _fake_git(module, monkeypatch, _history_answers())

        entries, new_base = module.plan("origin/main")

        assert [(e["version"], e["title"]) for e in entries] == [("1.63.2", "A fix"), ("1.63.0", "A visible thing")]
        assert entries[0] == {"version": "1.63.2", "date": "2026-09-14", "title": "A fix", "changes": ["It is fixed."]}
        assert new_base == "1.63.2"

    def it_gives_an_internal_fragment_no_entry(tree, monkeypatch):
        module, _ = tree
        _fake_git(module, monkeypatch, _history_answers())

        entries, _ = module.plan("origin/main")

        assert all("tooling" not in e["title"] for e in entries)
        assert len(entries) == 2

    def it_refuses_a_fragment_the_ref_never_added(tree, monkeypatch):
        module, _ = tree
        answers = _history_answers()
        answers[_LOG] = f"{_SECOND_PUSH}\nchangelog.d/11-fix.toml\nchangelog.d/12-tooling.toml\n"
        _fake_git(module, monkeypatch, answers)

        with pytest.raises(SystemExit, match="10-first.toml was never added on origin/main"):
            module.plan("origin/main")

    def it_refuses_an_empty_directory(tree, monkeypatch):
        module, tmp_path = tree
        for path in (tmp_path / "changelog.d").glob("*.toml"):
            path.unlink()
        _fake_git(module, monkeypatch, {})

        with pytest.raises(SystemExit, match="Nothing to sweep"):
            module.plan("origin/main")


def describe_sweep():
    def it_only_prints_the_plan_without_write(tree, monkeypatch, capsys):
        module, tmp_path = tree
        _fake_git(module, monkeypatch, _history_answers())

        module.sweep("origin/main", write=False)

        out = capsys.readouterr().out
        assert "2 entries to freeze; base.json 1.62.1 -> 1.63.2" in out
        assert "Plan only" in out
        assert sorted(p.name for p in (tmp_path / "changelog.d").iterdir()) == [
            "10-first.toml",
            "11-fix.toml",
            "12-tooling.toml",
            "README.md",
        ]
        assert json.loads((tmp_path / "base.json").read_text(encoding="utf-8")) == {"version": "1.62.1"}

    def it_freezes_moves_the_base_and_deletes_the_fragments_with_write(tree, monkeypatch, capsys):
        module, tmp_path = tree
        _fake_git(module, monkeypatch, _history_answers())

        module.sweep("origin/main", write=True)

        history = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
        assert [e["version"] for e in history] == ["1.63.2", "1.63.0", "1.62.1"]
        assert history[-1]["title"] == "Frozen"
        assert json.loads((tmp_path / "base.json").read_text(encoding="utf-8")) == {"version": "1.63.2"}
        assert [p.name for p in (tmp_path / "changelog.d").iterdir()] == ["README.md"]
        assert (tmp_path / "history.json").read_text(encoding="utf-8").endswith("\n")
        assert "Swept." in capsys.readouterr().out


def describe_main():
    def it_defaults_to_a_plan_against_origin_main(monkeypatch):
        module = _load_script()
        calls: list[tuple[str, bool]] = []
        monkeypatch.setattr(module, "sweep", lambda ref, *, write: calls.append((ref, write)))

        module.main([])

        assert calls == [("origin/main", False)]

    def it_passes_the_ref_and_the_write_flag_through(monkeypatch):
        module = _load_script()
        calls: list[tuple[str, bool]] = []
        monkeypatch.setattr(module, "sweep", lambda ref, *, write: calls.append((ref, write)))

        module.main(["--ref", "main", "--write"])

        assert calls == [("main", True)]
