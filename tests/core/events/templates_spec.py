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


def describe_wrap_email_html():
    def it_drops_the_fragment_into_the_branded_shell():
        wrapped = templates_module.wrap_email_html("<p>FRAGMENT-MARKER</p>")
        # The passed fragment is present...
        assert "FRAGMENT-MARKER" in wrapped
        # ...inside the branded shell (wordmark + navy card + footer).
        assert "Past Lives" in wrapped
        assert "#092E4C" in wrapped
        assert "Do It Together" in wrapped

    def it_returns_a_plain_str_not_a_safestring():
        # The admin preview iframe relies on this being attribute-escapable plain str.
        wrapped = templates_module.wrap_email_html("<p>x</p>")
        from django.utils.safestring import SafeString

        assert not isinstance(wrapped, SafeString)


def describe_rendered_message_email_wrapping():
    def it_wraps_the_html_body_for_the_email_channel():
        message = templates_module.rendered_message(
            "registration_confirmed",
            Channel.EMAIL,
            {
                "member_name": "Jo",
                "class_title": "Casting",
                "class_starts_at": "Sat",
                "class_url": "/c/1/",
            },
        )
        assert message.html_body is not None
        # Shell-only markers prove the wrap (use the footer / card color, NOT the
        # "Past Lives" wordmark, which also appears in the copy body).
        assert "Do It Together" in message.html_body
        assert "#092E4C" in message.html_body
        # The rendered fragment content is still present inside the shell.
        assert "Casting" in message.html_body

    def it_also_wraps_the_scheduled_email_channel():
        message = templates_module.rendered_message(
            "registration_confirmed",
            Channel.SCHEDULED_EMAIL,
            {"member_name": "Jo", "class_title": "Casting", "class_starts_at": "Sat", "class_url": "/c/1/"},
        )
        assert message.html_body is not None
        assert "Do It Together" in message.html_body
        assert "Casting" in message.html_body

    def it_does_not_wrap_a_non_email_channel_even_when_it_has_an_html_body():
        # Discord falls back to the email copy, so it carries an HTML body — but it
        # must stay the bare fragment, never the full email document.
        message = templates_module.rendered_message(
            "registration_confirmed",
            Channel.DISCORD,
            {"member_name": "Jo", "class_title": "Casting", "class_starts_at": "Sat", "class_url": "/c/1/"},
        )
        assert message.html_body is not None
        assert "Do It Together" not in message.html_body
        assert "#092E4C" not in message.html_body
        assert "Casting" in message.html_body

    def it_leaves_in_app_body_text_untouched_and_unwrapped():
        message = templates_module.rendered_message(
            "registration_confirmed",
            Channel.IN_APP,
            {"member_name": "Jo", "class_title": "Casting", "class_starts_at": "Sat", "class_url": "/c/1/"},
        )
        # In-app copy carries no HTML body, so there is nothing to (and we never) wrap.
        assert message.html_body is None
        assert "Casting" in message.body
        assert "Do It Together" not in message.body

    def it_leaves_an_empty_html_body_as_none_without_rendering_the_shell():
        # An email row with a blank HTML body must NOT render the shell around nothing.
        NotificationTemplate.objects.create(
            event_key="registration_confirmed",
            channel="email",
            subject="s",
            body_text="t",
            body_html="",
            is_overridden=True,
        )
        message = templates_module.rendered_message(
            "registration_confirmed",
            Channel.EMAIL,
            {"member_name": "Jo", "class_title": "C", "class_starts_at": "S", "class_url": "/c/"},
        )
        assert message.html_body is None

    def it_keeps_merge_values_autoescaped_after_wrapping():
        message = templates_module.rendered_message(
            "registration_confirmed",
            Channel.EMAIL,
            {
                "member_name": "<script>alert(1)</script>",
                "class_title": "Tom & Jerry",
                "class_starts_at": "Sat",
                "class_url": "/c/1/",
            },
        )
        assert message.html_body is not None
        # The merge VALUES are escaped...
        assert "<script>alert(1)</script>" not in message.html_body
        assert "&lt;script&gt;" in message.html_body
        assert "Tom &amp; Jerry" in message.html_body
        # ...while the trusted literal markup and the branded card survive.
        assert "<strong>" in message.html_body
        assert "#092E4C" in message.html_body


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
