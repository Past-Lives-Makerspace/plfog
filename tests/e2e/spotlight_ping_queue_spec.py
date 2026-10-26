"""End-to-end: Admin Tools > Spotlight's Waiting for Review section in a real browser (#943, part 1).

A queued Discord ping shows above the Spotlight messages with its drafted line, the whole Discord text, its guild,
its sender and a link that opens the message in Discord in a new tab; the section clears the card's intro, and at
phone width nothing scrolls sideways. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from core.factories import QueuedDiscordMessageFactory
from core.models import QueuedDiscordMessage, TourState
from membership.models import Member
from tests.membership.factories import GuildFactory, MemberFactory, MembershipPlanFactory

QUEUE = "[data-ping-queue]"
LINK = "https://discord.com/channels/1/111/777"


def _sign_in_admin(login_via_code, email: str) -> None:
    MembershipPlanFactory()
    login_via_code(email)
    user = get_user_model().objects.get(username=email)
    member = Member.objects.filter(user=user).first() or MemberFactory(user=user)
    member.fog_role = Member.FogRole.ADMIN
    member.welcome_dismissed_at = timezone.now()
    member.save(update_fields=["fog_role", "welcome_dismissed_at"])
    member.sync_user_permissions()
    TourState.objects.mark_completed(user, "member-welcome")


def _queue_one() -> None:
    text = "Please make sure to wash your space before you leave the studio tonight.\nThe kiln is loaded."
    ping = QueuedDiscordMessageFactory(
        discord_message_id="777",
        discord_url=LINK,
        original_text=text,
        line=QueuedDiscordMessage.draft_line(text, ["Ceramics"], "Jay Holtman", LINK),
    )
    ping.guilds.set([GuildFactory(name="Ceramics Guild")])


def describe_waiting_for_review():
    def it_shows_the_queued_ping_above_the_messages(page, live_server, login_via_code):
        _sign_in_admin(login_via_code, "ping-queue-e2e@example.com")
        _queue_one()
        page.set_viewport_size({"width": 1440, "height": 1000})

        page.goto(f"{live_server.url}{reverse('hub_admin_spotlight')}")

        queue = page.locator(QUEUE)
        expect(queue.locator("[data-ping-line]")).to_have_text(
            "Ceramics: Please make sure to wash your space before you leave the studio tonight. "
            "The kiln is loaded. (Jay Holtman)"
        )
        expect(queue.locator("[data-ping-original]")).to_contain_text("The kiln is loaded.")
        expect(queue.locator("[data-ping-guild]")).to_have_text("Ceramics Guild")
        expect(queue.locator("[data-ping-sender]")).to_have_text("Sent by Discord Dee")
        link = queue.locator("[data-ping-link]")
        expect(link).to_have_attribute("href", LINK)
        expect(link).to_have_attribute("target", "_blank")
        queue_box = queue.bounding_box()
        intro_box = page.locator(".pl-ann-admin__intro").bounding_box()
        assert intro_box["y"] - (queue_box["y"] + queue_box["height"]) >= 12

    def it_fits_a_phone_without_scrolling_sideways(page, live_server, login_via_code):
        _sign_in_admin(login_via_code, "ping-queue-phone-e2e@example.com")
        _queue_one()
        page.set_viewport_size({"width": 390, "height": 900})

        page.goto(f"{live_server.url}{reverse('hub_admin_spotlight')}")

        expect(page.locator(QUEUE)).to_be_visible()
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
