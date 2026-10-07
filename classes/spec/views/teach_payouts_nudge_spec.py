"""The payouts nudge on Manage My Classes, Overview (#662, part 1)."""

from __future__ import annotations

import pytest
from django.urls import reverse

from billing.models import BillingSettings, PayoutAccount

pytestmark = pytest.mark.django_db


def _payouts(*, on: bool) -> None:
    bs = BillingSettings.load()
    bs.connect_enabled = on
    bs.save()


def describe_teach_overview_payouts_nudge():
    def it_points_a_teacher_at_settings_payouts(client, member_user):
        client.force_login(member_user)
        _payouts(on=True)
        html = client.get(reverse("classes:teach_overview")).content.decode()
        assert 'data-payouts-nudge="classes"' in html
        assert 'href="/settings/?tab=payouts"' in html

    def it_stays_while_signup_is_unfinished(client, member_user):
        client.force_login(member_user)
        _payouts(on=True)
        PayoutAccount.objects.create(member=member_user.member, stripe_account_id="acct_1", livemode=False)
        assert 'data-payouts-nudge="classes"' in client.get(reverse("classes:teach_overview")).content.decode()

    def it_hides_once_connected(client, member_user):
        client.force_login(member_user)
        _payouts(on=True)
        PayoutAccount.objects.create(
            member=member_user.member,
            stripe_account_id="acct_1",
            livemode=False,
            status=PayoutAccount.Status.ACTIVE,
        )
        assert "data-payouts-nudge" not in client.get(reverse("classes:teach_overview")).content.decode()

    def it_hides_while_payouts_are_off(client, member_user):
        client.force_login(member_user)
        _payouts(on=False)
        assert "data-payouts-nudge" not in client.get(reverse("classes:teach_overview")).content.decode()
