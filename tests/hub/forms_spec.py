"""BDD specs for hub forms."""

from __future__ import annotations

import pytest

from hub.forms import ProfileSettingsForm, SiteAnnouncementForm
from tests.membership.factories import MemberFactory


@pytest.mark.django_db
def describe_profile_settings_form():
    def it_accepts_valid_data():
        member = MemberFactory(full_legal_name="Test User")
        form = ProfileSettingsForm({"preferred_name": "Testy", "phone": "555-1234"}, instance=member)

        assert form.is_valid()

    def it_accepts_blank_fields():
        member = MemberFactory(full_legal_name="Test User")
        form = ProfileSettingsForm({"preferred_name": "", "phone": ""}, instance=member)

        assert form.is_valid()

    def it_rejects_phone_exceeding_max_length():
        member = MemberFactory(full_legal_name="Test User")
        form = ProfileSettingsForm({"preferred_name": "Ok", "phone": "x" * 21}, instance=member)

        assert not form.is_valid()
        assert "phone" in form.errors

    def it_saves_to_member_instance():
        member = MemberFactory(full_legal_name="Test User")
        form = ProfileSettingsForm({"preferred_name": "Nick", "phone": "555-0000"}, instance=member)
        form.is_valid()
        saved = form.save()

        assert saved.preferred_name == "Nick"
        assert saved.phone == "555-0000"

    def it_only_includes_expected_fields():
        form = ProfileSettingsForm()
        assert list(form.fields.keys()) == [
            "preferred_name",
            "pronouns",
            "phone",
            "discord_handle",
            "other_contact_info",
            "about_me",
            "profile_photo",
            "show_in_directory",
            "open_for_commissions",
            "commission_note",
            "instructor_website",
            "instructor_social_handle",
            "show_pronouns",
            "show_phone",
            "show_email",
            "show_discord_handle",
            "show_other_contact_info",
            "show_about_me",
            "show_profile_photo",
            "show_skills",
        ]

    def it_writes_visibility_flags_into_directory_visibility_json():
        member = MemberFactory(full_legal_name="Visibility User")
        form = ProfileSettingsForm(
            {
                "preferred_name": "VU",
                "show_phone": "on",
                "show_email": "on",
                # pronouns, discord_handle, other_contact_info, about_me, profile_photo intentionally unchecked
            },
            instance=member,
        )
        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.directory_visibility == {
            "pronouns": False,
            "phone": True,
            "email": True,
            "discord_handle": False,
            "other_contact_info": False,
            "about_me": False,
            "profile_photo": False,
            "skills": False,
        }
        assert saved.is_public("phone") is True
        assert saved.is_public("about_me") is False
        # Missing key still defaults to public:
        assert saved.is_public("nonexistent") is True

    def it_initializes_visibility_flags_from_member_state():
        member = MemberFactory(
            full_legal_name="Init User",
            directory_visibility={"phone": False, "email": True},
        )
        form = ProfileSettingsForm(instance=member)
        assert form.fields["show_phone"].initial is False
        assert form.fields["show_email"].initial is True
        # Unset key defaults to True (public):
        assert form.fields["show_pronouns"].initial is True


def describe_site_announcement_form():
    def it_sanitizes_the_body_and_strips_script():
        form = SiteAnnouncementForm(
            {"title": "Hi", "body": "<p>Hello there</p><script>evil()</script>", "post_to_discord": ""}
        )
        assert form.is_valid(), form.errors
        assert "<script" not in form.cleaned_data["body"]
        assert "Hello there" in form.cleaned_data["body"]

    def it_rejects_an_empty_quill_doc_as_a_missing_message():
        form = SiteAnnouncementForm({"title": "Hi", "body": "<p><br></p>", "post_to_discord": ""})
        assert not form.is_valid()
        assert "body" in form.errors
