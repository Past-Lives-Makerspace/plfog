"""BDD specs for the find-account flow."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import Client

from core.models import TransactionalEmailLog
from membership.models import Member
from tests.membership.factories import MemberFactory


@pytest.mark.django_db
def describe_find_account():
    def describe_GET():
        def it_renders_the_form(client: Client):
            resp = client.get("/accounts/find-account/")
            assert resp.status_code == 200
            assert b"Find Your Account" in resp.content

    def describe_POST():
        def it_shows_done_page_on_submit(client: Client):
            resp = client.post("/accounts/find-account/", {"name": "Nobody"})
            assert resp.status_code == 200
            assert b"Check Your Email" in resp.content

        @patch("core.email.send_mail")
        def it_sends_email_when_member_found(mock_send_mail, client: Client):
            MemberFactory(
                full_legal_name="Alice Smith", _pre_signup_email="alice@example.com", status=Member.Status.ACTIVE
            )

            client.post("/accounts/find-account/", {"name": "Alice Smith"})

            mock_send_mail.assert_called_once()
            call_kwargs = mock_send_mail.call_args
            assert call_kwargs[1]["recipient_list"] == ["alice@example.com"]
            assert "alice@example.com" in call_kwargs[1]["message"]

        def it_matches_preferred_name(client: Client):
            MemberFactory(
                full_legal_name="Alice B. Smith",
                preferred_name="Ali",
                _pre_signup_email="alice@example.com",
                status=Member.Status.ACTIVE,
            )

            client.post("/accounts/find-account/", {"name": "Ali"})

            assert TransactionalEmailLog.objects.filter(trigger_kind="core.find_account").exists()

        def it_is_case_insensitive(client: Client):
            MemberFactory(
                full_legal_name="Alice Smith", _pre_signup_email="alice@example.com", status=Member.Status.ACTIVE
            )

            client.post("/accounts/find-account/", {"name": "alice smith"})

            assert TransactionalEmailLog.objects.filter(trigger_kind="core.find_account").exists()

        def it_does_not_send_email_for_unknown_name(client: Client):
            client.post("/accounts/find-account/", {"name": "Unknown Person"})

            assert not TransactionalEmailLog.objects.filter(trigger_kind="core.find_account").exists()

        def it_does_not_send_email_for_former_members(client: Client):
            MemberFactory(
                full_legal_name="Former Guy", _pre_signup_email="former@example.com", status=Member.Status.FORMER
            )

            client.post("/accounts/find-account/", {"name": "Former Guy"})

            assert not TransactionalEmailLog.objects.filter(trigger_kind="core.find_account").exists()

        def it_does_not_send_if_member_has_no_email(client: Client):
            MemberFactory(full_legal_name="No Email", _pre_signup_email="", status=Member.Status.ACTIVE)

            client.post("/accounts/find-account/", {"name": "No Email"})

            assert not TransactionalEmailLog.objects.filter(trigger_kind="core.find_account").exists()

        def it_re_renders_form_when_name_is_blank(client: Client):
            resp = client.post("/accounts/find-account/", {"name": ""})
            assert resp.status_code == 200
            assert b"Find Your Account" in resp.content
