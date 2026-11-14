"""BDD specs for ``.github/scripts/nightly_report.py``, the body of the nightly-red issue (#1192).

The morning routine works from this body, so each failing spec must come out as a node id
``pytest`` accepts, and a shard that wrote no report must be named rather than read as green.
The script is loaded from its file path via ``importlib``, like the other script specs.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import runpy
import sys
from types import ModuleType

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / ".github" / "scripts" / "nightly_report.py"
_RUN = "https://github.com/Past-Lives-Makerspace/plfog/actions/runs/1"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("nightly_report", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


report = _load_script()


def _junit(*cases: str) -> str:
    return f'<?xml version="1.0"?><testsuites><testsuite name="pytest">{"".join(cases)}</testsuite></testsuites>'


_PASSED = '<testcase classname="tests.hub.views_spec.describe_page" name="it_renders" file="tests/hub/views_spec.py"/>'
_FAILED = (
    '<testcase classname="tests.hub.views_spec.describe_page.describe_when_closed" name="it_hides[a.b]"'
    ' file="tests/hub/views_spec.py"><failure message="boom"/></testcase>'
)
_ERRORED = (
    '<testcase classname="classes.spec.models_spec.describe_offer" name="it_saves"'
    ' file="classes/spec/models_spec.py"><error message="fixture"/></testcase>'
)
_COLLECTION_ERROR = '<testcase classname="" name="tests.hub.broken_spec"><error message="ImportError"/></testcase>'


@pytest.fixture
def reports(tmp_path: pathlib.Path) -> pathlib.Path:
    for shard in report.SHARDS:
        (tmp_path / f"junit-{shard}.xml").write_text(_junit(_PASSED))
    return tmp_path


def describe_node_id():
    def it_joins_the_file_the_describe_blocks_and_the_spec():
        case = report.ET.fromstring(_FAILED)
        assert report.node_id(case) == "tests/hub/views_spec.py::describe_page::describe_when_closed::it_hides[a.b]"

    def it_uses_the_file_alone_for_a_module_level_spec():
        case = report.ET.fromstring(
            '<testcase classname="tests.hub.views_spec" name="it_x" file="tests/hub/views_spec.py"/>'
        )
        assert report.node_id(case) == "tests/hub/views_spec.py::it_x"

    def it_falls_back_to_the_classname_for_a_case_without_a_file():
        assert report.node_id(report.ET.fromstring(_COLLECTION_ERROR)) == "tests.hub.broken_spec"


def describe_body():
    def it_lists_failed_and_errored_specs_with_the_run_link(reports):
        (reports / "junit-2.xml").write_text(_junit(_PASSED, _FAILED))
        (reports / "junit-migrations-2.xml").write_text(_junit(_ERRORED))

        text = report.body(reports, _RUN, "success", "98.40")

        assert text.startswith(f"The nightly full suite on `main` is red: {_RUN}\n")
        assert "**Failing specs (2):**" in text
        assert "- `tests/hub/views_spec.py::describe_page::describe_when_closed::it_hides[a.b]`" in text
        assert "- `classes/spec/models_spec.py::describe_offer::it_saves`" in text
        assert "it_renders" not in text
        assert "**Coverage:** 98.40%, at or over the 98% gate." in text

    def it_names_a_shard_that_wrote_no_report(reports):
        (reports / "junit-3.xml").unlink()
        (reports / "junit-migrations-3.xml").unlink()

        text = report.body(reports, _RUN, "failure", "91.00")

        assert "**No report from shard 3, migrations-3:** it timed out or crashed" in text
        assert "Failing specs" not in text

    def it_says_when_coverage_is_under_the_gate(reports):
        assert "**Coverage:** 97.61%, under the 98% gate" in report.body(reports, _RUN, "failure", "97.61")

    def it_says_when_coverage_was_never_computed(reports):
        assert "**Coverage:** not computed" in report.body(reports, _RUN, "skipped", "")

    def it_lists_the_first_fifty_failures_and_counts_the_rest(reports):
        cases = [
            f'<testcase classname="tests.hub.views_spec" name="it_{n}" file="tests/hub/views_spec.py"><failure/></testcase>'
            for n in range(report.LISTED + 3)
        ]
        (reports / "junit-1.xml").write_text(_junit(*cases))

        text = report.body(reports, _RUN, "success", "99.00")

        assert f"**Failing specs ({report.LISTED + 3}):**" in text
        assert text.count("- `tests/hub/views_spec.py::it_") == report.LISTED
        assert "- and 3 more in the run log." in text


def describe_SHARDS():
    def it_matches_the_matrix_in_specs_yml():
        workflow = (_ROOT / ".github" / "workflows" / "specs.yml").read_text()
        matrix = re.search(r"shard: \[(.*)\]", workflow)
        assert matrix is not None
        assert tuple(name.strip().strip('"') for name in matrix.group(1).split(",")) == report.SHARDS


def describe_main():
    def it_prints_usage_without_exactly_one_folder(capsys):
        assert report.main(["nightly_report.py"]) == 2
        assert "usage" in capsys.readouterr().err

    def it_prints_the_body_from_the_environment_when_run_as_a_script(monkeypatch, capsys, reports):
        monkeypatch.setenv("RUN_URL", _RUN)
        monkeypatch.setenv("COVERAGE_RESULT", "failure")
        monkeypatch.setenv("COVERAGE_TOTAL", "97.00")
        monkeypatch.setattr(sys, "argv", ["nightly_report.py", str(reports)])

        with pytest.raises(SystemExit) as exit_info:
            runpy.run_path(str(_SCRIPT), run_name="__main__")

        assert exit_info.value.code == 0
        assert capsys.readouterr().out == report.body(reports, _RUN, "failure", "97.00")
