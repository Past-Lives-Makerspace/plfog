"""The Host a Workshop feature switch (#405), which is the awkward one of the seven.

Every other feature is a URL family you can fence in ``urls.py``. This one is not.
``classes:teach_overview`` is a single route with two faces — the instructor's dashboard and
the recruiting invitation — and ``teaching_member_required`` deliberately 302s a member
without teaching access *onto* it, so a locked deep link lands on the explainer instead of a
dead end. Gating that URL would 404 the redirect for every instructor-to-be.

So the gate sits on the branch, not the route: the invitation 404s, the dashboard never does.
The three recruiting routes beside it (``teach_why``, ``teach_apply``, ``teach_orientation``)
are fenced normally, because they have only the one face.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from django.utils import timezone

from membership.models import Member
from tests.features import coming_soon, hide, turn_on
from tests.membership.factories import MembershipPlanFactory, UserFactory

pytestmark = pytest.mark.django_db


def _login(client: Client, username: str, *, can_teach: bool) -> Member:
    """An ACTIVE member, logged in. A signal creates the Member with the User, so this reads
    that row back rather than making a second one."""
    MembershipPlanFactory()
    user = UserFactory(username=username)
    user.set_password("p")
    user.save()
    member = Member.objects.get(user=user)
    member.status = Member.Status.ACTIVE
    if can_teach:
        # What can_create_classes reads off — the same unlock the teaching portal checks.
        member.instructor_oriented_at = timezone.now()
    member.save()
    client.force_login(user)
    return member


def describe_a_member_who_cannot_teach_yet():
    def it_sees_the_invitation_while_the_feature_is_on(client: Client):
        turn_on("teach")
        _login(client, "invited_on", can_teach=False)
        assert client.get(reverse("classes:teach_overview")).status_code == 200

    def it_404s_the_invitation_when_the_feature_is_hidden(client: Client):
        hide("teach")
        _login(client, "invited_hidden", can_teach=False)
        assert client.get(reverse("classes:teach_overview")).status_code == 404

    def it_404s_the_invitation_when_the_feature_is_coming_soon(client: Client):
        coming_soon("teach", "Launching Sept 30th!")
        _login(client, "invited_soon", can_teach=False)
        assert client.get(reverse("classes:teach_overview")).status_code == 404


def describe_an_instructor():
    def it_keeps_the_dashboard_in_all_three_states(client: Client):
        """The carve-out that makes this switch safe to flip.

        An instructor's own teaching pages are not a recruiting surface, and an admin turning
        off recruitment must never take somebody's classes away from them.
        """
        _login(client, "instructor_states", can_teach=True)
        for state in (turn_on, hide):
            state("teach")
            assert client.get(reverse("classes:teach_overview")).status_code == 200
        coming_soon("teach", "Launching Sept 30th!")
        assert client.get(reverse("classes:teach_overview")).status_code == 200

    def it_keeps_the_teaching_portal_in_all_three_states(client: Client):
        _login(client, "instructor_portal", can_teach=True)
        hide("teach")
        assert client.get(reverse("classes:teach_dashboard")).status_code == 200
        assert client.get(reverse("classes:teach_class_create")).status_code == 200


def describe_the_recruiting_routes():
    def it_serves_them_while_the_feature_is_on(client: Client):
        turn_on("teach")
        _login(client, "why_on", can_teach=False)
        assert client.get(reverse("classes:teach_why")).status_code == 200

    def it_404s_them_when_the_feature_is_off(client: Client):
        hide("teach")
        _login(client, "why_off", can_teach=False)
        assert client.get(reverse("classes:teach_why")).status_code == 404
        assert client.post(reverse("classes:teach_apply")).status_code == 404

    def it_404s_them_for_an_instructor_too(client: Client):
        # Nobody bypasses (decision 1). The instructor keeps the portal, not the marketing page.
        hide("teach")
        _login(client, "why_off_instructor", can_teach=True)
        assert client.get(reverse("classes:teach_why")).status_code == 404
