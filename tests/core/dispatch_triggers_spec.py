"""Event-spine fan-out at core events — new_member_joined.

This sender was migrated onto ``core.events.emit.emit`` (Phase 4). The
characterization here pins the post-migration truth: the in-app Notification rows
still land for the right audience, exactly one SiteActivity row is written (emit
logs it — there is no second manual log), and ``new_member_joined`` now resolves
its audience via the FOG_ADMINS resolver (global admins) rather than a raw
``is_staff`` scan.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.test import RequestFactory
from factory.django import mute_signals

from core.models import Notification, SiteActivity
from membership.models import Member
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def _admin_member(email: str) -> Member:
    """A FOG admin Member with a linked, email-bearing User (the resolver audience).

    Signals are muted while creating the User so the ``ensure_user_has_member``
    post-save signal does not auto-create a second Member that would collide on the
    one-to-one ``user`` key.
    """
    member = MemberFactory(fog_role=Member.FogRole.ADMIN)
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"admin_{member.pk}", email=email)
    member.user = user
    member.save(update_fields=["user"])
    return member


def describe_new_member_joined_dispatch():
    def it_dispatches_to_fog_admins_on_signup():
        admin = _admin_member(email="fogadmin@example.com")
        new_user = User.objects.create_user(username="newbie", email="newbie@example.com")

        from allauth.account.signals import user_signed_up

        request = RequestFactory().get("/")
        user_signed_up.send(sender=User, request=request, user=new_user)

        assert Notification.objects.filter(user=admin.user, trigger="new_member_joined").exists()

    def it_does_not_dispatch_to_non_admin_members_on_signup():
        member = MemberFactory()
        with mute_signals(post_save):
            user = User.objects.create_user(username="plain", email="plain@example.com")
        member.user = user
        member.save(update_fields=["user"])
        new_user = User.objects.create_user(username="newbie2", email="newbie2@example.com")

        from allauth.account.signals import user_signed_up

        request = RequestFactory().get("/")
        user_signed_up.send(sender=User, request=request, user=new_user)

        assert not Notification.objects.filter(user=user, trigger="new_member_joined").exists()

    def it_logs_exactly_one_member_signup_activity_row():
        new_user = User.objects.create_user(username="newbie3", email="newbie3@example.com")

        from allauth.account.signals import user_signed_up

        request = RequestFactory().get("/")
        user_signed_up.send(sender=User, request=request, user=new_user)

        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.MEMBER_SIGNUP, actor=new_user).count() == 1
