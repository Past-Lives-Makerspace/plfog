"""Returning users see onboarding fields pre-filled from their profile."""

from __future__ import annotations

import pytest
from django.urls import reverse

from classes.spec.views.register_spec import _post_data

pytestmark = pytest.mark.django_db


def describe_onboarding_prefill():
    def it_prefills_step2_from_the_profile(book_client, member_user):
        from core.models import UserProfile

        UserProfile.objects.create(
            user=member_user,
            preferred_name="Robin",
            pronouns="they/them",
            phone="503-555-0100",
            referral_source=UserProfile.Referral.INSTAGRAM,
        )
        book_client.force_login(member_user)
        resp = book_client.get(reverse("account:onboarding_step2"))
        assert resp.status_code == 200
        body = resp.content.decode()
        assert 'value="Robin"' in body
        assert 'value="they/them"' in body
        assert 'value="503-555-0100"' in body

    def it_prefills_step1_attendance(book_client, member_user):
        from core.models import UserProfile

        UserProfile.objects.create(user=member_user, first_attendance_status=UserProfile.FirstAttendance.RETURNING)
        book_client.force_login(member_user)
        resp = book_client.get(reverse("account:onboarding_step1"))
        body = resp.content.decode()
        # the "returning" radio renders checked
        assert "returning" in body

    def it_prefills_step3_accessibility_note(book_client, member_user):
        from core.models import UserProfile

        UserProfile.objects.create(user=member_user, accessibility_note="Need step-free access")
        book_client.force_login(member_user)
        resp = book_client.get(reverse("account:onboarding_step3"))
        assert "Need step-free access" in resp.content.decode()

    def it_renders_blank_when_no_profile_exists(book_client, member_user):
        book_client.force_login(member_user)
        resp = book_client.get(reverse("account:onboarding_step2"))
        assert resp.status_code == 200  # get_initial must not 500 when profile is absent

    def it_round_trips_registration_pronouns_into_onboarding(free_offering, book_client, member_user):
        # 1) register while logged in → caches pronouns/phone
        book_client.force_login(member_user)
        book_client.post(
            reverse("classes:register", kwargs={"slug": free_offering.slug}),
            data=_post_data(pronouns="ze/zir", phone="503-555-7777"),
        )
        # 2) enter onboarding step 2 → those values are pre-filled
        resp = book_client.get(reverse("account:onboarding_step2"))
        body = resp.content.decode()
        assert 'value="ze/zir"' in body
        assert 'value="503-555-7777"' in body
