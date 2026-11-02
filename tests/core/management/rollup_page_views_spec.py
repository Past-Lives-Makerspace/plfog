"""BDD specs for the rollup_page_views job (#1078): finished weeks fold into PageViewWeek, old raw views go.

``now`` is frozen on Wednesday 2026-11-18 (Pacific), so the week of Nov 16 is unfinished and the
weeks of Nov 2 and Nov 9 are finished.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.utils import timezone

from core.models import PageView, PageViewWeek

pytestmark = pytest.mark.django_db


def _at(y, m, d, h=12):
    return timezone.make_aware(datetime(y, m, d, h))


_NOW = _at(2026, 11, 18, 9)


@pytest.fixture(autouse=True)
def frozen(monkeypatch):
    monkeypatch.setattr("core.management.commands.rollup_page_views.timezone.now", lambda: _NOW)


def _view(user, when, view_name="hub_home", role=PageView.Role.MEMBER):
    return PageView.objects.create(user=user, view_name=view_name, role=role, surface="members", viewed_at=when)


def _weeks():
    return {
        (row.week, row.view_name, row.role, row.surface): (row.views, row.people) for row in PageViewWeek.objects.all()
    }


def describe_rollup_page_views():
    def it_folds_each_finished_week_by_page_role_and_surface():
        ada, bo = User.objects.create_user("ada"), User.objects.create_user("bo")
        _view(ada, _at(2026, 11, 2))
        _view(ada, _at(2026, 11, 8, 23))  # Sunday 11 PM Pacific: still the week of Nov 2
        _view(bo, _at(2026, 11, 3))
        _view(bo, _at(2026, 11, 10), role=PageView.Role.ADMIN)
        _view(ada, _at(2026, 11, 17))  # this week: not finished

        call_command("rollup_page_views")

        assert _weeks() == {
            (date(2026, 11, 2), "hub_home", "member", "members"): (3, 2),
            (date(2026, 11, 9), "hub_home", "admin", "members"): (1, 1),
        }

    def it_is_safe_to_run_twice():
        ada = User.objects.create_user("ada")
        _view(ada, _at(2026, 11, 2))
        _view(ada, _at(2026, 11, 10))

        call_command("rollup_page_views")
        first = _weeks()
        call_command("rollup_page_views")

        assert _weeks() == first
        assert PageViewWeek.objects.count() == 2

    def it_deletes_raw_views_past_ninety_days_after_folding_their_week():
        ada = User.objects.create_user("ada")
        old = _NOW - timedelta(days=100)
        _view(ada, old)
        _view(ada, _at(2026, 11, 10))

        call_command("rollup_page_views")

        old_monday = timezone.localtime(old).date() - timedelta(days=timezone.localtime(old).weekday())
        assert _weeks()[(old_monday, "hub_home", "member", "members")] == (1, 1)
        assert list(PageView.objects.values_list("viewed_at", flat=True)) == [_at(2026, 11, 10)]

    def it_writes_nothing_with_no_views(capsys):
        call_command("rollup_page_views")
        assert not PageViewWeek.objects.exists()
        assert "Folded 0 weekly usage rows; deleted 0 old page views." in capsys.readouterr().out
