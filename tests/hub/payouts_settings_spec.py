"""Settings, Payouts tab and the Orientations page nudge (#662, part 1)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from billing.models import BillingSettings, PayoutAccount
from membership.models import GuildStaffMembership, Member
from tests.membership.factories import EquipmentStaffMembershipFactory, GuildFactory, GuildStaffMembershipFactory

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
    """#1097: the card is for members with the Orientor role, not everyone who manages orientations."""

    _NUDGE = 'data-payouts-nudge="orientations"'

    def _page(client) -> str:
        return client.get(reverse("hub_orientations")).content.decode()

    def _give_role(member: Member, role: str) -> None:
        GuildStaffMembershipFactory(guild=GuildFactory(), member=member, role=role)

    def _orientor(client) -> Member:
        member = _login(client)
        _give_role(member, GuildStaffMembership.Role.ORIENTER)
        return member

    def _admin(client) -> Member:
        member = _login(client)
        member.fog_role = Member.FogRole.ADMIN
        member.save(update_fields=["fog_role"])
        member.sync_user_permissions()
        return member

    def it_shows_for_a_member_with_the_orientor_role(client):
        _orientor(client)
        _payouts(on=True)
        html = _page(client)
        assert _NUDGE in html
        assert "Get paid a few days after each orientation" in html
        assert 'href="/settings/?tab=payouts"' in html

    def it_hides_while_payouts_are_off(client):
        _orientor(client)
        _payouts(on=False)
        assert "data-payouts-nudge" not in _page(client)

    def it_hides_once_connected(client):
        member = _orientor(client)
        _payouts(on=True)
        _account(member, PayoutAccount.Status.ACTIVE)
        assert "data-payouts-nudge" not in _page(client)

    def it_hides_for_a_member_who_does_not_orient(client):
        _login(client)
        _payouts(on=True)
        assert "data-payouts-nudge" not in _page(client)

    def it_hides_for_an_admin_without_the_orientor_role(client):
        _admin(client)
        _payouts(on=True)
        response = client.get(reverse("hub_orientations"))
        assert response.context["payouts_nudge"] is False
        assert _NUDGE not in response.content.decode()

    def it_shows_for_an_admin_with_the_orientor_role(client):
        _give_role(_admin(client), GuildStaffMembership.Role.ORIENTER)
        _payouts(on=True)
        assert _NUDGE in _page(client)

    def it_hides_for_an_admin_with_the_role_previewing_as_a_guest(client):
        _give_role(_admin(client), GuildStaffMembership.Role.ORIENTER)
        _payouts(on=True)
        session = client.session
        session["view_as_role"] = "guest"
        session.save()
        assert _NUDGE not in _page(client)

    def it_hides_for_a_guild_lead_without_the_orientor_role(client):
        GuildFactory(name="Quillmarsh Lead Guild", guild_lead=_login(client))
        _payouts(on=True)
        assert _NUDGE not in _page(client)

    @pytest.mark.parametrize(
        "role",
        [
            GuildStaffMembership.Role.CO_LEAD,
            GuildStaffMembership.Role.SECRETARY,
            GuildStaffMembership.Role.TREASURER,
        ],
    )
    def it_hides_for_other_guild_staff_roles(client, role):
        _give_role(_login(client), role)
        _payouts(on=True)
        assert _NUDGE not in _page(client)

    def it_shows_for_a_treasurer_who_is_also_an_orientor(client):
        member = _login(client)
        guild = GuildFactory()
        GuildStaffMembershipFactory(guild=guild, member=member, role=GuildStaffMembership.Role.TREASURER)
        GuildStaffMembershipFactory(guild=guild, member=member, role=GuildStaffMembership.Role.ORIENTER)
        _payouts(on=True)
        assert _NUDGE in _page(client)

    def it_hides_for_a_machine_manager_without_the_orientor_role(client):
        EquipmentStaffMembershipFactory(member=_login(client))
        _payouts(on=True)
        assert _NUDGE not in _page(client)

    def it_leaves_the_staff_tools_gate_unchanged(client):
        """The export is gated by ``manages_orientations``: leads and admins keep it, card or not."""
        export = reverse("hub_orientations_export")
        member = _login(client)
        assert client.get(export).status_code == 403
        GuildFactory(name="Quillmarsh Export Guild", guild_lead=member)
        assert client.get(export).status_code == 200
        assert _NUDGE not in _page(client)


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
