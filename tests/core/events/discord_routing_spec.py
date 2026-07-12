"""BDD specs for DB-backed Discord event→webhook routing (design §2.4, Decision 9)."""

from __future__ import annotations

import pytest

from core.events import discord as discord_module
from core.models import DiscordWebhookRoute

pytestmark = pytest.mark.django_db

_GLOBAL = "https://discord.example/global"
_ROUTED = "https://discord.example/guild-staff"


@pytest.fixture
def global_webhook(settings):
    """Configure a site-wide Discord webhook for the duration of a test."""
    settings.DISCORD_NOTIFY_WEBHOOK_URL = _GLOBAL
    return _GLOBAL


def describe_webhook_for_event_db_routing():
    def it_falls_back_to_the_global_webhook_with_no_route(global_webhook):
        assert discord_module.webhook_for_event("class_published") == global_webhook

    def it_uses_an_enabled_routes_url_over_the_global(global_webhook):
        DiscordWebhookRoute.objects.create(event_key="class_published", webhook_url=_ROUTED, is_enabled=True)
        assert discord_module.webhook_for_event("class_published") == _ROUTED

    def describe_an_enabled_route_with_a_blank_url():
        def it_silences_discord_for_that_event(global_webhook):
            # A deliberate disable: enabled but blank overrides the global with silence.
            DiscordWebhookRoute.objects.create(event_key="class_published", webhook_url="", is_enabled=True)
            assert discord_module.webhook_for_event("class_published") == ""

    def describe_a_disabled_route():
        def it_is_ignored_and_falls_back_to_global(global_webhook):
            DiscordWebhookRoute.objects.create(event_key="class_published", webhook_url=_ROUTED, is_enabled=False)
            assert discord_module.webhook_for_event("class_published") == global_webhook

    def it_only_routes_the_matching_event(global_webhook):
        DiscordWebhookRoute.objects.create(event_key="class_published", webhook_url=_ROUTED, is_enabled=True)
        assert discord_module.webhook_for_event("class_reminder") == global_webhook


def describe_route_model():
    def it_reports_effective_webhook_only_when_enabled():
        route = DiscordWebhookRoute(event_key="k", webhook_url=" {} ".format(_ROUTED), is_enabled=True)
        assert route.effective_webhook == _ROUTED.strip()
        route.is_enabled = False
        assert route.effective_webhook == ""

    def it_overrides_global_only_when_enabled():
        route = DiscordWebhookRoute(event_key="k", webhook_url=_ROUTED, is_enabled=True)
        assert route.overrides_global is True
        route.is_enabled = False
        assert route.overrides_global is False


def describe_save_routing():
    def it_creates_a_route_from_a_submitted_url():
        route = DiscordWebhookRoute.save_routing(
            event_key="class_published", submitted_url=_ROUTED, is_enabled=True, editor=None
        )
        assert route.webhook_url == _ROUTED
        assert route.is_enabled is True
        assert DiscordWebhookRoute.objects.filter(event_key="class_published").count() == 1

    def it_keeps_the_existing_url_when_the_submission_is_blank():
        DiscordWebhookRoute.objects.create(event_key="class_published", webhook_url=_ROUTED, is_enabled=True)
        route = DiscordWebhookRoute.save_routing(
            event_key="class_published", submitted_url="", is_enabled=False, editor=None
        )
        # A blank submission keeps the stored URL (toggling enabled never wipes it)…
        assert route.webhook_url == _ROUTED
        # …while the enabled toggle still applies.
        assert route.is_enabled is False

    def it_replaces_the_url_when_a_new_one_is_submitted():
        DiscordWebhookRoute.objects.create(event_key="class_published", webhook_url=_ROUTED, is_enabled=True)
        new_url = "https://discord.example/replacement"
        route = DiscordWebhookRoute.save_routing(
            event_key="class_published", submitted_url=new_url, is_enabled=True, editor=None
        )
        assert route.webhook_url == new_url

    def it_stores_blank_when_blank_is_submitted_with_no_existing_route():
        route = DiscordWebhookRoute.save_routing(
            event_key="class_reminder", submitted_url="", is_enabled=True, editor=None
        )
        assert route.webhook_url == ""

    def it_records_the_editor_when_one_has_a_pk():
        from django.contrib.auth.models import User

        editor = User.objects.create_user(username="route-editor", email="re@x.com")
        route = DiscordWebhookRoute.save_routing(
            event_key="class_published", submitted_url=_ROUTED, is_enabled=True, editor=editor
        )
        assert route.updated_by == editor

    def it_drops_an_editor_without_a_pk():
        from django.contrib.auth.models import User

        route = DiscordWebhookRoute.save_routing(
            event_key="class_published", submitted_url=_ROUTED, is_enabled=True, editor=User()
        )
        assert route.updated_by is None
