"""BDD specs for hub forms."""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from hub.forms import ProfileSettingsForm, SiteAnnouncementForm
from tests.membership.factories import MemberFactory


def _real_png_bytes() -> bytes:
    """A genuine PNG (~hundreds of bytes) that the form's ImageField accepts via Pillow.

    Big enough to trip the size validator once ``MAX_UPLOAD_IMAGE_BYTES`` is lowered.
    """
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (32, 32), (200, 120, 40)).save(buf, "PNG")
    return buf.getvalue()


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
            "about_me",
            "profile_photo",
            "show_in_directory",
            "open_for_commissions",
            "commission_note",
            "instructor_bio",
            "show_pronouns",
            "show_phone",
            "show_email",
            "show_discord_handle",
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
                # pronouns, discord_handle, about_me, profile_photo intentionally unchecked
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

    def describe_invalid_photo_does_not_lose_other_edits():
        def it_flags_only_the_photo_when_the_upload_is_too_large(settings):
            settings.MAX_UPLOAD_IMAGE_BYTES = 10  # smaller than the real PNG below
            member = MemberFactory(full_legal_name="Photo User")
            photo = SimpleUploadedFile("big.png", _real_png_bytes(), content_type="image/png")
            form = ProfileSettingsForm(
                {"preferred_name": "Pho", "about_me": "hi"},
                {"profile_photo": photo},
                instance=member,
            )
            assert form.is_valid() is False
            assert form.has_only_photo_errors is True
            assert set(form.errors) == {"profile_photo"}
            assert "MB" in form.photo_error

        def it_persists_text_and_visibility_while_discarding_the_rejected_photo(settings):
            settings.MAX_UPLOAD_IMAGE_BYTES = 10
            member = MemberFactory(full_legal_name="Photo User", about_me="")
            photo = SimpleUploadedFile("big.png", _real_png_bytes(), content_type="image/png")
            form = ProfileSettingsForm(
                {"preferred_name": "Pho", "discord_handle": "eddy", "about_me": "new bio", "show_phone": "on"},
                {"profile_photo": photo},
                instance=member,
            )
            assert form.is_valid() is False
            assert form.has_only_photo_errors is True

            saved = form.save_keeping_existing_photo()
            saved.refresh_from_db()
            assert saved.preferred_name == "Pho"
            assert saved.discord_handle == "eddy"
            assert saved.about_me == "new bio"
            assert saved.is_public("phone") is True
            assert saved.is_public("about_me") is False
            assert not saved.profile_photo  # the rejected upload is never written

        def it_does_not_treat_a_clean_form_as_a_photo_only_error():
            member = MemberFactory(full_legal_name="Clean User")
            form = ProfileSettingsForm({"preferred_name": "Ok"}, instance=member)
            assert form.is_valid() is True
            assert form.has_only_photo_errors is False

        def it_does_not_treat_a_text_error_as_a_photo_only_error():
            member = MemberFactory(full_legal_name="Text Err")
            form = ProfileSettingsForm({"preferred_name": "Ok", "phone": "x" * 21}, instance=member)
            assert form.is_valid() is False
            assert form.has_only_photo_errors is False


# NOTE: The hub Site Settings plain composer + the guild-edit composer moved to the
# /announcements/compose/ wizard (AnnouncementComposeForm), covered by
# tests/hub/announcement_compose_spec.py + tests/membership/announcement_draft_spec.py.
# SiteAnnouncementForm still backs the separate Django-admin composer (/admin/announcement/).
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

    def it_is_valid_without_a_push_message():
        form = SiteAnnouncementForm({"title": "Hi", "body": "<p>Hello there</p>", "post_to_discord": ""})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["push_message"] == ""

    def it_accepts_an_optional_push_message():
        form = SiteAnnouncementForm(
            {"title": "Hi", "body": "<p>Hello there</p>", "push_message": "Snow day. Closed.", "post_to_discord": ""}
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["push_message"] == "Snow day. Closed."

    def it_rejects_a_push_message_past_the_length_cap():
        form = SiteAnnouncementForm(
            {"title": "Hi", "body": "<p>Hi</p>", "push_message": "x" * 181, "post_to_discord": ""}
        )
        assert not form.is_valid()
        assert "push_message" in form.errors


def describe_push_test_form():
    def it_resolves_a_member_email_to_the_user(db):
        from django.contrib.auth.models import User

        from hub.forms import PushTestForm

        user = User.objects.create_user(username="pt", email="pt@example.com")
        form = PushTestForm({"email": "pt@example.com"})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["user"] == user

    def it_rejects_an_email_with_no_account(db):
        from hub.forms import PushTestForm

        form = PushTestForm({"email": "ghost@example.com"})
        assert not form.is_valid()
        assert "email" in form.errors
