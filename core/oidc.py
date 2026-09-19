"""OpenID Connect claims — what this app tells the Knowledge Base about a member.

The KB takes its identity from here (``OAUTH2_PROVIDER`` in settings). It has its own access
tiers and its own documents; what it does not have is an opinion about who anyone is. This module
is the whole of what crosses that boundary.

**This app owns the tiers.** ``kb_level`` is the KB access tier a member holds, decided here from
their ``fog_role``, and the KB applies it as given rather than re-deriving it. There is one place
where a role becomes access — ``KB_LEVELS`` below — and it is in the app that owns the roles, so a
promotion in the portal is the whole of the change. ``fog_role`` rides along beside it as the
human-readable reason, which is what makes a support question answerable without a second lookup.

**Only what the client asked for.** Each claim sits behind a scope, so the KB has to request
``roles`` to be told anything about authority. A future client that only needs a name gets a name.

**An admin here is staff there.** ``KB_LEVELS`` maps ``admin`` onto the KB's staff tier, which
reads documents carrying third-party contact details. That is the consequence of this app owning
the tiers, and it is deliberate: the alternative was a second ladder to keep in step. It also means
granting someone ``admin`` in this app grants them those documents — worth knowing at the moment
you set a role, not afterwards.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from oauth2_provider.oauth2_validators import OAuth2Validator

from membership.models import Member

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser


# Role → the KB's access tier. The KB's ladder is PUBLIC 0, MEMBER 10, OFFICER 20, ADMIN1 30,
# ADMIN2 40; it applies what arrives here rather than deciding for itself, so this table is the
# single place a role becomes access across both applications.
KB_LEVELS: dict[str, int] = {
    Member.FogRole.MEMBER: 10,
    Member.FogRole.GUILD_OFFICER: 20,
    Member.FogRole.ADMIN: 40,
}

# A signed-in user with no membership record — a bare staff login, say. Zero is the KB's PUBLIC
# tier: they see what a stranger sees, which is the safe reading of "we do not know who this is".
NO_MEMBERSHIP_LEVEL = 0


def kb_level_for(member) -> int:
    """The KB tier this member holds. An unmapped role gets PUBLIC, never a guess upward."""
    if member is None:
        return NO_MEMBERSHIP_LEVEL
    return KB_LEVELS.get(member.fog_role, NO_MEMBERSHIP_LEVEL)


def _member_of(user: AbstractBaseUser):
    """The Member behind a User, or None. A staff login may have no membership record."""
    return getattr(user, "member", None)


class FogOAuth2Validator(OAuth2Validator):
    """Adds the claims the Knowledge Base reads to the standard OIDC set."""

    oidc_claim_scope = OAuth2Validator.oidc_claim_scope | {
        "fog_role": "roles",
        "kb_level": "roles",
        "is_guild_leadership": "roles",
        "name": "profile",
        "email": "email",
    }

    def get_additional_claims(self, request):
        user = request.user
        member = _member_of(user)

        return {
            # `display_name`, which prefers `preferred_name` over `full_legal_name`. The name a
            # member chose is the one that should appear on their comments in the KB — and a legal
            # name is not something to hand to another system as a side effect of signing in.
            "name": (member.display_name if member else user.get_full_name() or user.get_username()),
            "email": member.primary_email if member else user.email,
            # The tier itself, decided here. The KB applies it as given.
            "kb_level": kb_level_for(member),
            # The role that produced it, carried alongside so the number is explicable. Nothing
            # reads this for access — `kb_level` is the answer, this is the reason.
            "fog_role": member.fog_role if member else "",
            # Whether this member leads or staffs any guild. A single boolean and not a list of
            # guilds: the KB has no guild-scoped documents, so naming them would be sending data
            # across a boundary for no reader — and `leadership` is per-guild here, which does not
            # translate into a tier there anyway.
            "is_guild_leadership": bool(member and (member.is_guild_lead or member.is_guild_staff)),
        }
