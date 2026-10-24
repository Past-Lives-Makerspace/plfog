"""The forms over the SiteConfiguration singleton, and which page each field lives on.

The three global signage fields LEFT SiteSettingsForm when the Slideshow admin became its
own page. They live on SlideshowSettingsForm alongside the self-building block switches,
and are deliberately not duplicated across the two forms. The Member Agreement, Manage
Features, Manage CMS and Automations pages took their fields the same way in #922.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client

from core.models import SiteConfiguration
from hub.forms import (
    AutomationsForm,
    ManageCmsForm,
    ManageFeaturesForm,
    MemberAgreementSettingsForm,
    SiteSettingsForm,
    SlideshowSettingsForm,
)

pytestmark = pytest.mark.django_db

_TIMING_FIELDS = [
    "signage_default_slide_seconds",
    "signage_event_days_ahead",
]

_BLOCK_SWITCHES = [
    "signage_show_events",
    "signage_show_classes",
    "signage_show_guilds",
    "signage_show_calendar",
    "signage_show_voting",
    "signage_show_directory",
    "signage_show_teach",
    "signage_show_tour",
]

# The tour block is the only one with a destination to type, so its URL rides with the
# switches rather than with the timing fields.
_DESTINATION_FIELDS = [
    "signage_tour_url",
]

# Still a column, deliberately on no form: event slides always carry a QR now, so the old
# per-block toggle is dead config rather than an admin control.
_RETIRED = {"signage_event_qr"}


def describe_ManageFeaturesForm():
    def it_declares_the_public_member_directory_toggle():
        assert "member_directory_public" in ManageFeaturesForm.Meta.fields
        assert "member_directory_public" not in SiteSettingsForm.Meta.fields


def describe_the_pages_split_from_site_settings():
    """Each page's form writes its own columns and no others (#922)."""

    _SLICES = [MemberAgreementSettingsForm, ManageFeaturesForm, ManageCmsForm, AutomationsForm]

    @pytest.mark.parametrize("form_class", _SLICES)
    def it_shares_no_field_with_site_settings(form_class) -> None:
        assert set(form_class.Meta.fields) & set(SiteSettingsForm.Meta.fields) == set()

    def it_never_puts_back_a_setting_another_page_changed_meanwhile() -> None:
        # Two admins: one loads Manage CMS, the other saves Site Settings' org name, then the
        # first saves. A whole row save from the stale copy would revert the org name.
        stale = SiteConfiguration.load()
        fresh = SiteConfiguration.load()
        fresh.org_name = "Changed Elsewhere"
        fresh.save()
        form = ManageCmsForm({"instructor_discount_codes_enabled": "on"}, instance=stale)
        assert form.is_valid(), form.errors
        form.save()
        config = SiteConfiguration.load()
        assert config.org_name == "Changed Elsewhere"
        assert config.instructor_discount_codes_enabled is True

    def it_writes_nothing_when_asked_not_to_commit() -> None:
        form = AutomationsForm({"legacy_cms_sync_enabled": "on"}, instance=SiteConfiguration.load())
        assert form.is_valid(), form.errors
        instance = form.save(commit=False)
        assert instance.legacy_cms_sync_enabled is True
        assert SiteConfiguration.load().legacy_cms_sync_enabled is False


def describe_SiteSettingsForm_signage():
    def it_no_longer_carries_any_signage_field():
        for name in [*_TIMING_FIELDS, *_BLOCK_SWITCHES, *_DESTINATION_FIELDS]:
            assert name not in SiteSettingsForm.Meta.fields

    def it_carries_no_signage_field_this_spec_has_not_heard_of():
        # The lists above have to be edited by hand, and a signage field added to BOTH forms
        # is invisible: site_settings.html names its fields explicitly, so the duplicate binds
        # but never renders, and every Site Settings save posts nothing for it. A blankable
        # field is then silently overwritten with "" — which for signage_tour_url drops the
        # Book a Tour slide off every screen while its switch still reads on. Ask the model.
        declared = {f.name for f in SiteConfiguration._meta.get_fields() if f.name.startswith("signage_")}
        assert declared, "SiteConfiguration grew no signage_* fields — this guard is looking at the wrong model."
        assert declared & set(SiteSettingsForm.Meta.fields) == set()


