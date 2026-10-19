"""Shared fixtures for the Holiday Bizarre HQ specs (Track C), in the kiln specs' shape."""

from __future__ import annotations

import io
from collections.abc import Callable

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from PIL import Image

from hq.models import EventHub
from membership.models import Member
from membership.services.provisioning import provision_user_for_member
from tests.hq.factories import EventHubFactory
from tests.membership.factories import MemberFactory


@pytest.fixture(autouse=True)
def _media_in_tmp(settings, tmp_path):
    """Hero images land in a throwaway folder, never the checkout's media/."""
    settings.MEDIA_ROOT = str(tmp_path / "media")


def signed_in(member: Member) -> Client:
    """A test client signed in as ``member`` (a linked User is made if needed)."""
    if member.user is None:
        provision_user_for_member(member)
        member.refresh_from_db()
    client = Client()
    client.force_login(member.user)
    return client


@pytest.fixture
def make_member(db) -> Callable[..., Member]:
    """A member with a login, any status."""

    def _make(status: str = Member.Status.ACTIVE, **kwargs: object) -> Member:
        member = MemberFactory(**kwargs)
        provision_user_for_member(member)
        member.refresh_from_db()
        if member.status != status:
            member.status = status
            member.save(update_fields=["status"])
        return member

    return _make


@pytest.fixture
def admin(make_member) -> Member:
    return make_member(fog_role=Member.FogRole.ADMIN)


@pytest.fixture
def admin_client(admin) -> Client:
    return signed_in(admin)


@pytest.fixture
def member(make_member) -> Member:
    return make_member()


@pytest.fixture
def member_client(member) -> Client:
    return signed_in(member)


@pytest.fixture
def open_hub(db) -> EventHub:
    """The hub the sidebar and /hq/ land on."""
    return EventHubFactory(is_open=True, slug="holiday-bizarre", title="Holiday Bizarre HQ")


def photo_upload(name: str = "hero.jpg", size: tuple[int, int] = (1200, 450)) -> SimpleUploadedFile:
    """A real JPEG the form can open and the model can resize."""
    buffer = io.BytesIO()
    Image.new("RGB", size, (9, 46, 76)).save(buffer, format="JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")
