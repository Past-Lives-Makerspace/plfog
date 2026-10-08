"""Shared fixtures for kiln ticket specs (#691)."""

from __future__ import annotations

import io
from collections.abc import Callable

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from PIL import Image

from membership.models import Guild, Member
from membership.services.provisioning import provision_user_for_member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, MemberFactory


@pytest.fixture(autouse=True)
def _media_in_tmp(settings, tmp_path):
    """Photos land in a throwaway folder, never the checkout's media/."""
    settings.MEDIA_ROOT = str(tmp_path / "media")


def set_kiln_open(is_open: bool) -> None:
    """Flip the launch switch (``SiteConfiguration.kiln_tickets_open``)."""
    from core.models import SiteConfiguration

    config = SiteConfiguration.load()
    config.kiln_tickets_open = is_open
    config.save(update_fields=["kiln_tickets_open"])


@pytest.fixture(autouse=True)
def _kiln_open(db) -> None:
    """Most kiln specs describe the launched feature; ``launch_switch_spec`` turns it off."""
    set_kiln_open(True)


@pytest.fixture
def kiln_guild(db) -> Guild:
    """The Ceramics Guild, found by its slug like production's guild 8."""
    return GuildFactory(name="Ceramics Guild", slug="ceramics-guild")


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
def maker(make_member) -> Member:
    return make_member()


@pytest.fixture
def maker_client(maker) -> Client:
    return signed_in(maker)


@pytest.fixture
def crew(make_member, kiln_guild) -> Member:
    member = make_member()
    GuildStaffMembershipFactory(guild=kiln_guild, member=member)
    return member


@pytest.fixture
def crew_client(crew) -> Client:
    return signed_in(crew)


def photo_upload(name: str = "pot.jpg", size: tuple[int, int] = (1200, 900)) -> SimpleUploadedFile:
    """A real JPEG the form can open and the model can resize."""
    buffer = io.BytesIO()
    Image.new("RGB", size, (150, 90, 60)).save(buffer, format="JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")
