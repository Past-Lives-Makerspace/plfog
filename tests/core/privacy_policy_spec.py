"""BDD specs for the public privacy policy page."""

import pytest
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
def describe_privacy_policy():
    def it_is_reachable_without_login(client: Client):
        response = client.get(reverse("privacy_policy"))

        assert response.status_code == 200

    def it_renders_the_policy_template(client: Client):
        response = client.get(reverse("privacy_policy"))

        assert "core/privacy_policy.html" in [t.name for t in response.templates]

    def it_shows_the_contact_email(client: Client):
        response = client.get(reverse("privacy_policy"))

        assert b"info@pastlives.space" in response.content

    def it_discloses_the_third_party_processors(client: Client):
        response = client.get(reverse("privacy_policy"))

        for needle in (b"Stripe", b"Mailchimp", b"Google Analytics", b"Discord"):
            assert needle in response.content

    def it_states_the_google_limited_use_terms_google_verification_requires(client: Client):
        response = client.get(reverse("privacy_policy"))

        assert b"Google Calendar for Tour Guides" in response.content
        assert b"https://developers.google.com/terms/api-services-user-data-policy" in response.content
        assert b"including the Limited Use requirements" in response.content

    def it_rejects_non_get_methods(client: Client):
        response = client.post(reverse("privacy_policy"))

        assert response.status_code == 405
