"""Guild featured pinned class — scoped picker + spotlight on the page."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from classes.factories import CategoryFactory, ClassOfferingFactory
from classes.models import ClassOffering
from hub.forms import GuildEditForm
from tests.membership.factories import GuildFactory, MembershipPlanFactory


def _member(username: str) -> User:
    MembershipPlanFactory()
    return User.objects.create_user(username=username, password="pw")


@pytest.mark.django_db
def describe_featured_class():
    def it_scopes_choices_to_the_guilds_published_classes():
        guild = GuildFactory()
        mine = ClassOfferingFactory(
            category=CategoryFactory(guild=guild), status=ClassOffering.Status.PUBLISHED, slug="feat-mine"
        )
        draft = ClassOfferingFactory(
            category=CategoryFactory(guild=guild), status=ClassOffering.Status.DRAFT, slug="feat-draft"
        )
        other = ClassOfferingFactory(
            category=CategoryFactory(guild=GuildFactory()), status=ClassOffering.Status.PUBLISHED, slug="feat-other"
        )
        choices = list(GuildEditForm(instance=guild).fields["featured_class"].queryset)
        assert mine in choices
        assert draft not in choices
        assert other not in choices

    def it_offers_no_choices_for_an_unsaved_guild():
        assert list(GuildEditForm().fields["featured_class"].queryset) == []

    def it_shows_the_featured_class_on_the_guild_page(client: Client):
        _member("f1")
        client.login(username="f1", password="pw")
        guild = GuildFactory()
        offering = ClassOfferingFactory(
            category=CategoryFactory(guild=guild),
            status=ClassOffering.Status.PUBLISHED,
            title="Featured Forge",
            slug="featured-forge",
        )
        guild.featured_class = offering
        guild.save(update_fields=["featured_class"])
        resp = client.get(reverse("hub_guild_detail", args=[guild.pk]))
        assert b"Featured Forge" in resp.content
        assert b"Featured" in resp.content
        assert reverse("classes:register", args=["featured-forge"]).encode() in resp.content
