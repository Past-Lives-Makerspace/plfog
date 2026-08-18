"""AnnouncementDraft — the compose wizard's saved-or-sent state + the fat ``send()`` transition.

A SITE send is emit-only (ephemeral). A GUILD send materializes a published GuildAnnouncement
(so the post shows on the guild page / edit list / slideshow) and reuses notify_members, passing
the branded email override + the opt-in @mention. Mark-sent (sent_at), never delete-on-send.
"""

from __future__ import annotations

import types
from datetime import date
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from classes.factories import ClassOfferingFactory, RegistrationFactory
from classes.models import Registration
from core.models import Notification, SiteConfiguration
from membership.models import AlreadySentError, AnnouncementDraft, GuildAnnouncement, resolve_channel_webhook
from tests.membership.factories import GuildFactory, GuildMembershipFactory, MemberFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

_SITE = AnnouncementDraft.Audience.SITE
_GUILD = AnnouncementDraft.Audience.GUILD
_CLASS = AnnouncementDraft.Audience.CLASS
_CHANNEL = GuildAnnouncement.DiscordChannel


def _author(username: str = "author") -> User:
    MembershipPlanFactory()
    return User.objects.create_user(username=username, email=f"{username}@x.com", password="pw")


def _activated_member(*, guild=None, username: str = "m"):
    """An ACTIVE member with a linked, signed-in (last_login) user — a real broadcast recipient."""
    member = MemberFactory()
    with mute_signals(post_save):
        user = User.objects.create_user(
            username=username, email=f"{username}@x.com", password="pw", last_login=timezone.now()
        )
    member.user = user
    member.save(update_fields=["user"])
    if guild is not None:
        GuildMembershipFactory(guild=guild, member=member)
    return member


def _confirmed_registrant(offering, username: str):
    """An activated member holding a CONFIRMED registration in ``offering`` — a class recipient."""
    member = _activated_member(username=username)
    RegistrationFactory(class_offering=offering, member=member, status=Registration.Status.CONFIRMED)
    return member


def _cleaned(**overrides):
    data = {
        "audience": "site",
        "guild": None,
        "title": "T",
        "body": "<p>x</p>",
        "send_email": True,
        "discord_channel": "none",
        "mention": "none",
        "expires_at": None,
    }
    data.update(overrides)
    return types.SimpleNamespace(cleaned_data=data)


