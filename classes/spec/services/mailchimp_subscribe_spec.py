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
    def it_subscribes_a_registration_whose_hidden_checkbox_was_recorded_as_opted_in(site_with_mailchimp):
        # RegistrationForm.save() sets wants_newsletter=True when it suppressed the
        # checkbox, so the class tags still go out for a returning opted-in member.
        reg = RegistrationFactory(wants_newsletter=True)
        with patch("core.integrations.mailchimp.MailchimpClient.subscribe", return_value=True) as spy:
            subscribe_registration(reg)
        spy.assert_called_once()
        assert "class-registrant" in spy.call_args.kwargs["tags"]

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
        instructor = InstructorFactory(instructor_slug="bea")
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

    def it_stamps_profile_timestamp_for_a_logged_in_registrant(site_with_mailchimp):
        from django.contrib.auth import get_user_model

        from core.models import UserProfile
        from membership.models import Member

        user = get_user_model().objects.create_user(username="opt", email="opt@example.com", password="x")
        profile = UserProfile.objects.create(user=user)
        # The User post_save signal already auto-created a linked Member.
        member = Member.objects.get(user=user)
        reg = RegistrationFactory(wants_newsletter=True, member=member)
        with patch(
            "core.integrations.mailchimp.MailchimpClient.subscribe",
            return_value=True,
        ):
            subscribe_registration(reg)
        profile.refresh_from_db()
        assert profile.subscribed_to_mailchimp_at is not None

    def it_does_not_error_for_an_anonymous_registrant(site_with_mailchimp):
        reg = RegistrationFactory(wants_newsletter=True, member=None)
        with patch(
            "core.integrations.mailchimp.MailchimpClient.subscribe",
            return_value=True,
        ):
            subscribe_registration(reg)  # must not raise
        reg.refresh_from_db()
        assert reg.subscribed_to_mailchimp is True


