"""The long CI and E2E workflows give up their runners when a pull request moves on.

On 2026-10-07 every Actions slot was held by `test` runs (about three hours each) for branches
that had already merged or been pushed again, and every new PR check sat queued behind them.
"""

import pathlib

WORKFLOWS = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows"


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text()


def describe_long_pull_request_workflows():
    def it_groups_runs_by_pull_request_or_branch_and_cancels_the_older_run():
        # Pushes group by branch, not commit: four merges in a minute on 2026-10-08 each held a
        # runner for a full `test` run while PR checks queued behind them.
        for name in ("ci.yml", "playwright.yml"):
            workflow = _workflow(name)
            assert "github.event.pull_request.number || github.ref }}" in workflow, name
            assert "cancel-in-progress: true" in workflow, name

    def it_cancels_when_the_pull_request_closes_and_runs_nothing_for_it():
        for name in ("ci.yml", "playwright.yml"):
            workflow = _workflow(name)
            assert "types: [opened, synchronize, reopened, closed]" in workflow, name
            assert "if: github.event.action != 'closed'" in workflow, name

    def it_skips_every_job_on_the_close_event():
        ci = _workflow("ci.yml")
        assert ci.count("if: github.event.action != 'closed'") == ci.count("runs-on: ubuntu-latest")

    def it_caps_each_job_so_a_stuck_run_cannot_hold_a_slot_all_day():
        for name in ("ci.yml", "playwright.yml"):
            workflow = _workflow(name)
            assert workflow.count("timeout-minutes:") == workflow.count("runs-on: ubuntu-latest"), name
