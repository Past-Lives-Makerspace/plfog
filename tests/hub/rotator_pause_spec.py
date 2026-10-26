"""The rotators' lasting pause (#890): each rotator's name and the signed in user's pk, which pl-rotator.js keys it on."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.factories import AnnouncementFactory
from tests.membership.factories import CommunityEventFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

HOME = reverse("hub_home")


@pytest.fixture
def member(client: Client) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username="zorblax-rotator", email="zorblax-rotator@example.com")
    client.force_login(user)
    return user


def _two_events() -> None:
    for n in range(2):
        start = timezone.now() + timedelta(days=3 + n)
        CommunityEventFactory(community=True, title=f"Zorblax event {n}", starts_at=start)


def describe_the_user_on_the_page():
    def it_puts_the_signed_in_users_pk_on_html_and_never_the_email(client: Client, member: User):
        html = client.get(HOME).content.decode()

        assert f'<html lang="en" data-pl-user="{member.pk}">' in html
        assert 'data-pl-user="zorblax-rotator@example.com"' not in html

    def it_leaves_it_empty_when_signed_out(client: Client):
        html = client.get("/accounts/login/").content.decode()

        assert '<html lang="en" data-pl-user="">' in html

    def it_loads_the_rotator_script_on_signed_out_pages_so_they_forget_every_pause(client: Client):
        html = client.get("/accounts/login/").content.decode()

        assert "js/pl-rotator.js" in html

    def it_loads_it_on_the_logout_confirm_page_whose_form_clears_them(client: Client, member: User):
        html = client.get(reverse("account_logout")).content.decode()

        assert f'data-pl-user="{member.pk}"' in html
        assert "js/pl-rotator.js" in html
        assert f'action="{reverse("account_logout")}"' in html


def describe_the_names():
    def it_names_each_rotator_on_home_apart(client: Client, member: User):
        for n in range(2):
            AnnouncementFactory(position=n)
        _two_events()

        html = client.get(HOME).content.decode()

        assert 'x-data="plRotator" data-rotator-interval="8000" data-rotator-name="home-banner"' in html
        assert 'x-data="plUpcomingQueue" data-rotator-interval="6000" data-rotator-name="home-upcoming"' in html
        assert 'x-data="plRotator" data-rotator-interval="8000" data-rotator-name="spotlight"' in html

    def it_gives_the_admin_preview_its_own_name(client: Client):
        User.objects.create_superuser(username="zorblax-admin", password="pass", email="zorblax-admin@example.com")
        client.login(username="zorblax-admin", password="pass")
        for n in range(2):
            AnnouncementFactory(position=n)

        html = client.get(reverse("hub_admin_spotlight")).content.decode()
        preview = html.split("data-spotlight-preview", 1)[1]

        assert 'data-rotator-name="spotlight-preview"' in preview
        assert 'data-rotator-name="spotlight"' in html.split("data-spotlight-preview", 1)[0]
