"""Shared setup for the wiki-moderation specs (spec D).

Nine spec files exercise the same three actors — a plain member, a guild lead, and an
admin — against the same feature flag, so the login plumbing lives here once instead of
nine times. Not a ``*_spec.py`` file, so pytest never collects it.
"""

from __future__ import annotations

from django.contrib.auth.models import User
from django.test import Client

from core.models import SiteConfiguration
from membership.models import Guild, Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory


def enable_wiki() -> SiteConfiguration:
    """Turn the feature flag on. Every wiki route 404s while it is off."""
    config = SiteConfiguration.load()
    config.wiki_enabled = True
    config.save()
    return config


def member_user(
    username: str,
    *,
    fog_role: str = Member.FogRole.MEMBER,
    status: str = Member.Status.ACTIVE,
    email: str = "",
) -> User:
    """A signed-up member. ``email`` is set when the spec asserts on a real send."""
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass", email=email)
    member = user.member
    member.fog_role = fog_role
    member.status = status
    member.full_legal_name = username.replace("_", " ").title()
    member.save()
    member.sync_user_permissions()
    return user


def login(client: Client, username: str, **kwargs: str) -> User:
    """Create a member and sign them in."""
    user = member_user(username, **kwargs)
    client.login(username=username, password="pass")
    return user


def login_lead(client: Client, username: str, guild: Guild | None = None) -> tuple[User, Guild]:
    """Sign in a plain member who is the lead of ``guild`` (created if not given).

    Lead authority comes solely from ``Guild.guild_lead`` — no FOG role and no
    ``is_staff`` flag — which is exactly the case every scoping spec here needs to
    separate from an admin's site-wide reach.
    """
    user = login(client, username)
    if guild is None:
        guild = GuildFactory(guild_lead=user.member)
    else:
        guild.guild_lead = user.member
        guild.save(update_fields=["guild_lead"])
    return user, guild
