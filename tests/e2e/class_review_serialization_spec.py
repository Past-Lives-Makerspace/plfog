"""Two reviewers deciding at once: the row lock in ``ClassApproval.decide``.

Lives under ``tests/e2e/`` because it needs a real database. SQLite reports
``has_select_for_update = False`` and silently drops the clause, so on the default unit run
(SQLite) these would pass without the lock ever existing. The e2e job runs Postgres, where
the lock is real; ``conftest.py`` there marks everything in this directory ``e2e``, so this
runs exactly where it means something. No browser is involved.

The unit suite asserts the transaction *structure* (``approval_gates_spec.py``); this asserts
the behaviour the structure buys.

Both review lanes are open at once now, so these interleavings are reachable by two people
clicking at the same moment, not by a contrived test:

* A guild lead approving while an admin publishes must not fire "Executive validation
  needed" at a class that is already live.
* A guild lead requesting changes while an admin publishes must not silently unpublish it.
"""

from __future__ import annotations

import threading
import time

import pytest
from django.db import connection

from classes.factories import CategoryFactory, ClassOfferingFactory
from classes.models import ClassApproval, ClassOffering
from core.models import Notification
from tests.membership.factories import GuildFactory, MembershipPlanFactory

Decision = ClassApproval.Decision
Role = ClassApproval.Role

pytestmark = pytest.mark.django_db(transaction=True)

#: Long enough for the second thread to reach the row lock and block on it, short enough
#: that a failure is a failure rather than a hang. Nothing is timing-sensitive after this:
#: the second thread stays blocked in the database until the first commits.
_REACH_THE_LOCK = 1.0


def _plan():
    """A MembershipPlan must exist before any User, or the auto-Member signal declines."""
    from membership.models import MembershipPlan

    if not MembershipPlan.objects.exists():
        MembershipPlanFactory()


def _admin_user():
    from django.contrib.auth import get_user_model

    from membership.models import Member

    _plan()
    user = get_user_model().objects.create_user(username="race-admin@example.com", email="race-admin@example.com")
    member = Member.objects.get(user=user)
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    return user


def _lead_user():
    from django.contrib.auth import get_user_model

    _plan()
    return get_user_model().objects.create_user(username="race-lead@example.com", email="race-lead@example.com")


def _submitted_offering(lead_user):
    from membership.models import Member

    lead = Member.objects.get(user=lead_user)
    guild = GuildFactory(name="Race Guild", guild_lead=lead)
    offering = ClassOfferingFactory(
        ready=True, category=CategoryFactory(guild=guild), status=ClassOffering.Status.DRAFT
    )
    offering.submit_for_review()
    return offering


def _race(offering, admin_user, lead_decision, lead_user, monkeypatch):
    """Run an admin publish and a guild-lead decision so they overlap on the offering row.

    The admin thread is held inside ``publish``, which runs while it still holds
    ``select_for_update`` on the offering. The lead thread then starts and blocks in the
    database until the admin commits. Returns whatever the lead thread raised.
    """
    holding = threading.Event()
    release = threading.Event()
    original_publish = ClassOffering.publish
    lead_error: list[BaseException] = []

    def slow_publish(self, actor):
        holding.set()
        release.wait(10)
        return original_publish(self, actor)

    monkeypatch.setattr(ClassOffering, "publish", slow_publish)

    def admin_publishes():
        try:
            ClassOffering.objects.get(pk=offering.pk).approve(admin_user)
        finally:
            connection.close()

    def lead_decides():
        try:
            row = ClassApproval.objects.get(class_offering=offering, role=Role.GUILD_LEAD)
            row.decide(lead_decision, user=lead_user, notes="Concurrent.")
        except BaseException as exc:  # noqa: BLE001  # the whole point is what it raised
            lead_error.append(exc)
        finally:
            connection.close()

    admin_thread = threading.Thread(target=admin_publishes)
    admin_thread.start()
    assert holding.wait(10), "the admin thread never reached publish"
    lead_thread = threading.Thread(target=lead_decides)
    lead_thread.start()
    time.sleep(_REACH_THE_LOCK)
    release.set()
    admin_thread.join(20)
    lead_thread.join(20)
    offering.refresh_from_db()
    return lead_error[0] if lead_error else None


def describe_decide_serializes_on_the_offering_row():
    def it_does_not_escalate_to_an_admin_after_the_class_is_live(monkeypatch):
        lead_user = _lead_user()
        admin_user = _admin_user()
        offering = _submitted_offering(lead_user)

        error = _race(offering, admin_user, Decision.APPROVED, lead_user, monkeypatch)

        assert offering.status == ClassOffering.Status.PUBLISHED
        assert isinstance(error, ValueError)
        assert "Only pending" in str(error)
        # The lead's approval never landed, so the lane reads overridden and nothing told an
        # admin that a live class needs their validation.
        gl_row = ClassApproval.objects.get(class_offering=offering, role=Role.GUILD_LEAD)
        assert gl_row.decision == Decision.OVERRIDDEN_BY_ADMIN
        assert not Notification.objects.filter(trigger="class_validation_requested").exists()

    def it_does_not_unpublish_a_live_class_on_a_concurrent_request_for_changes(monkeypatch):
        lead_user = _lead_user()
        admin_user = _admin_user()
        offering = _submitted_offering(lead_user)

        error = _race(offering, admin_user, Decision.CHANGES_REQUESTED, lead_user, monkeypatch)

        assert offering.status == ClassOffering.Status.PUBLISHED
        assert isinstance(error, ValueError)
        assert "Only pending" in str(error)
        gl_row = ClassApproval.objects.get(class_offering=offering, role=Role.GUILD_LEAD)
        assert gl_row.decision == Decision.OVERRIDDEN_BY_ADMIN
        assert gl_row.notes != "Concurrent."
