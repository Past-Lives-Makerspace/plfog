"""Fixtures shared by the hub specs."""

from __future__ import annotations

from datetime import date

import pytest


@pytest.fixture
def spring_launch(monkeypatch: pytest.MonkeyPatch) -> date:
    """The Growth dashboard's launch moved back to March 27, 2026, a day the specs pick.

    The real launch (September 21, 2026) is too recent for a spec to fit a range and the period
    before it between launch and the day a factory's ``auto_now_add`` lands. The Growth specs
    stamp their rows in the spring instead and pin the launch before them.
    """
    day = date(2026, 3, 27)
    monkeypatch.setattr("hub.growth.LAUNCH", day)
    return day
