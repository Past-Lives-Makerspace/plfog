"""BDD specs for the rewritten teaching-portal gate (Spec D §5).

``teaching_member_required`` now admits only active members who completed the
instructor orientation: non-members/inactive keep the 403, locked active
members are 302'd to the orientation page (never a dead end), unlocked and
grandfathered members pass. Public and registrant routes are untouched.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory, InstructorFactory, UserFactory
from classes.models import ClassOffering
from membership.models import Member

# A representative slice of the 21 teach_* routes — overview, create,
# registrations, dashboard, and profile all sit behind the same decorator.
TEACH_ROUTES = [
    "classes:teach_overview",
    "classes:teach_class_create",
    "classes:teach_registrations",
    "classes:teach_dashboard",
    "classes:teach_profile",
]


def _locked_member_user(username: str):
    """An ACTIVE member (auto-provisioned, no unlock) and their user."""
    user = UserFactory(username=username)
    member = Member.objects.get(user=user)
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["status"])
    return user, member


def describe_teaching_member_required():
    @pytest.mark.parametrize("route", TEACH_ROUTES)
    def it_redirects_anonymous_to_login(db, client, route):
        response = client.get(reverse(route))
        assert response.status_code == 302
        assert "/login/" in response["Location"] or "login" in response["Location"]

    @pytest.mark.parametrize("route", TEACH_ROUTES)
    def it_403s_an_inactive_member(db, client, route):
        user = UserFactory(username=f"inactive-{route.split(':')[1]}@example.com")
        member = Member.objects.get(user=user)
        member.status = Member.Status.FORMER
        member.save(update_fields=["status"])
        client.force_login(user)
        assert client.get(reverse(route)).status_code == 403

    @pytest.mark.parametrize("route", TEACH_ROUTES)
    def it_302s_a_locked_active_member_to_the_orientation_page(db, client, route):
        user, _ = _locked_member_user(f"locked-{route.split(':')[1]}@example.com")
        client.force_login(user)
        response = client.get(reverse(route))
        assert response.status_code == 302
        assert response["Location"] == reverse("classes:teach_orientation")

    @pytest.mark.parametrize("route", TEACH_ROUTES)
    def it_200s_an_unlocked_active_member(db, client, route):
        user, member = _locked_member_user(f"unlocked-{route.split(':')[1]}@example.com")
        member.instructor_oriented_at = timezone.now()
        member.save(update_fields=["instructor_oriented_at"])
        client.force_login(user)
        assert client.get(reverse(route)).status_code == 200

    def it_tells_an_unlocked_member_without_a_slug_when_their_page_goes_live(db, client):
        user, member = _locked_member_user("unlocked-profile@example.com")
        member.instructor_oriented_at = timezone.now()
        member.save(update_fields=["instructor_oriented_at"])
        client.force_login(user)
        response = client.get(reverse("classes:teach_profile"))
        assert response.status_code == 200
        assert b"goes live with your first published class" in response.content

    def it_200s_a_grandfathered_instructor(db, client):
        # InstructorFactory mirrors the 0110 backfill: slug holders carry the unlock.
        user = UserFactory(username="grandfathered@example.com")
        InstructorFactory(user=user, full_legal_name="Grand Parent", instructor_slug="grand-parent")
        client.force_login(user)
        assert client.get(reverse("classes:teach_overview")).status_code == 200

    def describe_ungated_routes():
        def it_serves_the_public_catalog_regardless_of_lock_state(db, client):
            user, _ = _locked_member_user("locked-catalog@example.com")
            client.force_login(user)
            assert client.get(reverse("classes:public_list")).status_code == 200

        def it_serves_the_tokenized_review_page_to_a_locked_reviewer(db, client):
            # The guild-lead review path never crosses the portal gate — a locked
            # (or revoked) reviewer still acts through the emailed token link.
            offering = ClassOfferingFactory(ready=True, status=ClassOffering.Status.DRAFT)
            (row,) = offering.submit_for_review()
            user, _ = _locked_member_user("locked-reviewer@example.com")
            client.force_login(user)
            response = client.get(reverse("classes:class_review", kwargs={"token": row.token}))
            assert response.status_code == 200