def describe_AnnouncementDraft():
    def describe_str():
        def it_labels_draft_vs_sent():
            author = _author()
            draft = AnnouncementDraft.objects.create(author=author, title="Hi")
            assert "draft" in str(draft)
            draft.sent_at = timezone.now()
            assert "sent" in str(draft)

    def describe_for_user():
        def it_returns_only_the_authors_unsent_drafts_newest_first():
            author = _author("a")
            other = _author("b")
            older = AnnouncementDraft.objects.create(author=author, title="Older")
            newer = AnnouncementDraft.objects.create(author=author, title="Newer")
            AnnouncementDraft.objects.create(author=author, title="Sent", sent_at=timezone.now())
            AnnouncementDraft.objects.create(author=other, title="Other's")
            assert list(AnnouncementDraft.objects.for_user(author)) == [newer, older]

    def describe_check_constraint():
        def it_rejects_a_guild_audience_without_a_guild():
            author = _author()
            with pytest.raises(IntegrityError):
                AnnouncementDraft.objects.create(author=author, audience=_GUILD, guild=None, title="x")

        def it_rejects_a_class_audience_without_a_class():
            author = _author()
            with pytest.raises(IntegrityError):
                AnnouncementDraft.objects.create(author=author, audience=_CLASS, class_offering=None, title="x")

    def describe_save_from_form():
        def it_upserts_an_existing_instance_without_duplicating():
            author = _author()
            existing = AnnouncementDraft.objects.create(author=author, title="Old")
            result = AnnouncementDraft.save_from_form(
                _cleaned(title="New", send_email=False), author, instance=existing
            )
            assert result.pk == existing.pk
            assert result.title == "New"
            assert result.send_email is False
            assert AnnouncementDraft.objects.filter(author=author).count() == 1

        def it_raises_for_a_guild_audience_without_a_guild():
            author = _author()
            with pytest.raises(ValidationError):
                AnnouncementDraft.save_from_form(_cleaned(audience="guild", guild=None), author)

        def it_raises_for_a_class_audience_without_a_class():
            author = _author()
            with pytest.raises(ValidationError):
                AnnouncementDraft.save_from_form(_cleaned(audience="class", class_offering=None), author)

        def it_stores_the_class_offering_for_a_class_audience():
            author = _author()
            offering = ClassOfferingFactory()
            draft = AnnouncementDraft.save_from_form(_cleaned(audience="class", class_offering=offering), author)
            assert draft.audience == _CLASS
            assert draft.class_offering == offering

    def describe_recipient_count():
        def it_counts_all_active_members_for_a_site_audience():
            _activated_member(username="s1")
            _activated_member(username="s2")
            assert AnnouncementDraft(audience=_SITE).recipient_count() == 2

        def it_counts_only_the_guilds_members_for_a_guild_audience():
            guild = GuildFactory()
            _activated_member(guild=guild, username="g1")
            _activated_member(username="g2")  # not in the guild
            assert AnnouncementDraft(audience=_GUILD, guild=guild).recipient_count() == 1

        def it_counts_only_confirmed_linked_registrants_for_a_class_audience():
            offering = ClassOfferingFactory()
            _confirmed_registrant(offering, username="c1")
            # A pending registrant and a guest (no linked member) are NOT on the roster.
            pending = _activated_member(username="c2")
            RegistrationFactory(class_offering=offering, member=pending, status=Registration.Status.PENDING)
            RegistrationFactory(class_offering=offering, member=None, status=Registration.Status.CONFIRMED)
            assert AnnouncementDraft(audience=_CLASS, class_offering=offering).recipient_count() == 1

    def describe_resolve_channel_webhook():
        def it_returns_the_guild_webhook_for_the_guild_channel():
            guild = GuildFactory(discord_webhook_url="https://d/guild")
            assert resolve_channel_webhook(_CHANNEL.GUILD, guild) == "https://d/guild"

        def it_returns_empty_for_the_guild_channel_without_a_guild():
            assert resolve_channel_webhook(_CHANNEL.GUILD, None) == ""

        def it_returns_the_general_leadership_and_officers_webhooks():
            config = SiteConfiguration.load()
            config.discord_general_webhook_url = "https://d/gen"
            config.discord_leadership_webhook_url = "https://d/lead"
            config.discord_officers_webhook_url = "https://d/officers"
            config.save()
            assert resolve_channel_webhook(_CHANNEL.GENERAL) == "https://d/gen"
            assert resolve_channel_webhook(_CHANNEL.LEADERSHIP) == "https://d/lead"
            assert resolve_channel_webhook(_CHANNEL.OFFICERS) == "https://d/officers"

        def it_returns_empty_for_none():
            assert resolve_channel_webhook(_CHANNEL.NONE) == ""

        def it_raises_for_an_unknown_channel():
            with pytest.raises(ValueError, match="Unknown Discord channel"):
                resolve_channel_webhook("bogus")

    def describe_send():
        def it_marks_sent_and_returns_the_recipient_count_for_a_site_send():
            author = _author()
            _activated_member(username="recip")
            draft = AnnouncementDraft.objects.create(author=author, audience=_SITE, title="Hi", body="<p>Hello</p>")
            count = draft.send()
            draft.refresh_from_db()
            assert draft.sent_at is not None
            assert count == (1, 1)  # (emailed, total) — one active member, all emailed

        def it_raises_already_sent_on_a_second_send():
            author = _author()
            draft = AnnouncementDraft.objects.create(author=author, title="Hi", body="<p>x</p>")
            draft.send()
            with pytest.raises(AlreadySentError):
                draft.send()

        def it_raises_when_the_body_sanitizes_empty():
            author = _author()
            draft = AnnouncementDraft.objects.create(author=author, title="Hi", body="<p><br></p>")
            with pytest.raises(ValidationError):
                draft.send()

        def it_raises_for_a_guild_audience_without_a_guild():
            author = _author()
            draft = AnnouncementDraft(author=author, audience=_GUILD, guild=None, title="Hi", body="<p>x</p>")
            with pytest.raises(ValidationError):
                draft.send()

        def it_creates_no_guild_announcement_for_a_site_send():
            author = _author()
            AnnouncementDraft.objects.create(author=author, audience=_SITE, title="Site", body="<p>x</p>").send()
            assert not GuildAnnouncement.objects.exists()

        def it_suppresses_email_but_keeps_the_bell_when_send_email_is_off(mailoutbox):
            author = _author()
            member = _activated_member(username="recip")
            draft = AnnouncementDraft.objects.create(
                author=author, audience=_SITE, title="Hi", body="<p>x</p>", send_email=False
            )
            draft.send()
            assert mailoutbox == []
            assert Notification.objects.filter(user=member.user, trigger="site_announcement").exists()

        def it_posts_to_the_chosen_channel_for_a_site_send():
            author = _author()
            config = SiteConfiguration.load()
            config.discord_general_webhook_url = "https://d/gen"
            config.save()
            draft = AnnouncementDraft.objects.create(
                author=author, audience=_SITE, title="T", body="<p>x</p>", discord_channel=_CHANNEL.GENERAL
            )
            with patch("core.events.discord.post_embed", return_value=True) as mock_post:
                draft.send()
            assert "https://d/gen" in [call.args[0] for call in mock_post.call_args_list]

        def it_posts_no_discord_when_the_site_channel_is_none():
            author = _author()
            draft = AnnouncementDraft.objects.create(
                author=author, audience=_SITE, title="T", body="<p>x</p>", discord_channel=_CHANNEL.NONE
            )
            with patch("core.events.discord.post_embed", return_value=True) as mock_post:
                draft.send()
            assert mock_post.call_count == 0

        def describe_guild_materialization():
            def it_creates_a_published_guild_announcement_with_the_flattened_body():
                author = _author()
                guild = GuildFactory()
                _activated_member(guild=guild, username="gm")
                draft = AnnouncementDraft.objects.create(
                    author=author,
                    audience=_GUILD,
                    guild=guild,
                    title="Forge night",
                    body="<p>rich <strong>body</strong></p>",
                    expires_at=date(2030, 1, 1),
                    discord_channel=_CHANNEL.NONE,
                )
                count = draft.send()
                ann = guild.announcements.published().get()
                assert ann.title == "Forge night"
                assert "<" not in ann.body  # plain-text flattening (guild page renders it as text)
                assert "body" in ann.body
                assert ann.expires_at == date(2030, 1, 1)
                assert ann.author == author
                assert count == (1, 1)  # (emailed, total) — one guild member, no custom addresses

            def it_sends_the_branded_email_override_to_guild_members(mailoutbox):
                author = _author()
                guild = GuildFactory()
                _activated_member(guild=guild, username="gm")
                draft = AnnouncementDraft.objects.create(
                    author=author, audience=_GUILD, guild=guild, title="T", body="<p>hello world</p>"
                )
                draft.send()
                assert len(mailoutbox) == 1
                html = mailoutbox[0].alternatives[0][0]
                assert "hello world" in html  # the rich body rode the branded shell

            def it_threads_the_everyone_mention_into_the_guild_discord_post():
                author = _author()
                guild = GuildFactory(discord_webhook_url="https://d/guild", discord_post_enabled=True)
                draft = AnnouncementDraft.objects.create(
                    author=author,
                    audience=_GUILD,
                    guild=guild,
                    title="T",
                    body="<p>x</p>",
                    discord_channel=_CHANNEL.GUILD,
                    mention=AnnouncementDraft.Mention.EVERYONE,
                )
                with patch("core.events.discord.post_embed", return_value=True) as mock_post:
                    draft.send()
                assert mock_post.call_args.args[1].discord_mention == "@everyone"

        def describe_class_send():
            def it_marks_sent_and_notifies_the_confirmed_roster():
                author = _author()
                offering = ClassOfferingFactory()
                member = _confirmed_registrant(offering, username="cr")
                draft = AnnouncementDraft.objects.create(
                    author=author, audience=_CLASS, class_offering=offering, title="Moved", body="<p>Thursday</p>"
                )
                count = draft.send()
                draft.refresh_from_db()
                assert draft.sent_at is not None
                assert count == (1, 1)  # (emailed, total) — one confirmed registrant, all emailed
                assert Notification.objects.filter(user=member.user, trigger="class_announcement").exists()

            def it_creates_no_guild_announcement_for_a_class_send():
                author = _author()
                offering = ClassOfferingFactory()
                _confirmed_registrant(offering, username="cr2")
                AnnouncementDraft.objects.create(
                    author=author, audience=_CLASS, class_offering=offering, title="T", body="<p>x</p>"
                ).send()
                assert not GuildAnnouncement.objects.exists()

            def it_raises_for_a_class_audience_without_a_class():
                author = _author()
                draft = AnnouncementDraft(
                    author=author, audience=_CLASS, class_offering=None, title="Hi", body="<p>x</p>"
                )
                with pytest.raises(ValidationError):
                    draft.send()

            def it_suppresses_email_but_keeps_the_bell_when_send_email_is_off(mailoutbox):
                author = _author()
                offering = ClassOfferingFactory()
                member = _confirmed_registrant(offering, username="cr3")
                AnnouncementDraft.objects.create(
                    author=author,
                    audience=_CLASS,
                    class_offering=offering,
                    title="Hi",
                    body="<p>x</p>",
                    send_email=False,
                ).send()
                assert mailoutbox == []
                assert Notification.objects.filter(user=member.user, trigger="class_announcement").exists()

            def it_emails_the_confirmed_roster_the_branded_class_announcement(mailoutbox):
                author = _author()
                offering = ClassOfferingFactory()
                _confirmed_registrant(offering, username="cr4")
                AnnouncementDraft.objects.create(
                    author=author, audience=_CLASS, class_offering=offering, title="T", body="<p>hello class</p>"
                ).send()
                assert len(mailoutbox) == 1
                html = mailoutbox[0].alternatives[0][0]
                assert "hello class" in html

        def describe_push_override():
            def it_passes_the_custom_push_text_to_a_site_send():
                from core.events.channels import Channel

                author = _author()
                draft = AnnouncementDraft.objects.create(
                    author=author, audience=_SITE, title="Snow", body="<p>x</p>", push_message="Snow day. Closed."
                )
                with patch("core.events.emit.emit", return_value=types.SimpleNamespace(recipient_count=0)) as mock_emit:
                    draft.send()
                messages = mock_emit.call_args.kwargs["messages"]
                assert messages[Channel.PUSH].body == "Snow day. Closed."
                assert messages[Channel.PUSH].trigger_kind == "site_announcement"

            def it_omits_the_push_override_when_no_short_text_is_set():
                from core.events.channels import Channel

                author = _author()
                draft = AnnouncementDraft.objects.create(author=author, audience=_SITE, title="Hi", body="<p>x</p>")
                with patch("core.events.emit.emit", return_value=types.SimpleNamespace(recipient_count=0)) as mock_emit:
                    draft.send()
                assert Channel.PUSH not in mock_emit.call_args.kwargs["messages"]

            def it_passes_the_custom_push_text_through_a_guild_send():
                from core.events.channels import Channel

                author = _author()
                guild = GuildFactory()
                draft = AnnouncementDraft.objects.create(
                    author=author,
                    audience=_GUILD,
                    guild=guild,
                    title="T",
                    body="<p>x</p>",
                    push_message="Forge tonight",
                )
                with patch("core.events.emit.emit", return_value=types.SimpleNamespace(recipient_count=0)) as mock_emit:
                    draft.send()
                messages = mock_emit.call_args.kwargs["messages"]
                assert messages[Channel.PUSH].body == "Forge tonight"
                assert messages[Channel.PUSH].trigger_kind == "guild_announcement"

            def it_passes_the_custom_push_text_to_a_class_send():
                from core.events.channels import Channel

                author = _author()
                offering = ClassOfferingFactory()
                draft = AnnouncementDraft.objects.create(
                    author=author,
                    audience=_CLASS,
                    class_offering=offering,
                    title="Moved",
                    body="<p>x</p>",
                    push_message="Thu 6pm",
                )
                with patch("core.events.emit.emit", return_value=types.SimpleNamespace(recipient_count=0)) as mock_emit:
                    draft.send()
                messages = mock_emit.call_args.kwargs["messages"]
                assert messages[Channel.PUSH].body == "Thu 6pm"
                assert messages[Channel.PUSH].trigger_kind == "class_announcement"
