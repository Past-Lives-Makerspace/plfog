"""Members saved on a guild's mailing list from an announcement (#729).

Covers the fat-model half: ``Guild.remember_announcement_recipients`` (who is saved on send, and who
never is), ``Guild.mailing_list_users`` / ``announcement_member_ids`` (saved members reached as
members), ``mailing_list_emails_deduped`` leaving linked rows out of the email-only extras, and the
send paths: a saved member gets the bell and one email, a guild send saves its additions, and a
class send saves nothing.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from core.models import Notification
from membership.models import AnnouncementDraft, GuildAnnouncement, GuildMailingListEmail, Member
from tests.membership.factories import (
    AnnouncementDraftFactory,
    GuildFactory,
    GuildMailingListEmailFactory,
    GuildMembershipFactory,
    MemberFactory,
)

pytestmark = pytest.mark.django_db

_seq = {"n": 0}


def _account(email: str, *, name: str = "", active: bool = True) -> Member:
    """A member with a login account at ``email`` (and the given legal name)."""
    _seq["n"] += 1
    member = MemberFactory(full_legal_name=name or f"Person {_seq['n']}")
    with mute_signals(post_save):
        user = User.objects.create_user(
            username=f"mlm_{_seq['n']}", email=email, last_login=timezone.now(), is_active=active
        )
    member.user = user
    member.save(update_fields=["user"])
    return member


def _roster_member(guild, email: str) -> Member:
    member = _account(email)
    GuildMembershipFactory(guild=guild, member=member)
    return member


def _emails(mailoutbox) -> list[str]:
    return [address for message in mailoutbox for address in message.to]


def _draft(guild, selection: dict) -> AnnouncementDraft:
    author = User.objects.create_user(username=f"author_{guild.pk}", email=f"author{guild.pk}@x.com")
    return AnnouncementDraftFactory(
        author=author,
        audience=AnnouncementDraft.Audience.GUILD,
        guild=guild,
        body="<p>Hello.</p>",
        send_email=True,
        discord_channel=GuildAnnouncement.DiscordChannel.NONE,
        recipient_selection=selection,
    )


def describe_remember_announcement_recipients():
    def it_saves_an_added_member_as_a_linked_row():
        guild = GuildFactory()
        added = _account("added@example.com")
        assert guild.remember_announcement_recipients(user_ids=[added.user_id], emails=[]) == 1
        row = guild.mailing_list_emails.get()
        assert row.user_id == added.user_id
        assert row.email == "added@example.com"

    def it_never_saves_someone_on_the_roster():
        guild = GuildFactory()
        roster = _roster_member(guild, "roster@example.com")
        saved = guild.remember_announcement_recipients(user_ids=[roster.user_id], emails=["ROSTER@example.com"])
        assert saved == 0
        assert not guild.mailing_list_emails.exists()

    def it_never_saves_a_member_or_an_address_twice():
        guild = GuildFactory()
        added = _account("added@example.com")
        GuildMailingListEmailFactory(guild=guild, email="booster@example.com")
        guild.remember_announcement_recipients(user_ids=[added.user_id], emails=["booster@example.com"])
        assert guild.remember_announcement_recipients(user_ids=[added.user_id], emails=["Booster@example.com"]) == 0
        assert guild.mailing_list_emails.count() == 2

    def it_links_a_plain_row_that_holds_the_members_address():
        guild = GuildFactory()
        added = _account("added@example.com")
        plain = GuildMailingListEmailFactory(guild=guild, email="added@example.com")
        assert guild.remember_announcement_recipients(user_ids=[added.user_id], emails=[]) == 1
        plain.refresh_from_db()
        assert plain.user_id == added.user_id
        assert guild.mailing_list_emails.count() == 1

    def it_saves_plain_addresses_lower_cased_after_the_last_row():
        guild = GuildFactory()
        GuildMailingListEmailFactory(guild=guild, email="first@example.com", sort_order=4)
        assert guild.remember_announcement_recipients(user_ids=[], emails=[" New@Example.com ", ""]) == 1
        row = guild.mailing_list_emails.get(email="new@example.com")
        assert row.user_id is None
        assert row.sort_order == 5

    def it_skips_turned_off_and_unknown_accounts():
        guild = GuildFactory()
        off = _account("off@example.com", active=False)
        assert guild.remember_announcement_recipients(user_ids=[off.user_id, 999999], emails=[]) == 0
        assert not guild.mailing_list_emails.exists()

    def it_never_saves_a_member_with_no_address():
        guild = GuildFactory()
        blank = _account("")
        assert guild.remember_announcement_recipients(user_ids=[blank.user_id], emails=[]) == 0
        assert not guild.mailing_list_emails.exists()

    def it_folds_a_plain_row_holding_a_members_other_login_address_into_their_row():
        from allauth.account.models import EmailAddress

        guild = GuildFactory()
        member = _account("main@example.com")
        EmailAddress.objects.create(user=member.user, email="Alias@example.com", verified=True, primary=False)
        Member.objects.filter(pk=member.pk).update(notification_email="notify@example.com")
        GuildMailingListEmailFactory(guild=guild, email="alias@example.com", label="Old alias")
        GuildMailingListEmailFactory(guild=guild, email="notify@example.com")
        GuildMailingListEmailFactory(guild=guild, email="booster@example.com")
        assert guild.remember_announcement_recipients(user_ids=[member.user_id], emails=[]) == 1
        linked = guild.mailing_list_emails.get(user=member.user)
        assert (linked.email, linked.label) == ("alias@example.com", "Old alias")
        assert sorted(guild.mailing_list_emails.values_list("email", flat=True)) == [
            "alias@example.com",
            "booster@example.com",
        ]

    def it_skips_a_member_whose_address_is_another_saved_members_row():
        guild = GuildFactory()
        first = _account("shared@example.com")
        second = _account("shared@example.com")
        GuildMailingListEmailFactory(guild=guild, email="shared@example.com", user=first.user)
        assert guild.remember_announcement_recipients(user_ids=[second.user_id], emails=[]) == 0
        assert list(guild.mailing_list_emails.values_list("user_id", flat=True)) == [first.user_id]

    def it_never_links_a_member_who_is_not_active_to_a_leads_plain_row():
        guild = GuildFactory()
        former = _account("former@example.com")
        Member.objects.filter(pk=former.pk).update(status=Member.Status.FORMER)
        plain = GuildMailingListEmailFactory(guild=guild, email="former@example.com")
        assert guild.remember_announcement_recipients(user_ids=[former.user_id], emails=[]) == 0
        plain.refresh_from_db()
        assert plain.user_id is None
        assert guild.mailing_list_emails_deduped(set()) == ["former@example.com"]

    def it_ignores_an_unverified_alias_that_is_someone_elses_row():
        from allauth.account.models import EmailAddress

        guild = GuildFactory()
        member = _account("main@example.com")
        EmailAddress.objects.create(user=member.user, email="theirs@example.com", verified=False, primary=False)
        theirs = GuildMailingListEmailFactory(guild=guild, email="theirs@example.com", label="Partner org")
        assert guild.remember_announcement_recipients(user_ids=[member.user_id], emails=[]) == 1
        theirs.refresh_from_db()
        assert (theirs.user_id, theirs.label) == (None, "Partner org")
        assert guild.mailing_list_emails.get(user=member.user).email == "main@example.com"

    def it_keeps_the_leads_label_from_a_folded_row():
        guild = GuildFactory()
        member = _account("main@example.com")
        Member.objects.filter(pk=member.pk).update(notification_email="notify@example.com")
        GuildMailingListEmailFactory(guild=guild, email="main@example.com")
        GuildMailingListEmailFactory(guild=guild, email="notify@example.com", label="Book club")
        guild.remember_announcement_recipients(user_ids=[member.user_id], emails=[])
        linked = guild.mailing_list_emails.get()
        assert (linked.email, linked.user_id, linked.label) == ("main@example.com", member.user_id, "Book club")

    def it_locks_the_guild_row_while_it_saves():
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        if not connection.features.has_select_for_update:
            pytest.skip("SQLite has no row locks; CI's Postgres run checks this.")
        guild = GuildFactory()
        added = _account("added@example.com")
        with CaptureQueriesContext(connection) as queries:
            guild.remember_announcement_recipients(user_ids=[added.user_id], emails=[])
        assert any('FROM "membership_guild"' in query["sql"] and "FOR UPDATE" in query["sql"] for query in queries)


def describe_saved_members_on_the_guild():
    def it_lists_active_saved_members_and_leaves_them_out_of_the_email_extras():
        guild = GuildFactory()
        saved = _account("saved@example.com")
        off = _account("off@example.com", active=False)
        GuildMailingListEmailFactory(guild=guild, email="saved@example.com", user=saved.user)
        GuildMailingListEmailFactory(guild=guild, email="off@example.com", user=off.user)
        GuildMailingListEmailFactory(guild=guild, email="booster@example.com")
        assert guild.mailing_list_users() == [saved.user]
        assert guild.mailing_list_emails_deduped(set()) == ["booster@example.com"]

    def it_drops_a_saved_member_who_is_no_longer_active_from_delivery_but_keeps_the_row():
        guild = GuildFactory()
        former = _account("former@example.com")
        Member.objects.filter(pk=former.pk).update(status=Member.Status.FORMER)
        GuildMailingListEmailFactory(guild=guild, email="former@example.com", user=former.user)
        assert guild.mailing_list_users() == []
        assert guild.announcement_member_ids() == set()
        assert guild.mailing_list_emails.filter(user=former.user).exists()

    def it_reaches_the_roster_and_the_saved_members_once_each():
        guild = GuildFactory()
        roster = _roster_member(guild, "roster@example.com")
        saved = _account("saved@example.com")
        GuildMailingListEmailFactory(guild=guild, email="saved@example.com", user=saved.user)
        GuildMailingListEmailFactory(guild=guild, email="roster@example.com", user=roster.user)
        assert guild.announcement_member_ids() == {roster.user_id, saved.user_id}

    def it_labels_a_saved_member_by_name_and_a_plain_row_by_nothing():
        guild = GuildFactory()
        saved = _account("saved@example.com", name="Sam Saved")
        linked = GuildMailingListEmailFactory(guild=guild, email="saved@example.com", user=saved.user)
        plain = GuildMailingListEmailFactory(guild=guild, email="booster@example.com")
        assert linked.member_label == "Sam Saved · saved@example.com"
        assert plain.member_label == ""


def describe_notify_members_with_saved_members():
    def it_bells_and_emails_a_saved_member_once(mailoutbox):
        guild = GuildFactory()
        _roster_member(guild, "roster@example.com")
        saved = _account("saved@example.com")
        GuildMailingListEmailFactory(guild=guild, email="saved@example.com", user=saved.user)
        announcement = GuildAnnouncement.objects.create(guild=guild, title="Hi", body="Body", send_email=True)
        announcement.notify_members()
        assert Notification.objects.filter(user=saved.user, trigger="guild_announcement").exists()
        assert sorted(_emails(mailoutbox)) == ["roster@example.com", "saved@example.com"]


def describe_draft_send_saves_additions():
    def it_saves_added_people_and_reaches_them_as_members(mailoutbox):
        guild = GuildFactory()
        roster = _roster_member(guild, "roster@example.com")
        added = [_account(f"added{i}@example.com") for i in range(3)]
        selection = {
            "users": [roster.user_id] + [member.user_id for member in added],
            "custom": ["typed@example.com"],
        }
        counts = _draft(guild, selection).send()
        assert set(guild.mailing_list_emails.values_list("user_id", flat=True)) == {
            member.user_id for member in added
        } | {None}
        assert guild.mailing_list_emails.filter(email="typed@example.com", user__isnull=True).exists()
        assert not guild.mailing_list_emails.filter(user=roster.user).exists()
        assert all(
            Notification.objects.filter(user=member.user, trigger="guild_announcement").exists() for member in added
        )
        emails = _emails(mailoutbox)
        assert sorted(emails) == sorted(set(emails))  # nobody emailed twice
        assert "typed@example.com" in emails
        assert counts == (4, 4)

    def it_reaches_a_member_with_no_address_without_saving_them():
        guild = GuildFactory()
        blank = _account("")
        _draft(guild, {"users": [blank.user_id], "custom": []}).send()
        assert Notification.objects.filter(user=blank.user, trigger="guild_announcement").exists()
        assert not guild.mailing_list_emails.exists()

    def it_counts_saved_members_when_the_whole_list_is_kept(mailoutbox):
        guild = GuildFactory()
        _roster_member(guild, "roster@example.com")
        saved = _account("saved@example.com")
        GuildMailingListEmailFactory(guild=guild, email="saved@example.com", user=saved.user)
        draft = _draft(guild, {})
        assert draft.recipient_count() == 2
        assert draft.send() == (2, 2)
        assert sorted(_emails(mailoutbox)) == ["roster@example.com", "saved@example.com"]


def describe_draft_send_order_and_selection():
    def it_emails_no_saved_address_when_every_one_was_unchecked(mailoutbox):
        guild = GuildFactory()
        roster = _roster_member(guild, "roster@example.com")
        GuildMailingListEmailFactory(guild=guild, email="booster@example.com")
        _draft(guild, {"users": [roster.user_id], "custom": []}).send()
        assert _emails(mailoutbox) == ["roster@example.com"]

    def it_still_emails_every_saved_address_for_an_old_draft_without_the_key(mailoutbox):
        guild = GuildFactory()
        roster = _roster_member(guild, "roster@example.com")
        GuildMailingListEmailFactory(guild=guild, email="booster@example.com")
        _draft(guild, {"users": [roster.user_id]}).send()
        assert sorted(_emails(mailoutbox)) == ["booster@example.com", "roster@example.com"]

    def it_posts_nothing_when_saving_the_list_fails(monkeypatch):
        from django.db import IntegrityError

        from membership.models import Guild

        guild = GuildFactory()
        added = _account("added@example.com")
        draft = _draft(guild, {"users": [added.user_id], "custom": []})

        def _fail(self, **_kwargs):
            raise IntegrityError("duplicate")

        monkeypatch.setattr(Guild, "remember_announcement_recipients", _fail)
        with pytest.raises(IntegrityError):
            draft.send()
        assert not GuildAnnouncement.objects.filter(guild=guild).exists()
        draft.refresh_from_db()
        assert draft.sent_at is None


def describe_class_send_saves_nothing():
    def it_reaches_added_people_and_saves_no_list_row(mailoutbox):
        from classes.factories import ClassOfferingFactory

        offering = ClassOfferingFactory()
        added = _account("added@example.com")
        author = User.objects.create_user(username="class_author", email="class_author@x.com")
        draft = AnnouncementDraftFactory(
            author=author,
            audience=AnnouncementDraft.Audience.CLASS,
            class_offering=offering,
            body="<p>Hello.</p>",
            send_email=True,
            recipient_selection={"users": [added.user_id], "custom": ["typed@example.com"]},
        )
        draft.send()
        assert sorted(_emails(mailoutbox)) == ["added@example.com", "typed@example.com"]
        assert not GuildMailingListEmail.objects.exists()
