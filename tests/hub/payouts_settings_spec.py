"""Settings, Payouts tab and the Orientations page nudge (#662, part 1)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from billing.models import BillingSettings, PayoutAccount
from membership.models import Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory

pytestmark = pytest.mark.django_db

_START = 'action="/billing/payouts/start/"'
_DASHBOARD = 'href="/billing/payouts/dashboard/"'


def _payouts(*, on: bool) -> None:
    bs = BillingSettings.load()
    bs.connect_enabled = on
    bs.save()


def _login(client, *, teacher: bool = False) -> Member:
    user = User.objects.create_user(username="payee", email="payee@example.com", password="pw12345!")
    member = Member.objects.get(user=user)
    if teacher:
        member.instructor_oriented_at = timezone.now()
        member.save(update_fields=["instructor_oriented_at"])
    client.force_login(user)
    return member


def _account(member: Member, status: str) -> PayoutAccount:
    return PayoutAccount.objects.create(member=member, stripe_account_id="acct_1", livemode=False, status=status)


def describe_payouts_tab():
    def it_is_absent_while_payouts_are_off(client):
        _login(client, teacher=True)
        _payouts(on=False)
        response = client.get(reverse("hub_user_settings"))
        assert response.context["payouts_tab"] is None
        assert "data-payouts-state" not in response.content.decode()

    def it_is_absent_for_a_member_who_neither_teaches_nor_orients(client):
        _login(client)
        _payouts(on=True)
        response = client.get(reverse("hub_user_settings") + "?tab=payouts")
        assert response.context["payouts_tab"] is None
        assert response.context["active_tab"] == "guilds"
        assert "data-payouts-state" not in response.content.decode()

    def it_opens_on_the_payouts_tab_for_a_payee(client):
        _login(client, teacher=True)
        _payouts(on=True)
        response = client.get(reverse("hub_user_settings") + "?tab=payouts")
        assert response.context["active_tab"] == "payouts"
        assert "go('payouts')" in response.content.decode()

    def it_shows_not_set_up_with_the_signup_button(client):
        _login(client, teacher=True)
        _payouts(on=True)
        html = client.get(reverse("hub_user_settings")).content.decode()
        assert 'data-payouts-state="not_set_up"' in html
        assert _START in html
        assert _DASHBOARD not in html
        assert 'class="pl-payouts-steps"' in html

    def it_shows_needs_info_with_finish_setup(client):
        member = _login(client, teacher=True)
        _payouts(on=True)
        _account(member, PayoutAccount.Status.NEEDS_INFO)
        html = client.get(reverse("hub_user_settings")).content.decode()
        assert 'data-payouts-state="needs_info"' in html
        assert "pl-status-badge--warn" in html
        assert _START in html
        assert _DASHBOARD not in html

    def it_shows_payouts_on_with_open_stripe(client):
        member = _login(client, teacher=True)
        _payouts(on=True)
        _account(member, PayoutAccount.Status.ACTIVE)
        html = client.get(reverse("hub_user_settings")).content.decode()
        assert 'data-payouts-state="active"' in html
        assert "pl-status-badge--ok" in html
        assert _DASHBOARD in html
        assert _START not in html

    def it_shows_paused_with_fix_in_stripe_and_open_stripe(client):
        member = _login(client, teacher=True)
        _payouts(on=True)
        _account(member, PayoutAccount.Status.PAUSED)
        html = client.get(reverse("hub_user_settings")).content.decode()
        assert 'data-payouts-state="paused"' in html
        assert "pl-status-badge--fail" in html
        assert _START in html
        assert _DASHBOARD in html

    def it_keeps_every_off_site_control_out_of_hx_boost(client):
        member = _login(client, teacher=True)
        _payouts(on=True)
        _account(member, PayoutAccount.Status.PAUSED)
        html = client.get(reverse("hub_user_settings")).content.decode()
        assert html.count('hx-boost="false"') >= 2


def describe_orientations_nudge():
    def _orientor(client) -> Member:
        member = _login(client)
        GuildStaffMembershipFactory(guild=GuildFactory(), member=member)
        return member

    def it_shows_for_someone_who_runs_orientations(client):
        _orientor(client)
        _payouts(on=True)
        html = client.get(reverse("hub_orientations")).content.decode()
        assert 'data-payouts-nudge="orientations"' in html
        assert 'href="/settings/?tab=payouts"' in html

    def it_hides_while_payouts_are_off(client):
        _orientor(client)
        _payouts(on=False)
        assert "data-payouts-nudge" not in client.get(reverse("hub_orientations")).content.decode()

    def it_hides_once_connected(client):
        member = _orientor(client)
        _payouts(on=True)
        _account(member, PayoutAccount.Status.ACTIVE)
        assert "data-payouts-nudge" not in client.get(reverse("hub_orientations")).content.decode()

    def it_hides_for_a_member_who_does_not_orient(client):
        _login(client)
        _payouts(on=True)
        assert "data-payouts-nudge" not in client.get(reverse("hub_orientations")).content.decode()


def describe_earnings_card():
    def it_says_so_when_there_are_no_earnings(client):
        _login(client, teacher=True)
        _payouts(on=True)
        html = client.get(reverse("hub_user_settings")).content.decode()
        assert "data-payouts-earnings" in html
        assert 'class="pl-table-empty"' in html
        assert "data-earning-state" not in html

    def it_lists_an_earning_owed_at_month_end_for_an_unconnected_teacher(client):
        from datetime import timedelta

        from classes.factories import ClassOfferingFactory, ClassSessionFactory, RegistrationFactory

        member = _login(client, teacher=True)
        _payouts(on=True)
        offering = ClassOfferingFactory(instructor=member, title="Glaze Chemistry")
        ClassSessionFactory(class_offering=offering, starts_at=timezone.now() - timedelta(days=2))
        RegistrationFactory(
            class_offering=offering, amount_paid_cents=9000, stripe_payment_id="pi_1", confirmed_at=timezone.now()
        )
        html = client.get(reverse("hub_user_settings")).content.decode()
        assert 'data-earning-state="owed"' in html
        assert "pl-status-badge--muted" in html
