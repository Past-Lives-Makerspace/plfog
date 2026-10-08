"""SiteSettingsForm and SlideshowSettingsForm — where the signage_* fields live now.

The three global signage fields LEFT SiteSettingsForm when the Slideshow admin became its
own page. They live on SlideshowSettingsForm alongside the self-building block switches,
and are deliberately not duplicated across the two forms.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.utils import timezone

from core.models import SiteConfiguration
from hub.forms import SiteSettingsForm, SlideshowSettingsForm
from membership.models import CommunityEvent
from tests.membership.factories import CommunityEventFactory

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


def describe_SiteSettingsForm_features():
    def it_declares_the_public_member_directory_toggle():
        assert "member_directory_public" in SiteSettingsForm.Meta.fields


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


def describe_SiteSettingsForm_member_agreement():
    @pytest.fixture
    def required_settings() -> dict[str, str]:
        return {
            "org_name": "Past Lives Makerspace",
            "registration_mode": SiteConfiguration.RegistrationMode.OPEN,
            "member_event_policy": SiteConfiguration.MemberEventPolicy.APPROVAL,
            "late_cancel_notice_hours": "24",
            "late_cancel_grace_hours": "2",
        }

    def it_fails_clean_if_required_but_no_url(required_settings: dict[str, str]) -> None:
        data = {
            **required_settings,
            "member_agreement_required": "on",
            "member_agreement_url": "",
        }
        form = SiteSettingsForm(data)
        assert not form.is_valid()
        assert set(form.errors) == {"member_agreement_url"}

    def it_passes_clean_if_required_and_url_provided(required_settings: dict[str, str]) -> None:
        data = {
            **required_settings,
            "member_agreement_required": "on",
            "member_agreement_url": "https://example.com",
        }
        form = SiteSettingsForm(data)
        assert form.is_valid(), form.errors

    def it_passes_clean_if_not_required(required_settings: dict[str, str]) -> None:
        data = {
            **required_settings,
            "member_agreement_required": "",
            "member_agreement_url": "",
        }
        form = SiteSettingsForm(data)
        assert form.is_valid(), form.errors


def describe_SiteSettingsForm_late_cancel_fees():
    """The switch and the window on the Features card (#456, part 1)."""

    def _data(**overrides: str) -> dict[str, str]:
        data = {
            "org_name": "Past Lives Makerspace",
            "registration_mode": SiteConfiguration.RegistrationMode.OPEN,
            "member_event_policy": SiteConfiguration.MemberEventPolicy.APPROVAL,
            "late_cancel_notice_hours": "24",
            "late_cancel_grace_hours": "2",
        }
        data.update(overrides)
        return data

    def it_declares_the_switch_and_the_window():
        for name in ("late_cancel_fees_enabled", "late_cancel_notice_hours", "late_cancel_grace_hours"):
            assert name in SiteSettingsForm.Meta.fields

    def it_ships_off_with_a_day_of_notice_and_two_hours_of_grace():
        config = SiteConfiguration.load()
        assert config.late_cancel_fees_enabled is False
        assert config.late_cancel_notice_hours == 24
        assert config.late_cancel_grace_hours == 2

    def it_saves_the_three_onto_the_singleton():
        form = SiteSettingsForm(
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
        form = SiteSettingsForm(_data(), instance=config)
        assert form.is_valid(), form.errors
        form.save()
        assert SiteConfiguration.load().late_cancel_fees_enabled is False

    def it_refuses_a_notice_under_an_hour():
        form = SiteSettingsForm(_data(late_cancel_notice_hours="0", late_cancel_grace_hours="0"))
        assert not form.is_valid()
        assert set(form.errors) == {"late_cancel_notice_hours"}
        assert "at least 1 hour" in str(form.errors["late_cancel_notice_hours"])

    def it_refuses_a_grace_equal_to_the_notice():
        form = SiteSettingsForm(_data(late_cancel_notice_hours="24", late_cancel_grace_hours="24"))
        assert not form.is_valid()
        assert set(form.errors) == {"late_cancel_grace_hours"}
        assert "shorter than the notice" in str(form.errors["late_cancel_grace_hours"])

    def it_refuses_a_grace_longer_than_the_notice():
        form = SiteSettingsForm(_data(late_cancel_notice_hours="24", late_cancel_grace_hours="30"))
        assert not form.is_valid()
        assert set(form.errors) == {"late_cancel_grace_hours"}

    def it_accepts_no_grace_at_all():
        form = SiteSettingsForm(_data(late_cancel_notice_hours="24", late_cancel_grace_hours="0"))
        assert form.is_valid(), form.errors

    def it_requires_both_hours():
        # Blank hours are the field's own required error; the window check stays quiet.
        form = SiteSettingsForm(_data(late_cancel_notice_hours="", late_cancel_grace_hours=""))
        assert set(form.errors) == {"late_cancel_notice_hours", "late_cancel_grace_hours"}
        assert "required" in str(form.errors["late_cancel_grace_hours"])

    def it_requires_the_grace_without_judging_a_missing_one():
        form = SiteSettingsForm(_data(late_cancel_notice_hours="24", late_cancel_grace_hours=""))
        assert set(form.errors) == {"late_cancel_grace_hours"}
        assert "shorter than the notice" not in str(form.errors["late_cancel_grace_hours"])


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
        assert name in SiteSettingsForm(instance=SiteConfiguration.load()).fields

    def it_saves_a_released_version() -> None:
        """Saved through the form rather than the model, so a missing field fails this.

        The value has to reach the database: a bound form with no errors proves the field
        validates, not that anything was stored (PastLivesReviewBot, #493).
        """
        config = SiteConfiguration.load()
        form = SiteSettingsForm(instance=config)
        data = {k: v for k, v in form.initial.items() if v is not None}
        data.update(
            {
                "member_agreement_required": "on",
                "member_agreement_url": "https://kb.example.test/doc/policies-membership-agreement/",
                "member_agreement_version": "2.4.0",
            }
        )
        bound = SiteSettingsForm(data=data, instance=config)
        assert bound.is_valid(), bound.errors
        bound.save()

        assert SiteConfiguration.objects.get(pk=config.pk).member_agreement_version == "2.4.0"

    def it_renders_the_version_beside_the_url(client: Client, admin_user: User) -> None:
        """Rendered by hand next to the URL, so it must also be excluded from the generic loop —
        get that wrong and the input appears twice."""
        from django.urls import reverse

        client.force_login(admin_user)
        html = client.get(reverse("hub_admin_site_settings")).content.decode()
        assert html.count('name="member_agreement_version"') == 1
        # Not asserting the explanatory note is absent from the page. A multi-line {# #} renders
        # as visible text and this spec would sail past it — but tests/template_comment_lint_spec.py
        # already catches that repo-wide, for every template, with a self-test of its own
        # (FRONTEND.md Rule 17). A second, weaker copy here would only rot.


def describe_SiteSettingsForm_building_with_you():
    """The three "Building with you" settings behind the version pill's panel (#699)."""

    def _meeting(**kwargs: object) -> CommunityEvent:
        start = timezone.now() + timedelta(days=5)
        return CommunityEventFactory(
            community=True,
            title="Zorblax Feature Meeting",
            starts_at=start,
            ends_at=start + timedelta(hours=1),
            **kwargs,
        )

    def it_declares_the_three_fields():
        for name in ("feature_meeting_event", "being_built_now", "backlog_url"):
            assert name in SiteSettingsForm.Meta.fields

    def it_offers_published_upcoming_or_repeating_events_and_a_blank():
        upcoming = _meeting()
        past_start = timezone.now() - timedelta(days=40)
        repeating = CommunityEventFactory(
            community=True,
            starts_at=past_start,
            ends_at=past_start + timedelta(hours=1),
            recurrence=CommunityEvent.Recurrence.MONTHLY,
        )
        over = CommunityEventFactory(community=True, starts_at=past_start, ends_at=past_start + timedelta(hours=1))
        pending = _meeting(pending=True)

        field = SiteSettingsForm(instance=SiteConfiguration.load()).fields["feature_meeting_event"]

        offered = set(field.queryset)
        assert {upcoming, repeating} <= offered
        assert over not in offered
        assert pending not in offered
        assert field.required is False
        assert field.empty_label == "No Feature Meeting"

    def it_keeps_the_current_pick_on_offer_after_it_ends():
        past_start = timezone.now() - timedelta(days=40)
        over = CommunityEventFactory(community=True, starts_at=past_start, ends_at=past_start + timedelta(hours=1))
        config = SiteConfiguration.load()
        config.feature_meeting_event = over
        config.save()

        assert over in SiteSettingsForm(instance=config).fields["feature_meeting_event"].queryset

    def it_labels_a_choice_with_its_first_date_and_how_it_repeats():
        start = timezone.make_aware(datetime(2026, 9, 8, 18, 0))
        event = CommunityEventFactory(
            community=True,
            title="Zorblax Feature Meeting",
            starts_at=start,
            ends_at=start + timedelta(hours=1),
            recurrence=CommunityEvent.Recurrence.MONTHLY,
        )
        field = SiteSettingsForm(instance=SiteConfiguration.load()).fields["feature_meeting_event"]

        assert field.label_from_instance(event) == "Zorblax Feature Meeting (from Sep 8, 2026, every month)"

    def it_labels_a_one_off_without_a_repeat():
        event = _meeting()
        field = SiteSettingsForm(instance=SiteConfiguration.load()).fields["feature_meeting_event"]

        assert field.label_from_instance(event).endswith(f"{timezone.localtime(event.starts_at):%Y})")

    def it_saves_the_three_settings():
        event = _meeting()
        config = SiteConfiguration.load()
        form = SiteSettingsForm(instance=config)
        data = {k: v for k, v in form.initial.items() if v is not None}
        data.update(
            {
                "feature_meeting_event": str(event.pk),
                "being_built_now": "Zorblax kiln queue\nZorblax laser hours",
                "backlog_url": "https://example.org/zorblax-board",
            }
        )
        bound = SiteSettingsForm(data=data, instance=config)
        assert bound.is_valid(), bound.errors
        bound.save()

        saved = SiteConfiguration.objects.get(pk=config.pk)
        assert saved.feature_meeting_event == event
        assert saved.being_built_now == "Zorblax kiln queue\nZorblax laser hours"
        assert saved.backlog_url == "https://example.org/zorblax-board"

    def it_saves_with_no_meeting_and_no_backlog():
        config = SiteConfiguration.load()
        form = SiteSettingsForm(instance=config)
        data = {k: v for k, v in form.initial.items() if v is not None}
        data.update({"feature_meeting_event": "", "being_built_now": "", "backlog_url": ""})
        bound = SiteSettingsForm(data=data, instance=config)
        assert bound.is_valid(), bound.errors
        bound.save()

        saved = SiteConfiguration.objects.get(pk=config.pk)
        assert saved.feature_meeting_event is None
        assert saved.backlog_url == ""

    def it_renders_its_own_section_on_the_general_tab_once(client: Client, admin_user: User) -> None:
        from django.urls import reverse

        client.force_login(admin_user)
        html = client.get(reverse("hub_admin_site_settings")).content.decode()

        section = html.split('id="building-with-you-settings"', 1)[1].split("x-show=\"tab === 'calendar'\"", 1)[0]
        assert "Building With You" in section
        for name in ("feature_meeting_event", "being_built_now", "backlog_url"):
            assert html.count(f'name="{name}"') == 1
            assert f'name="{name}"' in section