def describe_derive_tags():
    def it_includes_category_and_instructor_slugs():
        category = CategoryFactory(name="Wood", slug="wood")
        instructor = InstructorFactory(instructor_slug="alex")
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

    def describe_member_suppression():
        def it_suppresses_first_time_for_a_verified_member(db):
            # A linked member whose verified EmailAddress (not _pre_signup_email)
            # matches the registration email — exercises the EmailAddress arm.
            from django.contrib.auth import get_user_model

            from membership.models import Member

            user = get_user_model().objects.create_user(username="known", email="known@example.com", password="x")
            # The User post_save signal already linked a Member and created a
            # verified primary EmailAddress for known@example.com. Move the
            # member's stored pre-signup email aside so only the verified
            # EmailAddress can match.
            member = Member.objects.get(user=user)
            member._pre_signup_email = "other@example.com"
            member.save(update_fields=["_pre_signup_email"])
            reg = RegistrationFactory(email="known@example.com")
            tags = derive_tags(reg)
            assert "first-time-student" not in tags

        def it_suppresses_first_time_for_an_airtable_imported_member(db):
            # Unlinked member imported from Airtable: email lives only in
            # Member._pre_signup_email, no User / EmailAddress yet.
            from tests.membership.factories import MemberFactory

            MemberFactory(user=None, _pre_signup_email="imported@example.com")
            reg = RegistrationFactory(email="imported@example.com")
            tags = derive_tags(reg)
            assert "first-time-student" not in tags

        def it_suppresses_first_time_for_a_staged_member_email(db):
            from membership.models import MemberEmail
            from tests.membership.factories import MemberFactory

            member = MemberFactory(user=None, _pre_signup_email="primary@example.com")
            MemberEmail.objects.create(member=member, email="alias@example.com")
            reg = RegistrationFactory(email="alias@example.com")
            tags = derive_tags(reg)
            assert "first-time-student" not in tags

        def it_still_tags_a_brand_new_non_member(db):
            reg = RegistrationFactory(email="stranger@example.com")
            tags = derive_tags(reg)
            assert "first-time-student" in tags

        def it_matches_member_email_case_insensitively(db):
            from tests.membership.factories import MemberFactory

            MemberFactory(user=None, _pre_signup_email="Mixed@Example.com")
            reg = RegistrationFactory(email="mixed@example.com")
            tags = derive_tags(reg)
            assert "first-time-student" not in tags

    def describe_answer_tags():
        def _question(db, **kwargs):
            from classes.models import RegistrationQuestion

            defaults = {"prompt": "Experience level?"}
            defaults.update(kwargs)
            return RegistrationQuestion.objects.create(**defaults)

        def _answer(reg, question, text):
            from classes.models import RegistrationAnswer

            return RegistrationAnswer.objects.create(registration=reg, question=question, answer_text=text)

        def it_tags_a_single_choice_answer(db):
            from classes.models import RegistrationQuestion

            reg = RegistrationFactory()
            q = _question(
                db,
                question_type=RegistrationQuestion.QuestionType.SINGLE_CHOICE,
                choices_json=["Beginner", "Advanced"],
            )
            _answer(reg, q, "Beginner")
            tags = derive_tags(reg)
            assert "q-experience-level-beginner" in tags

        def it_uses_admin_set_mailchimp_tag_prefix(db):
            from classes.models import RegistrationQuestion

            reg = RegistrationFactory()
            q = _question(
                db,
                question_type=RegistrationQuestion.QuestionType.SINGLE_CHOICE,
                choices_json=["Beginner"],
                mailchimp_tag="skill",
            )
            _answer(reg, q, "Beginner")
            tags = derive_tags(reg)
            assert "skill-beginner" in tags

        def it_tags_a_yes_no_yes(db):
            from classes.models import RegistrationQuestion

            reg = RegistrationFactory()
            q = _question(
                db,
                prompt="Wants tool orientation?",
                question_type=RegistrationQuestion.QuestionType.YES_NO,
                mailchimp_tag="wants-tool-orientation",
            )
            _answer(reg, q, "yes")
            tags = derive_tags(reg)
            assert "wants-tool-orientation" in tags

        def it_omits_a_yes_no_no(db):
            from classes.models import RegistrationQuestion

            reg = RegistrationFactory()
            q = _question(
                db,
                prompt="Wants tool orientation?",
                question_type=RegistrationQuestion.QuestionType.YES_NO,
                mailchimp_tag="wants-tool-orientation",
            )
            _answer(reg, q, "no")
            tags = derive_tags(reg)
            assert "wants-tool-orientation" not in tags

        def it_does_not_tag_free_text_answers(db):
            from classes.models import RegistrationQuestion

            reg = RegistrationFactory()
            short_q = _question(db, prompt="Allergies?", question_type=RegistrationQuestion.QuestionType.SHORT_TEXT)
            long_q = _question(db, prompt="Goals?", question_type=RegistrationQuestion.QuestionType.LONG_TEXT)
            _answer(reg, short_q, "Peanuts")
            _answer(reg, long_q, "I want to learn to throw a pot.")
            tags = derive_tags(reg)
            assert not any(t.startswith("q-allergies") or t.startswith("q-goals") for t in tags)

        def it_skips_answers_that_produce_no_tag(db):
            from classes.models import RegistrationQuestion

            reg = RegistrationFactory()
            no_q = _question(
                db,
                prompt="Wants orientation?",
                question_type=RegistrationQuestion.QuestionType.YES_NO,
                mailchimp_tag="wants-orientation",
            )
            yes_q = _question(
                db,
                prompt="Wants newsletter recap?",
                question_type=RegistrationQuestion.QuestionType.YES_NO,
                mailchimp_tag="wants-recap",
            )
            _answer(reg, no_q, "no")  # yields no tag
            _answer(reg, yes_q, "yes")  # yields a tag
            tags = derive_tags(reg)
            assert "wants-orientation" not in tags
            assert "wants-recap" in tags

        def it_deduplicates_repeated_answer_tags(db):
            from classes.models import RegistrationQuestion

            reg = RegistrationFactory()
            # Two yes/no questions that share a tag prefix and both answered yes
            # would emit the same tag — it should appear only once.
            q1 = _question(
                db,
                prompt="First check?",
                question_type=RegistrationQuestion.QuestionType.YES_NO,
                mailchimp_tag="opted-in",
            )
            q2 = _question(
                db,
                prompt="Second check?",
                question_type=RegistrationQuestion.QuestionType.YES_NO,
                mailchimp_tag="opted-in",
            )
            _answer(reg, q1, "yes")
            _answer(reg, q2, "yes")
            tags = derive_tags(reg)
            assert tags.count("opted-in") == 1

        def it_passes_answer_tags_through_to_subscribe(site_with_mailchimp):
            from classes.models import RegistrationQuestion

            reg = RegistrationFactory(wants_newsletter=True)
            q = _question(
                None,
                prompt="Wants tool orientation?",
                question_type=RegistrationQuestion.QuestionType.YES_NO,
                mailchimp_tag="wants-tool-orientation",
            )
            _answer(reg, q, "yes")
            with patch(
                "core.integrations.mailchimp.MailchimpClient.subscribe",
                return_value=True,
            ) as spy:
                subscribe_registration(reg)
            assert "wants-tool-orientation" in spy.call_args.kwargs["tags"]


def describe_RegistrationQuestion_tag_for():
    def it_returns_none_for_short_text(db):
        from classes.models import RegistrationQuestion

        q = RegistrationQuestion(prompt="Allergies?", question_type=RegistrationQuestion.QuestionType.SHORT_TEXT)
        assert q.tag_for("Peanuts") is None

    def it_returns_none_for_a_blank_answer(db):
        from classes.models import RegistrationQuestion

        q = RegistrationQuestion(prompt="Level?", question_type=RegistrationQuestion.QuestionType.SINGLE_CHOICE)
        assert q.tag_for("   ") is None

    def it_auto_derives_a_prefix_from_the_prompt(db):
        from classes.models import RegistrationQuestion

        q = RegistrationQuestion(prompt="Skill level", question_type=RegistrationQuestion.QuestionType.SINGLE_CHOICE)
        assert q.tag_for("Advanced Beginner") == "q-skill-level-advanced-beginner"

    def it_returns_only_the_prefix_for_a_yes(db):
        from classes.models import RegistrationQuestion

        q = RegistrationQuestion(
            prompt="Bring kids?",
            question_type=RegistrationQuestion.QuestionType.YES_NO,
            mailchimp_tag="bringing-kids",
        )
        assert q.tag_for("Yes") == "bringing-kids"

    def it_returns_none_for_a_no(db):
        from classes.models import RegistrationQuestion

        q = RegistrationQuestion(
            prompt="Bring kids?",
            question_type=RegistrationQuestion.QuestionType.YES_NO,
            mailchimp_tag="bringing-kids",
        )
        assert q.tag_for("No") is None
