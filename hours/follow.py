"""Keep ``hours.Hours`` and ``hours.DayOff`` following the old rows while the old editors write them (#1300, part 1).

Mixed into the kind rows (``tours.TourHours``, ``membership.OrientationAvailability``) and the old
days off (``tours.TourDayOff``, ``membership.OrientationDayOff``): every ``save()`` and
``delete()`` of one regroups that person's hours or recopies their days off. A queryset
``update()`` or ``delete()`` and a cascade skip it, as they skip every ``save()``; nothing writes
these rows that way today, and the person's next save puts them right. Kept free of model
imports, so ``membership.models`` and ``tours.models`` can import it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from django.db import models

    _Base = models.Model
else:
    _Base = object


class _HeldLock:
    """Marks one person's member row as locked in the running transaction (an ``on_commit`` entry that does nothing).

    Django drops an ``on_commit`` entry exactly when PostgreSQL releases a row lock taken at the
    same point: at commit, at rollback, and at a rollback to a savepoint taken before it. So the
    entry is there while the lock is, with no extra query to ask.
    """

    def __init__(self, member_id: int) -> None:
        self.member_id = member_id

    def __call__(self) -> None:
        return None


def lock_person(member_id: int) -> None:
    """Hold one person's hours still until the transaction ends: ``SELECT ... FOR NO KEY UPDATE`` on their member row.

    Taken once per transaction: a second call for the same person (a tour hours save, then the
    regroup its rows' saves run) finds the lock already held and sends nothing, so a save still
    issues one lock query. NO KEY, so a booking's insert (FOR KEY SHARE on that row) never waits on it.

    Raises:
        RuntimeError: outside a transaction, where the lock would end at once.
    """
    from django.db import connection, transaction

    from membership.models import Member

    if not connection.in_atomic_block:
        raise RuntimeError("lock_person must run inside transaction.atomic().")
    if any(isinstance(entry[1], _HeldLock) and entry[1].member_id == member_id for entry in connection.run_on_commit):
        return
    list(Member.objects.select_for_update(no_key=True).filter(pk=member_id).values_list("pk", flat=True))
    transaction.on_commit(_HeldLock(member_id))


class FollowsHours(_Base):
    """A kind row ticking one set of a person's hours through its ``hours`` key.

    ``hours_person`` names the field holding the person (``guide``, ``orienter``). Before a save
    the row takes its ``hours`` key from the database, so an instance loaded before another row's
    regroup never writes back a set that has since moved; after it, it reads the key the regroup
    gave it.
    """

    hours_person: ClassVar[str]

    def _stored_hours_id(self) -> int | None:
        if self.pk is None:
            return None
        return type(self)._default_manager.filter(pk=self.pk).values_list("hours_id", flat=True).first()

    def _regroup(self, hours_id: int | None) -> None:
        from hours.models import Hours

        before = (
            None if hours_id is None else Hours.objects.filter(pk=hours_id).values_list("member_id", flat=True).first()
        )
        person: int | None = getattr(self, f"{self.hours_person}_id")
        for member_id in dict.fromkeys(member_id for member_id in (before, person) if member_id is not None):
            Hours.objects.regroup(member_id)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.hours_id = self._stored_hours_id()
        super().save(*args, **kwargs)
        self._regroup(self.hours_id)
        self.hours_id = self._stored_hours_id()

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        hours_id = self._stored_hours_id()
        result = super().delete(*args, **kwargs)
        self._regroup(hours_id)
        return result


class FollowsDaysOff(_Base):
    """An old day off (tours' ranges, orientations' single days): ``days_off_person`` names its person's field."""

    days_off_person: ClassVar[str]

    def _recopy(self) -> None:
        from hours.models import DayOff

        DayOff.objects.recopy(getattr(self, f"{self.days_off_person}_id"))

    def save(self, *args: Any, **kwargs: Any) -> None:
        super().save(*args, **kwargs)
        self._recopy()

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        result = super().delete(*args, **kwargs)
        self._recopy()
        return result
