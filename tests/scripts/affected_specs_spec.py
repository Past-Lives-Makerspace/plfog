"""BDD specs for ``.github/scripts/affected_specs.py``, which picks the specs a PR runs (#1192).

The selection is the merge gate's reach: a path that maps to too little lets a PR merge without
running the specs it can break, and one that maps to too much puts the hour-long full suite back
on every PR. So each rule gets its own spec, and the git argv is asserted exactly: ``...`` (the
merge base) rather than ``..``, and ``--no-renames`` so a moved file names the app it left.

The script lives under ``.github/scripts`` (not a Python package), so it is loaded from its file
path via ``importlib``, matching the other script specs.
"""

from __future__ import annotations

import importlib.util
import pathlib
import runpy
import subprocess
import sys
from types import ModuleType

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / ".github" / "scripts" / "affected_specs.py"

_LINT = ["tests/frontend_lint_spec.py", "tests/template_comment_lint_spec.py"]


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("affected_specs", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


affected = _load_script()


class _FakeCheckout:
    """A checkout holding ``files`` (and the folders above them), with spec sources by path."""

    def __init__(self, files: list[str], sources: dict[str, str] | None = None) -> None:
        self.files = set(files)
        self.sources = sources or {}

    def exists(self, path: str) -> bool:
        if path.endswith("/"):
            return any(file.startswith(path) for file in self.files)
        return path in self.files

    def lint_specs(self) -> list[str]:
        return list(_LINT)

    def specs_naming(self, name: str) -> list[str]:
        return sorted(path for path, source in self.sources.items() if name in source)


_CHECKOUT = _FakeCheckout(
    [
        "hub/views.py",
        "tests/hub/views_spec.py",
        "classes/models.py",
        "classes/spec/models_spec.py",
        "tests/classes/models_spec.py",
        "api/permissions.py",
        "tests/api/permissions_spec.py",
        "polls/models.py",
    ],
    sources={"tests/hub/views_spec.py": 'CSS = "static/css/hub.css"', "tests/classes/models_spec.py": "nothing"},
)


def _select(*changed: str) -> list[str]:
    return affected.select(list(changed), _CHECKOUT)


def describe_select():
    def it_runs_a_changed_spec_file_on_its_own():
        assert _select("tests/hub/views_spec.py") == ["tests/hub/views_spec.py"]

    def it_runs_nothing_for_a_deleted_spec_file():
        assert _select("tests/hub/gone_spec.py") == []

    def it_runs_the_app_folder_and_its_tests_folder_for_app_code():
        assert _select("classes/models.py") == ["classes/", "tests/classes/"]

    def it_runs_the_app_folder_alone_when_the_app_has_no_tests_folder():
        assert _select("polls/models.py") == ["polls/"]

    def it_runs_the_app_for_a_helper_in_the_apps_spec_folder():
        assert _select("classes/spec/migrations/_head.py") == ["classes/", "tests/classes/"]

    def it_maps_the_api_folder_like_an_app():
        assert _select("api/permissions.py") == ["api/", "tests/api/"]

    def it_runs_the_app_and_the_lint_specs_for_a_template_in_the_app_folder():
        assert _select("templates/hub/page.html") == sorted(["hub/", "tests/hub/", *_LINT])

    def it_runs_the_lint_specs_and_the_specs_naming_a_static_file():
        assert _select("static/css/hub.css") == sorted([*_LINT, "tests/hub/views_spec.py"])

    def it_runs_the_folder_of_a_changed_conftest():
        assert _select("tests/hub/conftest.py") == ["tests/hub/"]
        assert _select("classes/spec/views/conftest.py") == ["classes/spec/views/"]

    def it_runs_the_script_specs_for_github_and_scripts_files():
        assert _select(".github/scripts/check_pr_description.py") == ["tests/scripts/"]
        assert _select(".github/workflows/release.yml") == ["tests/scripts/"]
        assert _select("scripts/sweep_changelog.py") == ["tests/scripts/"]

    @pytest.mark.parametrize(
        "path",
        [
            "changelog.d/1192-ci.toml",
            "docs/GUIDANCE.md",
            "mockups/page.html",
            "mobile/android/app/build.gradle",
            "tests/e2e/class_share_spec.py",
            "tests/e2e/conftest.py",
            ".claude/skills/drive/SKILL.md",
            "CLAUDE.md",
            "hub/AGENTS.md",
        ],
    )
    def it_runs_nothing_for_a_file_no_python_spec_reads(path):
        assert _select(path) == []

    @pytest.mark.parametrize(
        "path",
        [
            "plfog/settings.py",
            "conftest.py",
            "requirements.txt",
            "requirements-dev.txt",
            "pyproject.toml",
            "templates/base.html",
            "templates/components/modal.html",
            "tests/membership/factories.py",
            ".github/workflows/ci.yml",
            ".github/workflows/specs.yml",
            ".github/workflows/nightly.yml",
            ".github/scripts/affected_specs.py",
        ],
    )
    def it_runs_the_full_suite_for_a_change_outside_any_app(path):
        assert _select(path) == [affected.FULL]

    def it_runs_the_full_suite_when_any_one_file_asks_for_it():
        assert _select("classes/models.py", "plfog/settings.py") == ["."]

    def it_drops_a_path_inside_a_folder_it_already_runs():
        assert _select("hub/views.py", "tests/hub/views_spec.py", "tests/hub/conftest.py") == ["hub/", "tests/hub/"]

    def it_runs_nothing_for_an_empty_diff():
        assert _select() == []


def describe_is_spec_file():
    def it_matches_the_collected_file_patterns():
        assert affected.is_spec_file("tests/hub/views_spec.py")
        assert affected.is_spec_file("tests/hub/test_views.py")
        assert not affected.is_spec_file("tests/hub/factories.py")
        assert not affected.is_spec_file("tests/hub/views_spec.txt")


def describe_DiskCheckout():
    @pytest.fixture
    def root(tmp_path: pathlib.Path) -> pathlib.Path:
        for path, source in {
            "tests/frontend_lint_spec.py": "lint",
            "tests/hub/views_spec.py": "hub.css",
            "hub/spec/menu_spec.py": "hub.css and more",
            "tests/e2e/hub_spec.py": "hub.css",
            ".venv/lib/site/pkg_spec.py": "hub.css",
            "tests/hub/factories.py": "hub.css",
        }.items():
            (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / path).write_text(source)
        return tmp_path

    def it_says_whether_a_path_exists(root):
        checkout = affected.DiskCheckout(root)
        assert checkout.exists("tests/hub/")
        assert not checkout.exists("tests/kiln/")

    def it_lists_the_spec_files_at_the_top_of_tests(root):
        assert affected.DiskCheckout(root).lint_specs() == ["tests/frontend_lint_spec.py"]

    def it_finds_the_spec_files_that_name_a_file_outside_e2e_and_the_venv(root):
        assert affected.DiskCheckout(root).specs_naming("hub.css") == [
            "hub/spec/menu_spec.py",
            "tests/hub/views_spec.py",
        ]

    def it_reads_the_spec_sources_once(root):
        checkout = affected.DiskCheckout(root)
        checkout.specs_naming("hub.css")
        (root / "tests/hub/late_spec.py").write_text("hub.css")
        assert "tests/hub/late_spec.py" not in checkout.specs_naming("hub.css")


class _Completed:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def _fake_git(monkeypatch: pytest.MonkeyPatch, stdout: str) -> list[list[str]]:
    calls: list[list[str]] = []

    def _run(argv: list[str], **kwargs: object) -> _Completed:
        calls.append(argv)
        return _Completed(stdout)

    monkeypatch.setattr(subprocess, "run", _run)
    return calls


def describe_changed_files():
    def it_diffs_from_the_merge_base_with_renames_as_two_paths(monkeypatch):
        calls = _fake_git(monkeypatch, "hub/views.py\n\nclasses/models.py\n")

        assert affected.changed_files("abc123") == ["hub/views.py", "classes/models.py"]
        assert calls == [["git", "diff", "--name-only", "--no-renames", "abc123...HEAD"]]


def describe_main():
    def it_prints_usage_without_exactly_one_base(capsys):
        assert affected.main(["affected_specs.py"]) == 2
        assert "usage" in capsys.readouterr().err

    def it_prints_one_pytest_path_per_line(monkeypatch, capsys, tmp_path):
        (tmp_path / "tests" / "hub").mkdir(parents=True)
        (tmp_path / "hub").mkdir()
        monkeypatch.chdir(tmp_path)
        _fake_git(monkeypatch, "hub/views.py\n")

        assert affected.main(["affected_specs.py", "abc123"]) == 0
        assert capsys.readouterr().out == "hub/\ntests/hub/\n"

    def it_exits_with_mains_status_when_run_as_a_script(monkeypatch, capsys):
        _fake_git(monkeypatch, "conftest.py\n")
        monkeypatch.setattr(sys, "argv", ["affected_specs.py", "abc123"])

        with pytest.raises(SystemExit) as exit_info:
            runpy.run_path(str(_SCRIPT), run_name="__main__")

        assert exit_info.value.code == 0
        assert capsys.readouterr().out == ".\n"
