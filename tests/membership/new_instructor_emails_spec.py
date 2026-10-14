"""A member who becomes an instructor gets every instructor email on (2026-10-14).

A save of the settings page before they taught, or before #612 made these default on, left
explicit off rows; becoming an instructor flips them back on. The data migration does the
same once for everyone who already teaches.
"""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps
from django.contrib.auth.models import User

from core.events import preferences
from core.events.registry import Channel
from core.models import NotificationPreference
from membership.models import Member

pytestmark = pytest.mark.django_db


def _member(username: str) -> Member:
    user = User.objects.create_user(username=username, email=f"{username}@example.com")
    member = Member.objects.get(user=user)
    NotificationPreference.objects.create(
        user=user, event_key="instructor_class_approved", channel="email", enabled=False
    )
    return member


def _wants(member: Member) -> bool:
    return preferences.wants(member.user, "instructor_class_approved", Channel.EMAIL)


def describe_becoming_an_instructor():
    def it_turns_the_emails_on_when_teaching_is_granted():
        member = _member("ni_grant")
        admin = _member("ni_admin")
        member.grant_teaching(granted_by=admin)
        assert _wants(member)
        row = NotificationPreference.objects.get(
            user=member.user, event_key="instructor_class_approved", channel="email"
        )
        assert row.changed_by == admin.user

    def it_turns_the_emails_on_when_the_instructor_page_is_made():
        member = _member("ni_slug")
        member.ensure_instructor_slug()
        assert _wants(member)

    def it_leaves_a_switch_the_instructor_turns_off_afterwards():
        member = _member("ni_after")
        member.grant_instructor(granted_by=None)
        NotificationPreference.objects.filter(user=member.user, event_key="instructor_class_approved").update(
            enabled=False
        )
        member.grant_instructor(granted_by=None)  # already teaching: a no-op
        assert not _wants(member)


def describe_the_migration():
    def it_turns_the_emails_on_for_everyone_who_teaches_and_nobody_else():
        teaching = _member("mig_teach")
        teaching.instructor_slug = "mig-teach"
        teaching.save(update_fields=["instructor_slug"])
        bystander = _member("mig_none")
        migration = importlib.import_module("core.migrations.0116_turn_on_instructor_emails")
        migration.turn_on(apps, None)
        assert _wants(teaching)
        assert not _wants(bystander)
