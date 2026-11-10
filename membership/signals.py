"""Signals for the membership app."""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from membership.models import LeadershipListing, LeadershipTab, Member, VotePreference

logger = logging.getLogger(__name__)

User = get_user_model()


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def ensure_user_has_member(sender: type, instance: Any, created: bool, **kwargs: Any) -> None:
    """Auto-create or link a Member record for any user who doesn't have one.

    After linking (or creating) a Member, also promotes any pre-signup
    ``MemberEmail`` staging rows for that member into
    ``allauth.account.EmailAddress`` so the user can log in via any of them.
    See ``docs/superpowers/specs/2026-04-07-user-email-aliases-design.md``.

    Gated on ``created=True``: every branch of this signal is only meaningful
    on the first save of a User. Re-running ``migrate_to_user`` on subsequent
    saves was a 1.4.0 bug — it would force-re-promote ``Member._pre_signup_email``
    to primary and silently revert any other primary the member or admin had
    set via allauth, because allauth's ``set_as_primary`` calls ``user.save()``
    internally. Skipping non-creation saves keeps allauth's primary stable.

    During allauth web signup, ``migrate_to_user`` is deliberately skipped here
    because allauth's ``setup_user_email`` (called after ``save_user``) asserts
    no EmailAddress rows exist yet. The adapter sets an ``is_in_allauth_signup``
    flag; the ``user_signed_up`` handler in ``core.signals`` calls
    ``migrate_to_user`` after allauth has finished its own email setup.
    """
    if not created:
        return

    from membership.services.provisioning import is_provisioning

    # FULL suppression (Review fix #3): while ``provision_user_for_member`` runs it
    # OWNS the create → EmailAddress → migrate → link chain. This signal must be a
    # complete no-op so a case/whitespace lookup miss here can't fall through to the
    # create branch and mint a SECOND Member (a OneToOne collision on ``user``).
    if is_provisioning():
        return

    from core.allauth_state import is_in_allauth_signup

    from .models import MemberEmail

    email = getattr(instance, "email", "") or ""
    if email:
        # Check primary email on Member
        try:
            member = Member.objects.get(_pre_signup_email__iexact=email, user__isnull=True)
            member.user = instance
            # A collected real name (signup form / registration) wins; otherwise keep
            # whatever the placeholder already had. Never the username — that's just
            # the lowercased email local part (issue #274).
            member.full_legal_name = instance.get_full_name() or member.full_legal_name
            member.status = _status_on_first_link(member.status)
            member.save(update_fields=["user", "full_legal_name", "status"])
            logger.info("Linked existing Member (primary email) to user %s.", instance.username)
            if not is_in_allauth_signup():
                MemberEmail.objects.migrate_to_user(instance)
            return
        except Member.DoesNotExist:
            pass

        # Check email aliases (pre-signup staging table)
        try:
            alias = MemberEmail.objects.select_related("member").get(email__iexact=email, member__user__isnull=True)
            member = alias.member
            member.user = instance
            member.full_legal_name = instance.get_full_name() or member.full_legal_name
            member.status = _status_on_first_link(member.status)
            member.save(update_fields=["user", "full_legal_name", "status"])
            logger.info("Linked existing Member (alias email %s) to user %s.", email, instance.username)
            if not is_in_allauth_signup():
                MemberEmail.objects.migrate_to_user(instance)
            return
        except MemberEmail.DoesNotExist:
            pass

    _auto_create_member_for_user(instance)


def _status_on_first_link(status: str) -> str:
    """The status a Member takes when a User first links to it.

    Only INVITED becomes ACTIVE: accepting the invite is what signing in means. Every other
    status is kept, so a FORMER member imported from Airtable who never had an account is not
    reactivated by requesting a login code, and the lockout (#409) still turns them away.
    """
    from .models import Member

    return str(Member.Status.ACTIVE) if status == Member.Status.INVITED else status


