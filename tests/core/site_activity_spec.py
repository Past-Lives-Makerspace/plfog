"""BDD-style tests for core.models.SiteActivity."""

import pytest
from django.contrib.auth.models import User

from core.models import SiteActivity, TransactionalEmailLog

pytestmark = pytest.mark.django_db


def describe_SiteActivity():
    def describe_log():
        def it_creates_a_row_with_actor_and_kind():
            user = User.objects.create_user(username="u1", email="u1@example.com")
            activity = SiteActivity.log(SiteActivity.Kind.LOGIN, actor=user)
            assert activity.kind == "login"
            assert activity.actor == user

        def it_accepts_a_null_actor_for_system_events():
            activity = SiteActivity.log(SiteActivity.Kind.FUNDING_SNAPSHOT_TAKEN, actor=None)
            assert activity.actor is None

        def it_ignores_an_unsaved_actor():
            ghost = User(username="ghost")  # no pk
            activity = SiteActivity.log(SiteActivity.Kind.LOGIN, actor=ghost)
            assert activity.actor is None

        def it_attaches_a_generic_target():
            user = User.objects.create_user(username="u2", email="u2@example.com")
            target_log = TransactionalEmailLog.objects.create(
                to_email="x@example.com",
                subject="s",
                trigger_kind="t",
                status=TransactionalEmailLog.Status.SENT,
            )
            activity = SiteActivity.log(
                SiteActivity.Kind.TAB_CHARGED,
                actor=user,
                target=target_log,
            )
            assert activity.target == target_log

        def it_links_an_email_log():
            email_log = TransactionalEmailLog.objects.create(
                to_email="x@example.com",
                subject="s",
                trigger_kind="billing.receipt",
                status=TransactionalEmailLog.Status.SENT,
            )
            activity = SiteActivity.log(SiteActivity.Kind.TAB_CHARGED, email_log=email_log)
            assert activity.email_log == email_log

        def it_defaults_payload_to_empty_dict():
            activity = SiteActivity.log(SiteActivity.Kind.LOGOUT)
            assert activity.payload == {}

    def describe_str():
        def it_is_readable():
            user = User.objects.create_user(username="u3", email="u3@example.com")
            activity = SiteActivity.log(SiteActivity.Kind.LOGIN, actor=user)
            assert "Logged in" in str(activity)
