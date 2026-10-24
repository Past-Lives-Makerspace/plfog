"""Manage Features (#922, once Site Settings' Features tab): the save path for the three-state
feature switches (#405).

The cards render inside the page's one form and the formset saves with its Save, the same way
the Automations page's toggles do. Everything here drives that POST rather than the
form in isolation, because the form was already covered and the *view* path was not: nothing in
the suite posted ``features-TOTAL_FORMS``, so the bound branch of ``_bind_feature_formset``, the
whole of ``_save_feature_formset`` and ``_resolve_feature_context`` with a bound formset never
ran. That is the feature's main action, and the only thing that writes ``updated_by``.

Shaped on ``tests/hub/site_settings_automations_spec.py``, which does the same job for the
sibling jobstate formset.
"""

from __future__ import annotations

from collections import Counter

import pytest
from django.test import Client
from django.urls import reverse

from core.features import FEATURES, FeatureState
from core.models import FeatureSwitch, SiteConfiguration

pytestmark = pytest.mark.django_db

URL = reverse("hub_admin_features")


def _superuser(client: Client, username: str = "featadmin"):
    from django.contrib.auth.models import User

    user = User.objects.create_superuser(username=username, email=f"{username}@x.com", password="p")
    client.login(username=username, password="p")
    return user


def _feature_post_data(states: dict[str, str] | None = None, messages: dict[str, str] | None = None) -> dict[str, str]:
    """Build the Features formset POST from the current rows, in the formset's own order.

    Every row is posted every time, exactly as the rendered page does — a formset with a
    management form promising N forms and fewer in the payload is not a partial save, it is an
    invalid one.
    """
    FeatureSwitch.objects.sync_registry()
    rows = list(FeatureSwitch.objects.all())  # Meta.ordering = feature_key, same as the formset
    states = states or {}
    messages = messages or {}
    data = {
        "features-TOTAL_FORMS": str(len(rows)),
        "features-INITIAL_FORMS": str(len(rows)),
        "features-MIN_NUM_FORMS": "0",
        "features-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(rows):
        data[f"features-{index}-id"] = str(row.pk)
        data[f"features-{index}-state"] = states.get(row.feature_key, row.state)
        data[f"features-{index}-message"] = messages.get(row.feature_key, row.message)
    return data


def _settings_post(**kwargs) -> dict[str, str]:
    """A Manage Features Save: the page's two required hour fields + the feature formset."""
    data = {"late_cancel_notice_hours": "24", "late_cancel_grace_hours": "2"}
    data.update(_feature_post_data(**kwargs))
    return data


def _state_of(key: str) -> str:
    return FeatureSwitch.objects.get(feature_key=key).state


def describe_saving_a_state_through_the_view():
    def it_round_trips_coming_soon(client: Client):
        _superuser(client)
        response = client.post(URL, _settings_post(states={"voting": FeatureState.SOON}))
        assert response.status_code == 302
        assert _state_of("voting") == FeatureState.SOON

    def it_round_trips_hidden(client: Client):
        _superuser(client)
        client.post(URL, _settings_post(states={"meetings": FeatureState.HIDDEN}))
        assert _state_of("meetings") == FeatureState.HIDDEN

    def it_comes_back_to_on(client: Client):
        """All three states through the view, not just through the form.

        Turning a feature back on is the half that matters operationally: an admin who cannot
        undo a switch has a broken switch.
        """
        _superuser(client)
        client.post(URL, _settings_post(states={"spaces": FeatureState.HIDDEN}))
        assert _state_of("spaces") == FeatureState.HIDDEN
        client.post(URL, _settings_post(states={"spaces": FeatureState.ON}))
        assert _state_of("spaces") == FeatureState.ON

    def it_leaves_the_other_features_alone(client: Client):
        _superuser(client)
        client.post(URL, _settings_post(states={"wiki": FeatureState.HIDDEN}))
        untouched = {f.key for f in FEATURES} - {"wiki"}
        assert all(_state_of(key) == FeatureState.ON for key in untouched)


def describe_saving_the_coming_soon_message():
    def it_persists_the_message(client: Client):
        _superuser(client)
        client.post(
            URL,
            _settings_post(
                states={"voting": FeatureState.SOON},
                messages={"voting": "Launching Sept 30th!"},
            ),
        )
        assert FeatureSwitch.objects.get(feature_key="voting").message == "Launching Sept 30th!"

    def it_renders_the_saved_message_back_on_the_next_load(client: Client):
        _superuser(client)
        client.post(
            URL,
            _settings_post(
                states={"voting": FeatureState.SOON},
                messages={"voting": "Launching Sept 30th!"},
            ),
        )
        html = client.get(URL).content
        assert b"Launching Sept 30th!" in html

    def it_keeps_a_typed_message_through_a_trip_back_to_on(client: Client):
        # The message only *means* anything in Coming soon, but clearing copy an admin wrote
        # because they flipped the state twice would be unkind.
        _superuser(client)
        client.post(
            URL,
            _settings_post(states={"wiki": FeatureState.SOON}, messages={"wiki": "Back in spring"}),
        )
        client.post(URL, _settings_post(states={"wiki": FeatureState.ON}))
        row = FeatureSwitch.objects.get(feature_key="wiki")
        assert row.state == FeatureState.ON
        assert row.message == "Back in spring"


def describe_the_updated_by_stamp():
    def it_records_the_admin_who_changed_the_state(client: Client):
        user = _superuser(client, "stamper")
        client.post(URL, _settings_post(states={"directory": FeatureState.HIDDEN}))
        assert FeatureSwitch.objects.get(feature_key="directory").updated_by == user

    def it_only_stamps_the_rows_that_changed(client: Client):
        # ``formset.save(commit=False)`` returns changed instances only, so an untouched feature
        # keeps whatever stamp it had — which is what makes the column answer "who turned this
        # off", rather than "who last saved the settings page".
        _superuser(client, "stamper_partial")
        client.post(URL, _settings_post(states={"equipment": FeatureState.HIDDEN}))
        assert FeatureSwitch.objects.get(feature_key="equipment").updated_by is not None
        assert FeatureSwitch.objects.get(feature_key="teach").updated_by is None


def describe_when_the_formset_is_not_posted():
    def it_is_left_untouched_by_a_site_settings_save(client: Client):
        """Site Settings' Save carries no feature data, so it must not reset the switches."""
        _superuser(client)
        FeatureSwitch.objects.sync_registry()
        FeatureSwitch.objects.filter(feature_key="voting").update(state=FeatureState.SOON)
        client.post(
            reverse("hub_admin_site_settings"),
            {
                "org_name": "Past Lives Makerspace",
                "registration_mode": SiteConfiguration.RegistrationMode.INVITE_ONLY,
                "member_event_policy": SiteConfiguration.MemberEventPolicy.APPROVAL,
                "feeds-TOTAL_FORMS": "0",
                "feeds-INITIAL_FORMS": "0",
                "feeds-MIN_NUM_FORMS": "0",
                "feeds-MAX_NUM_FORMS": "1000",
                "submitted_tab": "brand",
            },
        )
        assert _state_of("voting") == FeatureState.SOON

    def it_saves_nothing_on_this_page_and_shows_the_error(client: Client):
        """Without its management form the formset is invalid, so neither half of the page saves."""
        _superuser(client)
        # help_page_enabled is left out, so a save would have switched it off.
        response = client.post(URL, {"late_cancel_notice_hours": "48", "late_cancel_grace_hours": "2"})
        assert response.status_code == 200
        assert "pl-settings-page__errors" in response.content.decode()
        config = SiteConfiguration.load()
        assert config.late_cancel_notice_hours == 24
        assert config.help_page_enabled is True


def describe_a_save():
    def it_says_so_and_comes_back_here(client: Client):
        _superuser(client)
        response = client.post(URL, _settings_post(), follow=True)
        assert response.redirect_chain == [(URL, 302)]
        assert any(m.message == "Features saved." for m in response.context["messages"])

    def it_saves_neither_half_when_a_feature_message_is_too_long(client: Client):
        _superuser(client)
        data = _settings_post(messages={"voting": "x" * 201})
        data["late_cancel_notice_hours"] = "48"
        response = client.post(URL, data)
        assert response.status_code == 200
        assert SiteConfiguration.load().late_cancel_notice_hours == 24
        assert FeatureSwitch.objects.get(feature_key="voting").message == ""


def describe_each_page_renders_its_own_fields_once():
    """Every SiteConfiguration field belongs to exactly one page, and renders there once (#922).

    Site Settings renders its form inside ONE ``<form>``, and its General tab picks up the
    leftover fields through a generic loop guarded by a long ``field.name != '...'`` chain, so a
    field given its own place without ALSO joining that chain renders twice. That is how
    ``member_directory_public`` came to render as a styled toggle on Features and a raw,
    cramped checkbox on General at the same time.

    A duplicate is not merely ugly. Two inputs, one name: the browser submits the state of
    whichever the admin did not touch alongside the one they did, and for a checkbox the value
    that survives depends on DOM order rather than on intent. A field its page does not render
    at all is worse: that page's Save reads its missing checkbox as off.
    """

    from hub.forms import (
        AutomationsForm,
        ManageCmsForm,
        ManageFeaturesForm,
        MemberAgreementSettingsForm,
        SiteSettingsForm,
    )

    PAGES = [
        ("hub_admin_site_settings", SiteSettingsForm),
        ("hub_admin_features", ManageFeaturesForm),
        ("hub_admin_cms", ManageCmsForm),
        ("hub_admin_automations", AutomationsForm),
        ("hub_admin_member_agreement", MemberAgreementSettingsForm),
    ]

    def _rendered_names(client: Client, url: str) -> list[str]:
        import re

        body = client.get(url).content.decode()
        # Inputs only, and not the radio groups: a radio group is several inputs sharing one
        # name BY DESIGN (the Features state selector is three of them), which is the one
        # legitimate repeat on these pages.
        tags = re.findall(r"<(?:input|select|textarea)\b[^>]*>", body)
        names = []
        for tag in tags:
            if 'type="radio"' in tag:
                continue
            match = re.search(r'name="([^"]+)"', tag)
            if match:
                names.append(match.group(1))
        return names

    @pytest.mark.parametrize(("url_name", "form_class"), PAGES)
    def it_renders_every_field_of_its_form_exactly_once(client: Client, url_name, form_class):
        _superuser(client, "dupeadmin")
        counts = Counter(_rendered_names(client, reverse(url_name)))
        assert {name: counts[name] for name in form_class.base_fields} == dict.fromkeys(form_class.base_fields, 1)

    def it_gives_every_field_to_exactly_one_page():
        owners = Counter(name for _url, form_class in PAGES for name in form_class.base_fields)
        assert [name for name, n in owners.items() if n > 1] == []


def describe_saving_the_late_cancel_fee_settings():
    """The three #456 fields sit in the Features card and save with the page's Save."""

    def it_saves_the_switch_and_the_window_through_the_view(client: Client):
        _superuser(client, "latefeeadmin")
        data = _settings_post()
        data.update(
            {"late_cancel_fees_enabled": "on", "late_cancel_notice_hours": "48", "late_cancel_grace_hours": "6"}
        )
        response = client.post(URL, data)
        assert response.status_code == 302
        config = SiteConfiguration.load()
        assert config.late_cancel_fees_enabled is True
        assert config.late_cancel_notice_hours == 48
        assert config.late_cancel_grace_hours == 6

    def it_renders_the_three_together_ahead_of_class_registration(client: Client):
        _superuser(client, "latefeerender")
        html = client.get(URL).content.decode()
        positions = [
            html.index(f'name="{name}"')
            for name in (
                "late_cancel_fees_enabled",
                "late_cancel_notice_hours",
                "late_cancel_grace_hours",
                "class_registration_enabled",
            )
        ]
        assert positions == sorted(positions)

    def it_shows_the_window_error_on_the_page(client: Client):
        _superuser(client, "latefeeerror")
        data = _settings_post()
        data.update({"late_cancel_notice_hours": "24", "late_cancel_grace_hours": "24"})
        response = client.post(URL, data)
        assert response.status_code == 200
        assert "The grace period must be shorter than the notice." in response.content.decode()
        assert SiteConfiguration.load().late_cancel_grace_hours == 2


def describe_the_my_tab_card():
    """My Tab moved from a flat toggle into the registry (#416): a card, not a checkbox."""

    def it_shows_a_my_tab_card_with_the_three_states(client: Client):
        _superuser(client, "mytabcard")
        FeatureSwitch.objects.sync_registry()
        html = client.get(URL).content.decode()
        index = [row.feature_key for row in FeatureSwitch.objects.all()].index("my_tab")
        assert "My Tab" in html
        assert "The one switch here that does more than the sidebar." in html
        for state in ("on", "soon", "hidden"):
            assert f'name="features-{index}-state" value="{state}"' in html

    def it_no_longer_renders_a_flat_my_tab_toggle(client: Client):
        _superuser(client, "mytabflat")
        html = client.get(URL).content.decode()
        assert 'id="id_my_tab' not in html
        assert 'name="my_tab' not in html

    def it_saves_coming_soon_for_my_tab_through_the_view(client: Client):
        _superuser(client, "mytabsave")
        response = client.post(URL, _settings_post(states={"my_tab": FeatureState.SOON}))
        assert response.status_code == 302
        assert _state_of("my_tab") == FeatureState.SOON


def describe_a_row_error():
    """A row that fails blocks the whole Save, so the page must say which one and why (#922)."""

    def it_shows_a_bad_state_in_its_own_card(client: Client):
        _superuser(client)
        data = _settings_post()
        FeatureSwitch.objects.sync_registry()
        index = [row.feature_key for row in FeatureSwitch.objects.all()].index("wiki")
        data[f"features-{index}-state"] = "sideways"
        before = _state_of("wiki")
        response = client.post(URL, data)
        assert response.status_code == 200
        html = response.content.decode()
        card = html[html.index('<div class="pl-automation-name">Member Wiki</div>') :]
        card = card[: card.index('<div class="pl-automation-card')]
        assert "data-row-errors" in card
        assert "Select a valid choice" in card
        assert _state_of("wiki") == before

    def it_shows_a_row_that_no_longer_exists_above_the_cards(client: Client):
        _superuser(client)
        data = _settings_post()
        data["features-0-id"] = "999999"
        response = client.post(URL, data)
        assert response.status_code == 200
        html = response.content.decode()
        assert "data-unmatched-row-errors" in html
        assert "Reload the page and save again." in html
        assert html.index("data-unmatched-row-errors") < html.index("pl-automation-list")

    def it_shows_neither_on_a_clean_load(client: Client):
        _superuser(client)
        html = client.get(URL).content.decode()
        assert "data-row-errors" not in html
        assert "data-unmatched-row-errors" not in html
