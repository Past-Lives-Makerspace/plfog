"""The plfog-worker process: the live Discord gateway beside the task queue worker (#1270).

    python manage.py run_worker

Starts Fog Bot's reaction listener (:mod:`core.discord_gateway`) on a daemon thread, then runs
``db_worker --no-reload`` on the main thread, which keeps its own SIGTERM handling, so a
redeploy still stops the queue cleanly and the thread goes with the process. A blank bot token
(unset, or staging) starts only the queue. The Render worker's Docker command becomes this one
only after it is deployed: before, an unknown command would stop the queue.
"""

from __future__ import annotations

from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run the task queue worker with the live Discord reaction listener beside it."

    def handle(self, *args: Any, **options: Any) -> None:
        from core.discord_gateway import start_in_thread

        thread = start_in_thread()
        self.stdout.write("Discord gateway started." if thread else "Discord gateway off (the bot token is blank).")
        call_command("db_worker", reload=False)
