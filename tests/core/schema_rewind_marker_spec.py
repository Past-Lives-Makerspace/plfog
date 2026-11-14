"""The root conftest's rule for which spec files rewind the schema (#1192).

A migration spec gets the ``migration`` marker, which sends it to CI's migration shards and lets
``affected`` skip it, and the restore fixture runs after it. The rule reads the path inside the
repository: read from the absolute path, a checkout under a folder such as ``migrations-work``
would mark every spec in the suite.
"""

from __future__ import annotations

import importlib.util
import pathlib
from types import ModuleType

import pytest

_CONFTEST = pathlib.Path(__file__).resolve().parents[2] / "conftest.py"


def _load_conftest() -> ModuleType:
    spec = importlib.util.spec_from_file_location("root_conftest", _CONFTEST)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


root_conftest = _load_conftest()


def describe_is_migration_spec():
    @pytest.fixture
    def checkout(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
        root = tmp_path / "migrations-work" / "plfog"
        monkeypatch.setattr(root_conftest, "ROOT", root.resolve())
        return root

    def it_ignores_migration_in_the_folders_above_the_checkout(checkout):
        assert root_conftest.is_migration_spec(checkout / "tests" / "hub" / "views_spec.py") is False

    def it_marks_a_spec_with_migration_in_its_file_name(checkout):
        assert root_conftest.is_migration_spec(checkout / "tests" / "hub" / "seed_migration_spec.py") is True

    def it_marks_a_spec_in_a_migrations_folder(checkout):
        assert root_conftest.is_migration_spec(checkout / "classes" / "spec" / "migrations" / "cap_spec.py") is True

    def it_reads_this_checkout_from_the_conftests_own_folder():
        assert root_conftest.ROOT == _CONFTEST.parent
