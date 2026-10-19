"""Specs for ``core.deferred``: work handed off after the commit, on a background thread."""

from __future__ import annotations

import logging
import threading

import pytest
from django.db import connections, transaction

from core import deferred
from core.models import SiteConfiguration


def _join_deferred_threads() -> None:
    for thread in threading.enumerate():
        if thread.name.startswith(deferred.THREAD_NAME_PREFIX):
            thread.join(timeout=10)
            assert not thread.is_alive()


def describe_defer():
    def it_runs_the_task_at_the_call_under_the_test_schedule():
        ran: list[str] = []
        deferred.defer(lambda: ran.append("yes"), name="inline")
        assert ran == ["yes"]

    def it_logs_a_failed_task_instead_of_raising(caplog):
        def boom() -> None:
            raise ValueError("no send")

        with caplog.at_level(logging.ERROR, logger="core.deferred"):
            deferred.run_inline(boom, "event:9:announce")

        assert "Deferred task event:9:announce failed; its backstop will finish it" in caplog.text


def describe_after_commit_thread():
    @pytest.fixture(autouse=True)
    def _real_thread(monkeypatch):
        monkeypatch.setattr(deferred, "_schedule", deferred.after_commit_thread)

    @pytest.mark.django_db(transaction=True)
    def it_runs_on_a_named_non_daemon_thread_after_the_commit():
        seen: dict[str, object] = {}
        caller = threading.current_thread()

        def task() -> None:
            current = threading.current_thread()
            seen.update(name=current.name, daemon=current.daemon, other_thread=current is not caller)

        with transaction.atomic():
            deferred.defer(task, name="event:1:announce")
            assert seen == {}
        _join_deferred_threads()

        assert seen == {"name": "deferred:event:1:announce", "daemon": False, "other_thread": True}

    @pytest.mark.django_db(transaction=True)
    def it_runs_nothing_when_the_transaction_rolls_back():
        ran: list[str] = []

        with pytest.raises(RuntimeError), transaction.atomic():
            deferred.defer(lambda: ran.append("yes"), name="rolled-back")
            raise RuntimeError("save failed")
        _join_deferred_threads()

        assert ran == []

    @pytest.mark.django_db(transaction=True)
    def it_closes_the_threads_own_database_connections(monkeypatch):
        # Asserted on the call: SQLite's in-memory test database ignores close(), so the
        # wrapper's state would read open here and closed on Postgres.
        closed_by: list[str] = []
        real_close_all = connections.close_all

        def recording_close_all() -> None:
            closed_by.append(threading.current_thread().name)
            real_close_all()

        monkeypatch.setattr(connections, "close_all", recording_close_all)

        deferred.defer(SiteConfiguration.load, name="reads-the-db")
        _join_deferred_threads()

        assert closed_by == ["deferred:reads-the-db"]

    @pytest.mark.django_db(transaction=True)
    def it_closes_them_even_when_the_task_fails(monkeypatch, caplog):
        closed_by: list[str] = []
        real_close_all = connections.close_all

        def recording_close_all() -> None:
            closed_by.append(threading.current_thread().name)
            real_close_all()

        def task() -> None:
            SiteConfiguration.load()
            raise ValueError("no send")

        monkeypatch.setattr(connections, "close_all", recording_close_all)

        with caplog.at_level(logging.ERROR, logger="core.deferred"):
            deferred.defer(task, name="fails")
            _join_deferred_threads()

        assert closed_by == ["deferred:fails"]
        assert "Deferred task fails failed" in caplog.text
