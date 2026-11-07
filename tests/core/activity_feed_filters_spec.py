"""The Activity Feed's filter form and its wording (#1114), below the view."""

from datetime import date

import pytest
from django.contrib.auth.models import User
from django.http import QueryDict

from core.activity_feed_filters import ActivityFeedFilterForm, count_line
from core.models import SiteActivity

pytestmark = pytest.mark.django_db


def _kinds(form: ActivityFeedFilterForm) -> set[str]:
    return set(form.filter(SiteActivity.objects.all()).values_list("kind", flat=True))


def describe_ActivityFeedFilterForm():
    def describe_person():
        def it_reads_the_old_actor_param_as_the_person():
            assert ActivityFeedFilterForm({"actor": "zoe@example.com"}).person_value == "zoe@example.com"

        def it_prefers_person_over_actor():
            assert ActivityFeedFilterForm({"actor": "old", "person": "new"}).person_value == "new"

        def it_reads_the_last_value_of_a_repeated_param():
            assert ActivityFeedFilterForm(QueryDict("person=first&person=second")).person_value == "second"

        def it_matches_the_actor_email_in_any_case():
            user = User.objects.create_user(username="bram", email="Bram.Wick@Example.com", password="pw")
            SiteActivity.log(SiteActivity.Kind.LOGIN, actor=user)
            SiteActivity.log(SiteActivity.Kind.LOGOUT)
            assert _kinds(ActivityFeedFilterForm({"person": "bram.wick@"})) == {"login"}

    def describe_category_and_event():
        def it_ignores_an_unknown_category():
            SiteActivity.log(SiteActivity.Kind.LOGIN)
            form = ActivityFeedFilterForm({"category": "gardening"})
            assert form.category_value == ""
            assert _kinds(form) == {"login"}

        def it_ignores_an_event_outside_the_chosen_category():
            SiteActivity.log(SiteActivity.Kind.LOGIN)
            SiteActivity.log(SiteActivity.Kind.VOTE_SUBMITTED)
            form = ActivityFeedFilterForm({"category": "voting", "kind": "login"})
            assert form.kind_value == ""
            assert _kinds(form) == {"vote_submitted"}

        def it_keeps_an_event_inside_the_chosen_category():
            SiteActivity.log(SiteActivity.Kind.VOTE_SUBMITTED)
            SiteActivity.log(SiteActivity.Kind.VOTE_CHANGED)
            form = ActivityFeedFilterForm({"category": "voting", "kind": "vote_changed"})
            assert form.kind_value == "vote_changed"
            assert _kinds(form) == {"vote_changed"}

        def it_filters_by_event_alone():
            SiteActivity.log(SiteActivity.Kind.LOGIN)
            SiteActivity.log(SiteActivity.Kind.LOGOUT)
            assert _kinds(ActivityFeedFilterForm({"kind": "logout"})) == {"logout"}

        def it_finds_nothing_for_an_unknown_event():
            SiteActivity.log(SiteActivity.Kind.LOGIN)
            assert _kinds(ActivityFeedFilterForm({"kind": "k" * 120})) == set()

    def describe_event_groups():
        def it_lists_every_category_without_a_choice():
            groups = ActivityFeedFilterForm({}).event_groups()
            assert len(groups) == 9
            assert sum(len(events) for _label, events in groups) == len(SiteActivity.Kind.choices)

        def it_lists_only_the_chosen_category():
            assert ActivityFeedFilterForm({"category": "voting"}).event_groups() == [
                (
                    "Voting",
                    [
                        ("vote_submitted", "Submitted vote"),
                        ("vote_changed", "Changed vote"),
                        ("funding_snapshot_taken", "Funding snapshot taken"),
                        ("funding_snapshot_deleted", "Funding snapshot deleted"),
                    ],
                )
            ]

    def describe_dates():
        def it_ignores_a_malformed_date_but_keeps_the_other_filters():
            SiteActivity.log(SiteActivity.Kind.LOGIN)
            SiteActivity.log(SiteActivity.Kind.LOGOUT)
            form = ActivityFeedFilterForm({"kind": "login", "feed_from": "not-a-date"})
            assert _kinds(form) == {"login"}

        def it_skips_a_to_bound_on_the_last_representable_day():
            SiteActivity.log(SiteActivity.Kind.LOGIN)
            form = ActivityFeedFilterForm({"feed_to": date.max.isoformat()})
            assert _kinds(form) == {"login"}
            assert form.has_other_filters

    def describe_empty_message():
        def it_names_the_person_alone():
            assert ActivityFeedFilterForm({"person": " Zoe "}).empty_message() == "No activity by Zoe yet."

        def it_names_the_person_with_other_filters():
            form = ActivityFeedFilterForm({"person": "Zoe", "category": "voting"})
            assert form.empty_message() == "No activity by Zoe matches these filters."

        def it_says_the_filters_matched_nothing_without_a_person():
            assert ActivityFeedFilterForm({"feed_from": "2026-11-06"}).empty_message() == (
                "No activity matches these filters."
            )

        def it_says_no_activity_yet_with_no_filters():
            assert ActivityFeedFilterForm({}).empty_message() == "No activity yet."


def describe_count_line():
    def it_is_blank_for_an_empty_feed():
        assert count_line(0) == ""

    def it_uses_the_singular_for_one_event():
        assert count_line(1) == "1 event"

    def it_counts_many_events():
        assert count_line(124) == "124 events"
