"""The generic automation action log (#898) and how it attaches to the open run."""

from __future__ import annotations

import pytest

from core.factories import ScheduledTaskRunFactory
from core.models import AutomationAction, ScheduledTaskRun
from core.scheduled_jobs import Trigger, current_run, record_run
from tests.membership.factories import MemberFactory, UserFactory

pytestmark = pytest.mark.django_db


def _record(key: str = "job_a", **kwargs: object) -> AutomationAction:
    defaults: dict[str, object] = {
        "kind": AutomationAction.Kind.DISCORD_DM,
        "result": AutomationAction.Result.OK,
        "subject": "Ada",
    }
    defaults.update(kwargs)
    return AutomationAction.record(key, **defaults)  # type: ignore[arg-type]


def describe_record():
    def it_attaches_to_the_run_open_for_its_key():
        with record_run("job_a", trigger=Trigger.SCHEDULED) as run:
            action = _record()
        assert action.run == run
        assert list(run.actions.all()) == [action]

    def it_does_not_attach_to_another_jobs_run():
        with record_run("job_b", trigger=Trigger.SCHEDULED):
            action = _record("job_a")
        assert action.run is None

    def it_has_no_run_outside_one():
        assert current_run() is None
        assert _record().run is None

    def it_forgets_the_run_after_a_failure():
        with pytest.raises(RuntimeError), record_run("job_a", trigger=Trigger.SCHEDULED):
            raise RuntimeError("boom")
        assert current_run() is None

    def it_keeps_who_did_it_and_to_whom():
        member = MemberFactory()
        admin = UserFactory()
        action = _record(subject="x" * 250, member=member, actor=admin, detail="why")
        assert len(action.subject) == 200
        assert (action.member, action.actor, action.detail) == (member, admin, "why")
        assert str(action) == f"job_a discord_dm {'x' * 200} ok"

    def it_outlives_its_pruned_run():
        with record_run("job_a", trigger=Trigger.SCHEDULED) as run:
            action = _record()
        ScheduledTaskRun.objects.filter(pk=run.pk).delete()
        action.refresh_from_db()
        assert action.run is None


def describe_tone():
    @pytest.mark.parametrize(
        ("result", "tone"),
        [("ok", "ok"), ("failed", "danger"), ("skipped", "neutral")],
    )
    def it_maps_each_result_to_a_pill(result: str, tone: str):
        assert AutomationAction(result=result).tone == tone


def describe_history_for():
    def it_lists_one_jobs_runs_newest_first_with_their_actions(django_assert_num_queries):
        older = ScheduledTaskRunFactory(task_key="job_a")
        newer = ScheduledTaskRunFactory(task_key="job_a")
        ScheduledTaskRunFactory(task_key="job_b")
        for run in (older, newer):
            AutomationAction.objects.create(
                task_key="job_a", run=run, kind="discord_dm", result="ok", subject="s", member=MemberFactory()
            )
        with django_assert_num_queries(2):
            runs = list(ScheduledTaskRun.objects.history_for("job_a"))
            names = [action.member.display_name for run in runs for action in run.actions.all()]
        assert runs == [newer, older]
        assert len(names) == 2
