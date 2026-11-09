"""Who an email poll goes to (#1142 part 1): ``AnnouncementDraft.poll_recipient_ids`` for every audience, accounts only."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from classes.factories import ClassOfferingFactory
from membership.models import AnnouncementDraft, Member
from tests.membership.factories import AnnouncementDraftFactory, GuildFactory, GuildMembershipFactory, MemberFactory

pytestmark = pytest.mark.django_db

Audience = AnnouncementDraft.Audience
_seq = {"n": 0}


def _account(*, logged_in: bool = True) -> Member:
    """A member with a login account."""
    _seq["n"] += 1
    member = MemberFactory()
    with mute_signals(post_save):
        user = User.objects.create_user(
            username=f"epr_{_seq['n']}",
            email=f"epr{_seq['n']}@example.com",
            last_login=timezone.now() if logged_in else None,
        )
    member.user = user
    member.save(update_fields=["user"])
    return member


def _poll_draft(**kwargs: object) -> AnnouncementDraft:
    return AnnouncementDraftFactory(kind=AnnouncementDraft.Kind.POLL, **kwargs)


def describe_poll_recipient_ids():
    def describe_site():
        def it_reaches_every_active_member_who_has_signed_in():
            alice, bob = _account(), _account()
            newcomer = _account(logged_in=False)
            reached = _poll_draft().poll_recipient_ids()
            assert {alice.user_id, bob.user_id} <= reached
            assert newcomer.user_id not in reached

        def it_reaches_members_who_never_signed_in_when_asked():
            newcomer = _account(logged_in=False)
            assert newcomer.user_id in _poll_draft(include_never_logged_in=True).poll_recipient_ids()

        def it_adds_the_people_the_sender_added_but_never_a_typed_address():
            alice = _account()
            former = _account()
            Member.objects.filter(pk=former.pk).update(status=Member.Status.FORMER)
            draft = _poll_draft(added_recipients={"users": [former.user_id], "custom": ["guest@example.com"]})
            assert draft.poll_recipient_ids() == {alice.user_id, former.user_id}

        def it_reaches_exactly_a_selection():
            alice, _bob = _account(), _account()
            assert _poll_draft(recipient_selection={"users": [alice.user_id]}).poll_recipient_ids() == {alice.user_id}

    def describe_guild():
        def it_reaches_the_whole_guild():
            guild = GuildFactory()
            member = _account()
            GuildMembershipFactory(guild=guild, member=member)
            _account()
            assert _poll_draft(audience=Audience.GUILD, guild=guild).poll_recipient_ids() == {member.user_id}

        def it_reaches_the_chosen_members_and_never_a_mailing_list_address():
            guild = GuildFactory()
            chosen, skipped = _account(), _account()
            GuildMembershipFactory(guild=guild, member=chosen)
            GuildMembershipFactory(guild=guild, member=skipped)
            selection = {"users": [chosen.user_id], "custom": ["list@example.com"]}
            draft = _poll_draft(audience=Audience.GUILD, guild=guild, recipient_selection=selection)
            assert draft.poll_recipient_ids() == {chosen.user_id}

    def describe_class():
        def it_reaches_the_class_roster_accounts_only():
            draft = _poll_draft(audience=Audience.CLASS, class_offering=ClassOfferingFactory())
            assert draft.poll_recipient_ids() == draft._class_recipients()[0] == set()

        def it_reaches_exactly_a_selection():
            alice = _account()
            selection = {"users": [alice.user_id], "custom": ["guest@example.com"]}
            draft = _poll_draft(
                audience=Audience.CLASS, class_offering=ClassOfferingFactory(), recipient_selection=selection
            )
            assert draft.poll_recipient_ids() == {alice.user_id}

    def describe_guild_leads():
        def it_reaches_every_guild_lead():
            lead = _account()
            GuildFactory(guild_lead=lead)
            _account()
            assert _poll_draft(audience=Audience.LEADS).poll_recipient_ids() == {lead.user_id}

    def describe_instructors_and_past_students():
        def it_reaches_the_accounts_the_email_reaches():
            instructors = _poll_draft(audience=Audience.INSTRUCTORS)
            assert instructors.poll_recipient_ids() == instructors._instructor_recipients()[0]
            students = _poll_draft(audience=Audience.STUDENTS)
            assert students.poll_recipient_ids() == students._student_recipients()[0]
