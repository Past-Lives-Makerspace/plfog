"""BDD specs for hub forms."""

from __future__ import annotations

import pytest

from hub.forms import EmailPreferencesForm, ProfileSettingsForm
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
        assert list(form.fields.keys()) == ["preferred_name", "phone", "show_in_directory"]


def describe_email_preferences_form():
    def it_accepts_checked_voting_results():
        form = EmailPreferencesForm({"voting_results": "on"})
        assert form.is_valid()
        assert form.cleaned_data["voting_results"] is True

    def it_accepts_unchecked_voting_results():
        form = EmailPreferencesForm({})
        assert form.is_valid()
        assert form.cleaned_data["voting_results"] is False
