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
from core.models import EventDelivery, Notification, SiteConfiguration
from membership.models import (
    AlreadySentError,
    AnnouncementDraft,
    AnnouncementReach,
    GuildAnnouncement,
    resolve_channel_webhook,
)
from tests.membership.factories import (
    AnnouncementDraftFactory,
    GuildFactory,
    GuildMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
)

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

    def describe_states():
        """Drafts and Sent split every row by state, whoever wrote it (drafts are shared)."""

        def _one_of_each():
            return {
                "draft": AnnouncementDraftFactory(),
                "given_up": AnnouncementDraftFactory(given_up=True),
                "queued": AnnouncementDraftFactory(queued=True),
                "sent": AnnouncementDraftFactory(sent=True),
            }

        def it_keeps_unsent_unqueued_rows_from_any_author_as_resumable():
            rows = _one_of_each()
            assert set(AnnouncementDraft.objects.resumable()) == {rows["draft"], rows["given_up"]}

        def it_counts_sent_and_queued_rows_as_sent_or_sending():
            rows = _one_of_each()
            assert set(AnnouncementDraft.objects.sent_or_sending()) == {rows["queued"], rows["sent"]}

        def it_names_each_state_from_the_fields():
            rows = _one_of_each()
            assert {name: row.state for name, row in rows.items()} == {
                "draft": AnnouncementDraft.DraftState.DRAFT,
                "given_up": AnnouncementDraft.DraftState.COULD_NOT_SEND,
                "queued": AnnouncementDraft.DraftState.SENDING,
                "sent": AnnouncementDraft.DraftState.SENT,
            }
            assert [row.is_resumable for row in rows.values()] == [True, True, False, False]

    def describe_audience_value_and_label():
        def it_names_a_site_row():
            row = AnnouncementDraftFactory()
            assert (row.audience_value, row.audience_label) == ("site", "Everyone (site-wide)")

        def it_names_a_guild_row():
            guild = GuildFactory(name="Woodshop Guild")
            row = AnnouncementDraftFactory(audience=_GUILD, guild=guild)
            assert (row.audience_value, row.audience_label) == (f"guild:{guild.pk}", "Woodshop Guild")

        def it_names_a_class_row():
            offering = ClassOfferingFactory(title="Intro to Blacksmithing")
            row = AnnouncementDraftFactory(audience=_CLASS, class_offering=offering)
            assert (row.audience_value, row.audience_label) == (f"class:{offering.pk}", "Intro to Blacksmithing")

        def it_keys_the_ledger_on_the_audiences_event():
            guild = GuildFactory()
            offering = ClassOfferingFactory()
            assert [
                AnnouncementDraftFactory().event_key,
                AnnouncementDraftFactory(audience=_GUILD, guild=guild).event_key,
                AnnouncementDraftFactory(audience=_CLASS, class_offering=offering).event_key,
            ] == ["site_announcement", "guild_announcement", "class_announcement"]

    def describe_channel_labels():
        def it_lists_email_push_and_discord_when_all_are_on():
            row = AnnouncementDraftFactory(discord_channel=_CHANNEL.GENERAL)
            assert row.channel_labels == ["Email", "Push", "Discord"]

        def it_leaves_discord_out_when_it_is_off_or_has_no_channel():
            switched_off = AnnouncementDraftFactory(discord_enabled=False, discord_channel=_CHANNEL.GENERAL)
            no_channel = AnnouncementDraftFactory(discord_channel=_CHANNEL.NONE)
            assert switched_off.channel_labels == ["Email", "Push"]
            assert no_channel.channel_labels == ["Email", "Push"]

        def it_never_lists_discord_for_a_class():
            row = AnnouncementDraftFactory(
                audience=_CLASS, class_offering=ClassOfferingFactory(), discord_channel=_CHANNEL.GENERAL
            )
            assert row.channel_labels == ["Email", "Push"]
            assert row.discord_on is False

        def it_reads_app_only_when_every_channel_is_off():
            row = AnnouncementDraftFactory(send_email=False, push_enabled=False, discord_enabled=False)
            assert row.channel_labels == ["App only"]

        def it_lists_each_channel_on_its_own():
            assert AnnouncementDraftFactory(push_enabled=False, discord_enabled=False).channel_labels == ["Email"]
            assert AnnouncementDraftFactory(send_email=False, discord_enabled=False).channel_labels == ["Push"]

    def describe_author_label():
        def it_names_the_author_by_full_name_else_username():
            named = User.objects.create_user(username="ana", first_name="Ana", last_name="Ruiz")
            unnamed = User.objects.create_user(username="jo")
            assert AnnouncementDraftFactory(author=named).author_label == "Ana Ruiz"
            assert AnnouncementDraftFactory(author=unnamed).author_label == "jo"

        def it_reads_automatic_for_a_blank_author_on_a_draft_and_unknown_once_it_went_out():
            assert AnnouncementDraftFactory(author=None).author_label == "Automatic"
            assert AnnouncementDraftFactory(author=None, given_up=True).author_label == "Automatic"
            assert AnnouncementDraftFactory(author=None, sent=True).author_label == "Unknown"
            assert AnnouncementDraftFactory(author=None, queued=True).author_label == "Unknown"

        def it_keeps_a_sent_row_when_its_author_is_deleted():
            author = _author("gone")
            row = AnnouncementDraftFactory(author=author, sent=True)
            author.delete()
            row.refresh_from_db()
            assert row.author is None
            assert row.author_label == "Unknown"
            assert row._sender_line() == ""

    def describe_message_excerpt():
        def it_flattens_the_message_and_cuts_it_to_ninety_characters():
            row = AnnouncementDraftFactory(body="<p>" + "abcde " * 30 + "</p>")
            assert row.message_excerpt == ("abcde " * 15).strip()[:89] + "…"
            assert len(row.message_excerpt) == 90

        def it_keeps_a_short_message_whole_and_reads_blank_with_none():
            assert AnnouncementDraftFactory(body="<p>Bring <b>gloves</b>.</p>").message_excerpt == "Bring gloves."
            assert AnnouncementDraftFactory(body="").message_excerpt == ""

    def describe_discord_and_mention_labels():
        def it_names_the_guild_channel_by_the_guild():
            guild = GuildFactory(name="Ceramics Guild")
            row = AnnouncementDraftFactory(audience=_GUILD, guild=guild, discord_channel=_CHANNEL.GUILD)
            assert row.discord_target_label == "the Ceramics Guild channel"

        def it_names_a_shared_channel_by_its_label():
            assert AnnouncementDraftFactory(discord_channel=_CHANNEL.GENERAL).discord_target_label == "#general-chat"

        def it_names_the_ping_or_nothing():
            guild = GuildFactory(name="Ceramics Guild")
            assert AnnouncementDraftFactory(mention=AnnouncementDraft.Mention.NONE).mention_label == ""
            assert AnnouncementDraftFactory(mention=AnnouncementDraft.Mention.EVERYONE).mention_label == "@everyone"
            role = AnnouncementDraftFactory(audience=_GUILD, guild=guild, mention=AnnouncementDraft.Mention.ROLE)
            assert role.mention_label == "@Ceramics Guild"

        def it_names_a_role_ping_without_a_guild_by_its_choice_label():
            row = AnnouncementDraftFactory(mention=AnnouncementDraft.Mention.ROLE)
            assert row.mention_label == "@[Guild role]"

        def it_drops_a_trailing_full_stop_from_the_failure_reason():
            assert AnnouncementDraftFactory(send_error="Provider down.").failure_reason == "Provider down"
            assert AnnouncementDraftFactory(send_error="Provider down").failure_reason == "Provider down"

    def describe_reach():
        def _delivered(row, target_ref, channel, *, status=EventDelivery.Status.SENT, period=None, event_key=None):
            return EventDelivery.objects.create(
                event_key=event_key or row.event_key,
                target_ref=target_ref,
                channel=channel,
                period=row.delivery_period if period is None else period,
                status=status,
            )

        def it_counts_people_and_each_channel_from_the_ledger():
            row = AnnouncementDraftFactory(sent=True, delivery_period="announce:77")
            _delivered(row, "user:1", "in_app")
            _delivered(row, "user:1", "email")
            _delivered(row, "user:1", "push")
            _delivered(row, "user:2", "in_app")
            _delivered(row, "email:guest@x.com", "email")
            _delivered(row, "broadcast", "discord")
            _delivered(row, "user:3", "email", status=EventDelivery.Status.PENDING)
            _delivered(row, "user:4", "email", period="announce:78")
            _delivered(row, "user:5", "email", event_key="guild_announcement")
            assert row.reach() == AnnouncementReach(people=3, in_app=2, email=2, push=1, discord_posted=True)

        def it_says_discord_was_not_posted_without_a_discord_row():
            row = AnnouncementDraftFactory(sent=True, delivery_period="announce:79")
            _delivered(row, "user:1", "in_app")
            assert row.reach() == AnnouncementReach(people=1, in_app=1, email=0, push=0, discord_posted=False)

        def it_has_no_reach_without_a_recorded_period():
            assert AnnouncementDraftFactory(sent=True).reach() is None

        def it_reads_a_whole_page_in_one_query(django_assert_num_queries):
            first = AnnouncementDraftFactory(sent=True, delivery_period="announce:80")
            second = AnnouncementDraftFactory(sent=True, delivery_period="announce:81")
            silent = AnnouncementDraftFactory(sent=True, delivery_period="announce:82")
            unrecorded = AnnouncementDraftFactory(sent=True)
            for target in ("user:1", "user:2", "email:a@x.com"):
                _delivered(first, target, "email")
            _delivered(first, "broadcast", "discord")
            _delivered(second, "user:1", "in_app")
            _delivered(second, "user:1", "push")
            with django_assert_num_queries(1):
                reach = AnnouncementDraft.objects.reach_for([first, second, silent, unrecorded])
            assert reach == {first.pk: 3, second.pk: 1, silent.pk: 0}

        def it_runs_no_query_for_a_page_with_nothing_recorded(django_assert_num_queries):
            rows = [AnnouncementDraftFactory(sent=True), AnnouncementDraftFactory()]
            with django_assert_num_queries(0):
                assert AnnouncementDraft.objects.reach_for(rows) == {}

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


