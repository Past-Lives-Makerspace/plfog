"""BDD-style tests for the pull_prod_db management command."""

from __future__ import annotations

from io import StringIO
from unittest.mock import ANY, MagicMock, patch

import pytest
from django.core.management import CommandError, call_command

# Swapping DATABASES is exactly what these specs assert on — the command must refuse a
# sqlite local DB and accept a PostgreSQL one — and no connection is ever opened, so
# Django's generic "this can lead to unexpected behavior" warning is noise here.
pytestmark = pytest.mark.filterwarnings("ignore:Overriding setting DATABASES:UserWarning")


def describe_pull_prod_db():
    def describe_guard_rails():
        def it_refuses_to_run_when_debug_is_false(settings):
            settings.DEBUG = False
            with pytest.raises(CommandError, match="DEBUG is not True"):
                call_command("pull_prod_db", "--no-input", stdout=StringIO())

        @patch.dict("os.environ", {"PROD_DATABASE_URL": ""}, clear=False)
        def it_refuses_when_prod_database_url_is_empty(settings):
            settings.DEBUG = True
            with pytest.raises(CommandError, match="PROD_DATABASE_URL is not set"):
                call_command("pull_prod_db", "--no-input", stdout=StringIO())

        @patch.dict("os.environ", {"PROD_DATABASE_URL": "postgres://prod:5432/db"}, clear=False)
        def it_refuses_when_local_db_is_sqlite(settings):
            settings.DEBUG = True
            settings.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": "db.sqlite3"}}
            with pytest.raises(CommandError, match="local database must be PostgreSQL"):
                call_command("pull_prod_db", "--no-input", stdout=StringIO())

    def describe_interactive_confirmation():
        @patch.dict("os.environ", {"PROD_DATABASE_URL": "postgres://prod:5432/db"}, clear=False)
        @patch("builtins.input", return_value="n")
        def it_aborts_when_user_declines(mock_input, settings):
            settings.DEBUG = True
            settings.DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": "localdb"}}
            out = StringIO()
            call_command("pull_prod_db", stdout=out)
            assert "Aborted" in out.getvalue()

        @patch.dict(
            "os.environ",
            {"PROD_DATABASE_URL": "postgres://user:pass@host:5432/proddb"},
            clear=False,
        )
        @patch("core.management.commands.pull_prod_db.call_command")
        @patch("subprocess.run")
        @patch("builtins.input", return_value="y")
        def it_proceeds_when_user_confirms(mock_input, mock_run, mock_call_command, settings):
            settings.DEBUG = True
            settings.DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": "localdb"}}
            mock_run.return_value.returncode = 0

            out = StringIO()
            call_command("pull_prod_db", stdout=out)
            assert "Done" in out.getvalue()

    def describe_successful_pull():
        @patch.dict(
            "os.environ",
            {"PROD_DATABASE_URL": "postgres://user:pass@host:5432/proddb"},
            clear=False,
        )
        @patch("core.management.commands.pull_prod_db.call_command")
        @patch("subprocess.run")
        def it_dumps_and_loads_prod_data(mock_run, mock_call_command, settings):
            settings.DEBUG = True
            settings.DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": "localdb"}}
            mock_run.return_value.returncode = 0

            out = StringIO()
            call_command("pull_prod_db", "--no-input", stdout=out)

            commands_run = [c.args[0][0] for c in mock_run.call_args_list]
            assert "pg_dump" in commands_run
            assert "psql" in commands_run

            mock_call_command.assert_called_once_with("migrate", verbosity=1, stdout=ANY)

            output = out.getvalue()
            assert "Done" in output

        @patch.dict(
            "os.environ",
            {"PROD_DATABASE_URL": "postgres://user:pass@host:5432/proddb"},
            clear=False,
        )
        @patch("core.management.commands.pull_prod_db.call_command")
        @patch("subprocess.run")
        def it_fails_gracefully_on_pg_dump_error(mock_run, mock_call_command, settings):
            settings.DEBUG = True
            settings.DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": "localdb"}}
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = "connection refused"

            with pytest.raises(CommandError, match="pg_dump failed"):
                call_command("pull_prod_db", "--no-input", stdout=StringIO())

        @patch.dict(
            "os.environ",
            {"PROD_DATABASE_URL": "postgres://user:pass@host:5432/proddb"},
            clear=False,
        )
        @patch("core.management.commands.pull_prod_db.call_command")
        @patch("subprocess.run")
        def it_fails_gracefully_on_psql_error(mock_run, mock_call_command, settings):
            settings.DEBUG = True
            settings.DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": "localdb"}}
            # pg_dump succeeds, psql fails
            success = MagicMock(returncode=0)
            failure = MagicMock(returncode=1, stderr="permission denied")
            mock_run.side_effect = [success, failure]

            with pytest.raises(CommandError, match="psql failed"):
                call_command("pull_prod_db", "--no-input", stdout=StringIO())
