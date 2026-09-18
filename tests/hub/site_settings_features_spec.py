"""Site Settings → Features — the save path for the three-state feature switches (#405).

The tab renders inside ``#site-settings-form`` and its formset saves with the page's Save, the
same way the Automations toggles beside it do. Everything here drives that POST rather than the
form in isolation, because the form was already covered and the *view* path was not: nothing in
the suite posted ``features-TOTAL_FORMS``, so the bound branch of ``_bind_feature_formset``, the
whole of ``_save_feature_formset`` and ``_resolve_feature_context`` with a bound formset never
ran. That is the feature's main action, and the only thing that writes ``updated_by``.

Shaped on ``tests/hub/site_settings_automations_spec.py``, which does the same job for the
sibling jobstate formset.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from core.features import FEATURES, FeatureState
from core.models import FeatureSwitch, SiteConfiguration

pytestmark = pytest.mark.django_db

URL = reverse("hub_admin_site_settings")


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
    """A full Features-tab save: the shared SiteSettingsForm fields + feeds management form +
    the feature formset."""
    data = {
        "org_name": "Past Lives Makerspace",
        "registration_mode": SiteConfiguration.RegistrationMode.INVITE_ONLY,
        "member_event_policy": SiteConfiguration.MemberEventPolicy.APPROVAL,
        "feeds-TOTAL_FORMS": "0",
        "feeds-INITIAL_FORMS": "0",
        "feeds-MIN_NUM_FORMS": "0",
        "feeds-MAX_NUM_FORMS": "1000",
        "submitted_tab": "features",
    }
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
        html = client.get(f"{URL}?tab=features").content
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

    def describe_when_there_is_no_user_to_credit():
        def it_stores_no_stamp(client: Client):
            """The ``else None`` half of the stamp.

            Unreachable through the view — ``fog_admin_required`` guarantees a saved User — so
            the helper is called directly rather than left as an uncovered branch. An
            AnonymousUser has no pk, which is the condition the guard actually tests.
            """
            from django.contrib.auth.models import AnonymousUser

            from hub.forms import FeatureSwitchFormSet
            from hub.views import _feature_switch_queryset, _save_feature_formset

            FeatureSwitch.objects.sync_registry()
            data = _feature_post_data(states={"voting": FeatureState.HIDDEN})
            formset = FeatureSwitchFormSet(data, queryset=_feature_switch_queryset(), prefix="features")
            _save_feature_formset(formset, True, AnonymousUser())

            row = FeatureSwitch.objects.get(feature_key="voting")
            assert row.state == FeatureState.HIDDEN
            assert row.updated_by is None


def describe_when_the_formset_is_not_posted():
    def it_leaves_every_state_untouched(client: Client):
        """Another tab's save must not reset the switches.

        ``_bind_feature_formset`` only binds when the management form is present, so a POST from
        the Brand or Discord tab carries no feature data and must be a no-op here.
        """
        _superuser(client)
        FeatureSwitch.objects.sync_registry()
        FeatureSwitch.objects.filter(feature_key="voting").update(state=FeatureState.SOON)
        client.post(
            URL,
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