def _auto_create_member_for_user(instance: Any) -> None:
    """Create a fresh Member for a User with no pre-existing match (signal helper).

    Extracted from :func:`ensure_user_has_member` so the receiver stays under the
    complexity limit. Early-returns (logging) when no MembershipPlan exists; warns
    but proceeds when the user has no email (the emailless-account case).
    """
    from core.allauth_state import is_in_allauth_signup

    from .models import MemberEmail, MembershipPlan

    try:
        plan = MembershipPlan.objects.order_by("pk").earliest("pk")
    except MembershipPlan.DoesNotExist:
        logger.warning(
            "Cannot auto-create Member for user %s: no MembershipPlan exists.",
            instance.username,
        )
        return

    # Blank is deliberate when the user carries no name (issue #274): the old
    # ``or instance.username`` fallback seeded the lowercased email local part
    # as a legal name, which rendered as garbage in rosters. An empty
    # full_legal_name renders empty everywhere and surfaces in Manage Members.
    name = instance.get_full_name()
    member_email = instance.email or ""
    if not member_email:
        logger.warning(
            "Creating Member for user %s (id=%s) with NO email — this account has no "
            "usable email and will surface in Manage Members → 'Missing email'. "
            "See the member-email-integrity spec.",
            instance.username,
            instance.pk,
        )
    Member.objects.create(
        user=instance,
        full_legal_name=name,
        _pre_signup_email=member_email,
        membership_plan=plan,
        status=Member.Status.ACTIVE,
    )
    if not is_in_allauth_signup():
        MemberEmail.objects.migrate_to_user(instance)
    logger.info("Auto-created Member for user %s with plan '%s'.", instance.username, plan.name)


@receiver(post_save, sender=Member)
def auto_provision_member_user(sender: type, instance: Member, created: bool, **kwargs: Any) -> None:
    """Going-forward invariant: a newly-created ACTIVE Member gets a linked User.

    Fires only on the FIRST save of an ACTIVE member that has no user yet — the
    common case being an Airtable import (``airtable_pull`` creates members via
    ``Member.objects.create``). Provisioning is SILENT and idempotent.

    Scope guards (Review fix #1): non-ACTIVE members (INVITED placeholders,
    FORMER/SUSPENDED) are skipped — provisioning an INVITED stub would bypass
    invite acceptance, and resurrecting a cancelled person is wrong. The link path
    inside ``provision_user_for_member`` writes only ``user`` (never ``status``).

    Recursion is impossible: ``provision_user_for_member`` re-saves the member with
    ``update_fields=["user"]`` (``created=False`` → this handler returns early), and
    the User it creates runs under the provisioning guard so ``ensure_user_has_member``
    no-ops — so no new ACTIVE userless Member is ever created mid-provision.
    """
    if not created or instance.user_id is not None or instance.status != Member.Status.ACTIVE:
        return

    from membership.services.provisioning import provision_user_for_member

    provision_user_for_member(instance)


@receiver(post_save, sender=VotePreference)
def _log_vote_activity(sender: type, instance: VotePreference, created: bool, **kwargs: Any) -> None:
    from core.models import SiteActivity

    kind = SiteActivity.Kind.VOTE_SUBMITTED if created else SiteActivity.Kind.VOTE_CHANGED
    actor = instance.member.user if instance.member_id else None
    SiteActivity.log(kind, actor=actor, target=instance.member)


def _connect_request_bell_clearing() -> None:
    """A staff request deleted unanswered stops asking in the bell (#894 part 3).

    ``pre_delete`` fires for a single delete, a queryset delete and a cascade (a guild, a
    meeting, a member going away), which no decision method sees.
    """
    from django.db.models.signals import pre_delete

    from core.signals import clear_request_bells_on_delete
    from membership.models import (
        CommunityEvent,
        GuildAnnouncement,
        MeetingItemProposal,
        Member,
        SpaceRequest,
        WikiPage,
        WikiReport,
    )

    for model in (
        SpaceRequest,
        CommunityEvent,
        GuildAnnouncement,
        MeetingItemProposal,
        WikiReport,
        WikiPage,
        Member,
    ):
        pre_delete.connect(
            clear_request_bells_on_delete, sender=model, dispatch_uid=f"membership.request_bells.{model.__name__}"
        )


