"""Data-migration spec for membership 0275: the Guild Officer role is retired (ADR 0004).

The migration only writes fields that exist at head, so its function runs here against the
current app registry instead of rewinding the schema with ``MigrationExecutor``. The retired
value is no longer a choice, so each officer is seeded with a raw ``update``, as production
still held it.
"""

from __future__ import annotations

from importlib import import_module

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model

from membership.models import GuildStaffMembership, Member
from tests.membership.factories import GuildFactory, MemberFactory

_migration = import_module("membership.migrations.0275_retire_guild_officer_role")


def _officer(username: str, *, superuser: bool = False) -> Member:
    """A member holding the retired role, whose login carries the ``is_staff`` it gave them."""
    user = get_user_model().objects.create_user(username=username, email=f"{username}@example.com")
    user.is_staff = True
    user.is_superuser = superuser
    user.save(update_fields=["is_staff", "is_superuser"])
    Member.objects.filter(user=user).update(fog_role=_migration.RETIRED_ROLE)
    return Member.objects.get(user=user)


def _run() -> None:
    _migration.retire_guild_officers(apps, None)


@pytest.mark.django_db
def describe_migration_0275_retire_guild_officer_role():
    def it_makes_an_officer_a_member_and_drops_the_staff_flag():
        officer = _officer("officer-a")

        _run()

        officer.refresh_from_db()
        officer.user.refresh_from_db()
        assert officer.fog_role == Member.FogRole.MEMBER
        assert officer.user.is_staff is False
        assert officer.user.is_superuser is False

    def it_leaves_a_superuser_officers_flags_alone():
        officer = _officer("officer-super", superuser=True)

        _run()

        officer.refresh_from_db()
        officer.user.refresh_from_db()
        assert officer.fog_role == Member.FogRole.MEMBER
        assert officer.user.is_staff is True
        assert officer.user.is_superuser is True

    def it_migrates_an_officer_with_no_login():
        officer = MemberFactory(user=None)
        Member.objects.filter(pk=officer.pk).update(fog_role=_migration.RETIRED_ROLE)

        _run()

        officer.refresh_from_db()
        assert officer.fog_role == Member.FogRole.MEMBER

    def it_leaves_admins_and_members_untouched():
        admin_user = get_user_model().objects.create_user(username="admin-keep", email="admin-keep@example.com")
        admin_user.is_staff = True
        admin_user.save(update_fields=["is_staff"])
        Member.objects.filter(user=admin_user).update(fog_role=Member.FogRole.ADMIN)
        admin = Member.objects.get(user=admin_user)
        member = MemberFactory(fog_role=Member.FogRole.MEMBER)

        _run()

        admin.refresh_from_db()
        member.refresh_from_db()
        admin_user.refresh_from_db()
        assert admin.fog_role == Member.FogRole.ADMIN
        assert member.fog_role == Member.FogRole.MEMBER
        assert admin_user.is_staff is True

    def it_keeps_their_guild_rows_and_the_authority_they_give():
        lead = _officer("officer-lead")
        staffer = _officer("officer-staff")
        led = GuildFactory(guild_lead=lead)
        staffed = GuildFactory()
        GuildStaffMembership.objects.create(guild=staffed, member=staffer, role=GuildStaffMembership.Role.CO_LEAD)
        elsewhere = GuildFactory()

        _run()

        lead.refresh_from_db()
        staffer.refresh_from_db()
        led.refresh_from_db()
        assert led.guild_lead_id == lead.pk
        assert staffed.is_staffed_by(staffer)
        assert lead.can_edit_guild(led) is True
        assert staffer.can_edit_guild(staffed) is True
        assert lead.can_edit_guild(elsewhere) is False
        assert staffer.can_edit_guild(elsewhere) is False

    def it_is_a_no_op_in_reverse():
        _officer("officer-reverse")
        _run()

        _migration.Migration.operations[0].reverse_code(apps, None)

        assert not Member.objects.filter(fog_role=_migration.RETIRED_ROLE).exists()
