"""Thin wrapper around pyairtable for Django settings integration."""

from __future__ import annotations

from django.conf import settings
from pyairtable import Api
from pyairtable import Table


def get_api(*, timeout: tuple[int, int] | None = None, retry: bool = True) -> Api:
    """Return a pyairtable Api instance configured from Django settings.

    Args:
        timeout: Connect and read timeouts in seconds; None waits as long as Airtable takes,
            which suits the nightly pull but not a page an admin is waiting on.
        retry: Whether to retry a rate limited or failed call with backoff (pyairtable's default).
    """
    return Api(settings.AIRTABLE_API_TOKEN, timeout=timeout, retry_strategy=retry)


def get_table(table_id: str, *, timeout: tuple[int, int] | None = None, retry: bool = True) -> Table:
    """Return a pyairtable Table bound to the configured base (``get_api`` explains the options)."""
    return get_api(timeout=timeout, retry=retry).table(settings.AIRTABLE_BASE_ID, table_id)
