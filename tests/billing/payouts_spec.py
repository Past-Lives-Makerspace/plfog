"""Payouts part 1 (#662): Express accounts, their status, the switch, the payee rule and the signup views."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import stripe
from django.contrib.auth.models import User
from django.urls import reverse

from billing import payouts, stripe_utils
from billing.models import BillingSettings, PayoutAccount
from billing.webhook_handlers import handle_account_updated
from classes.factories import ClassOfferingFactory, RegistrationFactory
from membership.models import Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, MemberFactory

pytestmark = pytest.mark.django_db


def _payouts(*, on: bool = True, test_mode: bool = True) -> BillingSettings:
    bs = BillingSettings.load()
    bs.connect_enabled = on
    bs.test_mode = test_mode
    bs.save()
    return bs


def _stripe_account(**overrides: Any) -> dict[str, Any]:
    account: dict[str, Any] = {
        "id": "acct_1",
        "payouts_enabled": False,
        "details_submitted": False,
        "capabilities": {"transfers": "inactive"},
        "requirements": {"disabled_reason": "requirements.past_due"},
    }
    account.update(overrides)
    return account


_ACTIVE = {
    "payouts_enabled": True,
    "details_submitted": True,
    "capabilities": {"transfers": "active"},
    "requirements": {"disabled_reason": None},
}


def _login_member(client, username: str = "payee") -> Member:
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pw12345!")
    client.force_login(user)
    return Member.objects.get(user=user)


def _teacher(client) -> Member:
    from django.utils import timezone

    member = _login_member(client)
    member.instructor_oriented_at = timezone.now()
    member.save(update_fields=["instructor_oriented_at"])
    return member


def describe_PayoutAccount():
    def describe_status_from_stripe():
        def it_reads_payouts_on_when_payouts_and_transfers_are_enabled():
            assert PayoutAccount.status_from_stripe(_stripe_account(**_ACTIVE)) == PayoutAccount.Status.ACTIVE

        def it_reads_needs_info_while_transfers_are_not_active_yet():
            account = _stripe_account(**{**_ACTIVE, "capabilities": {"transfers": "pending"}})
            assert PayoutAccount.status_from_stripe(account) == PayoutAccount.Status.NEEDS_INFO

        def it_reads_needs_info_before_signup_is_finished():
            assert PayoutAccount.status_from_stripe(_stripe_account()) == PayoutAccount.Status.NEEDS_INFO

        def it_reads_needs_info_while_stripe_reviews_a_finished_signup():
            account = _stripe_account(
                details_submitted=True, requirements={"disabled_reason": "requirements.pending_verification"}
            )
            assert PayoutAccount.status_from_stripe(account) == PayoutAccount.Status.NEEDS_INFO

        def it_reads_paused_when_stripe_disables_a_finished_account():
            account = _stripe_account(details_submitted=True)
            assert PayoutAccount.status_from_stripe(account) == PayoutAccount.Status.PAUSED

        def it_reads_needs_info_for_a_finished_account_with_no_disabled_reason():
            account = _stripe_account(details_submitted=True, requirements={"disabled_reason": None})
            assert PayoutAccount.status_from_stripe(account) == PayoutAccount.Status.NEEDS_INFO

    def describe_apply_stripe_account():
        def it_writes_a_changed_status():
            account = PayoutAccount.objects.create(member=MemberFactory(), stripe_account_id="acct_1", livemode=False)
            account.apply_stripe_account(_stripe_account(**_ACTIVE))
            account.refresh_from_db()
            assert account.status == PayoutAccount.Status.ACTIVE

        def it_does_not_save_an_unchanged_status(django_assert_num_queries):
            account = PayoutAccount.objects.create(member=MemberFactory(), stripe_account_id="acct_1", livemode=False)
            with django_assert_num_queries(0):
                account.apply_stripe_account(_stripe_account())

    def describe_is_connected():
        @pytest.mark.parametrize(
            ("status", "connected"),
            [
                (PayoutAccount.Status.NEEDS_INFO, False),
                (PayoutAccount.Status.ACTIVE, True),
                (PayoutAccount.Status.PAUSED, True),
            ],
        )
        def it_is_true_once_stripe_has_the_details(status, connected):
            assert PayoutAccount(status=status).is_connected is connected

    def describe_str():
        def it_names_the_account_and_its_status():
            account = PayoutAccount(stripe_account_id="acct_9", status=PayoutAccount.Status.PAUSED)
            assert str(account) == "acct_9 (Paused by Stripe)"

    def describe_for_member():
        def it_returns_the_account_in_the_current_mode_only():
            member = MemberFactory()
            test_account = PayoutAccount.objects.create(member=member, stripe_account_id="acct_t", livemode=False)
            live_account = PayoutAccount.objects.create(member=member, stripe_account_id="acct_l", livemode=True)
            _payouts(test_mode=True)
            assert PayoutAccount.for_member(member) == test_account
            _payouts(test_mode=False)
            assert PayoutAccount.for_member(member) == live_account

        def it_is_none_before_signup_starts():
            assert PayoutAccount.for_member(MemberFactory()) is None

    def describe_open_for():
        def it_creates_the_stripe_account_once_and_keeps_it():
            member = MemberFactory()
            _payouts(test_mode=True)
            with patch("billing.stripe_utils.create_express_account", return_value="acct_new") as create:
                first = PayoutAccount.open_for(member)
                second = PayoutAccount.open_for(member)
            assert first == second
            assert (first.stripe_account_id, first.livemode, first.status) == (
                "acct_new",
                False,
                PayoutAccount.Status.NEEDS_INFO,
            )
            create.assert_called_once_with(
                email=member.primary_email, member_pk=member.pk, idempotency_key=f"payout-account-{member.pk}-test"
            )

        def it_keys_a_live_account_by_mode():
            member = MemberFactory()
            _payouts(test_mode=False)
            with patch("billing.stripe_utils.create_express_account", return_value="acct_live") as create:
                account = PayoutAccount.open_for(member)
            assert account.livemode is True
            assert create.call_args.kwargs["idempotency_key"] == f"payout-account-{member.pk}-live"

    def describe_stripe_delegates():
        def it_refreshes_from_stripe():
            account = PayoutAccount.objects.create(member=MemberFactory(), stripe_account_id="acct_1", livemode=False)
            with patch("billing.stripe_utils.retrieve_account", return_value=_stripe_account(**_ACTIVE)) as retrieve:
                account.refresh_from_stripe()
            retrieve.assert_called_once_with(account_id="acct_1")
            assert account.status == PayoutAccount.Status.ACTIVE

        def it_builds_an_onboarding_url():
            account = PayoutAccount(stripe_account_id="acct_1")
            with patch("billing.stripe_utils.create_account_link", return_value="https://connect.stripe.com/x") as link:
                assert account.onboarding_url(return_url="https://h/r") == "https://connect.stripe.com/x"
            link.assert_called_once_with(account_id="acct_1", return_url="https://h/r")

        def it_builds_a_dashboard_url():
            account = PayoutAccount(stripe_account_id="acct_1")
            with patch("billing.stripe_utils.create_login_link", return_value="https://connect.stripe.com/d") as link:
                assert account.dashboard_url() == "https://connect.stripe.com/d"
            link.assert_called_once_with(account_id="acct_1")

    def it_stores_no_bank_identity_or_tax_field():
        names = {field.name for field in PayoutAccount._meta.get_fields()}
        assert names == {"id", "member", "stripe_account_id", "livemode", "status", "created_at", "updated_at"}


def describe_payouts_module():
    def describe_payouts_on():
        def it_follows_the_connect_enabled_switch():
            _payouts(on=False)
            assert payouts.payouts_on() is False
            _payouts(on=True)
            assert payouts.payouts_on() is True

    def describe_has_earning():
        def it_is_true_for_a_paid_registration_in_their_class():
            member = MemberFactory()
            RegistrationFactory(class_offering=ClassOfferingFactory(instructor=member), amount_paid_cents=6000)
            assert payouts.has_earning(member) is True

        def it_ignores_free_registrations():
            member = MemberFactory()
            RegistrationFactory(class_offering=ClassOfferingFactory(instructor=member), amount_paid_cents=0)
            assert payouts.has_earning(member) is False

        def it_is_true_for_a_paid_orientation_they_ran():
            from tests.membership.factories import OrientationBookingFactory

            member = MemberFactory()
            OrientationBookingFactory(oriented_by=member, amount_paid_cents=5000)
            assert payouts.has_earning(member) is True

    def describe_is_payee():
        def it_includes_someone_who_can_teach(client, rf):
            member = _teacher(client)
            request = rf.get("/")
            request.user = member.user
            assert payouts.is_payee(request, member) is True

        def it_includes_someone_who_runs_orientations(client):
            # Through the real page: manages_orientations reads the view_as the middleware sets.
            member = _login_member(client)
            GuildStaffMembershipFactory(guild=GuildFactory(), member=member)
            _payouts(on=True)
            assert client.get(reverse("hub_user_settings")).context["payouts_tab"] is not None

        def it_includes_someone_with_an_earning(client, rf):
            member = _login_member(client)
            RegistrationFactory(class_offering=ClassOfferingFactory(instructor=member), amount_paid_cents=100)
            request = rf.get("/")
            request.user = member.user
            assert payouts.is_payee(request, member) is True

        def it_excludes_a_plain_member(client, rf):
            member = _login_member(client)
            request = rf.get("/")
            request.user = member.user
            assert payouts.is_payee(request, member) is False

    def describe_settings_tab():
        def it_is_hidden_while_payouts_are_off(client, rf):
            member = _teacher(client)
            _payouts(on=False)
            request = rf.get("/")
            request.user = member.user
            assert payouts.settings_tab(request, member) is None

        def it_is_hidden_without_a_member(rf):
            _payouts(on=True)
            assert payouts.settings_tab(rf.get("/"), None) is None

        def it_is_hidden_for_a_plain_member(client, rf):
            member = _login_member(client)
            _payouts(on=True)
            request = rf.get("/")
            request.user = member.user
            assert payouts.settings_tab(request, member) is None

        def it_reads_not_set_up_then_the_account_status(client, rf):
            member = _teacher(client)
            _payouts(on=True)
            request = rf.get("/")
            request.user = member.user
            tab = payouts.settings_tab(request, member)
            assert tab is not None
            assert tab.state == "not_set_up"
            PayoutAccount.objects.create(
                member=member, stripe_account_id="acct_1", livemode=False, status=PayoutAccount.Status.PAUSED
            )
            tab = payouts.settings_tab(request, member)
            assert tab is not None
            assert tab.state == "paused"

    def describe_needs_nudge():
        def it_is_false_while_payouts_are_off():
            _payouts(on=False)
            assert payouts.needs_nudge(MemberFactory()) is False

        def it_is_true_with_no_account():
            _payouts(on=True)
            assert payouts.needs_nudge(MemberFactory()) is True

        def it_is_true_while_signup_is_unfinished():
            _payouts(on=True)
            member = MemberFactory()
            PayoutAccount.objects.create(member=member, stripe_account_id="acct_1", livemode=False)
            assert payouts.needs_nudge(member) is True

        def it_is_false_once_connected():
            _payouts(on=True)
            member = MemberFactory()
            PayoutAccount.objects.create(
                member=member, stripe_account_id="acct_1", livemode=False, status=PayoutAccount.Status.ACTIVE
            )
            assert payouts.needs_nudge(member) is False


def describe_handle_account_updated():
    def _event(account: dict[str, Any], *, livemode: bool = False) -> dict[str, Any]:
        return {"type": "account.updated", "livemode": livemode, "account": account["id"], "data": {"object": account}}

    def it_writes_the_status_stripe_reports_now():
        _payouts(test_mode=True)
        account = PayoutAccount.objects.create(member=MemberFactory(), stripe_account_id="acct_1", livemode=False)
        with patch("billing.stripe_utils.retrieve_account", return_value=_stripe_account(**_ACTIVE)) as retrieve:
            handle_account_updated(_event(_stripe_account(**_ACTIVE)))
        retrieve.assert_called_once_with(account_id="acct_1")
        account.refresh_from_db()
        assert account.status == PayoutAccount.Status.ACTIVE

    def it_keeps_payouts_on_when_an_older_needs_info_event_arrives_late():
        _payouts(test_mode=True)
        account = PayoutAccount.objects.create(
            member=MemberFactory(), stripe_account_id="acct_1", livemode=False, status=PayoutAccount.Status.ACTIVE
        )
        stale = _event(_stripe_account())  # the needs_info payload Stripe retried after the account went active
        with patch("billing.stripe_utils.retrieve_account", return_value=_stripe_account(**_ACTIVE)):
            handle_account_updated(stale)
        account.refresh_from_db()
        assert account.status == PayoutAccount.Status.ACTIVE

    def it_lets_a_stripe_error_fail_the_delivery_so_stripe_retries():
        _payouts(test_mode=True)
        PayoutAccount.objects.create(member=MemberFactory(), stripe_account_id="acct_1", livemode=False)
        with (
            patch("billing.stripe_utils.retrieve_account", side_effect=stripe.APIError("down")),
            pytest.raises(stripe.APIError),
        ):
            handle_account_updated(_event(_stripe_account(**_ACTIVE)))

    def it_ignores_an_event_from_the_other_mode():
        _payouts(test_mode=True)
        account = PayoutAccount.objects.create(member=MemberFactory(), stripe_account_id="acct_1", livemode=False)
        with patch("billing.stripe_utils.retrieve_account") as retrieve:
            handle_account_updated(_event(_stripe_account(**_ACTIVE), livemode=True))
        retrieve.assert_not_called()
        account.refresh_from_db()
        assert account.status == PayoutAccount.Status.NEEDS_INFO

    def it_ignores_an_account_plfog_never_made():
        _payouts(test_mode=True)
        with patch("billing.stripe_utils.retrieve_account") as retrieve:
            handle_account_updated(_event(_stripe_account(id="acct_unknown", **_ACTIVE)))
        retrieve.assert_not_called()
        assert not PayoutAccount.objects.exists()

    def it_is_wired_to_the_stripe_webhook():
        from billing.views import _WEBHOOK_HANDLERS

        assert _WEBHOOK_HANDLERS["account.updated"] is handle_account_updated


def describe_stripe_utils_payouts():
    @pytest.fixture
    def client_mock(configured_billing_stripe):
        mock = MagicMock()
        with patch("billing.stripe_utils._get_stripe_client", return_value=mock):
            yield mock

    def it_creates_an_express_account_paid_out_daily(client_mock):
        client_mock.v1.accounts.create.return_value = MagicMock(id="acct_9")
        assert stripe_utils.create_express_account(email="a@b.c", member_pk=7, idempotency_key="k") == "acct_9"
        params = client_mock.v1.accounts.create.call_args.kwargs["params"]
        assert params["type"] == "express"
        assert params["capabilities"] == {"transfers": {"requested": True}}
        assert params["settings"] == {"payouts": {"schedule": {"interval": "daily"}}}
        assert params["email"] == "a@b.c"
        assert params["metadata"] == {"member_pk": "7"}
        assert client_mock.v1.accounts.create.call_args.kwargs["options"] == {"idempotency_key": "k"}

    def it_returns_to_the_same_url_on_return_and_refresh(client_mock):
        client_mock.v1.account_links.create.return_value = MagicMock(url="https://connect.stripe.com/setup/x")
        url = stripe_utils.create_account_link(account_id="acct_9", return_url="https://h/billing/payouts/return/")
        assert url == "https://connect.stripe.com/setup/x"
        params = client_mock.v1.account_links.create.call_args.kwargs["params"]
        assert params == {
            "account": "acct_9",
            "type": "account_onboarding",
            "return_url": "https://h/billing/payouts/return/",
            "refresh_url": "https://h/billing/payouts/return/",
        }

    def it_creates_a_login_link(client_mock):
        client_mock.v1.accounts.login_links.create.return_value = MagicMock(url="https://connect.stripe.com/express/x")
        assert stripe_utils.create_login_link(account_id="acct_9") == "https://connect.stripe.com/express/x"
        client_mock.v1.accounts.login_links.create.assert_called_once_with("acct_9")

    def it_retrieves_an_account_as_a_dict(client_mock):
        client_mock.v1.accounts.retrieve.return_value.to_dict.return_value = {"id": "acct_9"}
        assert stripe_utils.retrieve_account(account_id="acct_9") == {"id": "acct_9"}
        client_mock.v1.accounts.retrieve.assert_called_once_with("acct_9")

    def describe_construct_webhook_event_with_two_endpoints():
        def it_falls_back_to_the_connected_accounts_secret(configured_billing_stripe):
            configured_billing_stripe.connect_accounts_webhook_secret = "whsec_accounts"
            configured_billing_stripe.save()
            event = MagicMock()
            with patch(
                "billing.stripe_utils.stripe.Webhook.construct_event",
                side_effect=[stripe.SignatureVerificationError("no", "sig"), event],
            ) as construct:
                assert stripe_utils.construct_webhook_event(payload=b"{}", sig_header="sig") is event
            assert construct.call_args.kwargs["secret"] == "whsec_accounts"

        def it_reraises_when_no_connected_accounts_secret_is_set(configured_billing_stripe):
            with (
                patch(
                    "billing.stripe_utils.stripe.Webhook.construct_event",
                    side_effect=stripe.SignatureVerificationError("no", "sig"),
                ),
                pytest.raises(stripe.SignatureVerificationError),
            ):
                stripe_utils.construct_webhook_event(payload=b"{}", sig_header="sig")


def describe_payouts_views():
    def describe_payouts_start():
        def it_sends_a_payee_to_stripe_signup(client):
            member = _teacher(client)
            _payouts(on=True)
            with (
                patch("billing.stripe_utils.create_express_account", return_value="acct_1"),
                patch("billing.stripe_utils.create_account_link", return_value="https://connect.stripe.com/s") as link,
            ):
                response = client.post(reverse("billing_payouts_start"))
            assert response.status_code == 302
            assert response["Location"] == "https://connect.stripe.com/s"
            assert link.call_args.kwargs["return_url"] == "http://testserver/billing/payouts/return/"
            assert PayoutAccount.objects.get().member == member

        def it_does_nothing_while_payouts_are_off(client):
            _teacher(client)
            _payouts(on=False)
            with patch("billing.stripe_utils.create_express_account") as create:
                response = client.post(reverse("billing_payouts_start"))
            create.assert_not_called()
            assert response["Location"] == "/settings/?tab=payouts"

        def it_refuses_a_plain_member(client):
            _login_member(client)
            _payouts(on=True)
            with patch("billing.stripe_utils.create_express_account") as create:
                client.post(reverse("billing_payouts_start"))
            create.assert_not_called()

        def it_returns_to_the_tab_with_a_message_when_stripe_fails(client):
            _teacher(client)
            _payouts(on=True)
            with patch("billing.stripe_utils.create_express_account", side_effect=stripe.APIError("down")):
                response = client.post(reverse("billing_payouts_start"), follow=True)
            assert response.redirect_chain[0][0] == "/settings/?tab=payouts"
            assert [m.level_tag for m in response.context["messages"]] == ["error"]

        def it_requires_post(client):
            _teacher(client)
            assert client.get(reverse("billing_payouts_start")).status_code == 405

    def describe_payouts_return():
        def it_reads_the_status_back_then_lands_on_the_tab(client):
            member = _teacher(client)
            _payouts(on=True)
            account = PayoutAccount.objects.create(member=member, stripe_account_id="acct_1", livemode=False)
            with patch("billing.stripe_utils.retrieve_account", return_value=_stripe_account(**_ACTIVE)):
                response = client.get(reverse("billing_payouts_return"))
            assert response["Location"] == "/settings/?tab=payouts"
            account.refresh_from_db()
            assert account.status == PayoutAccount.Status.ACTIVE

        def it_keeps_the_last_status_when_stripe_fails(client):
            member = _teacher(client)
            _payouts(on=True)
            PayoutAccount.objects.create(member=member, stripe_account_id="acct_1", livemode=False)
            with patch("billing.stripe_utils.retrieve_account", side_effect=stripe.APIError("down")):
                response = client.get(reverse("billing_payouts_return"))
            assert response["Location"] == "/settings/?tab=payouts"
            assert PayoutAccount.objects.get().status == PayoutAccount.Status.NEEDS_INFO

        def it_just_lands_on_the_tab_without_an_account(client):
            _teacher(client)
            _payouts(on=True)
            with patch("billing.stripe_utils.retrieve_account") as retrieve:
                response = client.get(reverse("billing_payouts_return"))
            retrieve.assert_not_called()
            assert response["Location"] == "/settings/?tab=payouts"

        def it_ignores_a_member_who_is_not_a_payee(client):
            _login_member(client)
            _payouts(on=True)
            with patch("billing.stripe_utils.retrieve_account") as retrieve:
                client.get(reverse("billing_payouts_return"))
            retrieve.assert_not_called()

    def describe_payouts_dashboard():
        def it_opens_the_express_dashboard_once_connected(client):
            member = _teacher(client)
            _payouts(on=True)
            PayoutAccount.objects.create(
                member=member, stripe_account_id="acct_1", livemode=False, status=PayoutAccount.Status.ACTIVE
            )
            with patch("billing.stripe_utils.create_login_link", return_value="https://connect.stripe.com/express/1"):
                response = client.get(reverse("billing_payouts_dashboard"))
            assert response["Location"] == "https://connect.stripe.com/express/1"

        def it_sends_an_unconnected_payee_back_to_the_tab(client):
            member = _teacher(client)
            _payouts(on=True)
            PayoutAccount.objects.create(member=member, stripe_account_id="acct_1", livemode=False)
            with patch("billing.stripe_utils.create_login_link") as link:
                response = client.get(reverse("billing_payouts_dashboard"))
            link.assert_not_called()
            assert response["Location"] == "/settings/?tab=payouts"

        def it_sends_a_plain_member_back_to_the_tab(client):
            _login_member(client)
            _payouts(on=True)
            assert client.get(reverse("billing_payouts_dashboard"))["Location"] == "/settings/?tab=payouts"

        def it_returns_to_the_tab_with_a_message_when_stripe_fails(client):
            member = _teacher(client)
            _payouts(on=True)
            PayoutAccount.objects.create(
                member=member, stripe_account_id="acct_1", livemode=False, status=PayoutAccount.Status.PAUSED
            )
            with patch("billing.stripe_utils.create_login_link", side_effect=stripe.APIError("down")):
                response = client.get(reverse("billing_payouts_dashboard"), follow=True)
            assert response.redirect_chain[0][0] == "/settings/?tab=payouts"
            assert [m.level_tag for m in response.context["messages"]] == ["error"]


def describe_admin_switch():
    def it_turns_payouts_on_and_off_from_the_stripe_tab(client):
        user = User.objects.create_user(username="adm", email="adm@example.com", password="pw12345!")
        member = Member.objects.get(user=user)
        member.fog_role = Member.FogRole.ADMIN
        member.save()
        client.force_login(user)
        credentials = {
            "test_mode": "on",
            "test_connect_client_id": "ca_t",
            "test_connect_platform_publishable_key": "pk_test_1",
            "test_connect_platform_secret_key": "sk_test_1",
            "test_connect_platform_webhook_secret": "whsec_p",
            "test_connect_accounts_webhook_secret": "whsec_a",
        }
        client.post(reverse("billing_save_connect_platform"), {**credentials, "connect_enabled": "on"})
        assert payouts.payouts_on() is True
        client.post(reverse("billing_save_connect_platform"), credentials)
        assert payouts.payouts_on() is False

    def it_is_off_by_default():
        assert BillingSettings.load().connect_enabled is False
