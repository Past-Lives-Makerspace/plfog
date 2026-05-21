"""Create the database cache table used by Django's cache framework.

The cache backs allauth's rate limiting (and the login-code circuit breaker in
core.abuse_limits). Without a shared cache, each gunicorn worker keeps its own
LocMemCache and the rate limits are effectively multiplied by the worker count.
"""

from __future__ import annotations

from django.core.cache import caches
from django.core.cache.backends.db import BaseDatabaseCache
from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):  # noqa: ARG001
    cache = caches["default"]
    if isinstance(cache, BaseDatabaseCache):
        call_command("createcachetable", cache._table)


def drop_cache_table(apps, schema_editor):  # noqa: ARG001
    cache = caches["default"]
    if isinstance(cache, BaseDatabaseCache):
        schema_editor.execute(f'DROP TABLE IF EXISTS "{cache._table}"')


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0011_widen_ga_scope_help_text"),
    ]

    operations = [
        migrations.RunPython(create_cache_table, drop_cache_table),
    ]
