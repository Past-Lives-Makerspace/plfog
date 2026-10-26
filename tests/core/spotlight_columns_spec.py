"""The Spotlight Text and Meeting columns left in the database by 0124 until #938 drops them (#935).

The model no longer knows them, so nothing sets the meeting column to NULL when its event goes. 0124 drops the
foreign key constraint first, so deleting that event still works on a database where the column was set.
"""

from __future__ import annotations

import pytest
from django.db import connection, transaction

from core.models import SiteConfiguration
from tests.membership.factories import CommunityEventFactory

pytestmark = pytest.mark.django_db(transaction=True)


def describe_the_leftover_meeting_column():
    def it_lets_the_event_it_points_at_be_deleted():
        SiteConfiguration.load()
        event = CommunityEventFactory()
        pk = event.pk
        with connection.cursor() as cursor:
            cursor.execute("UPDATE core_siteconfiguration SET spotlight_meeting_event_id = %s", [pk])

        with transaction.atomic():
            event.delete()

        with connection.cursor() as cursor:
            cursor.execute("SELECT spotlight_meeting_event_id FROM core_siteconfiguration")
            assert cursor.fetchone()[0] == pk
