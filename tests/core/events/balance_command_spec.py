"""Specs for the ``/balance`` slash command handler (billing.discord_commands)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from billing.discord_commands import BALANCE, _balance
from billing.models import Tab
from core.models import SiteConfiguration
from tests.billing.factories import TabEntryFactory, TabFactory

pytestmark = pytest.mark.django_db


def _content(member) -> str:
    return _balance({}, member)["data"]["content"]


def describe_balance_command_definition():
    def it_is_a_gated_ephemeral_immediate_command():
        assert BALANCE.name == "balance"
        assert (BALANCE.requires_link, BALANCE.ephemeral, BALANCE.defer) == (True, True, False)


def describe_balance():
    def it_shows_balance_remaining_and_card_with_a_manage_link(linked_member, settings):
        settings.MEMBER_BASE_URL = "https://members.example"
        member = linked_member()
        tab = TabFactory(member=member, tab_limit=Decimal("150.00"))
        TabEntryFactory(tab=tab, amount=Decimal("42.50"))

        content = _content(member)

        assert "$42.50" in content
        assert "$107.50" in content  # 150.00 limit − 42.50 balance
        assert "Visa on file" in content
        assert "https://members.example/tab/" in content

    def describe_with_a_zero_balance():
        def it_says_all_clear(linked_member):
            member = linked_member()
            TabFactory(member=member)
            content = _content(member)
            assert "you're all clear ✨" in content
            assert "Remaining before limit" not in content

    def describe_with_no_payment_method():
        def it_offers_the_setup_link(linked_member, settings):
            settings.MEMBER_BASE_URL = "https://members.example"
            member = linked_member()
            TabFactory(member=member, stripe_payment_method_id="", payment_method_brand="")
            content = _content(member)
            assert "None on file" in content
            assert "Add a card" in content
            assert "https://members.example/billing/payment-method/setup/" in content

    def describe_with_a_locked_tab():
        def it_warns_and_links_to_update_the_card(linked_member):
            member = linked_member()
            TabFactory(member=member, is_locked=True)
            content = _content(member)
            assert "on hold after a failed payment" in content

    def describe_when_the_member_has_no_tab_yet():
        def it_creates_one_via_get_or_create(linked_member):
            member = linked_member()
            assert not Tab.objects.filter(member=member).exists()
            content = _content(member)
            assert "Your tab" in content
            assert Tab.objects.filter(member=member).exists()

    def describe_when_tab_payments_are_disabled():
        def it_returns_the_gate_reply_without_touching_the_tab(linked_member):
            config = SiteConfiguration.load()
            config.tab_payments_enabled = False
            config.save(update_fields=["tab_payments_enabled"])
            member = linked_member()

            content = _content(member)

            assert "aren't enabled" in content
            assert not Tab.objects.filter(member=member).exists()
