"""Billing's Discord slash command: ``/balance`` — the caller's own tab, in Discord.

Autodiscovered by :func:`core.events.discord_commands.autodiscover`. The handler stays
thin: it feature-gates, reads the caller's :class:`~billing.models.Tab`, and hands the
numbers to a reply builder. ``requires_link=True`` guarantees ``member`` is non-``None``.
"""

from __future__ import annotations

from core.events.discord_commands import SlashCommand, register
from core.events.discord_interactions import reply
from core.events.discord_replies import hub_url

# An interaction payload is Discord's JSON dict; the second arg is the resolved Member.
Interaction = dict


def _balance(interaction: Interaction, member) -> dict:  # noqa: ANN001 - Member | None, but linked so non-None
    """Render the caller's tab balance, remaining limit, and payment method — with a manage link.

    Feature-gated first: when My Tab & Payments is off site-wide there's no tab to read, so
    the gate reply returns immediately. Otherwise a tab is fetched (created on first use) and
    every state — zero balance, no card, locked — carries its actionable next step.
    """
    from billing.models import Tab
    from core.models import SiteConfiguration

    if not SiteConfiguration.load().tab_payments_enabled:
        return reply("Tab payments aren't enabled right now.", ephemeral=True)

    tab, _ = Tab.objects.get_or_create(member=member)
    tab_url = hub_url("hub_tab_detail")
    setup_url = hub_url("billing_setup_payment_method")

    lines = ["💳 **Your tab**"]
    balance = tab.current_balance
    if balance <= 0:
        lines.append("Current balance: **$0.00** — you're all clear ✨")
    else:
        lines.append(f"Current balance: **${balance:.2f}**")
        lines.append(f"Remaining before limit: **${tab.remaining_limit:.2f}**")

    if tab.has_payment_method:
        brand = tab.payment_method_brand.title() if tab.payment_method_brand else "Card"
        lines.append(f"Payment method: **{brand} on file**")
    else:
        lines.append("Payment method: **None on file**")

    if tab.is_locked:
        lines.append(f"Your tab is on hold after a failed payment — update your card: {setup_url}")
    elif not tab.has_payment_method:
        lines.append(f"Add a card to keep using your tab: {setup_url}")

    lines.append(f"Manage your tab: {tab_url}")
    return reply("\n".join(lines), ephemeral=True)


BALANCE = SlashCommand(
    name="balance",
    description="Check your tab balance and remaining limit.",
    handler=_balance,
    requires_link=True,
    ephemeral=True,
    defer=False,
    scope="guild",
)

register(BALANCE)
