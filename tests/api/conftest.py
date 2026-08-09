import pytest
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from factory.django import mute_signals
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from membership.models import Member
from tests.membership.factories import MemberFactory

User = get_user_model()


def _make_member_with_user(fog_role: str, suffix: str) -> Member:
    """Create a Member with a linked User, fully outside the signal path.

    MemberFactory mutes post_save on Member (so no auto-provision fires).
    We then create the User with signals muted so nothing auto-creates a competing
    Member row, then link via queryset update() to stay out of the Member post_save path.
    """
    member = MemberFactory(fog_role=fog_role)
    with mute_signals(post_save):
        user = User.objects.create(
            username=f"{suffix}_{member.pk}",
            email=f"{suffix}_{member.pk}@test.invalid",
        )
    Member.objects.filter(pk=member.pk).update(user=user)
    member.user = user
    return member


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def admin_member(db):
    return _make_member_with_user(Member.FogRole.ADMIN, "fogadmin")


@pytest.fixture
def admin_token(admin_member):
    token, _ = Token.objects.get_or_create(user=admin_member.user)
    return token


@pytest.fixture
def admin_client(api_client, admin_token):
    api_client.credentials(HTTP_AUTHORIZATION=f"Token {admin_token.key}")
    return api_client


@pytest.fixture
def member_member(db):
    return _make_member_with_user(Member.FogRole.MEMBER, "fogmember")


@pytest.fixture
def member_token(member_member):
    token, _ = Token.objects.get_or_create(user=member_member.user)
    return token


@pytest.fixture
def member_client(api_client, member_token):
    api_client.credentials(HTTP_AUTHORIZATION=f"Token {member_token.key}")
    return api_client
