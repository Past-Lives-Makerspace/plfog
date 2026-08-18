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
            result = AnnouncementDraft.save_from_form(_cleaned(send_email=False), author, instance=existing)
            assert result.pk == existing.pk
            # No member subject: the title is the auto category (site → "Makerspace Announcement").
            assert result.title == "Makerspace Announcement"
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

    def describe_announcement_category():
        def it_uses_the_guild_name_for_a_guild():
            guild = GuildFactory(name="Ceramics Guild")
            assert (
                AnnouncementDraft(audience=_GUILD, guild=guild).announcement_category == "Ceramics Guild Announcement"
            )

        def it_uses_class_announcement_for_a_class():
            assert AnnouncementDraft(audience=_CLASS).announcement_category == "Class Announcement"

        def it_uses_makerspace_for_a_site_send():
            assert AnnouncementDraft(audience=_SITE).announcement_category == "Makerspace Announcement"

        def it_leads_with_urgent_when_marked():
            guild = GuildFactory(name="Glass Guild")
            draft = AnnouncementDraft(audience=_GUILD, guild=guild, mark_as_urgent=True)
            assert draft.announcement_category == "Urgent: Glass Guild Announcement"

    def describe_email_context():
        def it_shows_the_from_line_when_show_sender_is_on():
            author = _author()
            draft = AnnouncementDraft(author=author, audience=_SITE, body="<p>hi</p>", show_sender=True)
            draft.title = draft.announcement_category
            assert "From " in draft.build_email_message("/").html_body

        def it_hides_the_from_line_when_show_sender_is_off():
            author = _author()
            draft = AnnouncementDraft(author=author, audience=_SITE, body="<p>hi</p>", show_sender=False)
            assert draft._sender_line() == ""

        def it_shows_the_class_title_as_the_email_subline():
            author = _author()
            offering = ClassOfferingFactory(title="Intro to Glass")
            draft = AnnouncementDraft(
                author=author, audience=_CLASS, class_offering=offering, body="<p>hi</p>", show_sender=False
            )
            draft.title = draft.announcement_category
            assert "Intro to Glass" in draft.build_email_message("/").html_body

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

        def it_counts_every_confirmed_registrant_including_guests_for_a_class_audience():
            offering = ClassOfferingFactory()
            _confirmed_registrant(offering, username="c1")
            # A pending registrant is NOT on the roster; a confirmed guest (no linked account) IS —
            # they still get the email even without an app account.
            pending = _activated_member(username="c2")
            RegistrationFactory(class_offering=offering, member=pending, status=Registration.Status.PENDING)
            RegistrationFactory(class_offering=offering, member=None, status=Registration.Status.CONFIRMED)
            assert AnnouncementDraft(audience=_CLASS, class_offering=offering).recipient_count() == 2

        def it_counts_the_waitlist_too_when_include_waitlist_is_set():
            offering = ClassOfferingFactory()
            _confirmed_registrant(offering, username="c1")
            RegistrationFactory(class_offering=offering, member=None, status=Registration.Status.WAITLISTED)
            draft = AnnouncementDraft(audience=_CLASS, class_offering=offering)
            assert draft.recipient_count() == 1  # confirmed only by default
            draft.include_waitlist = True
            assert draft.recipient_count() == 2  # + the waitlisted guest

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

            def it_threads_the_guild_role_mention_into_the_guild_discord_post():
                author = _author()
                guild = GuildFactory(
                    discord_webhook_url="https://d/guild",
                    discord_post_enabled=True,
                    discord_role_ids=["111", "222"],
                )
                draft = AnnouncementDraft.objects.create(
                    author=author,
                    audience=_GUILD,
                    guild=guild,
                    title="T",
                    body="<p>x</p>",
                    discord_channel=_CHANNEL.GUILD,
                    mention=AnnouncementDraft.Mention.ROLE,
                )
                with patch("core.events.discord.post_embed", return_value=True) as mock_post:
                    draft.send()
                # Every configured role id rides as <@&id>; build_embed_payload turns that into the
                # allowed_mentions roles gate. Glass has two roles — both ping.
                assert mock_post.call_args.args[1].discord_mention == "<@&111> <@&222>"

            def it_sends_an_inert_role_ping_when_the_guild_has_no_roles():
                author = _author()
                guild = GuildFactory(
                    discord_webhook_url="https://d/guild", discord_post_enabled=True, discord_role_ids=[]
                )
                draft = AnnouncementDraft.objects.create(
                    author=author,
                    audience=_GUILD,
                    guild=guild,
                    title="T",
                    body="<p>x</p>",
                    discord_channel=_CHANNEL.GUILD,
                    mention=AnnouncementDraft.Mention.ROLE,
                )
                with patch("core.events.discord.post_embed", return_value=True) as mock_post:
                    draft.send()
                assert mock_post.call_args.args[1].discord_mention == ""

            def it_has_an_inert_role_literal_with_no_guild():
                draft = AnnouncementDraft(audience=_SITE, mention=AnnouncementDraft.Mention.ROLE)
                assert draft._mention_literal() == ""

            def it_does_not_post_to_discord_when_discord_is_disabled():
                author = _author()
                guild = GuildFactory(discord_webhook_url="https://d/guild", discord_post_enabled=True)
                draft = AnnouncementDraft.objects.create(
                    author=author,
                    audience=_GUILD,
                    guild=guild,
                    title="T",
                    body="<p>x</p>",
                    discord_channel=_CHANNEL.GUILD,
                    discord_enabled=False,
                )
                with patch("core.events.discord.post_embed", return_value=True) as mock_post:
                    draft.send()
                assert mock_post.call_count == 0

            def it_passes_the_push_toggle_through_to_notify_members():
                author = _author()
                guild = GuildFactory()
                draft = AnnouncementDraft.objects.create(
                    author=author,
                    audience=_GUILD,
                    guild=guild,
                    title="T",
                    body="<p>x</p>",
                    discord_channel=_CHANNEL.NONE,
                    push_enabled=False,
                )
                with patch.object(GuildAnnouncement, "notify_members") as mock_notify:
                    draft.send()
                assert mock_notify.call_args.kwargs["suppress_push"] is True

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

            def it_emails_a_guest_registrant_who_has_no_account(mailoutbox):
                author = _author()
                offering = ClassOfferingFactory()
                RegistrationFactory(
                    class_offering=offering,
                    member=None,
                    email="guest@example.com",
                    status=Registration.Status.CONFIRMED,
                )
                emailed, total = AnnouncementDraft.objects.create(
                    author=author, audience=_CLASS, class_offering=offering, title="T", body="<p>hi</p>"
                ).send()
                assert (emailed, total) == (1, 1)  # the guest is a reachable recipient
                assert {addr for message in mailoutbox for addr in message.to} == {"guest@example.com"}

            def it_leaves_the_waitlist_out_by_default(mailoutbox):
                author = _author()
                offering = ClassOfferingFactory()
                _confirmed_registrant(offering, username="cw1")
                RegistrationFactory(
                    class_offering=offering,
                    member=None,
                    email="wait@example.com",
                    status=Registration.Status.WAITLISTED,
                )
                AnnouncementDraft.objects.create(
                    author=author, audience=_CLASS, class_offering=offering, title="T", body="<p>hi</p>"
                ).send()
                assert "wait@example.com" not in {addr for message in mailoutbox for addr in message.to}

            def it_includes_the_waitlist_when_opted_in(mailoutbox):
                author = _author()
                offering = ClassOfferingFactory()
                _confirmed_registrant(offering, username="cw2")
                RegistrationFactory(
                    class_offering=offering,
                    member=None,
                    email="wait@example.com",
                    status=Registration.Status.WAITLISTED,
                )
                emailed, total = AnnouncementDraft.objects.create(
                    author=author,
                    audience=_CLASS,
                    class_offering=offering,
                    title="T",
                    body="<p>hi</p>",
                    include_waitlist=True,
                ).send()
                assert (emailed, total) == (2, 2)
                assert "wait@example.com" in {addr for message in mailoutbox for addr in message.to}

            def it_honors_an_explicit_recipient_subset(mailoutbox):
                author = _author()
                offering = ClassOfferingFactory()
                _confirmed_registrant(offering, username="cs1")
                RegistrationFactory(
                    class_offering=offering,
                    member=None,
                    email="picked@example.com",
                    status=Registration.Status.CONFIRMED,
                )
                RegistrationFactory(
                    class_offering=offering,
                    member=None,
                    email="dropped@example.com",
                    status=Registration.Status.CONFIRMED,
                )
                AnnouncementDraft.objects.create(
                    author=author,
                    audience=_CLASS,
                    class_offering=offering,
                    title="T",
                    body="<p>hi</p>",
                    recipient_selection={"users": [], "custom": ["picked@example.com"]},
                ).send()
                recipients = {addr for message in mailoutbox for addr in message.to}
                assert "picked@example.com" in recipients
                assert "dropped@example.com" not in recipients

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

            def it_pushes_the_message_body_when_no_short_text_is_set():
                from core.events.channels import Channel

                author = _author()
                draft = AnnouncementDraft.objects.create(author=author, audience=_SITE, title="Hi", body="<p>x</p>")
                with patch("core.events.emit.emit", return_value=types.SimpleNamespace(recipient_count=0)) as mock_emit:
                    draft.send()
                # Push always leads with the category title; with no custom short text, the body is the tray line.
                push = mock_emit.call_args.kwargs["messages"][Channel.PUSH]
                assert push.title == "Hi"
                assert push.body == "x"

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
