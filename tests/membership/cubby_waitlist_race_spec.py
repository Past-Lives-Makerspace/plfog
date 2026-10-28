"""Two cubbies opening at once (#980): two different members hear, never the same one twice.

Needs PostgreSQL: SQLite writes no FOR UPDATE, so CI's postgres-only job runs it (STANDARDS.md 8).
The gap between reading the line and marking the first entry told is widened inside the guild lock;
without it both openings would read the same member as first in line.
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.utils import timezone

from membership import cubby_waitlist
from membership.models import CubbyWaitlistEntry, Member
from tests.membership.factories import CubbyFactory, GuildFactory, MembershipPlanFactory, ShelfUnitFactory


def _member(username: str) -> Member:
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="x")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["status"])
    return member


def describe_two_cubbies_opening_at_once():
    @pytest.mark.postgres_only
    @pytest.mark.django_db(transaction=True)
    def it_tells_two_different_members_in_line_order():
        MembershipPlanFactory()
        guild = GuildFactory(show_cubbies_tab=True)
        unit = ShelfUnitFactory(guild=guild)
        opened = [CubbyFactory(unit=unit, label=f"R{n}", row=1, column=n + 1) for n in range(2)]
        start = timezone.now() - timedelta(hours=1)
        waiting = [
            CubbyWaitlistEntry.objects.create(
                guild=guild, member=_member(f"waiter{n}"), joined_at=start + timedelta(minutes=n)
            )
            for n in range(3)
        ]
        barrier = threading.Barrier(len(opened))
        real_line = CubbyWaitlistEntry.objects.in_line
        errors: list[BaseException] = []

        def slow_line() -> Any:
            # Inside the lock, before the first entry is marked: without the lock both would read waiter0.
            entries = real_line()
            time.sleep(0.3)
            return entries

        def opening(cubby_pk: int) -> None:
            try:
                barrier.wait()
                cubby_waitlist.notify_next(cubby_pk)
            except BaseException as error:  # surfaced below; a thread's exception is otherwise lost
                errors.append(error)
            finally:
                connection.close()

        with (
            patch.object(CubbyWaitlistEntry.objects, "in_line", side_effect=slow_line),
            patch("membership.cubby_waitlist.notify_opened"),
        ):
            threads = [threading.Thread(target=opening, args=(cubby.pk,)) for cubby in opened]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        assert errors == []
        told = CubbyWaitlistEntry.objects.filter(notified_at__isnull=False)
        assert sorted(told.values_list("pk", flat=True)) == [waiting[0].pk, waiting[1].pk]
        assert sorted(told.values_list("notified_cubby_id", flat=True)) == sorted(c.pk for c in opened)
        assert CubbyWaitlistEntry.objects.get(pk=waiting[2].pk).is_waiting