_connect_request_bell_clearing()


# --- Discord leadership roles follow the Leadership Directory (#1148) ---
#
# A People tab listing is written from several places (Add a Person, Remove from Tab, the roster
# import, ``adopt_untabbed``, and a tab's delete cascading to its cards), so these receivers,
# rather than each writer, enqueue the member's role reconcile. Lead and staff changes have
# single model paths and call it there instead. Each save is compared with the row as stored,
# so a reorder or a rename enqueues nothing.

_LISTING_FIELDS = frozenset({"is_listed", "tab", "tab_id"})


def _touches(update_fields: Any, fields: frozenset[str]) -> bool:
    """Whether a save with these ``update_fields`` can write any of ``fields`` (None writes them all)."""
    return update_fields is None or bool(fields & set(update_fields))


@receiver(pre_save, sender=LeadershipListing)
def _remember_listing_state(
    sender: type, instance: LeadershipListing, update_fields: Any = None, **kwargs: Any
) -> None:
    """Note whether the card was on show and on which tab, before this save."""
    instance._leadership_was = None  # type: ignore[attr-defined]
    if instance.pk is not None and _touches(update_fields, _LISTING_FIELDS):
        instance._leadership_was = (  # type: ignore[attr-defined]
            LeadershipListing.objects.filter(pk=instance.pk).values_list("is_listed", "tab_id").first()
        )


@receiver(post_save, sender=LeadershipListing)
def _reconcile_after_listing_save(
    sender: type, instance: LeadershipListing, created: bool, update_fields: Any = None, **kwargs: Any
) -> None:
    """A card put on show, taken off or moved to another tab changes what its member should hold."""
    from core.events.discord_leadership_roles import reconcile_on_commit

    if created:
        if instance.is_listed:
            reconcile_on_commit(instance.member_id)
        return
    was = getattr(instance, "_leadership_was", None)
    if was is not None and was != (instance.is_listed, instance.tab_id):
        reconcile_on_commit(instance.member_id)


@receiver(post_delete, sender=LeadershipListing)
def _reconcile_after_listing_delete(sender: type, instance: LeadershipListing, **kwargs: Any) -> None:
    """A deleted card on show (alone, or with its deleted tab) may take leadership away."""
    from core.events.discord_leadership_roles import reconcile_on_commit

    if instance.is_listed:
        reconcile_on_commit(instance.member_id)


@receiver(pre_save, sender=LeadershipTab)
def _remember_tab_kind(sender: type, instance: LeadershipTab, update_fields: Any = None, **kwargs: Any) -> None:
    """Note the tab's kind before this save; only a People tab's cards earn roles."""
    instance._leadership_old_kind = None  # type: ignore[attr-defined]
    if instance.pk is not None and _touches(update_fields, frozenset({"kind"})):
        instance._leadership_old_kind = (  # type: ignore[attr-defined]
            LeadershipTab.objects.filter(pk=instance.pk).values_list("kind", flat=True).first()
        )


@receiver(post_save, sender=LeadershipTab)
def _reconcile_after_tab_kind_change(sender: type, instance: LeadershipTab, created: bool, **kwargs: Any) -> None:
    """A tab that changes kind changes what every member listed on it should hold."""
    from core.events.discord_leadership_roles import reconcile_on_commit

    old_kind = getattr(instance, "_leadership_old_kind", None)
    if created or old_kind is None or old_kind == instance.kind:
        return
    reconcile_on_commit(*instance.listings.filter(is_listed=True).values_list("member_id", flat=True))
