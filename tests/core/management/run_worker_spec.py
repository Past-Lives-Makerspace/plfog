"""BDD specs for ``run_worker`` (#1270, part 3): the Discord gateway thread, then the queue worker."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command

START = "core.discord_gateway.start_in_thread"
DB_WORKER = "core.management.commands.run_worker.call_command"


def _run(thread: object) -> tuple[str, list[tuple[tuple[object, ...], dict[str, object]]]]:
    out = StringIO()
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    with patch(START, return_value=thread), patch(DB_WORKER, side_effect=lambda *a, **k: calls.append((a, k))):
        call_command("run_worker", stdout=out)
    return out.getvalue(), calls


def describe_run_worker():
    def it_starts_the_gateway_then_hands_the_main_thread_to_db_worker_without_reload():
        out, calls = _run(object())
        assert "Discord gateway started." in out
        assert calls == [(("db_worker",), {"reload": False})]

    def it_runs_the_queue_alone_when_the_bot_token_is_blank():
        out, calls = _run(None)
        assert "Discord gateway off" in out
        assert calls == [(("db_worker",), {"reload": False})]
