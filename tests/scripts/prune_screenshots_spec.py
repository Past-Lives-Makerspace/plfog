"""BDD specs for ``.github/scripts/prune_screenshots.py``.

The script lives under ``.github/scripts`` (not a Python package), so it is loaded from its
file path via ``importlib``, matching the other script specs.
"""

from __future__ import annotations

import importlib.util
import pathlib
from types import ModuleType

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / ".github" / "scripts" / "prune_screenshots.py"
_CHECKER = pathlib.Path(__file__).resolve().parents[2] / ".github" / "scripts" / "check_pr_description.py"

_DAY = 86400
_NOW = 1_800_000_000


def _load(path: pathlib.Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prune = _load(_SCRIPT, "prune_screenshots")


def describe_last_touched():
    def it_keeps_each_paths_newest_commit_time():
        log = "\x00300\nmockups/screenshots/a.png\n\n\x00200\nmockups/screenshots/a.png\nmockups/screenshots/b.png\n"
        assert prune.last_touched(log) == {"mockups/screenshots/a.png": 300, "mockups/screenshots/b.png": 200}


def describe_stale():
    def it_picks_files_last_touched_before_the_cutoff():
        touched = {"old.png": _NOW - 31 * _DAY, "new.png": _NOW - 29 * _DAY}
        assert prune.stale(["old.png", "new.png"], touched, _NOW, 30) == ["old.png"]

    def it_never_picks_the_readme():
        readme = "mockups/screenshots/README.md"
        assert prune.stale([readme], {readme: 0}, _NOW, 30) == []

    def it_leaves_a_file_with_no_known_commit():
        assert prune.stale(["staged.png"], {}, _NOW, 30) == []


def describe_pr_body():
    def it_passes_the_description_check():
        checker = _load(_CHECKER, "check_pr_description")
        assert checker.description_errors(prune.pr_body(12, 4.2, 30)) == []
