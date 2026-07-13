"""BDD specs for the shared "Share & Print" QR card: the reusable component renders safely
inside a form (name-less copy input, themed wrapper), the event edit form shows the live card
only when the event is published, and the class-side rename left no ``.pl-class-share`` /
``classShare(`` references behind (the invisible refactor)."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse

from tests.membership.factories import CommunityEventFactory, GuildFactory, MembershipPlanFactory

REPO = Path(__file__).resolve().parents[2]


def describe_shared_qr_share_card():
    def it_renders_a_form_safe_themed_card():
        html = render_to_string(
            "components/qr_share_card.html",
            {
                "qr_svg": "<svg id='x'></svg>",
                "share_url": "https://members.test/events/5/",
                "svg_url": "/events/5/qr.svg/",
                "png_url": "/events/5/qr.png/",
                "title": "Share & Print",
                "hint": "This QR opens the event's public page.",
            },
        )
        assert "pl-qr-share" in html
        assert "qrShare()" in html
        # The QR SVG is dropped in raw (|safe).
        assert "<svg id='x'></svg>" in html
        # Copy input sits inside .hub-form-group (theme tokens → not a white box on dark).
        assert "hub-form-group" in html
        # ...and carries NO name, so it never rides along on a host form's Save.
        input_tag = html.split("<input", 1)[1].split(">", 1)[0]
        assert "name=" not in input_tag
        assert "readonly" in input_tag
        # Every control is a button/link — nothing submits.
        assert 'type="button"' in html
        assert "/events/5/qr.svg/" in html
        assert "/events/5/qr.png/" in html


@pytest.mark.django_db
def describe_event_edit_share_card():
    def _lead_of(username: str):
        MembershipPlanFactory()
        user = User.objects.create_user(username=username, password="pass")
        guild = GuildFactory(guild_lead=user.member)
        return user, guild

    def it_shows_the_live_card_for_a_published_saved_event(client: Client):
        _user, guild = _lead_of("ev_share_lead")
        event = CommunityEventFactory(guild=guild)  # published by default
        client.login(username="ev_share_lead", password="pass")
        resp = client.get(reverse("hub_guild_event_edit", args=[guild.pk, event.pk]))
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "pl-qr-share" in body
        assert "qrShare()" in body  # the live card, not the placeholder
        assert "hub-form-group" in body
        assert reverse("hub_event_qr", args=[event.pk, "svg"]) in body
        assert reverse("hub_event_qr", args=[event.pk, "png"]) in body

    def it_shows_a_placeholder_for_a_new_unsaved_event(client: Client):
        _user, guild = _lead_of("ev_share_lead2")
        client.login(username="ev_share_lead2", password="pass")
        resp = client.get(reverse("hub_guild_event_add", args=[guild.pk]))
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "appear here once this event is published" in body
        assert "qrShare()" not in body  # placeholder only — no dead QR


def describe_class_side_invisible_refactor():
    def it_has_no_surviving_class_share_references():
        offenders = []
        for path in [*REPO.glob("templates/**/*.html"), *REPO.glob("static/css/*.css")]:
            text = path.read_text()
            if "pl-class-share" in text or "classShare(" in text:
                offenders.append(str(path.relative_to(REPO)))
        assert offenders == []

    def it_kept_a_themed_placeholder_in_both_class_portals():
        # Catches the missed teach-portal file: renaming the CSS without updating this
        # template would silently strip its placeholder styling.
        admin_tpl = (REPO / "templates/classes/admin/class_form.html").read_text()
        teach_tpl = (REPO / "templates/classes/teach/class_form.html").read_text()
        assert "pl-qr-share__title" in admin_tpl
        assert "pl-qr-share__title" in teach_tpl

    def it_ships_the_shared_component():
        shared = (REPO / "templates/components/qr_share_card.html").read_text()
        assert "qrShare()" in shared
        assert "pl-qr-share" in shared