def describe_SlideshowSettingsForm():
    def it_declares_the_timing_fields():
        for name in _TIMING_FIELDS:
            assert name in SlideshowSettingsForm.Meta.fields

    def it_declares_every_self_building_block_switch():
        for name in _BLOCK_SWITCHES:
            assert name in SlideshowSettingsForm.Meta.fields

    def it_declares_the_tour_destination():
        for name in _DESTINATION_FIELDS:
            assert name in SlideshowSettingsForm.Meta.fields

    def it_owns_every_signage_field_the_model_has_not_retired():
        # The other half of the guard above: a signage field on NEITHER form is unreachable,
        # stuck at its default with no admin able to change it. _RETIRED is the deliberate
        # exception list, so adding a field means choosing which side it lands on.
        declared = {f.name for f in SiteConfiguration._meta.get_fields() if f.name.startswith("signage_")}
        assert declared - _RETIRED <= set(SlideshowSettingsForm.Meta.fields)

    def it_does_not_expose_the_removed_event_qr_toggle():
        # Event slides always carry a QR now — the toggle is gone.
        assert "signage_event_qr" not in SlideshowSettingsForm.Meta.fields

    def it_does_not_expose_the_removed_emergency_alert_fields():
        # The emergency-takeover feature was removed — its fields are gone.
        for name in ("signage_alert_active", "signage_alert_heading", "signage_alert_message"):
            assert name not in SlideshowSettingsForm.Meta.fields

    def it_round_trips_a_save_onto_the_singleton():
        config = SiteConfiguration.load()
        data = {
            "signage_default_slide_seconds": "20",
            "signage_show_events": "on",
            "signage_event_days_ahead": "45",
        }
        form = SlideshowSettingsForm(data, instance=config)
        assert form.is_valid(), form.errors
        form.save()
        config.refresh_from_db()
        assert config.signage_default_slide_seconds == 20
        assert config.signage_event_days_ahead == 45
        assert config.signage_show_events is True
        # Unchecked switches post nothing, which a ModelForm reads as False.
        assert config.signage_show_classes is False


def describe_model_errors_on_fields_a_form_lacks():
    """Each page keeps the model's errors it can show and drops those on another page's fields (#922)."""

    from django.core.exceptions import NON_FIELD_ERRORS, ValidationError

    def _bound() -> ManageCmsForm:
        form = ManageCmsForm({}, instance=SiteConfiguration.load())
        form.is_valid()
        return form

    def it_drops_an_error_on_another_pages_field_and_keeps_its_own() -> None:
        form = _bound()
        form._update_errors(
            ValidationError({"member_agreement_url": "elsewhere", "instructor_discount_codes_enabled": "mine"})
        )
        assert dict(form.errors) == {"instructor_discount_codes_enabled": ["mine"]}

    def it_keeps_a_non_field_error_in_a_dict() -> None:
        form = _bound()
        form._update_errors(ValidationError({NON_FIELD_ERRORS: "whole row", "member_agreement_url": "elsewhere"}))
        assert form.non_field_errors() == ["whole row"]
        assert "member_agreement_url" not in form.errors

    def it_keeps_a_plain_error_with_no_field() -> None:
        form = _bound()
        form._update_errors(ValidationError("whole row"))
        assert form.non_field_errors() == ["whole row"]

    def it_adds_nothing_when_every_error_belongs_elsewhere() -> None:
        form = _bound()
        form._update_errors(ValidationError({"member_agreement_url": "elsewhere"}))
        assert form.is_valid()

    def it_keeps_the_model_error_where_the_field_is() -> None:
        form = MemberAgreementSettingsForm({}, instance=SiteConfiguration.load())
        form.is_valid()
        form._update_errors(ValidationError({"member_agreement_url": "here"}))
        assert form.errors["member_agreement_url"] == ["here"]


def describe_MemberAgreementSettingsForm():
    def it_fails_clean_if_required_but_no_url() -> None:
        form = MemberAgreementSettingsForm({"member_agreement_required": "on", "member_agreement_url": ""})
        assert not form.is_valid()
        assert set(form.errors) == {"member_agreement_url"}
        # The form's own message first; the model's clean() repeats the rule after it.
        assert form.errors["member_agreement_url"][0] == "A URL is required if the agreement is enabled."

    def it_passes_clean_if_required_and_url_provided() -> None:
        form = MemberAgreementSettingsForm(
            {"member_agreement_required": "on", "member_agreement_url": "https://example.com"}
        )
        assert form.is_valid(), form.errors

    def it_passes_clean_if_not_required() -> None:
        form = MemberAgreementSettingsForm({"member_agreement_required": "", "member_agreement_url": ""})
        assert form.is_valid(), form.errors


