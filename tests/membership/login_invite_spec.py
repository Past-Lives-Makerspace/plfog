"""BDD specs for the "Send login invite" path (spec §4).

``Member.send_login_invite`` is the distinct, branded first-time-sign-in email for an
existing member who hasn't logged in — separate from ``Invite.create_and_send`` (which
rejects existing members). The login-code page override seeds ``?email=`` so the link
lands with the address pre-filled (Review fix #6).
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User

from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def describe_send_login_invite():
    def it_emails_a_branded_first_time_signin_link_to_the_member(mailoutbox):
        member = MemberFactory(_pre_signup_email="newbie@example.com")

        member.send_login_invite()

        assert len(mailoutbox) == 1
        msg = mailoutbox[0]
        assert msg.to == ["newbie@example.com"]
        html = msg.alternatives[0][0]
        # Branded shell marker (proves it inherited the v0.19.11 branded email shell).
        assert "because you have a Past Lives Makerspace account" in html
        # The CTA points at the login-code page with the member's email pre-filled.
        assert "/accounts/login/code/?email=newbie%40example.com" in html
        assert "Activate your account" in html

    def it_provisions_the_member_before_sending(mailoutbox):
        member = MemberFactory(_pre_signup_email="provisionme@example.com")
        assert member.user_id is None

        member.send_login_invite()

        member.refresh_from_db()
        assert member.user_id is not None
        assert User.objects.filter(email="provisionme@example.com").exists()

    def it_raises_when_the_member_has_no_email(mailoutbox):
        member = MemberFactory(_pre_signup_email="")

        with pytest.raises(ValueError, match="no email on file"):
            member.send_login_invite()

        assert mailoutbox == []

    def it_records_the_send_in_the_audit_log():
        from core.models import TransactionalEmailLog

        member = MemberFactory(_pre_signup_email="audited@example.com")

        member.send_login_invite()

        log = TransactionalEmailLog.objects.get(trigger_kind="member.login_invite")
        assert log.to_email == "audited@example.com"


def describe_login_code_email_prefill():
    def it_prefills_the_email_from_the_query_string(client):
        response = client.get("/accounts/login/code/?email=prefill@example.com")

        assert response.status_code == 200
        assert response.context["form"].initial.get("email") == "prefill@example.com"

    def it_leaves_the_email_unset_without_a_query_string(client):
        response = client.get("/accounts/login/code/")

        assert response.status_code == 200
        assert response.context["form"].initial.get("email") in (None, "")
