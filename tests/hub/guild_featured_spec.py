"""Guild featured pinned class — scoped picker + spotlight on the page."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory
from classes.models import Category, ClassOffering
from hub.forms import GuildEditForm
from tests.membership.factories import GuildFactory, MembershipPlanFactory


def _member(username: str) -> User:
    MembershipPlanFactory()
    return User.objects.create_user(username=username, password="pw")


def _with_session(offering: ClassOffering, days_from_now: int) -> ClassOffering:
    starts = timezone.now() + timedelta(days=days_from_now)
    ClassSessionFactory(class_offering=offering, starts_at=starts, ends_at=starts + timedelta(hours=2))
    return offering


def _published(category: Category, slug: str, **kwargs) -> ClassOffering:
    return ClassOfferingFactory(category=category, status=ClassOffering.Status.PUBLISHED, slug=slug, **kwargs)


@pytest.mark.django_db
def describe_featured_class():
    def it_scopes_choices_to_the_guilds_upcoming_published_classes():
        guild = GuildFactory()
        category = CategoryFactory(guild=guild)
        mine = _with_session(_published(category, "feat-mine"), days_from_now=7)
        draft = _with_session(
            ClassOfferingFactory(category=category, status=ClassOffering.Status.DRAFT, slug="feat-draft"),
            days_from_now=7,
        )
        other = _with_session(
            _published(CategoryFactory(guild=GuildFactory()), "feat-other"),
            days_from_now=7,
        )
        choices = list(GuildEditForm(instance=guild).fields["featured_class"].queryset)
        assert mine in choices
        assert draft not in choices
        assert other not in choices

    def it_excludes_a_finished_run_but_keeps_the_saved_pick_valid():
        # Last year's run stays PUBLISHED forever; it must not be offered, but a guild
        # that already spotlights it keeps a valid selection so saves never block.
        guild = GuildFactory()
        category = CategoryFactory(guild=guild)
        past = _with_session(_published(category, "feat-past"), days_from_now=-365)
        upcoming = _with_session(_published(category, "feat-upcoming"), days_from_now=14)

        choices = list(GuildEditForm(instance=guild).fields["featured_class"].queryset)
        assert past not in choices
        assert upcoming in choices

        guild.featured_class = past
        guild.save(update_fields=["featured_class"])
        kept = list(GuildEditForm(instance=guild).fields["featured_class"].queryset)
        assert past in kept
        assert upcoming in kept

    def it_labels_options_with_the_next_session_date():
        # duplicate_as_new_run keeps the title verbatim, so two runs of "Spooky Class"
        # are indistinguishable without a date on the option label.
        guild = GuildFactory()
        category = CategoryFactory(guild=guild)
        run_date = timezone.now() + timedelta(days=30)
        offering = _published(category, "feat-dated", title="Spooky Class")
        ClassSessionFactory(class_offering=offering, starts_at=run_date, ends_at=run_date + timedelta(hours=2))

        field = GuildEditForm(instance=guild).fields["featured_class"]
        label = field.label_from_instance(offering)
        assert label == f"Spooky Class ({timezone.localtime(run_date).strftime('%b %-d, %Y')})"

    def it_labels_a_dateless_option_with_the_bare_title():
        guild = GuildFactory()
        offering = _published(CategoryFactory(guild=guild), "feat-bare", title="Open Studio")
        guild.featured_class = offering
        guild.save(update_fields=["featured_class"])
        field = GuildEditForm(instance=guild).fields["featured_class"]
        assert field.label_from_instance(offering) == "Open Studio"

    def it_offers_no_choices_for_an_unsaved_guild():
        assert list(GuildEditForm().fields["featured_class"].queryset) == []

    def it_shows_the_featured_class_on_the_guild_page(client: Client):
        _member("f1")
        client.login(username="f1", password="pw")
        guild = GuildFactory()
        offering = _with_session(
            ClassOfferingFactory(
                category=CategoryFactory(guild=guild),
                status=ClassOffering.Status.PUBLISHED,
                title="Featured Forge",
                slug="featured-forge",
            ),
            days_from_now=21,
        )
        guild.featured_class = offering
        guild.save(update_fields=["featured_class"])
        resp = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert b"Featured Forge" in resp.content
        assert b"Featured" in resp.content
        assert reverse("classes:register", args=["featured-forge"]).encode() in resp.content
        session_at = offering.first_upcoming_session_at
        assert session_at is not None
        assert timezone.localtime(session_at).strftime("%b").encode() in resp.content