def describe_delivery_period_and_sender():
    """``send()`` stamps the ledger period it claimed, so the Announcements page can read the reach back."""

    def it_stamps_a_plain_site_send_with_its_own_pk():
        _activated_member(username="reader")
        draft = AnnouncementDraftFactory(author=_author())
        draft.send()
        draft.refresh_from_db()
        assert draft.delivery_period == f"announce:{draft.pk}"
        assert EventDelivery.objects.filter(event_key="site_announcement", period=draft.delivery_period).exists()

    def it_stamps_a_guild_send_with_its_guild_posts_period():
        guild = GuildFactory()
        _activated_member(guild=guild, username="gm")
        draft = AnnouncementDraftFactory(author=_author(), audience=_GUILD, guild=guild, discord_channel=_CHANNEL.NONE)
        draft.send()
        draft.refresh_from_db()
        post = GuildAnnouncement.objects.get()
        assert draft.delivery_period == f"announcement:{post.pk}"
        assert post.delivery_period == draft.delivery_period
        assert EventDelivery.objects.filter(event_key="guild_announcement", period=draft.delivery_period).exists()

    def it_stamps_a_class_send_with_the_exact_period_its_emit_used():
        offering = ClassOfferingFactory()
        _confirmed_registrant(offering, username="cr")
        draft = AnnouncementDraftFactory(author=_author(), audience=_CLASS, class_offering=offering)
        draft.send()
        draft.refresh_from_db()
        assert draft.delivery_period.startswith(f"announce:{draft.pk}:")
        assert EventDelivery.objects.filter(event_key="class_announcement", period=draft.delivery_period).exists()

    def it_refuses_to_send_without_a_sender(mailoutbox):
        _activated_member(username="reader")
        draft = AnnouncementDraftFactory(author=None)
        with pytest.raises(ValueError, match="An announcement needs a sender before it goes out."):
            draft.send()
        draft.refresh_from_db()
        assert (draft.sent_at, draft.delivery_period) == (None, "")
        assert mailoutbox == []
        assert not EventDelivery.objects.exists()

    def it_renders_the_email_with_no_from_line_for_a_blank_author():
        draft = AnnouncementDraftFactory(author=None, show_sender=True)
        assert "From " not in draft.build_email_message("https://x/").body
