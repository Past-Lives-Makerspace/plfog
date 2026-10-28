"""Races on the cubby waitlist (#980), each widened inside ``notify_next`` between reading the line and claiming.

* Two cubbies opening at once tell two different members, never the same one twice (the guild lock).
* A member who leaves while a cubby's opening is picking them is passed over, and the next in line is
  told (the claim is a conditional update on a still waiting row).

Needs PostgreSQL: SQLite writes no FOR UPDATE and serialises writers, so CI's postgres-only job runs
these (STANDARDS.md 8).
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
        real_claim = cubby_waitlist._claim
        errors: list[BaseException] = []

        def slow_claim(*args: Any) -> bool:
            # Inside the lock, after the line was read: without the lock both would read waiter0 first.
            time.sleep(0.3)
            return real_claim(*args)

        def opening(cubby_pk: int) -> None:
            try:
                barrier.wait()
                cubby_waitlist.notify_next(cubby_pk)
            except BaseException as error:  # surfaced below; a thread's exception is otherwise lost
                errors.append(error)
            finally:
                connection.close()

        with (
            patch("membership.cubby_waitlist._claim", side_effect=slow_claim),
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


def describe_leaving_while_a_cubby_opens():
    @pytest.mark.postgres_only
    @pytest.mark.django_db(transaction=True)
    def it_passes_over_the_member_who_left_and_tells_the_next():
        MembershipPlanFactory()
        guild = GuildFactory(show_cubbies_tab=True)
        cubby = CubbyFactory(unit=ShelfUnitFactory(guild=guild), label="L1")
        start = timezone.now() - timedelta(hours=1)
        leaver, next_up = (
            CubbyWaitlistEntry.objects.create(
                guild=guild, member=_member(f"leaving{n}"), joined_at=start + timedelta(minutes=n)
            )
            for n in range(2)
        )
        line_read = threading.Event()
        left = threading.Event()
        real_claim = cubby_waitlist._claim
        errors: list[BaseException] = []
        told: list[int] = []

        def claim_after_the_leave(entry: CubbyWaitlistEntry, opened: Any) -> bool:
            # The line is read with the leaver first; their Leave commits before this claim lands.
            line_read.set()
            left.wait(5)
            return real_claim(entry, opened)

        def opening() -> None:
            try:
                entry = cubby_waitlist.notify_next(cubby.pk)
                if entry is not None:
                    told.append(entry.pk)
            except BaseException as error:
                errors.append(error)
            finally:
                connection.close()

        def leaving() -> None:
            try:
                line_read.wait(5)
                cubby_waitlist.leave(guild, leaver.member)
                left.set()
            except BaseException as error:
                errors.append(error)
            finally:
                connection.close()

        with (
            patch("membership.cubby_waitlist._claim", side_effect=claim_after_the_leave),
            patch("membership.cubby_waitlist.notify_opened"),
        ):
            threads = [threading.Thread(target=opening), threading.Thread(target=leaving)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        assert errors == []
        assert told == [next_up.pk]
        leaver.refresh_from_db()
        assert leaver.notified_at is None and leaver.left_at is not None