def describe_ManageFeaturesForm_late_cancel_fees():
    """The switch and the window on Manage Features (#456, part 1; its own page since #922)."""

    def _data(**overrides: str) -> dict[str, str]:
        data = {"late_cancel_notice_hours": "24", "late_cancel_grace_hours": "2"}
        data.update(overrides)
        return data

    def it_declares_the_switch_and_the_window():
        for name in ("late_cancel_fees_enabled", "late_cancel_notice_hours", "late_cancel_grace_hours"):
            assert name in ManageFeaturesForm.Meta.fields

    def it_ships_off_with_a_day_of_notice_and_two_hours_of_grace():
        config = SiteConfiguration.load()
        assert config.late_cancel_fees_enabled is False
        assert config.late_cancel_notice_hours == 24
        assert config.late_cancel_grace_hours == 2

    def it_saves_the_three_onto_the_singleton():
        form = ManageFeaturesForm(
            _data(late_cancel_fees_enabled="on", late_cancel_notice_hours="48", late_cancel_grace_hours="4"),
            instance=SiteConfiguration.load(),
        )
        assert form.is_valid(), form.errors
        form.save()
        config = SiteConfiguration.load()
        assert config.late_cancel_fees_enabled is True
        assert config.late_cancel_notice_hours == 48
        assert config.late_cancel_grace_hours == 4

    def it_reads_an_unchecked_switch_as_off():
        config = SiteConfiguration.load()
        config.late_cancel_fees_enabled = True
        config.save()
        form = ManageFeaturesForm(_data(), instance=config)
        assert form.is_valid(), form.errors
        form.save()
        assert SiteConfiguration.load().late_cancel_fees_enabled is False

    def it_refuses_a_notice_under_an_hour():
        form = ManageFeaturesForm(_data(late_cancel_notice_hours="0", late_cancel_grace_hours="0"))
        assert not form.is_valid()
        assert set(form.errors) == {"late_cancel_notice_hours"}
        assert "at least 1 hour" in str(form.errors["late_cancel_notice_hours"])

    def it_accepts_a_notice_of_exactly_an_hour():
        form = ManageFeaturesForm(_data(late_cancel_notice_hours="1", late_cancel_grace_hours="0"))
        assert form.is_valid(), form.errors

    def it_refuses_a_grace_equal_to_the_notice():
        form = ManageFeaturesForm(_data(late_cancel_notice_hours="24", late_cancel_grace_hours="24"))
        assert not form.is_valid()
        assert set(form.errors) == {"late_cancel_grace_hours"}
        assert "shorter than the notice" in str(form.errors["late_cancel_grace_hours"])

    def it_refuses_a_grace_longer_than_the_notice():
        form = ManageFeaturesForm(_data(late_cancel_notice_hours="24", late_cancel_grace_hours="30"))
        assert not form.is_valid()
        assert set(form.errors) == {"late_cancel_grace_hours"}

    def it_accepts_no_grace_at_all():
        form = ManageFeaturesForm(_data(late_cancel_notice_hours="24", late_cancel_grace_hours="0"))
        assert form.is_valid(), form.errors

    def it_accepts_a_grace_an_hour_under_the_notice():
        form = ManageFeaturesForm(_data(late_cancel_notice_hours="24", late_cancel_grace_hours="23"))
        assert form.is_valid(), form.errors

    def it_requires_both_hours():
        # Blank hours are the field's own required error; the window check stays quiet.
        form = ManageFeaturesForm(_data(late_cancel_notice_hours="", late_cancel_grace_hours=""))
        assert set(form.errors) == {"late_cancel_notice_hours", "late_cancel_grace_hours"}
        assert "required" in str(form.errors["late_cancel_grace_hours"])

    def it_requires_the_grace_without_judging_a_missing_one():
        form = ManageFeaturesForm(_data(late_cancel_notice_hours="24", late_cancel_grace_hours=""))
        assert set(form.errors) == {"late_cancel_grace_hours"}
        assert "shorter than the notice" not in str(form.errors["late_cancel_grace_hours"])

    def it_judges_no_grace_against_a_missing_notice():
        form = ManageFeaturesForm(_data(late_cancel_notice_hours="", late_cancel_grace_hours="5"))
        assert set(form.errors) == {"late_cancel_notice_hours"}


def describe_member_agreement_fields() -> None:
    """The three Member Agreement fields are admin controls, not admin-only columns.

    `member_agreement_version` was a column with no form field: settable in Django admin and
    nowhere else, while the release note said Site Settings. Since setting a version is the whole
    mechanism that re-prompts members, a field only a superuser can reach is a feature nobody can
    operate (PastLivesReviewBot, #493).
    """

    _AGREEMENT_FIELDS = [
        "member_agreement_required",
        "member_agreement_url",
        "member_agreement_version",
    ]

    @pytest.mark.parametrize("name", _AGREEMENT_FIELDS)
    def it_offers_every_agreement_field(name: str) -> None:
        assert name in MemberAgreementSettingsForm(instance=SiteConfiguration.load()).fields

    def it_saves_a_released_version() -> None:
        """Saved through the form rather than the model, so a missing field fails this.

        The value has to reach the database: a bound form with no errors proves the field
        validates, not that anything was stored (PastLivesReviewBot, #493).
        """
        config = SiteConfiguration.load()
        data = {
            "member_agreement_required": "on",
            "member_agreement_url": "https://kb.example.test/doc/policies-membership-agreement/",
            "member_agreement_version": "2.4.0",
        }
        bound = MemberAgreementSettingsForm(data=data, instance=config)
        assert bound.is_valid(), bound.errors
        bound.save()

        assert SiteConfiguration.objects.get(pk=config.pk).member_agreement_version == "2.4.0"

    def it_renders_the_version_right_after_the_url(client: Client, admin_user: User) -> None:
        """The two are read together: the version names the edition published at the URL."""
        from django.urls import reverse

        client.force_login(admin_user)
        html = client.get(reverse("hub_admin_member_agreement")).content.decode()
        assert html.count('name="member_agreement_version"') == 1
        assert html.index('name="member_agreement_url"') < html.index('name="member_agreement_version"')
        # Not asserting the explanatory note is absent from the page. A multi-line {# #} renders
        # as visible text and this spec would sail past it — but tests/template_comment_lint_spec.py
        # already catches that repo-wide, for every template, with a self-test of its own
        # (FRONTEND.md Rule 17). A second, weaker copy here would only rot.
