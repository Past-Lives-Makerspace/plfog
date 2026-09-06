"""BDD specs for the hub home's Teaching card: absent when locked or empty, capped at three rows."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassApproval, ClassOffering
from hub.home import TEACHING_CAP, TeachingRow, _teaching
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory, UserFactory


@pytest.fixture
def instructor_user(db):
    MembershipPlanFactory()
    user = UserFactory(username="home-teacher@example.com")
    member = Member.objects.get(user=user)
    member.instructor_oriented_at = timezone.now()
    member.save(update_fields=["instructor_oriented_at"])
    return user


def _member(user) -> Member:
    return Member.objects.get(user=user)


def _bounced(member, title: str) -> ClassOffering:
    offering = ClassOfferingFactory(instructor=member, title=title, status=ClassOffering.Status.DRAFT)
    ClassApproval.objects.create(
        class_offering=offering,
        role=ClassApproval.Role.ADMIN,
        decision=ClassApproval.Decision.CHANGES_REQUESTED,
        notes="Add the price",
        decided_at=timezone.now(),
    )
    return offering


def describe_teaching_card():
    def it_is_absent_for_a_locked_member_even_with_classes(db, client):
        MembershipPlanFactory()
        user = UserFactory(username="locked-home@example.com")
        ClassOfferingFactory(instructor=_member(user), status=ClassOffering.Status.DRAFT)
        client.force_login(user)
        resp = client.get(reverse("hub_home"))
        assert resp.context["teaching"] == []
        assert b'data-block="teaching"' not in resp.content

    def it_is_absent_when_there_is_nothing_to_show(instructor_user, client):
        client.force_login(instructor_user)
        resp = client.get(reverse("hub_home"))
        assert resp.context["teaching"] == []
        assert b'data-block="teaching"' not in resp.content

    def it_renders_the_rows_in_priority_order(instructor_user, client):
        member = _member(instructor_user)
        bounced = _bounced(member, "Bounced Bowls")
        draft = ClassOfferingFactory(instructor=member, title="Draft Spoons", status=ClassOffering.Status.DRAFT)
        pending = ClassOfferingFactory(instructor=member, title="Pending Pots", status=ClassOffering.Status.PENDING)
        client.force_login(instructor_user)
        resp = client.get(reverse("hub_home"))
        rows = resp.context["teaching"]
        assert [(r.title, r.cta) for r in rows] == [
            ("Bounced Bowls", "Fix and resubmit"),
            ("Draft Spoons", "Finish"),
            ("Pending Pots", "Open"),
        ]
        assert rows[0].note == "An admin asked for changes: Add the price"
        assert rows[0].url == reverse("classes:teach_class_edit", kwargs={"pk": bounced.pk})
        assert rows[1].url == reverse("classes:teach_class_edit", kwargs={"pk": draft.pk})
        assert rows[2].note == "Awaiting admin"
        assert rows[2].url == reverse("classes:teach_class_detail", kwargs={"pk": pending.pk})
        html = resp.content.decode()
        assert 'data-block="teaching"' in html
        assert "Fix and resubmit" in html
        assert "Open teaching" in html
        assert reverse("classes:teach_overview") in html

    def it_shows_this_weeks_session_time_with_a_roster_link(instructor_user):
        member = _member(instructor_user)
        live = ClassOfferingFactory(instructor=member, title="Live Lathe", status=ClassOffering.Status.PUBLISHED)
        start = timezone.now() + timedelta(days=2)
        ClassSessionFactory(class_offering=live, starts_at=start, ends_at=start + timedelta(hours=2))
        far = ClassOfferingFactory(instructor=member, title="Far Forge", status=ClassOffering.Status.PUBLISHED)
        later = timezone.now() + timedelta(days=20)
        ClassSessionFactory(class_offering=far, starts_at=later, ends_at=later + timedelta(hours=2))
        rows = _teaching(member)
        assert [r.title for r in rows] == ["Live Lathe"]
        assert rows[0].cta == "Roster"
        assert rows[0].url == reverse("classes:teach_class_registrations", kwargs={"pk": live.pk})
        assert timezone.localtime(start).strftime("%a %b %-d") in rows[0].note

    def it_caps_at_three_rows(instructor_user):
        member = _member(instructor_user)
        for i in range(5):
            _bounced(member, f"Bounced {i}")
        rows = _teaching(member)
        assert len(rows) == TEACHING_CAP
        assert all(isinstance(r, TeachingRow) for r in rows)
