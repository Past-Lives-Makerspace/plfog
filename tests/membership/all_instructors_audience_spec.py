"""BDD specs for #806: the All Instructors announcement audience on the model.

Covers who it reaches (``Member.objects.instructors``: approved to teach, listed on a class or
holding an instructor page; active only; signed in or not), the email-only reach of an instructor
with no account, its title, its count, and its send (bell, email, preferences, urgent, Discord).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.utils import timezone

from classes.factories import ClassOfferingFactory
from core.models import EventDelivery, Notification, NotificationPreference
from membership.models import AnnouncementDraft, GuildAnnouncement, Member
from tests.membership.factories import MemberFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

_INSTRUCTORS = AnnouncementDraft.Audience.INSTRUCTORS


def _user(username: str, *, signed_in: bool = True) -> User:
    """A user with an auto-provisioned, active linked Member."""
    MembershipPlanFactory()
    return User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="x",
        last_login=timezone.now() if signed_in else None,
    )


def _oriented(username: str, *, signed_in: bool = True) -> User:
    user = _user(username, signed_in=signed_in)
    Member.objects.filter(pk=user.member.pk).update(instructor_oriented_at=timezone.now())
    return user


def _paged(username: str) -> User:
    user = _user(username)
    Member.objects.filter(pk=user.member.pk).update(instructor_slug=f"{username}-page")
    return user


def _listed(username: str) -> User:
    user = _user(username)
    ClassOfferingFactory(instructor=user.member)
    return user


def _former(username: str) -> User:
    user = _oriented(username)
    Member.objects.filter(pk=user.member.pk).update(status=Member.Status.FORMER)
    return user


def _userless(address: str) -> Member:
    return MemberFactory(_pre_signup_email=address, instructor_oriented_at=timezone.now())


def _reached(channel: str) -> set[str]:
    return set(
        EventDelivery.objects.filter(
            event_key="site_announcement", channel=channel, status=EventDelivery.Status.SENT
        ).values_list("target_ref", flat=True)
    )


def _draft(sender: User, **fields) -> AnnouncementDraft:
    values = {"title": "Instructors Announcement", "body": "<p>Pay day moved.</p>", **fields}
    return AnnouncementDraft.objects.create(author=sender, audience=_INSTRUCTORS, **values)


def describe_Member_objects_instructors():
    def it_takes_the_approved_the_listed_and_the_paged_signed_in_or_not():
        oriented = _oriented("oriented")
        listed = _listed("listed")
        paged = _paged("paged")
        never = _oriented("never", signed_in=False)
        userless = _userless("nologin@example.com")
        assert set(Member.objects.instructors()) == {
            oriented.member,
            listed.member,
            paged.member,
            never.member,
            userless,
        }

    def it_leaves_out_former_members_and_members_who_do_not_teach():
        _former("former")
        _user("plain")
        assert not Member.objects.instructors().exists()

    def it_lists_an_instructor_of_two_classes_once():
        listed = _listed("twice")
        ClassOfferingFactory(instructor=listed.member)
        assert list(Member.objects.instructors()) == [listed.member]


def describe_AnnouncementDraft_instructors_audience():
    def it_names_the_audience_and_the_title():
        draft = AnnouncementDraft(audience=_INSTRUCTORS)
        assert draft.audience_value == "instructor"
        assert draft.audience_label == "All Instructors"
        assert draft.announcement_category == "Instructors Announcement"

    def it_leads_the_title_with_urgent_when_urgent():
        draft = AnnouncementDraft(audience=_INSTRUCTORS, mark_as_urgent=True)
        assert draft.announcement_category == "Urgent: Instructors Announcement"

    def describe_recipients():
        def it_reaches_an_account_by_pk_and_a_userless_instructor_by_address():
            oriented = _oriented("acct")
            _userless("NoLogin@Example.com ")
            user_ids, emails = AnnouncementDraft(audience=_INSTRUCTORS)._instructor_recipients()
            assert user_ids == {oriented.pk}
            assert emails == ["nologin@example.com"]

        def it_drops_a_userless_address_an_instructor_account_already_uses():
            oriented = _oriented("shared")
            _userless("shared@example.com")
            assert AnnouncementDraft(audience=_INSTRUCTORS)._instructor_recipients() == ({oriented.pk}, [])

        def it_leaves_out_an_instructor_with_no_address_at_all():
            _userless("")
            emailless = _oriented("emailless")
            User.objects.filter(pk=emailless.pk).update(email="")
            assert AnnouncementDraft(audience=_INSTRUCTORS)._instructor_recipients() == (set(), [])

    def describe_recipient_count():
        @pytest.fixture
        def people(db):
            _oriented("a")
            _oriented("b", signed_in=False)
            _userless("c@example.com")
            _former("d")
            _user("e")

        def it_counts_the_accounts_and_the_userless_addresses_while_email_is_on(people):
            assert AnnouncementDraft(audience=_INSTRUCTORS, send_email=True).recipient_count() == 3

        def it_counts_only_the_accounts_while_email_is_off(people):
            assert AnnouncementDraft(audience=_INSTRUCTORS, send_email=False).recipient_count() == 2

    def describe_send():
        @pytest.fixture
        def people(db):
            return {
                "oriented": _oriented("oriented-s"),
                "listed": _listed("listed-s"),
                "never": _oriented("never-s", signed_in=False),
                "userless": _userless("userless-s@example.com"),
                "former": _former("former-s"),
                "plain": _user("plain-s"),
                "sender": _user("sender-s"),
            }

        def it_reaches_every_instructor_and_nobody_else(people):
            draft = _draft(people["sender"])
            count = draft.recipient_count()
            emailed, total = draft.send()
            assert (emailed, total) == (4, 4) == (count, count)
            accounts = {f"user:{people[key].pk}" for key in ("oriented", "listed", "never")}
            assert _reached("in_app") == accounts
            assert _reached("email") == accounts | {"email:userless-s@example.com"}
            assert set(Notification.objects.values_list("user_id", flat=True)) == {
                people[key].pk for key in ("oriented", "listed", "never")
            }
            outbox = {address for message in mail.outbox for address in message.to}
            assert outbox == {
                "oriented-s@example.com",
                "listed-s@example.com",
                "never-s@example.com",
                "userless-s@example.com",
            }
            assert {message.subject for message in mail.outbox} == {"Instructors Announcement"}
            draft.refresh_from_db()
            assert draft.sent_at is not None
            assert draft.delivery_period == f"announce:{draft.pk}"

        def it_emails_nobody_and_skips_the_userless_instructor_with_email_off(people):
            draft = _draft(people["sender"], send_email=False)
            count = draft.recipient_count()
            assert draft.send() == (0, 3)
            assert count == 3
            assert _reached("email") == set()
            assert mail.outbox == []

        def it_respects_an_instructors_makerspace_announcement_email_opt_out(people):
            NotificationPreference.objects.create(
                user=people["oriented"], event_key="site_announcement", channel="email", enabled=False
            )
            _draft(people["sender"]).send()
            assert f"user:{people['oriented'].pk}" in _reached("in_app")
            assert f"user:{people['oriented'].pk}" not in _reached("email")

        def it_overrides_the_opt_out_when_urgent(people):
            NotificationPreference.objects.create(
                user=people["oriented"], event_key="site_announcement", channel="email", enabled=False
            )
            _draft(people["sender"], mark_as_urgent=True, title="Urgent: Instructors Announcement").send()
            assert f"user:{people['oriented'].pk}" in _reached("email")

        def it_posts_nothing_to_discord_with_no_channel(people):
            _draft(people["sender"], discord_channel=GuildAnnouncement.DiscordChannel.NONE).send()
            assert not EventDelivery.objects.filter(channel="discord").exists()

        def it_resolves_the_chosen_central_channel(people):
            draft = _draft(people["sender"], discord_channel=GuildAnnouncement.DiscordChannel.LEADERSHIP)
            with patch("membership.models.resolve_channel_webhook", return_value="") as webhook:
                draft.send()
            webhook.assert_called_once_with(GuildAnnouncement.DiscordChannel.LEADERSHIP, None)
