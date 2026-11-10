"""Specs for ``core.tasks``: after save work on the durable task queue (#1150).

Under pytest the immediate backend runs a task at enqueue time; pytest's own transaction
never commits, so a spec that wants the commit to fire uses
``django_capture_on_commit_callbacks(execute=True)``.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from django.conf import settings
from django.db import transaction
from django.tasks import task, task_backends
from django.tasks.backends.immediate import ImmediateBackend
from django.tasks.base import TaskError

from core.tasks import enqueue_on_commit, error_message_lines
from core.models import SiteConfiguration

pytestmark = pytest.mark.django_db

ran: list[str] = []


@task
def remember(label: str) -> None:
    ran.append(label)


@task
def remember_saved_site_name() -> None:
    ran.append(SiteConfiguration.load().general_calendar_color)


received: list[list[object]] = []


@task
def remember_shape(*args: object) -> None:
    received.append(list(args))


@pytest.fixture(autouse=True)
def _clear_ran():
    ran.clear()
    received.clear()
    yield
    ran.clear()
    received.clear()


def describe_settings():
    def it_selects_the_immediate_backend_under_test():
        assert settings.TASKS["default"]["BACKEND"] == "django.tasks.backends.immediate.ImmediateBackend"
        assert isinstance(task_backends["default"], ImmediateBackend)

    def it_installs_the_database_backends_app():
        assert "django_tasks_db" in settings.INSTALLED_APPS


def describe_enqueue_on_commit():
    def it_runs_the_task_once_the_transaction_commits(django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            with transaction.atomic():
                enqueue_on_commit(remember, "after commit")
                assert ran == []

        assert ran == ["after commit"]

    def it_lets_the_task_read_the_rows_the_save_committed(django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            with transaction.atomic():
                config = SiteConfiguration.load()
                config.general_calendar_color = "#123456"
                config.save()
                enqueue_on_commit(remember_saved_site_name)

        assert ran == ["#123456"]

    def it_passes_keyword_arguments(django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            enqueue_on_commit(remember, label="by keyword")

        assert ran == ["by keyword"]

    def it_enqueues_nothing_when_the_transaction_rolls_back(django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            with pytest.raises(RuntimeError), transaction.atomic():
                enqueue_on_commit(remember, "rolled back")
                raise RuntimeError("the save failed")

        assert callbacks == []
        assert ran == []

    def it_refuses_a_model_instance_at_the_call(django_capture_on_commit_callbacks):
        # Stored as JSON: a model instance would only fail after the save committed.
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            with pytest.raises(TypeError):
                enqueue_on_commit(remember, SiteConfiguration.load())

        assert callbacks == []

    @pytest.mark.django_db(transaction=True)
    def it_logs_a_failed_enqueue_instead_of_failing_the_committed_save(caplog):
        with (
            patch.object(ImmediateBackend, "enqueue", side_effect=RuntimeError("queue table missing")),
            caplog.at_level(logging.ERROR, logger="core.tasks"),
        ):
            with transaction.atomic():
                enqueue_on_commit(remember, "lost")

        assert "Could not enqueue task tests.core.tasks_spec.remember" in caplog.text
        assert "queue table missing" in caplog.text
        assert ran == []


def describe_the_arguments_a_task_receives():
    def it_hands_the_task_the_json_round_trip_the_database_would_store(django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            enqueue_on_commit(remember_shape, (1, 2), {"ids": (3,)}, b"bytes")

        assert received == [[[1, 2], {"ids": [3]}, "bytes"]]

    def it_refuses_bytes_that_are_not_utf8_with_a_type_error(django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            with pytest.raises(TypeError, match="Task arguments must be JSON"):
                enqueue_on_commit(remember_shape, b"\xff\xfe")

        assert callbacks == []


def describe_error_message_lines():
    def it_reads_a_builtin_exceptions_message_from_the_traceback():
        error = TaskError(
            exception_class_path="builtins.ValueError",
            traceback='Traceback (most recent call last):\n  File "x.py", line 1\nValueError: no send\n',
        )
        assert error_message_lines(error) == ["no send"]

    def it_keeps_every_line_of_a_multi_line_message_for_the_alert_to_cut():
        error = TaskError(
            exception_class_path="psycopg.errors.UniqueViolation",
            traceback=(
                "Traceback (most recent call last):\n"
                "psycopg.errors.UniqueViolation: duplicate key\n"
                "DETAIL:  Key (email)=(jane@example.com) already exists.\n"
            ),
        )
        assert error_message_lines(error) == [
            "duplicate key",
            "DETAIL:  Key (email)=(jane@example.com) already exists.",
        ]

    def it_reads_the_last_exception_of_a_chained_traceback():
        error = TaskError(
            exception_class_path="builtins.RuntimeError",
            traceback=(
                "Traceback (most recent call last):\nKeyError: 'x'\n\n"
                "During handling of the above exception, another exception occurred:\n\n"
                "Traceback (most recent call last):\nRuntimeError: wrapped\n"
            ),
        )
        assert error_message_lines(error) == ["wrapped"]

    def it_falls_back_to_the_class_name_without_a_message():
        error = TaskError(
            exception_class_path="core.errors.Boom",
            traceback="Traceback (most recent call last):\ncore.errors.Boom\n",
        )
        assert error_message_lines(error) == ["Boom"]


def describe_the_admin():
    def it_lists_failed_tasks_for_a_superuser_in_the_unfold_theme():
        from django.contrib import admin
        from django.contrib.auth.models import User
        from django.test import Client
        from django_tasks_db.models import DBTaskResult
        from unfold.admin import ModelAdmin

        from core.scheduled_jobs import failed_tasks_path

        row = DBTaskResult.objects.create(
            task_path="tests.core.tasks_spec.remember",
            args_kwargs={"args": ["x"], "kwargs": {}},
            backend_name="default",
            status="FAILED",
        )
        client = Client()
        client.force_login(User.objects.create_superuser(username="tasks-admin", email="tasks@example.com"))

        response = client.get(failed_tasks_path())

        assert response.status_code == 200
        assert f"{row.pk}/change/" in response.content.decode()
        assert isinstance(admin.site._registry[DBTaskResult], ModelAdmin)

    def it_lets_only_a_superuser_delete_a_task_row():
        from django.contrib import admin
        from django.contrib.auth.models import User
        from django.test import RequestFactory
        from django_tasks_db.models import DBTaskResult

        model_admin = admin.site._registry[DBTaskResult]
        request = RequestFactory().get("/admin/")
        request.user = User.objects.create_superuser(username="tasks-delete", email="del@example.com")
        assert model_admin.has_delete_permission(request) is True
        request.user = User.objects.create_user(username="tasks-staff", email="staff@example.com", is_staff=True)
        assert model_admin.has_delete_permission(request) is False
