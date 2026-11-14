"""Pick the specs a pull request runs in CI's ``affected`` job (#1192).

The full suite is about 30,000 specs and takes hours, so a pull request runs only what its diff
can reasonably break, and the nightly full run (``nightly.yml``) catches the rest:

- a spec file the diff adds or changes runs itself;
- a change inside an app runs every spec of that app: the app folder (its ``spec/`` folders) and
  ``tests/<app>/``. A template under ``templates/<app>/`` counts as that app;
- except the app's migration specs (a spec with "migration" in its path, the root conftest's rule),
  which rewind the schema and take most of a run: they run only when the diff adds or edits a file
  in ``<app>/migrations/``, or changes the migration spec itself. Otherwise each is printed as an
  ``--ignore=`` line, which pytest takes beside the paths. The nightly runs them all;
- a ``conftest.py`` under ``tests/`` or a ``spec/`` folder runs the folder it sits in;
- a file under ``static/`` runs the spec files that name it (``hub.css``), since no Python runs a
  stylesheet or a script and only a spec that reads the file can break;
- any change under ``templates/`` or ``static/`` also runs the lint specs at the top of ``tests/``,
  which read every template and stylesheet;
- ``.github/`` and ``scripts/`` run ``tests/scripts/``, where their specs live, except the files
  that decide how the suite itself runs (``FULL_SUITE_FILES``);
- docs, mockups, changelog fragments, the mobile app and e2e specs run nothing here (the
  ``fragment`` and ``e2e`` checks cover the last two);
- anything else (settings, the root conftest, requirements, shared templates, test factories, the
  CI workflows) runs the full suite, because no folder bounds what it can break.

Prints the pytest paths (and ``--ignore=`` options) one per line, ``.`` alone for the full suite, and nothing when no spec
applies. Stdlib only, so the job that runs it installs nothing.

Usage: ``python .github/scripts/affected_specs.py <base-sha>`` from the repository root, with the
base commit fetched. The diff is ``<base>...HEAD``, against the merge base.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
from collections.abc import Iterable
from typing import Protocol

#: The Django apps with specs of their own. ``api`` is not an installed app but has its own
#: ``tests/api/``, so it maps the same way.
APPS = ("airtable_sync", "api", "billing", "classes", "core", "hq", "hub", "kiln", "membership", "polls", "tours")

#: What pytest is given for the whole suite.
FULL = "."

#: Files that decide how the suite runs; changing one must prove the whole suite still runs.
FULL_SUITE_FILES = (
    ".github/workflows/ci.yml",
    ".github/workflows/nightly.yml",
    ".github/workflows/specs.yml",
    ".github/scripts/affected_specs.py",
)

#: Paths no Python spec reads.
NO_SPEC_PREFIXES = ("changelog.d/", "docs/", "mockups/", "mobile/", "tests/e2e/", ".claude/")
NO_SPEC_SUFFIXES = (".md",)

SCRIPT_SPECS = "tests/scripts/"


class Checkout(Protocol):
    """What the selection needs to know about the files on disk."""

    def exists(self, path: str) -> bool:
        """Whether ``path`` is in the checkout; a deleted spec file is not run."""

    def lint_specs(self) -> list[str]:
        """The spec files at the top of ``tests/``, which lint every template and stylesheet."""

    def specs_naming(self, name: str) -> list[str]:
        """The spec files whose source contains ``name``."""

    def migration_specs(self, folder: str) -> list[str]:
        """The spec files under ``folder`` with "migration" in their path."""


def is_spec_file(path: str) -> bool:
    """Whether pytest collects ``path`` as a spec file (``python_files`` in pyproject.toml)."""
    name = path.rsplit("/", 1)[-1]
    return path.endswith(".py") and (name.endswith("_spec.py") or name.startswith("test_"))


def _app_paths(app: str, checkout: Checkout) -> list[str]:
    return [path for path in (f"{app}/", f"tests/{app}/") if checkout.exists(path)]


def paths_for(path: str, checkout: Checkout) -> list[str] | None:
    """The pytest paths one changed file asks for: a list (maybe empty), or None for the full suite.

    Args:
        path: A changed file, relative to the repository root.
        checkout: The files on disk.
    """
    if path in FULL_SUITE_FILES:
        return None
    if path.startswith(NO_SPEC_PREFIXES):
        return []
    if path.startswith((".github/", "scripts/")):
        return [SCRIPT_SPECS]
    if path.endswith(NO_SPEC_SUFFIXES):
        return []
    if is_spec_file(path):
        return [path] if checkout.exists(path) else []
    if (path.startswith("tests/") or "/spec/" in path) and path.endswith("/conftest.py"):
        return [path.removesuffix("conftest.py")]
    parts = path.split("/")
    if parts[0] == "static":
        return checkout.lint_specs() + checkout.specs_naming(parts[-1])
    if parts[0] == "templates" and len(parts) > 2 and parts[1] in APPS:
        return checkout.lint_specs() + _app_paths(parts[1], checkout)
    if parts[0] in APPS and len(parts) > 1:
        return _app_paths(parts[0], checkout)
    return None


def _skipped_migration_specs(changed: list[str], selected: set[str], checkout: Checkout) -> set[str]:
    """The migration specs of the apps the diff touches but whose ``migrations/`` it leaves alone.

    A spec under a folder a changed conftest selected (``classes/spec/migrations/``) still runs.
    """
    touched = {app for app in APPS if {f"{app}/", f"tests/{app}/"} & selected}
    migrated = {path.split("/")[0] for path in changed if path.split("/")[1:2] == ["migrations"]}
    return {
        spec
        for app in touched - migrated
        for folder in _app_paths(app, checkout)
        for spec in checkout.migration_specs(folder)
        if spec not in changed and not any(spec.startswith(kept) for kept in selected - {f"{app}/", f"tests/{app}/"})
    }


def select(changed: Iterable[str], checkout: Checkout) -> list[str]:
    """The pytest arguments for a whole diff: ``[FULL]`` when any file asks for the full suite.

    A path inside a selected folder is dropped, so pytest never collects a spec twice, and the
    migration specs of an app whose migrations did not change follow as ``--ignore=`` options.
    """
    changed = list(changed)
    selected: set[str] = set()
    for path in changed:
        wanted = paths_for(path, checkout)
        if wanted is None:
            return [FULL]
        selected.update(wanted)
    folders = {path for path in selected if path.endswith("/")}
    paths = sorted(
        path for path in selected if not any(path != folder and path.startswith(folder) for folder in folders)
    )
    return paths + [f"--ignore={spec}" for spec in sorted(_skipped_migration_specs(changed, selected, checkout))]


class DiskCheckout:
    """The checkout under ``root``, its spec sources read once on first use."""

    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        self._sources: dict[str, str] | None = None

    def exists(self, path: str) -> bool:
        return (self.root / path).exists()

    def lint_specs(self) -> list[str]:
        return sorted(path.relative_to(self.root).as_posix() for path in (self.root / "tests").glob("*_spec.py"))

    def specs_naming(self, name: str) -> list[str]:
        if self._sources is None:
            self._sources = {
                path.relative_to(self.root).as_posix(): path.read_text()
                for path in self.root.rglob("*_spec.py")
                if not {".venv", "node_modules", "e2e"} & set(path.relative_to(self.root).parts)
            }
        return sorted(path for path, source in self._sources.items() if name in source)

    def migration_specs(self, folder: str) -> list[str]:
        return sorted(
            path.relative_to(self.root).as_posix()
            for path in (self.root / folder).rglob("*_spec.py")
            if "migration" in path.relative_to(self.root).as_posix()
        )


def changed_files(base: str) -> list[str]:
    """Every file the diff from the merge base touches, a rename as both its old and new path."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", f"{base}...HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: affected_specs.py <base-sha>", file=sys.stderr)
        return 2
    for path in select(changed_files(argv[1]), DiskCheckout(pathlib.Path.cwd())):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
