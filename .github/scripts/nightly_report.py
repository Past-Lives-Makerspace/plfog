"""Write the body of the nightly-red issue from a red nightly run (#1192).

``nightly.yml`` runs the full suite in the shards ``specs.yml`` defines, each writing a JUnit
report (``junit-<shard>.xml``, ``junit_family=xunit1`` so each case carries its file). This reads
them and prints Markdown naming every failing spec as a pytest node id, any shard that wrote no
report (it timed out or crashed), the combined coverage against the 98% gate, and the run link.
The workflow posts it as a new issue or a comment on the open one; the morning routine works from
it, so a node id here must be one ``pytest`` accepts as it is.

Stdlib only. Usage: ``python .github/scripts/nightly_report.py <reports-dir>`` with ``RUN_URL``,
``COVERAGE_RESULT`` and ``COVERAGE_TOTAL`` in the environment.
"""

from __future__ import annotations

import os
import pathlib
import sys
import xml.etree.ElementTree as ET

#: The shards in specs.yml's matrix, each of which writes ``junit-<shard>.xml``.
SHARDS = ("1", "2", "3", "4", "migrations-1", "migrations-2", "migrations-3", "migrations-4")

#: Failing specs listed in full; past this the run log has the rest.
LISTED = 50

GATE = 98


def node_id(case: ET.Element) -> str:
    """The pytest node id of a JUnit test case: ``file::describe_block::it_spec``.

    xunit1 gives ``file`` and a dotted ``classname`` of the module then the describe blocks. A
    case with no file (a collection error) falls back to its classname and name.
    """
    file = case.get("file", "")
    classname = case.get("classname", "")
    name = case.get("name", "")
    module = file.removesuffix(".py").replace("/", ".")
    if not file or not classname.startswith(module):
        return "::".join(part for part in (classname, name) if part)
    blocks = [block for block in classname[len(module) :].split(".") if block]
    return "::".join([file, *blocks, name])


def failures(report: pathlib.Path) -> list[str]:
    """The node ids of the failed and errored cases in one JUnit report."""
    root = ET.parse(report).getroot()
    return [
        node_id(case)
        for case in root.iter("testcase")
        if case.find("failure") is not None or case.find("error") is not None
    ]


def body(reports: pathlib.Path, run_url: str, coverage_result: str, coverage_total: str) -> str:
    """The issue body for a red night.

    Args:
        reports: The folder holding the downloaded ``junit-<shard>.xml`` files.
        run_url: The workflow run's page.
        coverage_result: The ``coverage`` job's result (``success`` when the total met the gate).
        coverage_total: The combined total, a percentage, or empty when it was never computed.
    """
    failed: list[str] = []
    missing: list[str] = []
    for shard in SHARDS:
        report = reports / f"junit-{shard}.xml"
        if report.exists():
            failed += failures(report)
        else:
            missing.append(shard)

    lines = [f"The nightly full suite on `main` is red: {run_url}", ""]
    if failed:
        lines.append(f"**Failing specs ({len(failed)}):**")
        lines += [f"- `{spec}`" for spec in failed[:LISTED]]
        if len(failed) > LISTED:
            lines.append(f"- and {len(failed) - LISTED} more in the run log.")
        lines.append("")
    if missing:
        names = ", ".join(missing)
        lines += [f"**No report from shard {names}:** it timed out or crashed; its log is in the run.", ""]
    if not coverage_total:
        lines.append("**Coverage:** not computed; see the run's coverage job.")
    elif coverage_result == "success":
        lines.append(f"**Coverage:** {coverage_total}%, at or over the {GATE}% gate.")
    else:
        lines.append(
            f"**Coverage:** {coverage_total}%, under the {GATE}% gate; the coverage job lists the missed lines."
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: nightly_report.py <reports-dir>", file=sys.stderr)
        return 2
    print(
        body(
            pathlib.Path(argv[1]),
            run_url=os.environ["RUN_URL"],
            coverage_result=os.environ["COVERAGE_RESULT"],
            coverage_total=os.environ["COVERAGE_TOTAL"],
        ),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
