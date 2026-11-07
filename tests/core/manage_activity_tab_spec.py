"""The /manage/activity/ tab parameter only ever reaches the page as a known tab name."""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client(client):
    User.objects.create_user(username="a", email="a@example.com", password="pw12345!", is_staff=True)
    client.login(username="a", password="pw12345!")
    return client


def describe_activity_tab():
    def it_opens_the_tab_it_is_asked_for(staff_client):
        resp = staff_client.get(reverse("manage_activity"), {"tab": "emails"})
        assert resp.context["active_tab"] == "emails"

    def it_falls_back_to_the_feed_for_an_unknown_tab(staff_client):
        resp = staff_client.get(reverse("manage_activity"), {"tab": "';alert(1)//"})
        assert resp.context["active_tab"] == "feed"
        assert b"alert(1)" not in resp.content

    def it_opens_the_email_log_for_a_ring_on_recipient(staff_client):
        resp = staff_client.get(reverse("manage_activity"), {"show": "activity.email-search"})
        assert resp.context["active_tab"] == "emails"

    def it_keeps_an_explicit_tab_over_a_ring(staff_client):
        resp = staff_client.get(reverse("manage_activity"), {"tab": "queue", "show": "activity.email-search"})
        assert resp.context["active_tab"] == "queue"

    def it_opens_new_members_when_asked(staff_client):
        resp = staff_client.get(reverse("manage_activity"), {"tab": "newmembers"})
        assert resp.context["active_tab"] == "newmembers"
        assert b"tabs: ['feed', 'emails', 'queue', 'newmembers']" in resp.content
        assert b'data-composer-step="4"' in resp.content

    def it_opens_new_members_for_a_ring_on_the_lookup(staff_client):
        resp = staff_client.get(reverse("manage_activity"), {"show": "activity.new-members"})
        assert resp.context["active_tab"] == "newmembers"
        assert b'data-guidance-target="activity.new-members"' in resp.content
