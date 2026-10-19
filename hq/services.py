"""What the HQ page computes across models: the goal band's rows.

Each live goal kind is counted by one function here, so a later part of Track C swaps a
stub for a real count without touching the page or the model.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from hq.models import EventHub, HubGoal
from membership.models import CommunityEvent


@dataclass(frozen=True)
class GoalRow:
    """One chip in the goal band, ready to render."""

    label: str
    current: int
    target: int
    is_money: bool

    @property
    def percent(self) -> int:
        """How full the meter is, 0 to 100; a goal with no target reads empty."""
        if self.target == 0:
            return 0
        return min(100, round(self.current * 100 / self.target))

    @property
    def current_display(self) -> str:
        return _figure(self.current, self.is_money)

    @property
    def target_display(self) -> str:
        return _figure(self.target, self.is_money)


def _figure(value: int, is_money: bool) -> str:
    return f"${value:,}" if is_money else f"{value:,}"


def rsvp_count(event: CommunityEvent | None) -> int:
    """How many are going: the event's RSVP rows, or 0 with no event.

    A3b (guest RSVPs with a party size) switches this to ``event.rsvp_people_count``.
    """
    if event is None:
        return 0
    return event.rsvps.count()


def volunteer_spots_filled(hub: EventHub) -> int:
    """Volunteer shift sign-ups. Always 0 until C2 adds the shifts."""
    return 0


def bottles_logged(hub: EventHub) -> int:
    """Bottles on the wine wall. Always 0 until C3 adds the wall."""
    return 0


def bottle_value(hub: EventHub) -> int:
    """The wine wall's value in whole dollars. Always 0 until C3 adds the wall."""
    return 0


_LIVE_COUNTS: dict[str, Callable[[EventHub], int]] = {
    HubGoal.Kind.RSVPS: lambda hub: rsvp_count(hub.event),
    HubGoal.Kind.VOLUNTEER_SPOTS: volunteer_spots_filled,
    HubGoal.Kind.BOTTLES: bottles_logged,
    HubGoal.Kind.BOTTLE_VALUE: bottle_value,
}


def goal_rows(hub: EventHub) -> list[GoalRow]:
    """Every goal on ``hub`` with its live figure, in band order.

    One query for the goals and at most one per live kind that appears, however many goals
    share a kind; a manual goal costs nothing beyond its row.
    """
    counts: dict[str, int] = {}
    rows: list[GoalRow] = []
    for goal in hub.goals.all():
        if goal.kind == HubGoal.Kind.MANUAL:
            current = goal.current_value
        else:
            if goal.kind not in counts:
                counts[goal.kind] = _LIVE_COUNTS[goal.kind](hub)
            current = counts[goal.kind]
        rows.append(GoalRow(label=goal.label, current=current, target=goal.target, is_money=goal.is_money))
    return rows
