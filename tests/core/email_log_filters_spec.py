"""The Email Log's filter form and its wording (#1112), below the view."""

import pytest

from core.email_log_filters import EmailLogFilterForm, count_line, type_choices, type_label
from core.factories import TransactionalEmailLogFactory
from core.models import TransactionalEmailLog

pytestmark = pytest.mark.django_db


def describe_type_label():
    def it_uses_the_registry_label_for_a_known_key():
        assert type_label("member.login_invite") == "Sign in to Past Lives for the first time"

    def it_falls_back_to_the_raw_key():
        assert type_label("billing.receipt") == "billing.receipt"


def describe_type_choices():
    def it_lists_each_logged_type_once_sorted_by_label():
        TransactionalEmailLogFactory(trigger_kind="zz.unregistered")
        TransactionalEmailLogFactory(trigger_kind="member.login_invite")
        TransactionalEmailLogFactory(trigger_kind="member.login_invite")
        TransactionalEmailLogFactory(trigger_kind="billing.receipt")
        assert type_choices() == [
            ("billing.receipt", "billing.receipt"),
            ("member.login_invite", "Sign in to Past Lives for the first time"),
            ("zz.unregistered", "zz.unregistered"),
        ]


def describe_EmailLogFilterForm():
    def it_ignores_a_malformed_date_but_keeps_the_other_filters():
        TransactionalEmailLogFactory(to_email="kept@example.com")
        TransactionalEmailLogFactory(to_email="dropped@example.com")
        form = EmailLogFilterForm({"recipient": "kept", "date_from": "not-a-date"})
        assert list(form.filter(TransactionalEmailLog.objects.all()).values_list("to_email", flat=True)) == [
            "kept@example.com"
        ]
        assert not form.has_other_filters

    def it_ignores_an_unknown_status():
        TransactionalEmailLogFactory()
        form = EmailLogFilterForm({"status": "bounced"})
        assert form.filter(TransactionalEmailLog.objects.all()).count() == 1

    def describe_empty_message():
        def it_names_the_address_alone():
            assert EmailLogFilterForm({"recipient": " zoe@example.com "}).empty_message() == (
                "No emails to zoe@example.com have been logged."
            )

        def it_names_the_address_with_other_filters():
            form = EmailLogFilterForm({"recipient": "zoe@example.com", "status": "failed"})
            assert form.empty_message() == "No emails to zoe@example.com match these filters."

        def it_says_the_filters_matched_nothing_without_a_recipient():
            assert EmailLogFilterForm({"date_to": "2026-11-06"}).empty_message() == "No emails match these filters."

        def it_says_nothing_is_logged_with_no_filters():
            assert EmailLogFilterForm({}).empty_message() == "No emails logged yet."


def describe_count_line():
    def it_is_blank_for_an_empty_set():
        assert count_line(TransactionalEmailLog.objects.all()) == ""

    def it_uses_the_singular_for_one_email():
        TransactionalEmailLogFactory(status="failed")
        assert count_line(TransactionalEmailLog.objects.all()) == "1 email: 1 failed"

    def it_names_each_outcome_present_in_model_order():
        for status in ("failed", "sent", "sent"):
            TransactionalEmailLogFactory(status=status)
        assert count_line(TransactionalEmailLog.objects.all()) == "3 emails: 2 sent, 1 failed"
