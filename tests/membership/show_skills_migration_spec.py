"""Data-migration spec for membership 0213: every member's skills show on their card again (#674).

The migration only writes a field that exists at head, so its function runs here against the
current app registry instead of rewinding the schema with ``MigrationExecutor``.
"""

from __future__ import annotations

from importlib import import_module

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from membership.models import Member, Skill
from tests.membership.factories import MemberFactory, MembershipPlanFactory, MemberSkillFactory, SkillFactory

_migration = import_module("membership.migrations.0213_show_skills_on_directory_cards")

OTHER_KEYS = {
    "pronouns": False,
    "phone": True,
    "email": False,
    "discord_handle": True,
    "about_me": False,
    "profile_photo": True,
}


def _visibility(member: Member) -> dict[str, bool]:
    member.refresh_from_db()
    return member.directory_visibility


@pytest.mark.django_db
def describe_migration_0213_show_skills_on_directory_cards():
    def it_flips_hidden_skills_to_shown_and_leaves_the_other_keys_alone():
        member = MemberFactory(directory_visibility={**OTHER_KEYS, "skills": False})

        _migration.show_skills_on_directory_cards(apps, None)

        assert _visibility(member) == {**OTHER_KEYS, "skills": True}

    def it_adds_the_skills_key_where_it_was_missing():
        member = MemberFactory(directory_visibility={"phone": False})

        _migration.show_skills_on_directory_cards(apps, None)

        assert _visibility(member) == {"phone": False, "skills": True}

    def it_leaves_a_member_already_showing_skills_unchanged():
        member = MemberFactory(directory_visibility={**OTHER_KEYS, "skills": True})

        _migration.show_skills_on_directory_cards(apps, None)

        assert _visibility(member) == {**OTHER_KEYS, "skills": True}

    def it_puts_approved_skills_back_on_the_directory_card(client: Client):
        MembershipPlanFactory()
        member = MemberFactory(
            show_in_directory=True, full_legal_name="Tess Tech", directory_visibility={"skills": False}
        )
        MemberSkillFactory(member=member, skill=SkillFactory(name="Soldering", status=Skill.Status.APPROVED))
        client.force_login(get_user_model().objects.create_user(username="viewer"))
        chip = b'<span class="pl-skill-chip">Soldering'
        assert chip not in client.get(reverse("hub_member_directory")).content

        _migration.show_skills_on_directory_cards(apps, None)

        assert chip in client.get(reverse("hub_member_directory")).content

    def it_reverses_as_a_noop():
        member = MemberFactory(directory_visibility={"skills": False})
        _migration.show_skills_on_directory_cards(apps, None)

        _migration.Migration.operations[0].reverse_code(apps, None)

        assert _visibility(member) == {"skills": True}
