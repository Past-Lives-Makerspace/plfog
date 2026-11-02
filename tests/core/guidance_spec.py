"""BDD specs for ``core.guidance``, the feature registry What's New and the CI scripts read (#1007).

The registry is checked the way ``tests/core/tours_spec.py`` checks the tours: every key against
``KEY_PATTERN``, every ``tour`` against ``TOURS``, every ``help_key`` against ``HELP_KEYS`` and
every ``url_name`` against the URLconf, so a typo fails here rather than as a dead Open it link.
The import-light check runs the module in a subprocess with no Django settings, because the two
CI scripts import it exactly that way.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
from typing import cast

import pytest
from django.urls import reverse

from core.guidance import GUIDANCE, Guidance, url_for
from core.help_registry import HELP_KEYS, KEY_PATTERN
from core.tours import TOURS
from membership.models import AdminCapability, Member
from tests.membership.factories import GuildFactory, GuildMembershipFactory, MemberFactory

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.django_db


def describe_GUIDANCE():
    def it_registers_the_keys_the_plan_names():
        required = {
            "guild.cubbies",
            "guild.voting",
            "guild.orientations",
            "guild.reservations",
            "guild.wiki",
            "guild.meetings",
            "admin.tools",
            "admin.payment-report",
            "admin.email-members",
            "admin.manage-members",
            "classes.catalog",
            "classes.teach",
            "classes.email-past-students",
            "home.door-code",
            "home.whats-new",
        }
        assert required <= set(GUIDANCE)

    def it_keys_every_entry_by_its_own_key_in_the_help_key_grammar():
        for key, guidance in GUIDANCE.items():
            assert guidance.key == key
            assert KEY_PATTERN.match(key), key

    def it_gives_every_entry_a_title_and_two_to_five_steps():
        for guidance in GUIDANCE.values():
            assert guidance.title, guidance.key
            assert 2 <= len(guidance.steps) <= 5, guidance.key
            assert all(step.strip() for step in guidance.steps), guidance.key

    def it_only_names_registered_tours():
        unknown = [g.key for g in GUIDANCE.values() if g.tour is not None and g.tour not in TOURS]
        assert not unknown, unknown

    def it_only_names_registered_help_keys():
        unknown = [g.key for g in GUIDANCE.values() if g.help_key is not None and g.help_key not in HELP_KEYS]
        assert not unknown, unknown

    def it_only_names_targets_in_the_key_grammar():
        # A default target (#1051) is a data-guidance-target key; tests/hub/guidance_targets_spec.py
        # holds it to a template, this holds it to the grammar.
        bad = [g.key for g in GUIDANCE.values() if g.target is not None and not KEY_PATTERN.match(g.target)]
        assert not bad, bad

    def it_reverses_every_url_without_arguments():
        for guidance in GUIDANCE.values():
            if guidance.url_kwargs is None:
                assert reverse(guidance.url_name), guidance.key

    def it_imports_with_no_django_settings():
        # The CI scripts import this module the way they import plfog.changelog: bare Python,
        # no settings, no dependency install. A Django import at module level fails here.
        # The CI job has no Django installed either, so a `django` import at module level
        # would fail there even though it imports fine here: assert none was loaded.
        env = {name: value for name, value in os.environ.items() if name != "DJANGO_SETTINGS_MODULE"}
        program = (
            "import sys, core.guidance; "
            "print(len(core.guidance.GUIDANCE), any(name.split('.')[0] == 'django' for name in sys.modules))"
        )
        completed = subprocess.run(
            [sys.executable, "-c", program], capture_output=True, text=True, check=False, cwd=_REPO_ROOT, env=env
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.split() == [str(len(GUIDANCE)), "False"]


def _member(**fields: object) -> Member:
    """A factory member as its model: the factory returns one at runtime, its typing does not say so."""
    return cast(Member, MemberFactory(**fields))


def describe_url_for():
    def _lead(fog_role: str = Member.FogRole.MEMBER) -> Member:
        member = _member(fog_role=fog_role)
        GuildFactory(name="Led Guild", slug="led-guild", guild_lead=member)
        return member

    def it_resolves_every_url_that_needs_a_member():
        # An admin who leads a guild can open every registered page, Manage Classes included.
        member = _lead(fog_role=Member.FogRole.ADMIN)
        for guidance in GUIDANCE.values():
            if guidance.url_kwargs is not None:
                assert url_for(guidance.key, member).startswith("/"), guidance.key

    def describe_class_review():
        """Manage Classes is for admins and CMS Administrators; a guild reviewer reviews from the email."""

        def it_opens_manage_classes_for_an_admin():
            assert url_for("classes.review", _member(fog_role=Member.FogRole.ADMIN)) == reverse("classes:admin_classes")

        def it_opens_manage_classes_for_a_cms_administrator():
            member = _member()
            AdminCapability.objects.create(member=member, capability=AdminCapability.Capability.CLASS_APPROVER)
            assert url_for("classes.review", member) == reverse("classes:admin_classes")

        def it_hides_it_from_a_guild_lead_or_class_reviewer():
            # classes_review_access_required answers them with a 403, so Open it must not render.
            with pytest.raises(ValueError, match="cannot open Manage Classes"):
                url_for("classes.review", _lead())

    def it_lands_on_the_tab_the_entry_names():
        assert url_for("guild.cubbies", _lead()) == "/guilds/led-guild/?tab=cubbies"

    def it_leaves_a_page_without_a_query_alone():
        assert url_for("guild.voting", _member()) == reverse("hub_guild_voting")

    def it_opens_the_guild_a_member_staffs_first():
        member = _lead()
        GuildMembershipFactory(member=member, guild=GuildFactory(name="A Joined Guild", slug="a-joined-guild"))
        assert url_for("guild.page", member) == "/guilds/led-guild/"

    def it_falls_back_to_a_guild_the_member_belongs_to():
        member = _member()
        GuildMembershipFactory(member=member, guild=GuildFactory(name="Joined Guild", slug="joined-guild"))
        assert url_for("guild.page", member) == "/guilds/joined-guild/"

    def it_falls_back_to_the_first_active_guild_for_an_admin():
        GuildFactory(name="Zed Guild", slug="zed-guild")
        GuildFactory(name="Alpha Guild", slug="alpha-guild")
        GuildFactory(name="Aardvark Guild", slug="aardvark-guild", is_active=False)
        admin = _member(fog_role=Member.FogRole.ADMIN)
        assert url_for("guild.page", admin) == "/guilds/alpha-guild/"
        assert url_for("guild.staff", admin).startswith("/guilds/")

    def it_refuses_a_member_with_no_guild_to_open():
        GuildFactory(name="Some Guild", slug="some-guild")
        with pytest.raises(ValueError, match="no guild"):
            url_for("guild.page", _member())

    def it_needs_lead_authority_for_the_guild_edit_page():
        # Belonging to a guild opens its page but not its settings.
        member = _member()
        GuildMembershipFactory(member=member, guild=GuildFactory(name="Joined Guild", slug="joined-guild"))
        with pytest.raises(ValueError, match="no guild"):
            url_for("guild.staff", member)

    def it_fails_loudly_on_an_unregistered_key():
        with pytest.raises(KeyError):
            url_for("nope.nothing", _member())


def describe_Guidance():
    def it_defaults_the_optional_materials_to_none():
        guidance = Guidance(key="a.b", title="T", url_name="hub_home", steps=("One.", "Two."))
        assert (guidance.url_kwargs, guidance.query, guidance.tour, guidance.help_key, guidance.target) == (
            None,
            None,
            None,
            None,
            None,
        )
