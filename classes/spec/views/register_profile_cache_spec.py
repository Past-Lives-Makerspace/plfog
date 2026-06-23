"""Logged-in registration caches overlapping answers onto the user's profile."""

from __future__ import annotations

import pytest
from django.urls import reverse

from classes.spec.views.register_spec import _post_data

pytestmark = pytest.mark.django_db


def describe_registration_profile_cache():
    def it_caches_pronouns_and_phone_for_a_logged_in_user(free_offering, client, member_user):
        from core.models import UserProfile

        client.force_login(member_user)
        data = _post_data(pronouns="xe/xem", phone="503-555-9999")
        resp = client.post(reverse("classes:register", kwargs={"slug": free_offering.slug}), data=data)
        assert resp.status_code == 302
        profile = UserProfile.objects.get(user=member_user)
        assert profile.pronouns == "xe/xem"
        assert profile.phone == "503-555-9999"

    def it_does_not_clobber_an_existing_profile_value(free_offering, client, member_user):
        from core.models import UserProfile

        UserProfile.objects.create(user=member_user, pronouns="she/her")
        client.force_login(member_user)
        data = _post_data(pronouns="xe/xem", phone="503-555-9999")
        client.post(reverse("classes:register", kwargs={"slug": free_offering.slug}), data=data)
        profile = UserProfile.objects.get(user=member_user)
        assert profile.pronouns == "she/her"  # untouched
        assert profile.phone == "503-555-9999"  # was empty → filled

    def it_does_nothing_for_an_anonymous_registrant(free_offering, client):
        from core.models import UserProfile

        data = _post_data(pronouns="xe/xem", phone="503-555-9999")
        resp = client.post(reverse("classes:register", kwargs={"slug": free_offering.slug}), data=data)
        assert resp.status_code == 302
        assert not UserProfile.objects.exists()
