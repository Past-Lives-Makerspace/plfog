"""BDD specs for the Registration -> Mailchimp subscribe bridge."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    InstructorFactory,
    RegistrationFactory,
)
from classes.models import Registration
from classes.services.mailchimp_subscribe import derive_tags, subscribe_registration
from core.models import SiteConfiguration

pytestmark = pytest.mark.django_db


@pytest.fixture
def site_with_mailchimp():
    site = SiteConfiguration.load()
    site.mailchimp_api_key = "abc-us17"
    site.mailchimp_list_id = "LISTID"
    site.save()
    return site


def describe_subscribe_registration():
    def it_does_nothing_when_user_did_not_opt_in(site_with_mailchimp):
        reg = RegistrationFactory(wants_newsletter=False)
        with patch("core.integrations.mailchimp.MailchimpClient.subscribe") as spy:
            subscribe_registration(reg)
        spy.assert_not_called()

    def it_does_nothing_when_already_subscribed(site_with_mailchimp):
        reg = RegistrationFactory(wants_newsletter=True, subscribed_to_mailchimp=True)
        with patch("core.integrations.mailchimp.MailchimpClient.subscribe") as spy:
            subscribe_registration(reg)
        spy.assert_not_called()

    def it_does_nothing_when_mailchimp_disabled():
        # No site config — client.enabled is False
        reg = RegistrationFactory(wants_newsletter=True)
        with patch("core.integrations.mailchimp.MailchimpClient.subscribe") as spy:
            subscribe_registration(reg)
        spy.assert_not_called()
        reg.refresh_from_db()
        assert reg.subscribed_to_mailchimp is False

    def it_calls_subscribe_with_derived_tags(site_with_mailchimp):
        category = CategoryFactory(name="Glass", slug="glass")
        instructor = InstructorFactory(slug="bea")
        offering = ClassOfferingFactory(category=category, instructor=instructor)
        reg = RegistrationFactory(
            class_offering=offering,
            wants_newsletter=True,
            email="ada@example.com",
            first_name="Ada",
            last_name="Lovelace",
        )
        with patch(
            "core.integrations.mailchimp.MailchimpClient.subscribe",
            return_value=True,
        ) as spy:
            subscribe_registration(reg)
        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        assert kwargs["email"] == "ada@example.com"
        assert kwargs["first_name"] == "Ada"
        assert kwargs["last_name"] == "Lovelace"
        assert "class-registrant" in kwargs["tags"]
        assert "category-glass" in kwargs["tags"]
        assert "instructor-bea" in kwargs["tags"]

    def it_sets_subscribed_flag_on_success(site_with_mailchimp):
        reg = RegistrationFactory(wants_newsletter=True)
        with patch(
            "core.integrations.mailchimp.MailchimpClient.subscribe",
            return_value=True,
        ):
            subscribe_registration(reg)
        reg.refresh_from_db()
        assert reg.subscribed_to_mailchimp is True

    def it_does_not_set_flag_on_failure(site_with_mailchimp):
        reg = RegistrationFactory(wants_newsletter=True)
        with patch(
            "core.integrations.mailchimp.MailchimpClient.subscribe",
            return_value=False,
        ):
            subscribe_registration(reg)
        reg.refresh_from_db()
        assert reg.subscribed_to_mailchimp is False


def describe_derive_tags():
    def it_includes_category_and_instructor_slugs():
        category = CategoryFactory(name="Wood", slug="wood")
        instructor = InstructorFactory(slug="alex")
        offering = ClassOfferingFactory(category=category, instructor=instructor)
        reg = RegistrationFactory(class_offering=offering)
        tags = derive_tags(reg)
        assert "class-registrant" in tags
        assert "category-wood" in tags
        assert "instructor-alex" in tags

    def it_includes_guild_tag_when_category_has_guild():
        from tests.membership.factories import GuildFactory

        guild = GuildFactory(name="Woodworkers Guild")
        category = CategoryFactory(slug="wood", guild=guild)
        offering = ClassOfferingFactory(category=category)
        reg = RegistrationFactory(class_offering=offering)
        tags = derive_tags(reg)
        assert "guild-woodworkers-guild" in tags

    def it_omits_guild_tag_when_category_has_no_guild():
        category = CategoryFactory(slug="wood", guild=None)
        offering = ClassOfferingFactory(category=category)
        reg = RegistrationFactory(class_offering=offering)
        tags = derive_tags(reg)
        assert not any(t.startswith("guild-") for t in tags)

    def it_tags_first_time_students():
        reg = RegistrationFactory(email="new@example.com")
        tags = derive_tags(reg)
        assert "first-time-student" in tags

    def it_does_not_tag_first_time_when_prior_confirmed_exists():
        # Pre-existing confirmed registration under the same email — same user, second class.
        RegistrationFactory(email="repeat@example.com", status=Registration.Status.CONFIRMED)
        reg = RegistrationFactory(email="repeat@example.com")
        tags = derive_tags(reg)
        assert "first-time-student" not in tags
