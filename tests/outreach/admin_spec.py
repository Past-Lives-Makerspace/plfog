"""Admin changelist HTTP tests for the outreach app."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from tests.outreach.factories import BuyableFactory, BuyablePurchaseFactory, EventFactory, LeadFactory, TourFactory

User = get_user_model()


@pytest.fixture()
def admin_client():
    """Return a Django test client logged in as a superuser."""
    user = User.objects.create_superuser(
        username="outreach-admin-test",
        password="outreach-admin-pw",
        email="outreach-admin@example.com",
    )
    client = Client()
    client.force_login(user)
    return client


@pytest.mark.django_db
def describe_admin_lead_views():
    def it_loads_changelist(admin_client):
        LeadFactory(name="Changelist Lead")
        resp = admin_client.get("/admin/outreach/lead/")
        assert resp.status_code == 200


@pytest.mark.django_db
def describe_admin_tour_views():
    def it_loads_changelist(admin_client):
        TourFactory()
        resp = admin_client.get("/admin/outreach/tour/")
        assert resp.status_code == 200


@pytest.mark.django_db
def describe_admin_event_views():
    def it_loads_changelist(admin_client):
        EventFactory(name="Changelist Event")
        resp = admin_client.get("/admin/outreach/event/")
        assert resp.status_code == 200


@pytest.mark.django_db
def describe_admin_buyable_views():
    def it_loads_changelist(admin_client):
        BuyableFactory(name="Changelist Buyable")
        resp = admin_client.get("/admin/outreach/buyable/")
        assert resp.status_code == 200


@pytest.mark.django_db
def describe_admin_buyable_purchase_views():
    def it_loads_changelist(admin_client):
        BuyablePurchaseFactory()
        resp = admin_client.get("/admin/outreach/buyablepurchase/")
        assert resp.status_code == 200
