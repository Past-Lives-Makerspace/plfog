"""Shared fixtures for facility tour specs (#947)."""

from __future__ import annotations

import pytest
from django.test import Client

from membership.models import AdminCapability, Member
from membership.services.provisioning import provision_user_for_member
from tests.membership.factories import MemberFactory


def signed_in(member: Member) -> Client:
    """A test client signed in as ``member`` (a linked User is made if needed)."""
    if member.user is None:
        provision_user_for_member(member)
        member.refresh_from_db()
    client = Client()
    client.force_login(member.user)
    return client


def make_guide(**kwargs: object) -> Member:
    """An active member holding the Tour Guide permission."""
    member = MemberFactory(**kwargs)
    member.admin_capabilities.create(capability=AdminCapability.Capability.TOUR_GUIDE)
    return member


@pytest.fixture
def guide(db) -> Member:
    return make_guide(full_legal_name="Gina Guide")


@pytest.fixture
def admin(db) -> Member:
    return MemberFactory(full_legal_name="Ada Admin", fog_role=Member.FogRole.ADMIN)


@pytest.fixture
def plain(db) -> Member:
    return MemberFactory(full_legal_name="Pat Plain")


@pytest.fixture
def google_on(settings) -> None:
    """Google sign in for Tour Guides is set up (#951); every call to Google is mocked in the spec."""
    settings.GOOGLE_OAUTH_CLIENT_ID = "client-id"
    settings.GOOGLE_OAUTH_CLIENT_SECRET = "client-secret"
    settings.MEMBER_BASE_URL = "https://members.example.org"
