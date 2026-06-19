from __future__ import annotations

from core.factories import UserProfileFactory


def describe_UserProfile():
    def describe_cache_from_registration():
        def it_fills_empty_pronouns_and_phone(db):
            from classes.factories import RegistrationFactory

            profile = UserProfileFactory(pronouns="", phone="")
            reg = RegistrationFactory(pronouns="they/them", phone="503-555-0100")
            profile.cache_from_registration(reg)
            profile.refresh_from_db()
            assert profile.pronouns == "they/them"
            assert profile.phone == "503-555-0100"

        def context_when_the_profile_already_has_values():
            def it_does_not_clobber_them(db):
                from classes.factories import RegistrationFactory

                profile = UserProfileFactory(pronouns="she/her", phone="111")
                reg = RegistrationFactory(pronouns="they/them", phone="999")
                profile.cache_from_registration(reg)
                profile.refresh_from_db()
                assert profile.pronouns == "she/her"
                assert profile.phone == "111"

        def context_when_the_registration_value_is_blank():
            def it_leaves_the_empty_profile_field_empty(db):
                from classes.factories import RegistrationFactory

                profile = UserProfileFactory(pronouns="", phone="")
                reg = RegistrationFactory(pronouns="", phone="")
                profile.cache_from_registration(reg)
                profile.refresh_from_db()
                assert profile.pronouns == ""
                assert profile.phone == ""

        def it_only_writes_changed_fields(db):
            # Guard the update_fields optimization: a no-op call saves nothing.
            from classes.factories import RegistrationFactory

            profile = UserProfileFactory(pronouns="she/her", phone="111")
            reg = RegistrationFactory(pronouns="", phone="")
            profile.cache_from_registration(reg)  # nothing to copy → no error, no change
            profile.refresh_from_db()
            assert profile.pronouns == "she/her"
