"""BDD-style tests for TourState + TourStateManager (guided tours, Spec C §4)."""

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError

from core.models import TourState

pytestmark = pytest.mark.django_db


def _user(name: str = "tourist") -> User:
    return User.objects.create_user(username=name, email=f"{name}@example.com")


def describe_tour_migrations():
    def it_ships_only_auto_reversible_schema_operations():
        # Spec C §4: no data migrations — both migrations are pure schema ops
        # (CreateModel / AddField), which Django reverses automatically.
        from importlib import import_module

        for module_name in (
            "core.migrations.0059_tourstate",
            "membership.migrations.0108_member_guided_tours_enabled",
        ):
            migration = import_module(module_name).Migration
            for operation in migration.operations:
                assert type(operation).__name__ in {"CreateModel", "AddField"}


def describe_TourState():
    def it_is_unique_per_user_and_tour():
        user = _user("uq")
        TourState.objects.create(user=user, tour_key="member-welcome")
        with pytest.raises(IntegrityError):
            TourState.objects.create(user=user, tour_key="member-welcome")

    def it_str_includes_email_key_and_status():
        user = _user("strr")
        state = TourState.objects.create(user=user, tour_key="member-welcome")
        assert str(state) == "strr@example.com:member-welcome=Offered"

    def describe_mark_offered():
        def it_creates_an_offered_row_once():
            user = _user("offer")
            state = TourState.objects.mark_offered(user, "member-welcome")
            assert state.status == TourState.Status.OFFERED
            again = TourState.objects.mark_offered(user, "member-welcome")
            assert again.pk == state.pk
            assert TourState.objects.filter(user=user, tour_key="member-welcome").count() == 1

        def it_never_changes_an_existing_rows_status():
            user = _user("offer2")
            TourState.objects.mark_completed(user, "instructor")
            state = TourState.objects.mark_offered(user, "instructor")
            assert state.status == TourState.Status.COMPLETED

        def it_raises_on_an_unregistered_key():
            with pytest.raises(ValueError, match="Unknown tour key"):
                TourState.objects.mark_offered(_user("badkey"), "instructor-orientation")

    def describe_mark_completed():
        def it_creates_a_completed_row_when_absent():
            user = _user("comp")
            state = TourState.objects.mark_completed(user, "guild-lead")
            assert state.status == TourState.Status.COMPLETED

        def it_upgrades_an_offered_row():
            user = _user("comp2")
            TourState.objects.mark_offered(user, "guild-lead")
            TourState.objects.mark_completed(user, "guild-lead")
            assert TourState.objects.get(user=user, tour_key="guild-lead").status == TourState.Status.COMPLETED

        def it_upgrades_a_dismissed_row():
            user = _user("comp3")
            TourState.objects.mark_dismissed(user, "guild-lead")
            TourState.objects.mark_completed(user, "guild-lead")
            assert TourState.objects.get(user=user, tour_key="guild-lead").status == TourState.Status.COMPLETED

        def it_raises_on_an_unregistered_key():
            with pytest.raises(ValueError, match="Unknown tour key"):
                TourState.objects.mark_completed(_user("badkey2"), "nope")

    def describe_mark_dismissed():
        def it_creates_a_dismissed_row_when_absent():
            user = _user("dis")
            state = TourState.objects.mark_dismissed(user, "member-welcome")
            assert state.status == TourState.Status.DISMISSED

        def it_downgrades_an_offered_row():
            user = _user("dis2")
            TourState.objects.mark_offered(user, "member-welcome")
            TourState.objects.mark_dismissed(user, "member-welcome")
            assert TourState.objects.get(user=user, tour_key="member-welcome").status == TourState.Status.DISMISSED

        def it_is_a_noop_on_a_completed_row():
            # The sticky guard Spec D leans on: a re-run Esc'd halfway never
            # erases the completion.
            user = _user("dis3")
            TourState.objects.mark_completed(user, "member-welcome")
            state = TourState.objects.mark_dismissed(user, "member-welcome")
            assert state.status == TourState.Status.COMPLETED
            assert TourState.objects.get(user=user, tour_key="member-welcome").status == TourState.Status.COMPLETED

        def it_raises_on_an_unregistered_key():
            with pytest.raises(ValueError, match="Unknown tour key"):
                TourState.objects.mark_dismissed(_user("badkey3"), "nope")

    def describe_status_for():
        def it_returns_none_when_no_row():
            assert TourState.objects.status_for(_user("sf"), "member-welcome") is None

        def it_returns_the_status():
            user = _user("sf2")
            TourState.objects.mark_offered(user, "member-welcome")
            assert TourState.objects.status_for(user, "member-welcome") == TourState.Status.OFFERED

        def it_raises_on_an_unregistered_key():
            with pytest.raises(ValueError, match="Unknown tour key"):
                TourState.objects.status_for(_user("sf3"), "nope")

    def describe_statuses_for():
        def it_maps_every_tour_key_to_its_status_in_one_dict():
            user = _user("all")
            TourState.objects.mark_offered(user, "member-welcome")
            TourState.objects.mark_completed(user, "instructor")
            assert TourState.objects.statuses_for(user) == {
                "member-welcome": TourState.Status.OFFERED,
                "instructor": TourState.Status.COMPLETED,
            }

        def it_returns_an_empty_dict_for_an_untoured_user():
            assert TourState.objects.statuses_for(_user("none")) == {}
