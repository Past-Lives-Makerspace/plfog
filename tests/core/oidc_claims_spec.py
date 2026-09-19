"""What this app tells the Knowledge Base about a member.

These are boundary tests. Everything asserted here leaves the application and lands in a system
with its own accounts and its own tiers, so the interesting cases are the ones about what we do
*not* send, and about a claim meaning what the other side thinks it means.
"""

from __future__ import annotations


from classes.factories import UserFactory
from membership.models import Member


class _Request:
    """The shape `get_additional_claims` reads: oauthlib hands it a request with a `.user`."""

    def __init__(self, user):
        self.user = user


def _claims(user):
    from core.oidc import FogOAuth2Validator

    return FogOAuth2Validator().get_additional_claims(_Request(user))


def _member_user(**member_fields):
    """A signed-in user with a Member.

    `UserFactory` already provisions a Member through `ensure_user_has_member`, so this updates
    that one rather than creating a second — `Member.user` is unique and a second one raises.
    """
    user = UserFactory()
    member = user.member
    for field, value in member_fields.items():
        setattr(member, field, value)
    member.save()
    user.refresh_from_db()
    return user


def describe_fog_role_claim():
    def it_sends_the_role_the_member_actually_holds(db):
        user = _member_user(fog_role=Member.FogRole.GUILD_OFFICER)
        assert _claims(user)["fog_role"] == Member.FogRole.GUILD_OFFICER


def describe_the_name_claim():
    def it_prefers_the_name_the_member_chose(db):
        user = _member_user(full_legal_name="Jonathan Smith", preferred_name="Jo")
        assert _claims(user)["name"] == "Jo"

    def it_never_sends_a_legal_name_that_was_overridden(db):
        """A legal name is not something to hand another system as a side effect of signing in."""
        user = _member_user(full_legal_name="Jonathan Smith", preferred_name="Jo")
        assert "Jonathan" not in _claims(user)["name"]


def describe_guild_leadership_claim():
    def it_is_false_for_an_ordinary_member(db):
        user = _member_user(fog_role=Member.FogRole.MEMBER)
        assert _claims(user)["is_guild_leadership"] is False


def describe_what_is_not_sent():
    def it_sends_no_phone_or_address(db):
        user = _member_user()
        assert set(_claims(user)) == {"name", "email", "kb_level", "fog_role", "is_guild_leadership"}


def describe_claim_scopes():
    def it_puts_every_role_claim_behind_the_roles_scope(db):
        """A client that does not ask for `roles` learns nothing about authority."""
        from core.oidc import FogOAuth2Validator

        scopes = FogOAuth2Validator.oidc_claim_scope
        assert scopes["fog_role"] == "roles"
        assert scopes["kb_level"] == "roles"
        assert scopes["is_guild_leadership"] == "roles"


def describe_the_kb_level_claim():
    """This app owns the tiers, so this table is where a role becomes access in both systems."""

    def it_gives_a_member_the_member_tier(db):
        assert _claims(_member_user(fog_role=Member.FogRole.MEMBER))["kb_level"] == 10

    def it_gives_a_guild_officer_the_officer_tier(db):
        assert _claims(_member_user(fog_role=Member.FogRole.GUILD_OFFICER))["kb_level"] == 20

    def it_gives_an_admin_the_staff_tier(db):
        """An admin here reads the KB's staff documents, which carry third-party contacts."""
        assert _claims(_member_user(fog_role=Member.FogRole.ADMIN))["kb_level"] == 40

    def it_covers_every_role_so_a_new_one_cannot_be_forgotten(db):
        """A FogRole with no entry would silently grant PUBLIC. Fail here instead of in the KB."""
        from core.oidc import KB_LEVELS

        assert set(KB_LEVELS) == set(Member.FogRole.values)


def describe_a_signed_in_user_with_no_membership():
    """A bare staff login. This is the branch `NO_MEMBERSHIP_LEVEL` exists for, and the one most
    likely to be wrong, because every other spec here goes through a Member."""

    def _bare_user(db):
        """A User whose auto-provisioned Member has been removed, leaving `user.member` absent."""
        user = UserFactory()
        user.member.delete()
        user.refresh_from_db()
        return user

    def it_grants_the_public_tier(db):
        assert _claims(_bare_user(db))["kb_level"] == 0

    def it_sends_an_empty_role(db):
        """An empty role is not a role the KB knows, and it is not asked to guess one."""
        assert _claims(_bare_user(db))["fog_role"] == ""

    def it_falls_back_to_the_django_name_and_email(db):
        user = _bare_user(db)
        user.first_name, user.last_name = "Bare", "Login"
        user.email = "bare@example.test"
        user.save()
        claims = _claims(user)
        assert claims["name"] == "Bare Login"
        assert claims["email"] == "bare@example.test"

    def it_falls_back_to_the_username_when_there_is_no_full_name(db):
        user = _bare_user(db)
        user.first_name = user.last_name = ""
        user.save()
        assert _claims(user)["name"] == user.get_username()

    def it_is_not_guild_leadership(db):
        assert _claims(_bare_user(db))["is_guild_leadership"] is False


def describe_guild_leadership_is_reported_when_true():
    """Asserting only the False case would pass against a claim that is always False."""

    def it_is_true_for_a_guild_lead(db):
        from tests.membership.factories import GuildFactory

        user = _member_user(fog_role=Member.FogRole.MEMBER)
        GuildFactory(guild_lead=user.member)
        user.refresh_from_db()
        assert _claims(user)["is_guild_leadership"] is True
