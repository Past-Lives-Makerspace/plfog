"""BDD specs for send-time copy resolution + emit() copy mode (design §2.3, P3)."""

from __future__ import annotations

import pytest

from core.events import templates as templates_module
from core.events.registry import Channel
from core.models import NotificationPreference, NotificationTemplate, Notification

pytestmark = pytest.mark.django_db


def describe_resolved_copy():
    def it_uses_the_seeded_default_when_no_db_row_exists():
        subject, _text, _html = templates_module.resolved_copy("registration_confirmed", Channel.EMAIL)
        assert "{{ class_title }}" in subject

    def it_prefers_an_admin_edited_db_row_over_the_default():
        NotificationTemplate.objects.create(
            event_key="registration_confirmed",
            channel="email",
            subject="CUSTOM {{ class_title }}",
            body_text="b",
            body_html="",
            is_overridden=True,
        )
        subject, _text, _html = templates_module.resolved_copy("registration_confirmed", Channel.EMAIL)
        assert subject == "CUSTOM {{ class_title }}"


def describe_rendered_message():
    def it_renders_the_resolved_copy_against_a_context():
        message = templates_module.rendered_message(
            "registration_confirmed",
            Channel.EMAIL,
            {
                "member_name": "Jo",
                "class_title": "Casting",
                "class_starts_at": "Sat",
                "class_url": "/c/1/",
            },
            url="/c/1/",
        )
        assert "Casting" in message.title
        assert message.trigger_kind == "registration_confirmed"
        assert message.url == "/c/1/"

    def it_autoescapes_html_values():
        message = templates_module.rendered_message(
            "registration_confirmed",
            Channel.EMAIL,
            {
                "member_name": "<b>x</b>",
                "class_title": "C",
                "class_starts_at": "S",
                "class_url": "/c/",
            },
        )
        assert message.html_body is not None
        assert "<b>x</b>" not in message.html_body
        assert "&lt;b&gt;" in message.html_body


def describe_emit_copy_mode():
    def it_renders_db_copy_when_no_explicit_strings_are_passed(linked_member):
        from core.events.emit import emit

        member = linked_member(email="reg@example.com")
        # Opt the member into email for this event.
        NotificationPreference.objects.create(
            user=member.user, event_key="registration_confirmed", channel="email", enabled=True
        )
        emit(
            "registration_confirmed",
            context={
                "member": member,
                "class_title": "Lost-Wax Casting",
                "member_name": member.user.email,
                "class_starts_at": "Saturday",
                "class_url": "/classes/1/",
            },
        )
        note = Notification.objects.get(user=member.user)
        # In-app copy was rendered from the seeded default (carries the class title).
        assert "Lost-Wax Casting" in note.title

    def it_preserves_the_explicit_string_path_unchanged(linked_member):
        from core.events.emit import emit

        member = linked_member(email="reg2@example.com")
        emit(
            "registration_confirmed",
            context={"member": member},
            title="Explicit title",
            body="Explicit body",
        )
        note = Notification.objects.get(user=member.user)
        assert note.title == "Explicit title"
        assert note.body == "Explicit body"
