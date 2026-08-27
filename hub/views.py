"""Views for the member hub."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any, cast

from django.utils import timezone as dj_timezone

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.views import redirect_to_login
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Prefetch, Q, QuerySet
from django.forms import BaseInlineFormSet, BaseModelFormSet
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST, require_http_methods

from billing.exceptions import NoPaymentMethodError, TabLimitExceededError, TabLockedError
from billing.models import BillingSettings, Tab, TabCharge
from classes.models import Category, ClassOffering
from core.models import HeroCropMixin, SiteConfiguration
from hub.view_as import ALL_ROLES, SESSION_ROLE_KEY, fog_admin_required
from hub.forms import (
    BetaFeedbackForm,
    CalendarFeedFormSet,
    DeleteAccountConfirmForm,
    DiscordGuildEmojiFormSet,
    GuildEditForm,
    GuildRoleFormSet,
    MeetingItemProposalForm,
    MemberAdminEditForm,
    MemberCapabilitiesForm,
    MemberContactForm,
    MemberContactFormSet,
    MemberSkillForm,
    OrgInfoPageForm,
    ProfileSettingsForm,
    ReleaseAnnouncementForm,
    ScheduledJobStateFormSet,
    SiteSettingsForm,
    SkillSuggestionForm,
    SlideshowSlideFormSet,
    SlideshowZoneFormSet,
    TourSettingsForm,
    TourStateForm,
    VotePreferenceForm,
)
from hub.toast import trigger_toast
from membership.cycle import get_cycle_context
from membership.vote_calculator import compute_live_standings, compute_new_votes_since
from membership.models import (
    AdminCapability,
    CommunityEvent,
    FundingSnapshot,
    Guild,
    HelpCategory,
    Meeting,
    MeetingItemProposal,
    Member,
    MemberContact,
    OrgInfoPage,
    Skill,
    SkillCategory,
    SpaceRequestQuerySet,
    VotePreference,
    WikiArticle,
)
from membership.ical import ical_escape
from membership.permissions import can_edit_category as _can_edit_category
from membership.permissions import can_edit_class as _can_edit_offering
from membership.permissions import can_edit_guild as _can_edit_guild
from membership.permissions import can_manage_orientations as _can_manage_orientations
from membership.permissions import can_propose_to_meeting as _can_propose_to_meeting
from membership.services.account_deletion import delete_own_account

logger = logging.getLogger("hub")


def _get_hub_context(request: HttpRequest) -> dict[str, Any]:
    """Build common sidebar context for all hub pages."""
    # Match hub_sidebar: inactive guilds are unlisted (direct link only).
    guilds = Guild.objects.filter(is_active=True).order_by("name")
    initials = ""
    photo_url = ""
    show_welcome_modal = False
    if request.user.is_authenticated:
        member: Member | None = getattr(request.user, "member", None)
        if member is not None:
            initials = member.initials
            if member.profile_photo:
                photo_url = member.profile_photo.url
            # First-login nudge: brand-new members who haven't customized anything and
            # haven't dismissed it yet. Established members are never shown it (no backfill).
            show_welcome_modal = member.welcome_dismissed_at is None and not member.has_started_profile
    return {
        "guilds": guilds,
        "user_initials": initials,
        "user_profile_photo_url": photo_url,
        "show_welcome_modal": show_welcome_modal,
    }


def _get_member(request: HttpRequest) -> Member | None:
    """Get the Member for the current user, or None.

    Anonymous-safe: an unauthenticated request (e.g. the public ``event_detail`` page) has no
    ``member`` on its user, so this returns None rather than raising.
    """
    member: Member | None = getattr(request.user, "member", None)
    return member


@login_required
@require_POST
def welcome_dismiss(request: HttpRequest) -> HttpResponse:
    """Dismiss the first-login 'set up your profile' welcome modal.

    Both modal buttons POST here so the dismissal always sticks server-side.
    "Set up my profile" (destination=profile) stamps and lands the member on their
    profile settings; "Maybe later" stamps and returns them to where they were.
    """
    member = _get_member(request)
    if member is not None:
        member.dismiss_welcome()
    if request.POST.get("destination") == "profile":
        return redirect(f"{reverse('hub_user_settings')}?tab=profile")
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect("hub_community_calendar")


@login_required
@require_POST
def tour_state(request: HttpRequest, tour_key: str) -> HttpResponse:
    """Record how a guided tour ended for this user (Spec C §5): completed or dismissed.

    The offer card's *No thanks* and the tour runtime's ``onDestroyStarted`` hook both
    POST here — there is deliberately no separate offer-dismiss endpoint. Unknown tour
    → 404; bad status → 400 with the form errors. ``mark_dismissed`` is a no-op on a
    completed row (completed is sticky), so a re-run Esc'd halfway never downgrades.
    No toast — tour endings are self-evident on screen.
    """
    from core.models import TourState

    form = TourStateForm(data={"tour_key": tour_key, "status": request.POST.get("status", "")})
    if not form.is_valid():
        if "tour_key" in form.errors:
            raise Http404("Unknown tour.")
        return JsonResponse({"errors": form.errors}, status=400)
    user = cast(User, request.user)
    if form.cleaned_data["status"] == "completed":
        TourState.objects.mark_completed(user, tour_key)
    else:
        TourState.objects.mark_dismissed(user, tour_key)
    return HttpResponse(status=204)


@login_required
@require_POST
def onboarding_dismiss(request: HttpRequest) -> HttpResponse:
    """Dismiss the home "Get started" onboarding checklist card (HTMX; empty 200 + toast).

    Returns an empty **200** body so the card's ``hx-swap="outerHTML"`` removes it — a 204
    would run no swap and leave the card in place. Sticky: the model records the dismissal so
    it never comes back. No dead end — the toast names where the member can still finish setup.
    """
    member = _get_member(request)
    if member is not None:
        member.dismiss_onboarding()
    response = HttpResponse("")
    trigger_toast(
        response,
        "You can finish setup anytime — Settings → Guilds, your profile, and the voting page.",
        "info",
    )
    return response


@login_required
def guild_voting(request: HttpRequest) -> HttpResponse:
    """Guild voting page — members submit or update their persistent guild preferences."""
    member = _get_member(request)
    ctx = _get_hub_context(request)
    ctx["active_tab"] = "overview"  # the everyone-facing first tab of the Voting surface
    cycle_ctx = get_cycle_context()

    preference: VotePreference | None = None
    if member is not None:
        preference = getattr(member, "vote_preference", None)

    latest_snapshot = FundingSnapshot.objects.order_by("-snapshot_at").first()
    since = latest_snapshot.snapshot_at if latest_snapshot else None

    # Live vote standings: tally points from all current VotePreference records
    vote_standings = compute_live_standings()
    new_vote_standings = compute_new_votes_since(since)

    if member is None:
        messages.info(request, "Your account is not linked to a membership.")
        return render(
            request,
            "hub/guild_voting.html",
            {
                **ctx,
                **cycle_ctx,
                "member": None,
                "form": None,
                "preference": None,
                "latest_snapshot": latest_snapshot,
                "vote_standings": vote_standings,
                "new_vote_standings": new_vote_standings,
            },
        )

    if request.method == "POST":
        form = VotePreferenceForm(request.POST)
        if form.is_valid():
            VotePreference.objects.cast_ballot(
                member,
                guild_1st=form.cleaned_data["guild_1st"],
                guild_2nd=form.cleaned_data["guild_2nd"],
                guild_3rd=form.cleaned_data["guild_3rd"],
            )
            action = "updated" if preference else "submitted"
            messages.success(request, f"Your vote has been {action}.")
            return redirect("hub_guild_voting")
    else:
        initial: dict[str, Any] = {}
        if preference is not None:
            initial = {
                "guild_1st": preference.guild_1st,
                "guild_2nd": preference.guild_2nd,
                "guild_3rd": preference.guild_3rd,
            }
        form = VotePreferenceForm(initial=initial)

    return render(
        request,
        "hub/guild_voting.html",
        {
            **ctx,
            **cycle_ctx,
            "member": member,
            "form": form,
            "preference": preference,
            "latest_snapshot": latest_snapshot,
            "vote_standings": vote_standings,
            "new_vote_standings": new_vote_standings,
        },
    )


def member_directory(request: HttpRequest) -> HttpResponse:
    """Member directory page — lists all active members.

    Sign-in is required unless the "Public member directory" site setting is on
    (the original open-directory behavior); anonymous visitors are otherwise sent
    through the same login redirect ``@login_required`` would issue.

    Prefetches each member's primary allauth ``EmailAddress`` so
    ``Member.primary_email`` stays O(1) per member instead of firing a query
    on every template access. See the three-email-store note on
    ``Member.primary_email`` and docs/superpowers/specs/2026-04-07-user-email-aliases-design.md.
    """
    if not request.user.is_authenticated and not SiteConfiguration.load().member_directory_public:
        return redirect_to_login(request.get_full_path())
    ctx = _get_hub_context(request)
    current_member = _get_member(request)
    view_as = getattr(request, "view_as", None)
    is_admin = view_as is not None and view_as.is_admin
    # Admins see every active member (a web-only affordance); everyone else gets the
    # shared directory-privacy filter (MemberQuerySet.directory_visible).
    if is_admin:
        member_qs = Member.objects.filter(status=Member.Status.ACTIVE).distinct()
    else:
        member_qs = Member.objects.directory_visible()
    skill_slug = request.GET.get("skill", "")
    if skill_slug:
        member_qs = member_qs.with_skill(skill_slug)
    commissions_only = request.GET.get("commissions") == "1"
    if commissions_only:
        member_qs = member_qs.open_for_commissions()
    query = request.GET.get("q", "").strip()
    if query:
        member_qs = member_qs.search_skills(query)
    members = (
        member_qs.select_related("membership_plan", "user")
        .prefetch_related(
            Prefetch(
                "user__emailaddress_set",
                queryset=EmailAddress.objects.filter(primary=True),
                to_attr="_primary_emailaddresses",
            ),
            "skills__skill__category",
            Prefetch(
                "contacts",
                queryset=MemberContact.objects.filter(show_in_directory=True),
                to_attr="visible_contacts",
            ),
        )
        .order_by("full_legal_name")
    )
    return render(
        request,
        "hub/member_directory.html",
        {
            **ctx,
            "members": members,
            "current_member": current_member,
            "is_admin": is_admin,
            "skill_categories": _skill_categories_with_approved(),
            "selected_skill": skill_slug,
            "commissions_only": commissions_only,
            "query": query,
        },
    )


@login_required
def snapshot_history(request: HttpRequest) -> HttpResponse:
    """Funding snapshot history page — lists all past snapshots."""
    ctx = _get_hub_context(request)
    snapshots = FundingSnapshot.objects.order_by("-snapshot_at")
    return render(request, "hub/snapshot_history.html", {**ctx, "snapshots": snapshots})


@login_required
def snapshot_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Funding snapshot detail page — shows full results for a single snapshot."""
    ctx = _get_hub_context(request)
    snapshot = get_object_or_404(FundingSnapshot, pk=pk)
    return render(request, "hub/snapshot_detail.html", {**ctx, "snapshot": snapshot})


# Edit-permission helpers now live in membership/permissions.py (the single
# source of truth) and are imported above as _can_edit_guild / _can_edit_offering
# / _can_edit_category. Guild-lead authority comes solely from the
# Guild.guild_lead FK — no role or staff flag required.


@login_required
@require_POST
def hub_hero_adjust(request: HttpRequest) -> JsonResponse:
    """AJAX endpoint to update hero crop fields for Guild, Category, or ClassOffering."""
    try:
        data = json.loads(request.body)
        ct_id = int(data["content_type_id"])
        object_id = int(data["object_id"])
        crop = data["crop"]  # {"x": int, "y": int, "w": int, "h": int}
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return JsonResponse({"error": "Invalid request data"}, status=400)

    ct = get_object_or_404(ContentType, pk=ct_id)
    model_class = ct.model_class()
    if model_class not in [Guild, Category, ClassOffering, OrgInfoPage]:
        return JsonResponse({"error": "Unsupported model"}, status=400)

    obj = get_object_or_404(model_class, pk=object_id)

    # Permission checks
    allowed = False
    if isinstance(obj, Guild):
        allowed = _can_edit_guild(request, obj)
    elif isinstance(obj, ClassOffering):
        allowed = _can_edit_offering(request, obj)
    elif isinstance(obj, Category):
        allowed = _can_edit_category(request, obj)
    elif isinstance(obj, OrgInfoPage):
        allowed = _viewing_as_admin(request)

    if not allowed:
        return JsonResponse({"error": "Forbidden"}, status=403)

    # Update crop fields
    hero_obj = cast(HeroCropMixin, obj)
    hero_obj.hero_crop_x = int(crop["x"])
    hero_obj.hero_crop_y = int(crop["y"])
    hero_obj.hero_crop_w = int(crop.get("w") or 0)
    hero_obj.hero_crop_h = int(crop.get("h") or 0)
    hero_obj.save(update_fields=["hero_crop_x", "hero_crop_y", "hero_crop_w", "hero_crop_h"])

    return JsonResponse(
        {
            "status": "ok",
            "object_position": hero_obj.hero_object_position,
        }
    )


def _guild_pulse(guild: "Guild", limit: int = 6) -> list[dict[str, Any]]:
    """A short 'what's happening' feed for a guild: recent announcements and new classes.

    Synthesized from existing rows (no new activity table) and merged newest-first.
    Deliberately carries NO membership-derived lines: who follows a guild is a
    notification preference, not social content, so new subscriptions are never
    broadcast here by name.
    """
    from classes.models import ClassOffering

    items: list[dict[str, Any]] = []
    for announcement in guild.announcements.published().active().order_by("-published_at")[:limit]:
        items.append({"when": announcement.published_at, "text": f"Announcement: {announcement.title}"})
    classes = (
        ClassOffering.objects.filter(category__guild=guild, status=ClassOffering.Status.PUBLISHED)
        .exclude(published_at=None)
        .order_by("-published_at")[:limit]
    )
    for offering in classes:
        items.append({"when": offering.published_at, "text": f"New class: {offering.title}"})
    items.sort(key=lambda item: item["when"], reverse=True)
    return items[:limit]


def guild_detail_redirect(request: HttpRequest, pk: int) -> HttpResponse:
    """301 an old numeric guild URL (/guilds/<id>/) to its slug URL — keeps shared links alive."""
    guild = get_object_or_404(Guild, pk=pk)
    return redirect("hub_guild_detail", slug=guild.slug, permanent=True)


def guild_directory(request: HttpRequest) -> HttpResponse:
    """Public guild directory — featured guilds first, then alphabetical.

    Renders in guest chrome on the guilds surface (guilds.pastlives.app); the
    sidebar context is ignored there but keeps parity on the members host.
    """
    guilds = Guild.objects.directory().select_related("guild_lead").annotate(member_total=Count("memberships"))
    ctx = _get_hub_context(request)
    hero_stats = {
        "guilds": len(guilds),
        "members": (
            Member.objects.filter(guild_memberships__guild__in=guilds, status=Member.Status.ACTIVE).distinct().count()
        ),
        "classes": ClassOffering.objects.bookable().count(),
    }
    return render(request, "guilds/directory.html", {**ctx, "guilds": guilds, "hero_stats": hero_stats})


def guild_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """Guild detail page — shows about text, active products, and cart interface."""
    from billing.forms import CONTEXT_MEMBER_GUILD_PAGE, TabItemForm, build_product_split_formset
    from billing.models import Product

    guild = get_object_or_404(
        Guild.objects.select_related("featured_class__instructor").prefetch_related(
            "products__splits__guild",
            # Feeds Guild.studio_hours_display() + next_meeting_occurrence() from one cache (no N+1).
            "events",
        ),
        slug=slug,
    )
    ctx = _get_hub_context(request)
    products = guild.products.order_by("name").prefetch_related("splits__guild")
    member = _get_member(request)

    tab: Tab | None = None
    if member is not None:
        tab, _created = Tab.objects.get_or_create(member=member)

    eyop_form = TabItemForm(context=CONTEXT_MEMBER_GUILD_PAGE, user=request.user, guild=guild)

    # Editor affordances never render on the guest guilds surface: a logged-in
    # lead there would otherwise see Edit / Adjust / product-admin buttons that
    # 404 (the editor endpoints aren't in the guilds allowlist). Leads edit on FOG.
    can_edit_this_guild = _can_edit_guild(request, guild) and getattr(request, "surface", "members") != "guilds"
    product_form = None
    product_splits_formset = None
    all_guilds = None
    if can_edit_this_guild:
        from billing.forms import ProductForm

        product_form = ProductForm()
        product_splits_formset = build_product_split_formset(instance=Product())
        all_guilds = Guild.objects.filter(is_active=True).order_by("name")

    gallery_images = guild.gallery_images.all()
    faq_items = guild.faq_items.all()
    # Prepend the always-present, virtual "[Guild] Classes" link so it leads the Links card.
    # Only here in the detail view — never in the edit Links formset (which iterates
    # guild.links directly), so it's structurally non-editable and non-deletable.
    guilds_surface = getattr(request, "surface", "members") == "guilds"
    links = [guild.classes_link(guilds_surface=guilds_surface), *guild.links.all()]
    announcements = guild.announcements.published().active()[:5]
    # The §6.4 Meetings tab: the guild's soonest upcoming meeting + last 5 approved minutes.
    next_guild_meeting = (
        Meeting.objects.for_scope(guild).upcoming().select_related("guild").annotate(topic_count=Count("items")).first()
    )
    # Everyone who may propose sees the button — editors included; their submissions
    # land in the same pending-proposals queue.
    can_propose_next = next_guild_meeting is not None and _can_propose_to_meeting(request, next_guild_meeting)
    propose_form = MeetingItemProposalForm(auto_id="propose-item-%s") if can_propose_next else None
    pending_proposal_count = (
        next_guild_meeting.proposals.filter(state=MeetingItemProposal.State.PENDING).count()
        if can_edit_this_guild and next_guild_meeting is not None
        else 0
    )
    recent_minutes = Meeting.objects.for_scope(guild).approved().select_related("guild")[:5]
    # Not-yet-approved meetings in the archive window (past + undated), so a published or
    # slipped meeting stays reachable from its guild page: members see published ones,
    # editors also see slipped drafts (mirrors the Meetings-home needs-attention scope).
    awaiting_window = Meeting.objects.for_scope(guild).archive().select_related("guild")
    awaiting_minutes = (
        awaiting_window.exclude(status=Meeting.Status.APPROVED)
        if can_edit_this_guild
        else awaiting_window.filter(status=Meeting.Status.PUBLISHED)
    )[:3]
    # Gate the roster on the viewer, not just the guild opt-in: an anonymous guest
    # must never see member names/avatars (the count-only chip lives in the hero).
    roster = guild.roster_members() if guild.show_members and member is not None else None
    is_member_of_guild = member is not None and guild.memberships.filter(member=member).exists()

    from classes.models import ClassOffering

    guild_classes = ClassOffering.objects.filter(category__guild=guild)
    member_count = guild.memberships.count()
    class_count = guild_classes.filter(status=ClassOffering.Status.PUBLISHED).count()
    upcoming_classes = guild_classes.bookable().select_related("instructor")[:4]
    calendar = _get_calendar_context(request, guild=guild)
    calendar["events_url"] = reverse("hub_guild_calendar_events", args=[guild.pk])
    # No default_filters_json: the guild calendar persists *disabled* filters
    # client-side (like the Community Calendar), so every legend key — including
    # this guild's own classes key — defaults to visible without a seeded list.
    pulse = _guild_pulse(guild)

    from membership.models import GuildOrientationSettings

    orientation = GuildOrientationSettings.objects.filter(guild=guild).first()
    orientation_booking = member.active_orientation_for(guild) if member is not None else None
    is_oriented = member.is_oriented_for(guild) if member is not None else False
    show_orientation = orientation is not None and orientation.is_enabled
    orientation_slots = (
        # bookable() (not upcoming()) so a departed orienter's surviving personal slot
        # never reappears in the member list once its booking is declined or cancelled.
        list(guild.orientation_slots.bookable().select_related("orienter").order_by("starts_at")[:30])
        if orientation is not None
        and show_orientation
        and not is_oriented
        and orientation_booking is None
        and not orientation.is_closed
        else []
    )
    # Guild-wide duplicate-first-name disambiguation ("with Bob P.") — computed here
    # because only a guild-wide view can see the collision; with_label stays cheap.
    orienter_labels = guild.orienter_name_labels() if orientation_slots else {}
    for slot in orientation_slots:
        if slot.orienter_id is None:
            slot.with_display = ""
        else:
            label = orienter_labels.get(slot.orienter_id, "")
            slot.with_display = f"with {label}" if label else slot.with_label

    from hub.forms import OrientationCustomRequestForm

    custom_request_form = OrientationCustomRequestForm()

    guild_ct = ContentType.objects.get_for_model(Guild)

    return render(
        request,
        "hub/guild_detail.html",
        {
            **ctx,
            "guild": guild,
            "guild_ct_id": guild_ct.pk,
            "products": products,
            "tab": tab,
            "eyop_form": eyop_form,
            "can_edit_this_guild": can_edit_this_guild,
            "product_form": product_form,
            "product_splits_formset": product_splits_formset,
            "all_guilds": all_guilds,
            "gallery_images": gallery_images,
            "faq_items": faq_items,
            "links": links,
            "announcements": announcements,
            "next_guild_meeting": next_guild_meeting,
            "can_propose_next": can_propose_next,
            "propose_form": propose_form,
            "pending_proposal_count": pending_proposal_count,
            "recent_minutes": recent_minutes,
            "awaiting_minutes": awaiting_minutes,
            "roster": roster,
            "member": member,
            "is_member_of_guild": is_member_of_guild,
            "member_count": member_count,
            "class_count": class_count,
            "upcoming_classes": upcoming_classes,
            "calendar": calendar,
            "pulse": pulse,
            "orientation": orientation,
            "orientation_booking": orientation_booking,
            "is_oriented": is_oriented,
            "show_orientation": show_orientation,
            "orientation_slots": orientation_slots,
            "custom_request_form": custom_request_form,
        },
    )


def _require_can_edit_guild(request: HttpRequest, guild: Guild) -> HttpResponse | None:
    """Return a 403 response if the user cannot edit ``guild``, else None."""
    if not _can_edit_guild(request, guild):
        return HttpResponse("Forbidden", status=403)
    return None


def _require_can_manage_orientations(request: HttpRequest, guild: Guild) -> HttpResponse | None:
    """Return a 403 response if the user cannot run ``guild``'s orientations, else None.

    Wider than ``_require_can_edit_guild``: it also lets the guild's designated
    orienters through. Use it for the orientation operational surfaces (dashboard,
    booking responses, availability) — not for guild-page or class editing.
    """
    if not _can_manage_orientations(request, guild):
        return HttpResponse("Forbidden", status=403)
    return None


def _viewing_as_admin(request: HttpRequest) -> bool:
    """True when the request's effective (``view_as``-aware) role is admin."""
    view_as = getattr(request, "view_as", None)
    return view_as is not None and view_as.is_admin


def _require_admin(request: HttpRequest) -> HttpResponse | None:
    """Return a 403 response if the user is not viewing as an admin, else None.

    Mirrors the inline ``view_as.is_admin`` gate used by the directory/admin surfaces,
    so the gate and the template's ``events_can_manage`` flag stay in lock-step.
    """
    if not _viewing_as_admin(request):
        return HttpResponse("Forbidden", status=403)
    return None


def _google_sync_enabled() -> bool:
    """True only when Google Calendar push is on at BOTH gates: the ``GOOGLE_CALENDAR_SYNC_ENABLED``
    env master switch (credentials present on the server) AND the admin runtime toggle in Site
    Settings. Drives the sync-state badges (Screens C/E) — when False they stay hidden entirely,
    so spaces not using Google never see a "pending forever" badge.
    """
    env_on = bool(getattr(settings, "GOOGLE_CALENDAR_SYNC_ENABLED", False))
    return env_on and SiteConfiguration.load().google_calendar_sync_enabled


def _guild_edit_context(
    request: HttpRequest,
    guild: Guild,
    *,
    form: GuildEditForm | None = None,
    orientation_form: Any = None,
    emails_form: Any = None,
    rule_formset: Any = None,
    guild_rule_formset: Any = None,
    hours_scope_member: Any = None,
    studio_hours_formset: Any = None,
    mailing_list_formset: Any = None,
) -> dict[str, Any]:
    """Build the full render context for the guild edit page (all nine in-page tabs).

    Shared by ``guild_edit`` (GET + invalid-POST re-render) and ``guild_orientation_edit``'s
    invalid-POST re-render, so the inlined Orientations / Meeting Notes / Events tabs always
    have their data. Pass a bound ``form`` / ``orientation_form`` / ``rule_formset`` to surface
    validation errors; unbound defaults are built otherwise. Orientation, FAQ, and Links each
    save via their own endpoint (the FAQ/Links idiom), so their formsets are unbound here.
    """
    from hub.forms import (
        GuildEmailsForm,
        GuildFAQItemFormSet,
        GuildLinkFormSet,
        GuildMailingListFormSet,
        GuildOrientationSettingsForm,
        GuildStaffAddForm,
        OrientationAvailabilityFormSet,
        OrientationSlotForm,
        StudioHoursFormSet,
    )
    from membership.models import GuildOrientationSettings, Member
    from membership.permissions import can_edit_orienter_hours

    settings_obj, _ = GuildOrientationSettings.objects.get_or_create(guild=guild)
    ctx = _get_hub_context(request)
    recipients = guild.announcement_recipients()

    # ── Orientations tab: per-orienter hours scope + overview + slots ─────────
    viewer = _get_member(request)
    can_edit_others_hours = can_edit_orienter_hours(request, guild, None)
    hours_scope = hours_scope_member
    if hours_scope is None:
        hours_scope = viewer
        orienter_param = request.GET.get("orienter", "")
        # ?orienter=<pk> scopes the My Hours editor to that person (leads/admins only);
        # without permission, or with a bogus pk, the param is ignored — self it is.
        if orienter_param.isdigit() and can_edit_others_hours:
            candidate = Member.objects.filter(pk=int(orienter_param)).first()
            if candidate is not None:
                hours_scope = candidate
    hours_editing_other = hours_scope is not None and (viewer is None or hours_scope.pk != viewer.pk)
    personal_rules_qs = (
        guild.orientation_rules.for_orienter(hours_scope) if hours_scope is not None else guild.orientation_rules.none()
    )
    leadership = guild.leadership_members()
    leadership_ids = {m.pk for m in leadership}
    # An admin/officer who is not on this guild's leadership gets no self-scoped My Hours
    # card — their personal rules would never generate slots (the save 403s that scope
    # too). Edit-on-behalf (?orienter=, incl. Former Staff cleanup) still renders.
    show_my_hours_card = hours_editing_other or (viewer is not None and viewer.pk in leadership_ids)
    rules_by_orienter: dict[int, list[Any]] = {}
    orphan_orienters: dict[int, Any] = {}
    for rule in guild.orientation_rules.exclude(orienter=None).select_related("orienter"):
        orienter_id = rule.orienter_id
        assert orienter_id is not None  # guaranteed by the exclude(orienter=None) filter
        rules_by_orienter.setdefault(orienter_id, []).append(rule)
        if orienter_id not in leadership_ids:
            orphan_orienters[orienter_id] = rule.orienter
    orienter_overview = [(m, rules_by_orienter.get(m.pk, [])) for m in leadership]
    former_staff_overview = sorted(
        ((m, rules_by_orienter[pk]) for pk, m in orphan_orienters.items()),
        key=lambda pair: pair[0].display_name.lower(),
    )
    guild_rules_qs = guild.orientation_rules.guild_level()
    has_guild_rules = guild_rules_qs.exists()
    upcoming_slots_admin = list(
        guild.orientation_slots.upcoming()
        .with_active_booking_count()  # one aggregate, not a COUNT per row — the list is unbounded
        .select_related("orienter")
        .order_by("starts_at")
    )

    return {
        **ctx,
        "guild": guild,
        "announcement_recipient_count": len(recipients),
        "announcement_recipient_emails": sorted(user.email for user, _reason in recipients),
        "form": form if form is not None else GuildEditForm(instance=guild),
        "faq_formset": GuildFAQItemFormSet(instance=guild, prefix="faq"),
        "link_formset": GuildLinkFormSet(instance=guild, prefix="links"),
        "mailing_list_formset": (
            mailing_list_formset
            if mailing_list_formset is not None
            else GuildMailingListFormSet(instance=guild, prefix="mailing_list")
        ),
        "staff_by_member": guild.staff_by_member(),
        "staff_add_form": GuildStaffAddForm(member_queryset=_staff_candidates(guild), guild=guild),
        "is_admin": _viewing_as_admin(request),
        "google_sync_enabled": _google_sync_enabled(),
        "notes": guild.meeting_notes.prefetch_related("attachments"),
        # Studio hours have their own Meetings-tab editor, so the Events tab lists only meetings.
        "events": guild.events.meetings().upcoming().select_related("guild"),
        "studio_hours_formset": (
            studio_hours_formset
            if studio_hours_formset is not None
            else StudioHoursFormSet(
                queryset=guild.events.studio_hours(), prefix="studio_hours", form_kwargs={"guild": guild}
            )
        ),
        "orientation_form": (
            orientation_form if orientation_form is not None else GuildOrientationSettingsForm(instance=settings_obj)
        ),
        "emails_form": emails_form if emails_form is not None else GuildEmailsForm(instance=settings_obj),
        "rule_formset": (
            rule_formset
            if rule_formset is not None
            else OrientationAvailabilityFormSet(instance=guild, prefix="rules", queryset=personal_rules_qs)
        ),
        "hours_scope_member": hours_scope,
        "hours_editing_other": hours_editing_other,
        "show_my_hours_card": show_my_hours_card,
        "can_edit_others_hours": can_edit_others_hours,
        "orienter_overview": orienter_overview,
        "former_staff_overview": former_staff_overview,
        "guild_rule_formset": (
            guild_rule_formset
            if guild_rule_formset is not None
            else (
                OrientationAvailabilityFormSet(instance=guild, prefix="guild_rules", queryset=guild_rules_qs)
                if has_guild_rules and can_edit_others_hours
                else None
            )
        ),
        "guild_rules_readonly": (list(guild_rules_qs) if has_guild_rules and not can_edit_others_hours else []),
        "upcoming_slots_admin": upcoming_slots_admin,
        "slot_form": OrientationSlotForm(guild=guild, acting_member=viewer, lock_to_acting=not can_edit_others_hours),
        "slot_form_locked": not can_edit_others_hours,
    }


@login_required
def guild_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Full guild edit page (GET) + handler (POST). Admin, officer, or this guild's lead/staff only.

    Orientations, Meeting Notes, and Events are in-page tabs here (see ``_guild_edit_context``),
    not separate pages. Each non-Basic/Meetings/Images section saves via its own endpoint, so the
    main form below only covers Basic/Meetings/Images.
    """
    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden

    if request.method == "POST":
        form = GuildEditForm(request.POST, request.FILES, instance=guild)
        if form.is_valid():
            form.save()
            guild.add_gallery_images(request.FILES.getlist("gallery_images"))
            messages.success(request, "Guild page updated.")
            if request.POST.get("after") == "edit":
                return redirect("hub_guild_edit", pk=guild.pk)
            return redirect("hub_guild_detail", slug=guild.slug)
        return render(request, "hub/guild_edit.html", _guild_edit_context(request, guild, form=form))

    from core.tours import tour_offer_context

    return render(
        request,
        "hub/guild_edit.html",
        {**_guild_edit_context(request, guild), **tour_offer_context(request, "guild-lead")},
    )


@login_required
def guild_qr_download(request: HttpRequest, pk: int, fmt: str) -> HttpResponse:
    """Download this guild's vanity-URL QR code as SVG (default) or PNG (editor-gated)."""
    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden
    if fmt == "svg":
        resp = HttpResponse(guild.qr_svg(), content_type="image/svg+xml")
    elif fmt == "png":
        resp = HttpResponse(guild.qr_png_bytes(), content_type="image/png")
    else:
        raise Http404
    resp["Content-Disposition"] = f'attachment; filename="{guild.slug}-qr.{fmt}"'
    return resp


@login_required
def guild_flyer(request: HttpRequest, pk: int) -> HttpResponse:
    """Print-optimized one-page flyer for a guild (leads → Print → Save as PDF)."""
    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden
    return render(request, "hub/guild_flyer.html", {"guild": guild, "qr_svg": guild.qr_svg()})


@login_required
@require_POST
def guild_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Soft-delete a guild (admin only). Hides it everywhere; its data and relations are kept."""
    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_admin(request)
    if forbidden is not None:
        return forbidden
    guild.soft_delete()
    messages.success(request, f"“{guild.name}” has been deleted.")
    return redirect("home")


@login_required
def guild_orientation_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Save handler for the Orientations tab's settings form (recurring hours save separately).

    The editor itself is an in-page tab on ``guild_edit``, so a GET just sends the viewer there.
    A POST validates the settings, saves, regenerates slots (seat/duration changes affect them),
    and redirects back to the tab; an invalid POST re-renders the full guild edit page with the
    settings form's errors. Recurring hours save through their own form
    (:func:`guild_orientation_hours_save`). Open to anyone who may manage the guild's orientations
    (lead, admin, or staff).
    """
    from hub.forms import GuildOrientationSettingsForm
    from membership.models import GuildOrientationSettings

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_manage_orientations(request, guild)
    if forbidden is not None:
        return forbidden
    orientations_tab = f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=orientations"
    if request.method != "POST":
        return redirect(orientations_tab)

    settings_obj, _ = GuildOrientationSettings.objects.get_or_create(guild=guild)
    form = GuildOrientationSettingsForm(request.POST, instance=settings_obj)
    if form.is_valid():
        form.save()
        # Materialize bookable slots now so seat/duration changes show up immediately —
        # don't make the editor wait for the nightly generation cron.
        from membership import orientations

        orientations.generate_slots(guild=guild)
        messages.success(request, "Orientation settings updated.")
        return redirect(orientations_tab)

    ctx = _guild_edit_context(request, guild, orientation_form=form)
    ctx["active_tab"] = "orientations"
    return render(request, "hub/guild_edit.html", ctx)


def _hours_save_message(*, guild: Guild, guild_scope: bool, deleted_rules: int, removed: int, kept: int) -> str:
    """The success flash for an hours save — with real retirement counts on a delete."""
    if not deleted_rules:
        return "Hours saved."
    if guild_scope and not guild.orientation_rules.guild_level().exists():
        return (
            "Shared hours deleted. From now on recurring hours are personal. "
            "Use an Any orienter one-off slot for shared coverage."
        )
    parts = [
        "Hours deleted.",
        f"Removed {removed} upcoming open slot{'' if removed == 1 else 's'}.",
    ]
    if kept:
        pronoun = "it" if kept == 1 else "them"
        parts.append(
            f"{kept} booked slot{'' if kept == 1 else 's'} kept. Cancel {pronoun} from the Upcoming Slots card."
        )
    return " ".join(parts)


@login_required
@require_POST
def guild_orientation_hours_save(request: HttpRequest, pk: int) -> HttpResponse:
    """Save one scope of recurring orientation hours from the Orientations tab.

    The posted hidden ``orienter_scope`` field is read FIRST and selects which formset
    prefix binds — a member pk scopes the personal ``rules`` prefix to that person's
    rows; an empty scope binds the legacy ``guild_rules`` prefix to the guild-level
    rows. Binding the wrong prefix against a mismatched management form is a crash, not
    a validation error, so scope selection precedes any formset construction. Gate is
    ``can_edit_orienter_hours`` (own hours: any orientation manager; someone else's or
    the guild rows: lead/admin). Deleted rows retire via ``retire_rule`` (removing their
    future open slots), new rows are stamped with the scope's orienter, and saved hours
    materialize slots immediately. An invalid POST re-renders the tab with the scope
    echoed back, so an edit-on-behalf error still shows under the right heading.
    """
    from hub.forms import OrientationAvailabilityFormSet
    from membership import orientations
    from membership.models import Member
    from membership.permissions import can_edit_orienter_hours

    guild = get_object_or_404(Guild, pk=pk)
    scope_raw = request.POST.get("orienter_scope", "")
    target = None
    if scope_raw:
        if not scope_raw.isdigit():
            raise Http404("Unknown orienter scope.")
        target = get_object_or_404(Member, pk=int(scope_raw))
    if not can_edit_orienter_hours(request, guild, target):
        return HttpResponse("Forbidden", status=403)
    if target is not None:
        prefix = "rules"
        queryset = guild.orientation_rules.for_orienter(target)
    else:
        prefix = "guild_rules"
        queryset = guild.orientation_rules.guild_level()
    formset = OrientationAvailabilityFormSet(request.POST, instance=guild, prefix=prefix, queryset=queryset)
    if formset.is_valid():
        deleted_rules, removed, kept = _apply_hours_formset(formset, target=target)
        # Same side effect the combined save had — saved hours materialize slots immediately.
        orientations.generate_slots(guild=guild)
        messages.success(
            request,
            _hours_save_message(
                guild=guild, guild_scope=target is None, deleted_rules=deleted_rules, removed=removed, kept=kept
            ),
        )
        return redirect(f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=orientations")

    if target is not None:
        ctx = _guild_edit_context(request, guild, rule_formset=formset, hours_scope_member=target)
    else:
        ctx = _guild_edit_context(request, guild, guild_rule_formset=formset)
    # The invalid POST lands on the hours-save URL (no ?tab) — keep the Orientations tab open.
    ctx["active_tab"] = "orientations"
    return render(request, "hub/guild_edit.html", ctx)


def _apply_hours_formset(formset: Any, *, target: Any) -> tuple[int, int, int]:
    """Apply a valid hours formset: retire deleted rules, stamp + save the kept rows.

    Returns ``(deleted_rules, open_slots_removed, kept_with_bookings)`` for the flash.
    """
    from membership import orientations

    removed = kept = deleted_rules = 0
    for rule_form in formset.deleted_forms:
        if rule_form.instance.pk:
            rule_removed, rule_kept = orientations.retire_rule(rule_form.instance)
            removed += rule_removed
            kept += rule_kept
            deleted_rules += 1
    for rule in formset.save(commit=False):
        if rule.orienter_id is None and target is not None:
            rule.orienter = target  # scope stamps new rows — orienter is write-once from the UI
        rule.save()
    return deleted_rules, removed, kept


@login_required
@require_POST
def guild_studio_hours_save(request: HttpRequest, pk: int) -> HttpResponse:
    """Save the weekly Studio Hours from their own form on the Studio Hours tab.

    The studio-hours list editor is its own ``<form>`` (outside the main guild form, like the
    FAQ/Links/recurring-hours editors), so it saves independently. Each row is a WEEKLY,
    PUBLIC-targeted ``STUDIO_HOURS`` :class:`CommunityEvent`; the form does the translation.
    Deleted rows are removed from Google *before* the FOG row is gone; saved rows are mirrored to
    the Public calendar best-effort (a Google outage never blocks the save). Editor-gated.
    """
    from hub.forms import StudioHoursFormSet

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden
    formset = StudioHoursFormSet(
        request.POST,
        queryset=guild.events.studio_hours(),
        prefix="studio_hours",
        form_kwargs={"guild": guild},
    )
    if formset.is_valid():
        for form in formset.deleted_forms:
            if form.instance.pk:
                form.instance.remove_from_google()  # best-effort; must run before the row is deleted
                form.instance.remove_from_discord()  # no-op for studio hours; keeps the delete paths parallel
        saved = formset.save()  # creates/updates the kept rows, deletes the flagged ones
        for event in saved:
            event.push_to_google()  # best-effort, gated — mirrors the row to the Public calendar
            event.push_to_discord()  # no-op for studio hours (never a Scheduled Event)
        messages.success(request, "Studio hours saved.")
        return redirect(f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=studio_hours")

    ctx = _guild_edit_context(request, guild, studio_hours_formset=formset)
    return render(request, "hub/guild_edit.html", ctx)


def _staff_candidates(guild: Guild) -> Any:
    """Active members who can be added as guild staff — excludes the guild's lead."""
    from membership.models import Member

    qs = Member.objects.filter(status=Member.Status.ACTIVE)
    if guild.guild_lead_id is not None:
        qs = qs.exclude(pk=guild.guild_lead_id)
    return qs.order_by("full_legal_name")


@login_required
@require_POST
def guild_staff_add(request: HttpRequest, pk: int) -> HttpResponse:
    """POST-only — a lead/admin/staff member assigns another member a guild staff role."""
    from hub.forms import GuildStaffAddForm
    from membership.models import GuildStaffMembership

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden

    form = GuildStaffAddForm(request.POST, member_queryset=_staff_candidates(guild), guild=guild)
    if form.is_valid():
        staff = GuildStaffMembership.objects.create(
            guild=guild,
            member=form.cleaned_data["member"],
            role=form.cleaned_data["role"] or "",
            custom_title=form.cleaned_data["custom_title"],
        )
        messages.success(request, f"{staff.member.display_name} is now {staff.display_title} of {guild.name}.")
    else:
        error_list = form.non_field_errors() or next(iter(form.errors.values()))
        messages.error(request, str(error_list[0]))
    return redirect(f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=staff")


@login_required
@require_POST
def guild_staff_remove(request: HttpRequest, pk: int, staff_pk: int) -> HttpResponse:
    """POST-only — a lead/admin/staff member removes a staff role from this guild."""
    from membership.models import GuildStaffMembership

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden

    staff = get_object_or_404(GuildStaffMembership, pk=staff_pk, guild=guild)
    removed_member = staff.member
    member_name = removed_member.display_name
    title_label = staff.display_title
    staff.delete()
    message = f"{member_name} is no longer {title_label} of {guild.name}."
    # Retire their personal hours ONLY when this was their last leadership row — staff can
    # hold multiple roles (Treasurer + Orienter), and dropping one must not nuke their hours.
    if removed_member.pk not in {m.pk for m in guild.leadership_members()}:
        from membership import orientations

        _removed, booked_remaining = orientations.retire_orienter(guild, removed_member)
        if booked_remaining:
            message += (
                f" They still have {booked_remaining} upcoming booked "
                f"orientation{'' if booked_remaining == 1 else 's'}. Cancel them from the "
                "Upcoming Slots card on the Orientations tab if they won't be run."
            )
    messages.success(request, message)
    return redirect(f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=staff")


@login_required
@require_POST
def guild_orientation_slot_add(request: HttpRequest, pk: int) -> HttpResponse:
    """POST-only — add a one-off orientation slot to this guild. Editors only."""
    from hub.forms import OrientationSlotForm
    from membership.models import OrientationSlot
    from membership.permissions import can_edit_orienter_hours

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_manage_orientations(request, guild)
    if forbidden is not None:
        return forbidden

    # Leads/admins may pick any leadership member (or "Any orienter"); plain staff
    # add slots for themselves only — the form locks and forces the acting member.
    form = OrientationSlotForm(
        request.POST,
        guild=guild,
        acting_member=_get_member(request),
        lock_to_acting=not can_edit_orienter_hours(request, guild, None),
    )
    if form.is_valid():
        slot = form.save(commit=False)
        slot.guild = guild
        slot.source = OrientationSlot.Source.MANUAL
        slot.save()
        messages.success(request, "Orientation slot added.")
    else:
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{field}: {error}")
    return redirect(f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=orientations")


@login_required
@require_POST
def guild_orientation_slot_cancel(request: HttpRequest, pk: int, slot_pk: int) -> HttpResponse:
    """POST-only — cancel a one-off or generated orientation slot (and its bookings). Editors only."""
    from membership import orientations

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_manage_orientations(request, guild)
    if forbidden is not None:
        return forbidden

    slot = get_object_or_404(guild.orientation_slots, pk=slot_pk)
    orientations.cancel_slot(slot, reason=request.POST.get("reason", ""))
    messages.success(request, "Orientation slot cancelled.")
    return redirect(f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=orientations")


@login_required
@require_POST
def orientation_book(request: HttpRequest, slot_pk: int) -> HttpResponse:
    """POST-only — a logged-in member requests an orientation for a slot."""
    from membership import orientations
    from membership.models import OrientationError, OrientationSlot

    slot = get_object_or_404(OrientationSlot, pk=slot_pk)
    member = _get_member(request)
    if member is None:
        messages.error(request, "You need a member profile to book an orientation.")
        return redirect("hub_guild_detail", slug=slot.guild.slug)
    try:
        orientations.request_orientation(slot, member, note=request.POST.get("note", ""))
        messages.success(
            request,
            "Orientation requested! Check your email for the details — it's not official until the guild lead confirms.",
        )
    except OrientationError as exc:
        messages.error(request, str(exc))
    return redirect("hub_guild_detail", slug=slot.guild.slug)


@login_required
@require_POST
def guild_orientation_request_custom(request: HttpRequest, pk: int) -> HttpResponse:
    """POST-only — a member proposes a custom orientation time. Creates a one-off slot
    at that time and books it, reusing the normal request/confirm/email flow."""
    from datetime import timedelta

    from hub.forms import OrientationCustomRequestForm
    from membership import orientations
    from membership.models import GuildOrientationSettings, OrientationError, OrientationSlot

    guild = get_object_or_404(Guild, pk=pk)
    member = _get_member(request)
    if member is None:
        messages.error(request, "You need a member profile to request an orientation.")
        return redirect("hub_guild_detail", slug=guild.slug)
    settings_obj = GuildOrientationSettings.objects.filter(guild=guild).first()
    if settings_obj is None or not settings_obj.is_accepting or not settings_obj.allow_custom_requests:
        messages.error(request, "This guild isn't taking custom orientation requests right now.")
        return redirect("hub_guild_detail", slug=guild.slug)
    form = OrientationCustomRequestForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Pick a valid future time for your orientation.")
        return redirect("hub_guild_detail", slug=guild.slug)
    starts = form.cleaned_data["starts_at"]
    slot = OrientationSlot.objects.create(
        guild=guild,
        starts_at=starts,
        ends_at=starts + timedelta(minutes=settings_obj.default_duration_minutes),
        seats=1,
        location=settings_obj.default_location,
        source=OrientationSlot.Source.MANUAL,
    )
    try:
        orientations.request_orientation(slot, member, note=form.cleaned_data["note"])
    except OrientationError as exc:
        slot.delete()
        messages.error(request, str(exc))
        return redirect("hub_guild_detail", slug=guild.slug)
    messages.success(request, "Your orientation request was sent — the guild lead will confirm a time.")
    return redirect("hub_guild_detail", slug=guild.slug)


@login_required
def orientation_info(request: HttpRequest, pk: int) -> HttpResponse:
    """The guild's orientation info page (what to expect, how to prepare)."""
    from membership.models import GuildOrientationSettings

    guild = get_object_or_404(Guild, pk=pk)
    ctx = _get_hub_context(request)
    return render(
        request,
        "hub/orientation_info.html",
        {**ctx, "guild": guild, "orientation": GuildOrientationSettings.objects.filter(guild=guild).first()},
    )


@login_required
def orientation_respond(request: HttpRequest, booking_pk: int) -> HttpResponse:
    """Lead/admin respond view — confirm, decline, or cancel an orientation request."""
    from membership import orientations
    from membership.models import OrientationBooking

    booking = get_object_or_404(OrientationBooking.objects.select_related("slot", "guild", "member"), pk=booking_pk)
    forbidden = _require_can_manage_orientations(request, booking.guild)
    if forbidden is not None:
        return forbidden

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "confirm":
            # Decision 7: credit the staffer who actually confirmed, not the guild lead.
            orientations.confirm_orientation(booking, oriented_by=_get_member(request))
            messages.success(request, "Orientation confirmed — the member has been emailed.")
        elif action == "decline":
            orientations.decline_orientation(booking, note=request.POST.get("note", ""))
            messages.success(request, "Orientation declined — the member has been notified.")
        return redirect("hub_orientation_respond", booking_pk=booking.pk)

    ctx = _get_hub_context(request)
    return render(request, "hub/orientation_respond.html", {**ctx, "booking": booking})


@login_required
@require_POST
def orientation_lead_cancel(request: HttpRequest, booking_pk: int) -> HttpResponse:
    """POST-only — a lead/admin cancels a confirmed orientation (used by the respond page modal)."""
    from membership import orientations
    from membership.models import OrientationBooking

    booking = get_object_or_404(OrientationBooking.objects.select_related("guild"), pk=booking_pk)
    forbidden = _require_can_manage_orientations(request, booking.guild)
    if forbidden is not None:
        return forbidden
    orientations.cancel_orientation(booking, actor_label="the guild")
    messages.success(request, "Orientation cancelled — the member has been notified.")
    return redirect("hub_orientation_respond", booking_pk=booking.pk)


@login_required
@require_POST
def orientation_cancel_mine(request: HttpRequest, booking_pk: int) -> HttpResponse:
    """POST-only — a member cancels their own orientation booking."""
    from membership import orientations
    from membership.models import OrientationBooking

    booking = get_object_or_404(OrientationBooking, pk=booking_pk)
    member = _get_member(request)
    if member is None or booking.member_id != member.pk:
        return HttpResponse("Forbidden", status=403)
    orientations.cancel_orientation(booking, actor_label=member.display_name)
    messages.success(request, "Your orientation was cancelled.")
    return redirect("hub_guild_detail", slug=booking.guild.slug)


def orientation_action(request: HttpRequest, token: str) -> HttpResponse:
    """No-login landing for email action links (lead confirm/decline, member cancel).

    GET shows a one-click confirmation page (so email-client link prefetch can't
    mutate); POST applies the action. The signed token authorizes exactly one
    action on one booking.
    """
    from django.core.signing import BadSignature

    from membership import orientations
    from membership.models import OrientationBooking

    try:
        booking, action = orientations.read_action_token(token)
    except (BadSignature, OrientationBooking.DoesNotExist):
        return render(request, "hub/orientation_action.html", {"invalid": True}, status=400)
    result = orientations.apply_token_action(booking, action) if request.method == "POST" else None
    return render(request, "hub/orientation_action.html", {"booking": booking, "action": action, "result": result})


def _can_access_orientations(request: HttpRequest) -> bool:
    """True for admins, any guild lead, and any guild staff member — they may view the dashboard."""
    view_as = getattr(request, "view_as", None)
    if view_as is not None and view_as.has_actual("admin"):
        return True
    member = _get_member(request)
    return member is not None and (member.is_guild_lead or member.is_guild_staff)


def _manageable_slots(request: HttpRequest) -> Any:
    """Upcoming slots this request may add members to: all for admins, own-guild for leads/staff."""
    from membership.models import OrientationSlot

    qs = OrientationSlot.objects.upcoming().select_related("guild")
    view_as = getattr(request, "view_as", None)
    if view_as is not None and view_as.has_actual("admin"):
        return qs
    member = _get_member(request)
    if member is None:
        return OrientationSlot.objects.none()
    return qs.filter(Q(guild__guild_lead=member) | Q(guild__staff_memberships__member=member)).distinct()


def _filter_orientations(request: HttpRequest, bookings: Any) -> Any:
    """Apply the dashboard's guild / scope / status / completed / date-range filters."""
    member = _get_member(request)
    guild_filter = request.GET.get("guild", "")
    if guild_filter.isdigit():
        bookings = bookings.filter(guild_id=int(guild_filter))
    if request.GET.get("scope") == "mine" and member is not None:
        bookings = bookings.filter(Q(guild__guild_lead=member) | Q(guild__staff_memberships__member=member)).distinct()
    status_filter = request.GET.get("status", "")
    if status_filter:
        bookings = bookings.filter(status=status_filter)
    completed = request.GET.get("completed", "")
    if completed == "yes":
        bookings = bookings.filter(is_completed=True)
    elif completed == "no":
        bookings = bookings.filter(is_completed=False)
    start = request.GET.get("start", "")
    if start:
        bookings = bookings.filter(slot__starts_at__date__gte=start)
    end = request.GET.get("end", "")
    if end:
        bookings = bookings.filter(slot__starts_at__date__lte=end)
    return bookings


@login_required
def orientations_dashboard(request: HttpRequest) -> HttpResponse:
    """Admin/guild-lead dashboard: upcoming + a sortable, filterable, exportable table."""
    from classes.table import prepare_table

    from hub.forms import OrientationAddMemberForm
    from membership.models import Guild, OrientationBooking

    if not _can_access_orientations(request):
        return HttpResponse("Forbidden", status=403)

    base = OrientationBooking.objects.select_related("slot", "slot__orienter", "guild", "member", "oriented_by")
    table = prepare_table(
        request,
        _filter_orientations(request, base),
        search_fields=["member__full_legal_name", "member__preferred_name", "guild__name"],
        default_sort="slot__starts_at",
        default_dir="desc",
    )
    upcoming = (
        OrientationBooking.objects.upcoming()
        .select_related("slot", "slot__orienter", "guild", "member")
        .order_by("slot__starts_at")[:25]
    )
    view_as = getattr(request, "view_as", None)
    member = _get_member(request)
    # Guilds the member may manage orientations for: lead OR any staff role (co-lead,
    # secretary, treasurer, orienter) — the same set the "Mine" scope filter uses. The
    # "Mark done" action must track this, not lead-only, or staff can't record completions.
    my_leadership_guild_ids = (
        set(
            Guild.objects.filter(Q(guild_lead=member) | Q(staff_memberships__member=member)).values_list(
                "pk", flat=True
            )
        )
        if member is not None
        else set()
    )
    # "Post your hours" nudge — a staffer/lead with zero personal rules anywhere gets a
    # banner linking to each staffed guild's Orientations tab; it disappears with a rule.
    from membership.models import OrientationAvailability

    hours_nudge_guilds: list[Guild] = []
    if (
        member is not None
        and my_leadership_guild_ids
        and not OrientationAvailability.objects.filter(orienter=member).exists()
    ):
        hours_nudge_guilds = list(Guild.objects.filter(pk__in=my_leadership_guild_ids).order_by("name"))
    return render(
        request,
        "hub/orientations_dashboard.html",
        {
            **_get_hub_context(request),
            **table,
            "upcoming": upcoming,
            "hours_nudge_guilds": hours_nudge_guilds,
            "guilds": Guild.objects.filter(is_active=True).order_by("name"),
            "statuses": OrientationBooking.Status.choices,
            "add_member_form": OrientationAddMemberForm(slot_queryset=_manageable_slots(request)),
            "is_admin": view_as is not None and view_as.has_actual("admin"),
            "my_member_id": member.pk if member is not None else None,
            "my_leadership_guild_ids": my_leadership_guild_ids,
            "guild_filter": request.GET.get("guild", ""),
            "scope": request.GET.get("scope", ""),
            "status_filter": request.GET.get("status", ""),
            "completed_filter": request.GET.get("completed", ""),
            "start": request.GET.get("start", ""),
            "end": request.GET.get("end", ""),
        },
    )


@login_required
def orientations_export(request: HttpRequest) -> HttpResponse | StreamingHttpResponse:
    """Download the filtered orientations list as CSV."""
    from membership.models import OrientationBooking
    from membership.orientation_exports import stream_orientations_csv

    if not _can_access_orientations(request):
        return HttpResponse("Forbidden", status=403)
    bookings = _filter_orientations(request, OrientationBooking.objects.all())
    return stream_orientations_csv(bookings)


@login_required
@require_POST
def orientation_add_member(request: HttpRequest) -> HttpResponse:
    """POST-only — admin/lead adds a member to an orientation slot (emails them like a self-booking)."""
    from hub.forms import OrientationAddMemberForm
    from membership import orientations
    from membership.models import OrientationError

    if not _can_access_orientations(request):
        return HttpResponse("Forbidden", status=403)
    form = OrientationAddMemberForm(request.POST, slot_queryset=_manageable_slots(request))
    if form.is_valid():
        try:
            orientations.request_orientation(form.cleaned_data["slot"], form.cleaned_data["member"])
            messages.success(request, f"Added {form.cleaned_data['member'].display_name} — they've been emailed.")
        except OrientationError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, "Couldn't add the member — pick an active member and an upcoming slot.")
    return redirect("hub_orientations_dashboard")


@login_required
@require_POST
def orientation_toggle_completed(request: HttpRequest, booking_pk: int) -> HttpResponse:
    """POST-only — flip an orientation's completed flag (lead of that guild / admin only)."""
    from membership.models import OrientationBooking

    booking = get_object_or_404(OrientationBooking.objects.select_related("guild"), pk=booking_pk)
    forbidden = _require_can_manage_orientations(request, booking.guild)
    if forbidden is not None:
        return forbidden
    if booking.is_completed:
        booking.uncomplete()
    else:
        booking.mark_completed()
    return redirect("hub_orientations_dashboard")


def _surface_product_errors(request: HttpRequest, form: Any, formset: Any) -> None:
    """Flash per-field form + formset errors onto ``request`` as messages."""
    if form.errors:
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{field}: {error}")
    for non_form_error in formset.non_form_errors():
        messages.error(request, f"Splits: {non_form_error}")
    for idx, form_errors in enumerate(formset.errors):
        for field, errors in form_errors.items():
            for error in errors:
                messages.error(request, f"Split row {idx + 1} ({field}): {error}")
    if not (
        form.errors or formset.non_form_errors() or any(formset.errors)
    ):  # pragma: no cover — defensive; is_valid()=False implies at least one error source
        messages.error(request, "Could not add product — see errors below.")


@login_required
@require_POST
def guild_product_create(request: HttpRequest, pk: int) -> HttpResponse:
    """POST-only — create a Product for this guild with its revenue splits.

    Permission: admin / guild_officer / this guild's lead.
    """
    from billing.forms import ProductForm, build_product_split_formset
    from billing.models import Product

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden

    form = ProductForm(data=request.POST)
    formset = build_product_split_formset(data=request.POST, instance=Product())

    if form.is_valid() and formset.is_valid():
        product = form.save(commit=False)
        product.guild = guild  # always bind to the page's guild
        product.save()
        formset.instance = product
        formset.save()
        messages.success(request, f"Added product '{product.name}'.")
    else:
        _surface_product_errors(request, form, formset)

    return redirect("hub_guild_detail", slug=guild.slug)


@login_required
@require_POST
def guild_product_update(request: HttpRequest, pk: int, product_pk: int) -> HttpResponse:
    """POST-only — update a Product (and its revenue splits) for this guild.

    Permission: admin / guild_officer / this guild's lead.
    """
    from billing.forms import ProductForm, build_product_split_formset
    from billing.models import Product

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden

    product = get_object_or_404(Product, pk=product_pk, guild=guild)
    form = ProductForm(data=request.POST, instance=product)
    # The Alpine modal posts fresh splits rows (no PKs) regardless of mode, so
    # we replace the existing splits wholesale rather than diffing them. Build
    # the formset against an unsaved Product instance so it treats every row
    # as new; the actual link to ``product`` happens on save() below.
    formset = build_product_split_formset(data=request.POST, instance=Product())

    if form.is_valid() and formset.is_valid():
        updated = form.save(commit=False)
        updated.guild = guild  # always bind to the page's guild
        updated.save()
        updated.splits.all().delete()
        formset.instance = updated
        formset.save()
        messages.success(request, f"Updated product '{updated.name}'.")
    else:
        _surface_product_errors(request, form, formset)

    return redirect("hub_guild_detail", slug=guild.slug)


@login_required
@require_POST
def guild_product_delete(request: HttpRequest, pk: int, product_pk: int) -> HttpResponse:
    """POST-only — delete a product from this guild. Permission same as guild_edit."""
    from billing.models import Product

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden

    product = get_object_or_404(Product, pk=product_pk, guild=guild)
    name = product.name
    product.delete()
    messages.success(request, f"Deleted product '{name}'.")
    return redirect("hub_guild_detail", slug=guild.slug)


@login_required
@require_POST
def guild_cart_confirm(request: HttpRequest, pk: int) -> HttpResponse:
    """Batch-add cart items to the member's tab. Expects JSON body with items array."""

    guild = get_object_or_404(Guild, pk=pk)
    member = _get_member(request)
    if member is None:  # pragma: no cover — defensive; signal auto-creates Member on User creation
        return JsonResponse({"error": "No linked membership."}, status=400)

    tab, _created = Tab.objects.get_or_create(member=member)
    if not tab.can_add_entry:
        return JsonResponse({"error": "Payment method required."}, status=400)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    items = body.get("items", [])
    if not items:
        return JsonResponse({"error": "Cart is empty."}, status=400)

    active_products = {p.pk: p for p in guild.products.all()}
    entries_created = 0

    for item in items:
        product_pk = item.get("product_pk")
        quantity = item.get("quantity", 1)
        product = active_products.get(product_pk)
        if product is None:
            return JsonResponse({"error": f"Product {product_pk} not found."}, status=400)

        for _ in range(int(quantity)):
            try:
                tab.add_entry(
                    description=product.name,
                    amount=product.price,
                    added_by=request.user,  # type: ignore[arg-type]
                    is_self_service=True,
                    product=product,
                )
                entries_created += 1
            except (TabLockedError, TabLimitExceededError) as e:
                response = JsonResponse({"error": str(e)}, status=400)
                trigger_toast(response, str(e), "error")
                return response

    success_response = HttpResponse(status=204)
    item_word = "item" if entries_created == 1 else "items"
    trigger_toast(success_response, f"{entries_created} {item_word} added to your tab!", "success")
    return success_response


@login_required
@require_http_methods(["GET", "POST"])
def guild_eyop_form(request: HttpRequest, pk: int) -> HttpResponse:
    """Return the EYOP form partial (GET) or process submission (POST)."""
    from billing.forms import CONTEXT_MEMBER_GUILD_PAGE, TabItemForm

    guild = get_object_or_404(Guild, pk=pk)
    member = _get_member(request)
    if member is None:  # pragma: no cover — defensive; signal auto-creates Member on User creation
        return HttpResponse("No linked membership.", status=400)

    tab, _created = Tab.objects.get_or_create(member=member)

    if request.method == "POST":
        form = TabItemForm(request.POST, context=CONTEXT_MEMBER_GUILD_PAGE, user=request.user, guild=guild)
        if form.is_valid():
            quantity = form.cleaned_data["quantity"]
            try:
                if not tab.can_add_entry:
                    raise NoPaymentMethodError("Payment method required.")
                for _ in range(quantity):
                    form.apply_to_tab(tab, added_by=request.user, is_self_service=True)  # type: ignore[arg-type]
                response = HttpResponse(status=204)
                word = "item" if quantity == 1 else "items"
                trigger_toast(response, f"{quantity} {word} added to your tab!", "success")
                return response
            except NoPaymentMethodError:
                response = HttpResponse(status=400)
                trigger_toast(response, "You need a payment method on file.", "error")
                return response
            except TabLockedError:
                response = HttpResponse(status=400)
                trigger_toast(response, "Your tab is locked.", "error")
                return response
            except TabLimitExceededError:
                response = HttpResponse(status=400)
                trigger_toast(response, "This would exceed your tab limit.", "error")
                return response

        return render(request, "hub/partials/eyop_form.html", {"eyop_form": form, "guild": guild})

    form = TabItemForm(context=CONTEXT_MEMBER_GUILD_PAGE, user=request.user, guild=guild)
    return render(request, "hub/partials/eyop_form.html", {"eyop_form": form, "guild": guild})


def _handle_tours_form(
    request: HttpRequest, member: Member | None
) -> tuple[TourSettingsForm | None, HttpResponse | None]:
    """The ``form_id="tours"`` branch of ``user_settings`` (Spec C §6.4).

    Returns ``(form_for_render, response)`` — a non-None response short-circuits
    the view (successful save → message + redirect back to the Notifications tab,
    matching the tab's sibling forms; unlinked account → the established error path).
    """
    if not (request.method == "POST" and request.POST.get("form_id") == "tours"):
        return (TourSettingsForm(instance=member) if member is not None else None), None
    if member is None:
        messages.error(request, "Your account is not linked to a membership.")
        return None, redirect("hub_user_settings")
    form = TourSettingsForm(request.POST, instance=member)
    if form.is_valid():
        form.save()
        messages.success(request, "Guided tour preference saved.")
        return form, redirect(f"{request.path}?tab=notifications")
    return form, None


def _notification_prefs_via_token(request: HttpRequest) -> HttpResponse:
    """Render/save ONLY the notification matrix for a logged-out member via an email token.

    The token (``t``) is minted per-recipient in the email footer's "manage preferences"
    link. A missing or invalid token falls back to the normal login redirect, so
    ``/settings/`` stays login-gated for everyone else; a valid one authorizes editing the
    notification matrix and nothing else.
    """
    from django.contrib.auth.views import redirect_to_login

    from core.email_prefs import read_prefs_token
    from core.events import settings_matrix

    token = request.POST.get("t") or request.GET.get("t") or ""
    resolved = read_prefs_token(token)
    if resolved is None:
        return redirect_to_login(request.get_full_path())
    user = cast(User, resolved)

    if request.method == "POST" and request.POST.get("form_id") == "notifications":
        settings_matrix.save_matrix(user, request.POST)
        messages.success(request, "Notification preferences updated.")
        return redirect(f"{reverse('hub_user_settings')}?tab=notifications&t={token}")

    notif_channels = [
        (channel, settings_matrix.CHANNEL_LABELS[channel]) for channel in settings_matrix.visible_channels(user)
    ]
    return render(
        request,
        "hub/settings_notifications_token.html",
        {
            "notif_matrix": settings_matrix.build_matrix(user),
            "notif_channels": notif_channels,
            "notif_channel_labels": {channel.value: label for channel, label in notif_channels},
            "prefs_token": token,
            "prefs_email": user.email,
            "member": None,
        },
    )


@login_required
def guild_updates_prompt(request: HttpRequest) -> HttpResponse:
    """First-login interstitial: pick which guilds you want updates from (one-time).

    Routed here by the allauth adapter for members with no answered-stamp and no
    subscriptions. GET renders the multi-select picker; POST Save subscribes to the
    picks and stamps, POST Skip (``skip`` in POST) stamps with zero picks — Skip
    deliberately discards any boxes checked alongside it (the UI disables Skip once
    anything is picked, but the server pins the semantics). Ineligible visits redirect
    home silently: the page is one-time and a bookmark never resurrects it. Zero active
    guilds → stamp and redirect (never trap a member on an empty picker). Full-page
    form, so feedback is Django messages, not toasts.
    """
    from hub.forms import GuildUpdatesPromptForm
    from hub.guild_membership import build_my_guilds_rows

    member = _get_member(request)
    if member is None:
        messages.info(request, "Your account is not linked to a membership.")
        return redirect("hub_home")
    if not member.needs_guild_updates_prompt:
        return redirect("hub_home")
    if not Guild.objects.filter(is_active=True).exists():
        member.mark_guild_updates_answered()
        return redirect("hub_home")

    form = GuildUpdatesPromptForm()
    picked: list[str] = []
    if request.method == "POST":
        if "skip" in request.POST:
            member.answer_guild_updates_prompt([])
            messages.info(request, "No problem. You can pick guilds anytime in Settings.")
            return redirect("hub_home")
        form = GuildUpdatesPromptForm(request.POST)
        if form.is_valid():
            count = member.answer_guild_updates_prompt(form.cleaned_data["guilds"])
            if count:
                plural = "" if count == 1 else "s"
                messages.success(
                    request,
                    f"You'll get updates from {count} guild{plural}. Change your picks anytime in Settings.",
                )
            else:
                messages.info(request, "You didn't pick any guilds. You can choose some anytime in Settings.")
            return redirect("hub_home")
        # Re-render with the member's checks preserved. Keep only pks that render a
        # real checked row (valid, active, deduped) — the template seeds the Alpine
        # ``picked`` counter from this list's length, and counting invalid pks would
        # leave Skip stuck disabled after unchecking every visible box.
        valid_pks = {str(pk) for pk in Guild.objects.filter(is_active=True).values_list("pk", flat=True)}
        picked = [pk for pk in dict.fromkeys(request.POST.getlist("guilds")) if pk in valid_pks]

    ctx = _get_hub_context(request)
    return render(
        request,
        "hub/guild_updates_prompt.html",
        {
            **ctx,
            "member": member,
            "form": form,
            "guild_rows": build_my_guilds_rows(member),
            "picked": picked,
        },
    )


def user_settings(request: HttpRequest) -> HttpResponse:
    """Tabbed user settings page — Profile + Emails + Notifications.

    Three concerns POST to this endpoint, disambiguated by the ``form_id`` hidden
    field: ``profile`` (member info) and ``notifications`` (the event × channel
    preference matrix). Email address management (add, primary, verify, remove) POSTs
    to allauth's ``account_email`` URL, which is overridden in ``plfog.urls`` to
    redirect back here after each action. The Notifications tab is the unified
    preferences matrix (design §2.7) sourced from the event registry.
    """
    if not request.user.is_authenticated:
        # Logged-out visitors are bounced to login, except the email "manage preferences"
        # link, which carries a token that opens the notifications matrix alone.
        return _notification_prefs_via_token(request)

    from allauth.account.forms import AddEmailForm
    from allauth.account.models import EmailAddress

    ctx = _get_hub_context(request)
    member = _get_member(request)

    profile_form: ProfileSettingsForm | None
    # Parameterized on purpose: django-stubs 6.0.8 types inlineformset_factory's product as
    # BaseInlineFormSet[MemberContact, Member, MemberContactForm], and a bare BaseInlineFormSet
    # means BaseInlineFormSet[Any, Any, ModelForm[Any]], which no longer accepts that assignment.
    contact_formset: BaseInlineFormSet[MemberContact, Member, MemberContactForm] | None
    if request.method == "POST" and request.POST.get("form_id") == "profile":
        if member is None:
            messages.error(request, "Your account is not linked to a membership.")
            return redirect("hub_user_settings")
        profile_form = ProfileSettingsForm(request.POST, request.FILES, instance=member)
        contact_formset = MemberContactFormSet(request.POST, instance=member, prefix="contacts")
        contacts_ok = contact_formset.is_valid()
        if profile_form.is_valid() and contacts_ok:
            profile_form.save()
            contact_formset.save()
            _log_profile_updated(cast(User, request.user), member)
            messages.success(request, "Profile updated.")
            return redirect(f"{request.path}?tab=profile")
        if profile_form.has_only_photo_errors and contacts_ok:
            # A rejected photo (too large / not an image) must never discard the member's
            # other edits — save everything except the photo and flag just the photo.
            profile_form.save_keeping_existing_photo()
            contact_formset.save()
            _log_profile_updated(cast(User, request.user), member)
            messages.warning(request, f"Your profile was saved, but the new photo wasn't: {profile_form.photo_error}")
            return redirect(f"{request.path}?tab=profile")
    elif member is not None:
        profile_form = ProfileSettingsForm(instance=member)
        contact_formset = MemberContactFormSet(instance=member, prefix="contacts")
    else:
        profile_form = None
        contact_formset = None

    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    if request.method == "POST" and request.POST.get("form_id") == "notifications":
        from core.events import settings_matrix

        settings_matrix.save_matrix(user, request.POST)
        messages.success(request, "Notification preferences updated.")
        return redirect(f"{request.path}?tab=notifications")

    tours_form, tours_response = _handle_tours_form(request, member)
    if tours_response is not None:
        return tours_response

    add_email_form = AddEmailForm(user=request.user)
    email_addresses = list(EmailAddress.objects.filter(user=request.user).order_by("-primary", "email"))
    primary_email = next((ea for ea in email_addresses if ea.primary), None)
    primary_verified_json = "true" if primary_email is None or primary_email.verified else "false"

    active_tab = _resolve_settings_tab(request, member)

    if member is None and request.method == "GET" and not request.GET.get("tab"):
        messages.info(request, "Your account is not linked to a membership.")

    from core.events import settings_matrix
    from hub.guild_membership import build_my_guilds_rows

    notif_matrix = settings_matrix.build_matrix(user)
    notif_channels = [
        (channel, settings_matrix.CHANNEL_LABELS[channel]) for channel in settings_matrix.visible_channels(user)
    ]
    # Channel labels keyed by channel value, so each matrix cell can build its own
    # screen-reader name (event × channel) via the get_item template filter.
    notif_channel_labels = {channel.value: label for channel, label in notif_channels}
    # Full admins get a shortcut from the Staff & leadership section to their own capability
    # checkboxes (the master switch for those emails). Only admins can edit capabilities, so
    # the link is theirs alone; guild leads see the section but manage it via channel toggles.
    capabilities_url = (
        f"{reverse('hub_admin_member_edit', args=[member.pk])}?tab=permissions"
        if member is not None and member.fog_role == Member.FogRole.ADMIN
        else None
    )

    return render(
        request,
        "hub/user_settings.html",
        {
            **ctx,
            "member": member,
            "capabilities_url": capabilities_url,
            "profile_form": profile_form,
            "contact_formset": contact_formset,
            "skill_categories": _skill_categories_with_approved(),
            "add_email_form": add_email_form,
            "email_addresses": email_addresses,
            "primary_verified_json": primary_verified_json,
            "active_tab": active_tab,
            "notif_matrix": notif_matrix,
            "notif_channels": notif_channels,
            "notif_channel_labels": notif_channel_labels,
            "tours_form": tours_form,
            "my_guilds_rows": build_my_guilds_rows(member),
            "max_upload_image_bytes": settings.MAX_UPLOAD_IMAGE_BYTES,
            "photo_upload_hint": (
                "Optional. Shown next to your name in the member directory and in the navbar. "
                f"Max {settings.MAX_UPLOAD_IMAGE_BYTES / (1024 * 1024):.0f} MB."
            ),
        },
    )


def _resolve_settings_tab(request: HttpRequest, member: Member | None) -> str:
    """Whitelist the settings ``tab`` param and record a Guilds-tab landing.

    The whitelist matters because the tab flows into an Alpine x-data JS expression —
    HTML escaping alone isn't enough to stop a payload like ``?tab='+alert(1)+'``.

    Landing on the Guilds tab via its ``?tab=guilds`` deep link counts as having seen
    and chosen your guild updates — the checklist step, the prompt's Skip fallback, and
    every cross-link arrive this way. A deliberate idempotent write-on-GET (login-gated,
    one-way, no-op after the first hit); see ``Member.mark_guild_updates_answered``.
    """
    tab_param = request.GET.get("tab", "profile")
    active_tab = tab_param if tab_param in {"profile", "emails", "notifications", "guilds", "account"} else "profile"
    if active_tab == "guilds" and member is not None:
        member.mark_guild_updates_answered()
    return active_tab


def _log_profile_updated(user: User, member: Member) -> None:
    """Record a profile-update activity entry (used by both the full and photo-only saves)."""
    from core.models import SiteActivity

    SiteActivity.log(SiteActivity.Kind.PROFILE_UPDATED, actor=user, target=member)


@login_required
@require_POST
def profile_photo_delete(request: HttpRequest) -> HttpResponse:
    """Clear the logged-in member's profile photo and redirect back to settings."""
    member = _get_member(request)
    if member is None:
        messages.error(request, "Your account is not linked to a membership.")
        return redirect("hub_user_settings")
    if member.profile_photo:
        member.profile_photo.delete(save=True)
        messages.success(request, "Profile photo removed.")
    return redirect(f"{reverse('hub_user_settings')}?tab=profile")


@login_required
@require_POST
def account_delete(request: HttpRequest) -> HttpResponse:
    """Self-service account deletion: anonymize PII, lock login, sign the member out."""
    member = _get_member(request)
    if member is None:
        messages.error(request, "Your account is not linked to a membership.")
        return redirect("hub_user_settings")

    form = DeleteAccountConfirmForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Type DELETE exactly to confirm account deletion.")
        return redirect(f"{reverse('hub_user_settings')}?tab=account")

    delete_own_account(member)
    auth_logout(request)
    return redirect("hub_account_deleted")


def account_deleted(request: HttpRequest) -> HttpResponse:
    """Public confirmation shown after self-service deletion has signed the member out.

    Deliberately undecorated (no ``@login_required``): the member is already logged out
    by the time they land here, so this must render for an anonymous visitor. It confirms
    the deletion on its own — it does not rely on a flash message, which ``auth_logout``'s
    session flush would discard before the redirect.
    """
    return render(request, "hub/account_deleted.html")


def _skill_categories_with_approved() -> QuerySet[SkillCategory]:
    """Categories with their approved skills prefetched, for the skill picker."""
    return SkillCategory.objects.prefetch_related(
        Prefetch("skills", queryset=Skill.objects.filter(status=Skill.Status.APPROVED).order_by("name"))
    )


def _render_profile_skills(request: HttpRequest, member: Member, message: str, level: str) -> HttpResponse:
    """Re-render the member's skill editor partial and attach a toast."""
    response = render(
        request,
        "hub/partials/profile_skills.html",
        {"member": member, "skill_categories": _skill_categories_with_approved()},
    )
    trigger_toast(response, message, level)
    return response


def _skills_no_member_response(request: HttpRequest) -> HttpResponse:
    """Error response for a logged-in account with no linked membership."""
    response = HttpResponse(status=403)
    trigger_toast(response, "Your account is not linked to a membership.", "error")
    return response


@login_required
@require_POST
def skill_add(request: HttpRequest) -> HttpResponse:
    """POST-only — add a skill to the logged-in member's profile."""
    member = _get_member(request)
    if member is None:
        return _skills_no_member_response(request)
    form = MemberSkillForm(member=member, data=request.POST)
    if form.is_valid():
        form.save()
        return _render_profile_skills(request, member, "Skill added.", "success")
    return _render_profile_skills(request, member, form.errors.as_text(), "error")


@login_required
@require_POST
def skill_remove(request: HttpRequest, skill_pk: int) -> HttpResponse:
    """POST-only — remove one of the logged-in member's skills."""
    member = _get_member(request)
    if member is None:
        return _skills_no_member_response(request)
    member.skills.filter(pk=skill_pk).delete()
    return _render_profile_skills(request, member, "Skill removed.", "success")


@login_required
@require_POST
def skill_suggest(request: HttpRequest) -> HttpResponse:
    """POST-only — suggest a new skill, created pending admin approval."""
    member = _get_member(request)
    if member is None:
        return _skills_no_member_response(request)
    form = SkillSuggestionForm(member=member, data=request.POST)
    if form.is_valid():
        form.save()
        return _render_profile_skills(request, member, "Thanks! Your skill is pending review.", "success")
    return _render_profile_skills(request, member, form.errors.as_text(), "error")


@login_required
@require_POST
def guild_banner_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Clear a guild's banner image and redirect back to the guild page."""
    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden
    if guild.banner_image:
        guild.banner_image.delete(save=True)
        messages.success(request, "Banner removed.")
    return redirect("hub_guild_detail", slug=guild.slug)


@login_required
@require_POST
def guild_membership_set(request: HttpRequest, pk: int) -> HttpResponse:
    """Subscribe to / unsubscribe from a guild's updates via the settings toggle (HTMX; 204 + toast).

    Delegates to the fat-model subscribe/unsubscribe path and returns a toast instead
    of a full-page redirect. The toggle's checkbox posts ``joined`` when checked
    (subscribe) and omits the field when unchecked (unsubscribe), so the presence of
    ``joined`` in POST is the switch state. Every hit — either direction — stamps
    ``guild_updates_prompt_answered_at``: flipping any toggle is an answer, so the
    first-login prompt never resurrects (including for a legacy member unsubscribing
    from their last guild).
    """
    guild = get_object_or_404(Guild, pk=pk)
    member = _get_member(request)
    response = HttpResponse(status=204)
    if member is None:
        trigger_toast(response, "Your account is not linked to a membership.", "error")
        return response
    if "joined" in request.POST:
        member.subscribe_to_guild(guild)
        trigger_toast(response, f"You'll get updates from {guild.name}.", "success")
    else:
        member.unsubscribe_from_guild(guild)
        trigger_toast(
            response,
            f"You won't get updates from {guild.name} anymore. Turn them back on anytime.",
            "info",
        )
    member.mark_guild_updates_answered()
    return response


@login_required
@require_POST
def guild_image_delete(request: HttpRequest, pk: int, image_pk: int) -> HttpResponse:
    """Delete a gallery image. Editor only."""
    from membership.models import GuildImage

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden
    image = get_object_or_404(GuildImage, pk=image_pk, guild=guild)
    image.image.delete(save=False)
    image.delete()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return HttpResponse(status=204)
    messages.success(request, "Image removed.")
    return redirect("hub_guild_edit", pk=guild.pk)


@login_required
@require_POST
def guild_image_upload(request: HttpRequest, pk: int) -> HttpResponse:
    """AJAX endpoint — upload a gallery image for this guild. Editor only."""
    from membership.models import GuildImage

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden

    if guild.gallery_images.count() >= 10:
        return JsonResponse({"error": "Maximum 10 gallery images."}, status=400)

    file = request.FILES.get("image")
    if not file:
        return JsonResponse({"error": "No file provided."}, status=400)

    # Check size (3MB limit matches ClassImage)
    if file.size is None or file.size > 3 * 1024 * 1024:
        return JsonResponse({"error": "Image must be under 3 MB."}, status=400)

    next_order = (guild.gallery_images.order_by("-sort_order").values_list("sort_order", flat=True).first() or 0) + 1
    img = GuildImage(guild=guild, image=file, sort_order=next_order)
    img.save()

    return JsonResponse({"id": img.pk, "url": img.image.url, "alt_text": img.alt_text})


@login_required
@require_POST
def guild_image_reorder(request: HttpRequest, pk: int) -> HttpResponse:
    """AJAX endpoint — reorder gallery images for this guild. Editor only."""
    from membership.models import GuildImage

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden

    try:
        payload = json.loads(request.body)
        order = payload.get("order", [])
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if not order:
        return JsonResponse({"error": "No order provided"}, status=400)

    # Reorder the images
    images = {img.pk: img for img in guild.gallery_images.all()}
    to_save = []
    for idx, image_pk in enumerate(order):
        if image_pk in images:
            img = images[image_pk]
            img.sort_order = idx
            to_save.append(img)

    GuildImage.objects.bulk_update(to_save, ["sort_order"])
    return HttpResponse(status=204)


@login_required
@require_POST
def guild_image_alt_update(request: HttpRequest, pk: int, image_pk: int) -> HttpResponse:
    """AJAX endpoint — update alt text for a gallery image. Editor only."""
    from membership.models import GuildImage

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden

    image = get_object_or_404(GuildImage, pk=image_pk, guild=guild)

    try:
        payload = json.loads(request.body)
        alt_text = payload.get("alt_text", "")
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    image.alt_text = alt_text
    image.save(update_fields=["alt_text"])
    return HttpResponse(status=204)


# --- Announcement compose wizard (/announcements/compose/) -----------------------------
# One Alpine-stepper page replacing the old site-settings plain composer AND the guild-edit
# inline create form: Step 1 audience + rich message, Step 2 "also email" + live preview,
# Step 3 Discord channel + opt-in @mention. Drafts (AnnouncementDraft) save / resume / delete.


def _compose_editable_guilds(request: HttpRequest, member: Member | None) -> QuerySet[Guild]:
    """Guilds this user may address in the composer: all active guilds for an admin, else staffed."""
    if _viewing_as_admin(request):
        return Guild.objects.filter(is_active=True).order_by("name")
    if member is not None:
        return member.staffed_guilds.filter(is_active=True).order_by("name")
    return Guild.objects.none()


def _compose_editable_classes(request: HttpRequest, member: Member | None) -> QuerySet[ClassOffering]:
    """Classes this user may address in the composer: the published classes they instruct.

    Scoped to ``for_instructor`` (not ``editable_by``) on purpose — announcing to a class's
    roster is the instructor's own duty, distinct from edit rights; an admin who does not
    teach sees no class options (they reach members via the site/guild audiences instead).
    """
    if member is None:
        return ClassOffering.objects.none()
    return ClassOffering.objects.for_instructor(member).filter(status=ClassOffering.Status.PUBLISHED).order_by("title")


def _can_announce_to_class(request: HttpRequest, offering: ClassOffering) -> bool:
    """True when the user may announce to a class's roster — the class's instructor, or an admin."""
    if _viewing_as_admin(request):
        return True
    member = _get_member(request)
    return member is not None and offering.instructor_id == member.pk


def _can_compose(request: HttpRequest, member: Member | None) -> bool:
    """True when the user can compose *something* — an admin, a guild lead/staff, or an instructor."""
    if _viewing_as_admin(request):
        return True
    if member is None:
        return False
    return member.staffed_guilds.filter(is_active=True).exists() or member.is_instructor


def _can_use_admin_tools(request: HttpRequest, member: Member | None) -> bool:
    """True when the Admin Tools hub (sidebar entry + page) is available to this request.

    Anyone with elevated permissions that unlock a tool it collects sees it — a site admin, a
    guild lead/staff (Announcements + Orientations), or an instructor (Announcements). For an
    ACTUAL admin it is view-as-aware: an admin previewing the site as a plain member does NOT see
    it, while a real (non-admin) lead or instructor always does.
    """
    is_actual_admin = getattr(request.user, "is_superuser", False) or (member is not None and member.is_fog_admin)
    if is_actual_admin:
        return _viewing_as_admin(request)
    return member is not None and (member.is_guild_lead or member.is_guild_staff or member.is_instructor)


def _compose_form_kwargs(request: HttpRequest) -> dict[str, Any]:
    """Permission-derived kwargs for :class:`~hub.forms.AnnouncementComposeForm` (audience choices)."""
    member = _get_member(request)
    editable_guilds = list(_compose_editable_guilds(request, member))
    editable_classes = list(_compose_editable_classes(request, member))
    # A pre-scoped (locked) target the user may address but doesn't personally own must still be a
    # valid audience choice — e.g. an admin sending to one class's roster from that class's page,
    # where the class isn't in their own "classes I teach" list. Only ever ADD a target the user is
    # actually allowed to address (re-checked here); the send path re-checks again.
    requested = request.GET.get("audience") or request.POST.get("audience")
    if requested:
        from hub.forms import split_audience

        _audience, guild, offering = split_audience(requested)
        if offering is not None and offering not in editable_classes and _can_announce_to_class(request, offering):
            editable_classes.append(offering)
        if guild is not None and guild not in editable_guilds and _can_edit_guild(request, guild):
            editable_guilds.append(guild)
    return {
        "is_admin": _viewing_as_admin(request),
        "editable_guilds": editable_guilds,
        "editable_classes": editable_classes,
    }


def _compose_audience_forbidden(request: HttpRequest, raw_audience: str) -> HttpResponse | None:
    """403 if the user can't address ``raw_audience`` (never trust the posted audience), else None."""
    from hub.forms import split_audience
    from membership.models import AnnouncementDraft

    audience, guild, offering = split_audience(raw_audience)
    if audience == AnnouncementDraft.Audience.SITE.value:
        return None if _viewing_as_admin(request) else HttpResponse("Forbidden", status=403)
    if audience == AnnouncementDraft.Audience.GUILD.value and guild is not None and _can_edit_guild(request, guild):
        return None
    if (
        audience == AnnouncementDraft.Audience.CLASS.value
        and offering is not None
        and _can_announce_to_class(request, offering)
    ):
        return None
    return HttpResponse("Forbidden", status=403)


def _compose_count_for(
    audience: str, guild: Guild | None, offering: ClassOffering | None = None, *, include_waitlist: bool = False
) -> int:
    """Live recipient count for an audience (reuses the model's roster-backed count).

    ``include_waitlist`` widens a class count to its waitlisted registrants too, matching what
    the "also include the waitlist" toggle will actually send.
    """
    from membership.models import AnnouncementDraft

    if audience == AnnouncementDraft.Audience.GUILD.value and guild is None:
        return 0
    if audience == AnnouncementDraft.Audience.CLASS.value and offering is None:
        return 0
    return AnnouncementDraft(
        audience=audience or AnnouncementDraft.Audience.SITE.value,
        guild=guild,
        class_offering=offering,
        include_waitlist=include_waitlist,
    ).recipient_count()


def _draft_initial(draft: Any) -> dict[str, Any]:
    """Form ``initial`` for resuming a draft — the combined audience value + the saved fields."""
    from membership.models import AnnouncementDraft

    if draft.audience == AnnouncementDraft.Audience.SITE.value:
        audience_value = AnnouncementDraft.Audience.SITE.value
    elif draft.audience == AnnouncementDraft.Audience.CLASS.value:
        audience_value = f"class:{draft.class_offering_id}"
    else:
        audience_value = f"guild:{draft.guild_id}"
    initial = {
        "audience": audience_value,
        "body": draft.body,
        "push_message": draft.push_message,
        "push_enabled": draft.push_enabled,
        "send_email": draft.send_email,
        "discord_enabled": draft.discord_enabled,
        "mark_as_urgent": draft.mark_as_urgent,
        "show_sender": draft.show_sender,
        "include_waitlist": draft.include_waitlist,
        "discord_channel": draft.discord_channel,
        "mention": draft.mention,
        "expires_at": draft.expires_at,
    }
    # A present selection resumes exactly those recipients; an empty one (the default) is left
    # unset so the form falls back to all-selected. (The drafts UI is dormant — this keeps the
    # resume path faithful for when it returns.)
    selection = draft.recipient_selection or {}
    if selection:
        initial["recipients"] = [f"user:{pk}" for pk in selection.get("users", [])] + [
            f"custom:{addr}" for addr in selection.get("custom", [])
        ]
    return initial


def _render_compose(
    request: HttpRequest,
    *,
    form: Any,
    draft: Any,
    locked: bool = False,
    locked_label: str = "",
    compose_heading: str = "",
) -> HttpResponse:
    """Render the single-screen composer for GET and for an invalid-POST re-render (with errors)."""
    from membership.models import AnnouncementDraft

    # The URL-bearing live-count refresh (fires on audience change; the form can't reverse URLs).
    # The waitlist toggle fires the same refresh so the roster + count re-scope when it flips; both
    # controls include the other's value so switching audience keeps the waitlist choice (and back).
    count_url = reverse("hub_compose_count")
    form.fields["audience"].widget.attrs.update(
        {
            "hx-get": count_url,
            "hx-trigger": "change",
            "hx-include": "[name=audience],[name=include_waitlist]",
            "hx-swap": "none",
        }
    )
    form.fields["include_waitlist"].widget.attrs.update(
        {
            "hx-get": count_url,
            "hx-trigger": "change",
            "hx-include": "[name=audience],[name=include_waitlist]",
            "hx-swap": "none",
        }
    )
    count = _compose_count_for(
        form.current_audience, form.current_guild, form.current_class, include_waitlist=form.waitlist_included
    )
    # The auto category (title) for the current audience, without the client-side "Urgent: " lead.
    category_draft = AnnouncementDraft(
        audience=form.current_audience or AnnouncementDraft.Audience.SITE.value,
        guild=form.current_guild,
        class_offering=form.current_class,
    )
    ctx = _get_hub_context(request)
    return render(
        request,
        "hub/announcement_compose.html",
        {
            **ctx,
            "form": form,
            "draft": draft,
            "audience_value": form.audience_value,
            "initial_recipient_count": count,
            "announcement_category": category_draft.announcement_category,
            "drafts": AnnouncementDraft.objects.for_user(cast(User, request.user)),
            "locked": locked,
            "locked_label": locked_label,
            "compose_heading": compose_heading,
        },
    )


def _compose_lock(requested: str | None, want_lock: bool) -> tuple[bool, str, str]:
    """Resolve the locked-audience banner from a pre-scoped audience value.

    Returns ``(locked, locked_label, heading)``. Locked only when a class/guild target resolves —
    a site audience is never locked (nothing to pin it to). Used by both the GET entry
    (``?audience=…&lock=1``) and the invalid-POST re-render (the hidden ``lock`` field).
    """
    if not (want_lock and requested):
        return False, "", ""
    from hub.forms import split_audience

    _audience, guild, offering = split_audience(requested)
    if offering is not None:
        return True, f"Registrants of {offering.title}", f"Announce to {offering.title}"
    if guild is not None:
        return True, f"Members of {guild.name}", f"Announce to {guild.name}"
    return False, "", ""


def _compose_first_error(form: Any) -> str:
    """A member-friendly message for the save-draft error toast (title is the common miss)."""
    if form.errors.get("title"):
        return "Add a subject before saving."
    for errors in form.errors.values():
        if errors:
            return str(errors[0])
    return "Fix the highlighted fields before saving."


@login_required
def hub_compose(request: HttpRequest, draft_pk: int | None = None) -> HttpResponse:
    """The compose wizard page. GET renders all three steps + the drafts list.

    A ``draft_pk`` resumes an unsent draft you own (a foreign / already-sent pk 404s);
    ``?audience=guild:<pk>`` pre-scopes a fresh compose. A member who can compose nothing
    (not an admin, leads no guild) is redirected to the separate propose flow.
    """
    from hub.forms import AnnouncementComposeForm
    from membership.models import AnnouncementDraft

    member = _get_member(request)
    if not _can_compose(request, member):
        return redirect("hub_guild_announcement_propose")

    draft = None
    initial: dict[str, Any] = {}
    locked, locked_label, heading = False, "", ""
    if draft_pk is not None:
        draft = get_object_or_404(AnnouncementDraft, pk=draft_pk, author=request.user, sent_at__isnull=True)
        initial = _draft_initial(draft)
    else:
        requested = request.GET.get("audience")
        if requested:
            initial["audience"] = requested
        locked, locked_label, heading = _compose_lock(requested, bool(request.GET.get("lock")))

    form = AnnouncementComposeForm(initial=initial, **_compose_form_kwargs(request))
    return _render_compose(
        request, form=form, draft=draft, locked=locked, locked_label=locked_label, compose_heading=heading
    )


@login_required
@require_POST
def hub_compose_preview(request: HttpRequest) -> HttpResponse:
    """HTMX: render the category-led announcement email preview — byte-faithful to what sends.

    There is no member-typed subject: the title is the auto category (audience + urgency), the
    email carries the class subline + optional "From <sender>". Building an unsaved draft and
    reusing its own :meth:`AnnouncementDraft.build_email_message` keeps the preview identical to
    the sent email.
    """
    from core.html_sanitize import sanitize_rich_html
    from hub.forms import split_audience
    from membership.models import AnnouncementDraft
    from membership.orientations import _absolute_url

    if not _can_compose(request, _get_member(request)):
        return HttpResponse("Forbidden", status=403)
    audience, guild, offering = split_audience(request.POST.get("audience") or "")
    draft = AnnouncementDraft(
        author=cast(User, request.user),
        audience=audience or AnnouncementDraft.Audience.SITE.value,
        guild=guild,
        class_offering=offering,
        mark_as_urgent=bool(request.POST.get("mark_as_urgent")),
        show_sender=bool(request.POST.get("show_sender")),
        body=sanitize_rich_html(request.POST.get("body") or ""),
    )
    draft.title = draft.announcement_category
    message = draft.build_email_message(_absolute_url("/"))
    return render(
        request,
        "hub/partials/_compose_email_preview.html",
        {"preview_html": message.html_body, "preview_subject": draft.title},
    )


@login_required
def hub_compose_count(request: HttpRequest) -> HttpResponse:
    """HTMX: push the live recipient count (HX-Trigger) + OOB-swap the re-scoped channel picker.

    The re-scope also OOB-swaps the email recipient checklist so a multi-guild lead who switches
    guilds gets the new guild's roster (all checked) instead of the previous guild's — otherwise
    the checklist would send the wrong subset.
    """
    from hub.forms import AnnouncementComposeForm, split_audience

    raw = request.GET.get("audience") or request.POST.get("audience") or ""
    forbidden = _compose_audience_forbidden(request, raw)
    if forbidden is not None:
        return forbidden
    audience, guild, offering = split_audience(raw)
    include_waitlist = bool(request.GET.get("include_waitlist") or request.POST.get("include_waitlist"))
    count = _compose_count_for(audience, guild, offering, include_waitlist=include_waitlist)
    form = AnnouncementComposeForm(
        initial={"audience": raw, "include_waitlist": include_waitlist}, **_compose_form_kwargs(request)
    )
    response = render(request, "hub/partials/_compose_oob_refresh.html", {"form": form})
    response["HX-Trigger"] = json.dumps({"compose-count": {"count": count}})
    return response


@login_required
@require_POST
def hub_compose_test(request: HttpRequest) -> HttpResponse:
    """HTMX: send a branded test of the current draft to the author's own inbox (never the spine)."""
    from core.email import send as send_email
    from core.html_sanitize import sanitize_rich_html
    from hub.forms import split_audience
    from membership.models import AnnouncementDraft
    from membership.orientations import _absolute_url

    if not _can_compose(request, _get_member(request)):
        return HttpResponse("Forbidden", status=403)
    to = (cast(User, request.user).email or "").strip()
    if not to:
        response = HttpResponse(status=204)
        trigger_toast(response, "Your account has no email address to send a test to.", "error")
        return response
    audience, guild, offering = split_audience(request.POST.get("audience") or "")
    draft = AnnouncementDraft(
        author=cast(User, request.user),
        audience=audience or AnnouncementDraft.Audience.SITE.value,
        guild=guild,
        class_offering=offering,
        mark_as_urgent=bool(request.POST.get("mark_as_urgent")),
        show_sender=bool(request.POST.get("show_sender")),
        body=sanitize_rich_html(request.POST.get("body") or ""),
    )
    draft.title = draft.announcement_category
    message = draft.build_email_message(_absolute_url("/"))
    send_email(
        to=to,
        subject=draft.title,
        trigger_kind="announcement.test",
        text_body=message.body,
        html_body=message.html_body,
        best_effort=True,
    )
    response = HttpResponse(status=204)
    trigger_toast(response, f"Test sent to {to}.")
    return response


@login_required
@require_POST
def hub_compose_push_test(request: HttpRequest) -> HttpResponse:
    """HTMX: fire a canned test push at the author's own devices (never the spine).

    The push equivalent of :func:`hub_compose_test` — lets the composer confirm their own
    phone/browser is actually registered before sending the announcement. Best-effort; a dead
    token is reaped by the sender mid-loop, so it doubles as a cleanup pass.
    """
    from core.push_admin import send_test_push

    if not _can_compose(request, _get_member(request)):
        return HttpResponse("Forbidden", status=403)
    result = send_test_push(cast(User, request.user), url=request.build_absolute_uri("/"))
    response = HttpResponse(status=204)
    if result.attempted == 0:
        trigger_toast(response, "No push devices are registered on your account yet.", "error")
    elif result.all_delivered:
        trigger_toast(response, f"Test push sent to your {result.delivered} device(s).")
    else:
        trigger_toast(
            response,
            f"Sent to {result.delivered} of {result.attempted} device(s); the rest didn't respond.",
            "error",
        )
    return response


@login_required
def hub_push_test(request: HttpRequest) -> HttpResponse:
    """Admin support tool: inspect a member's push devices and fire a test push.

    A staffer types a member's email; on lookup the page lists their registered devices
    (native app tokens + browser subscriptions). "Send test push" fires a canned notification
    at every one and reports how many were delivered — a dead token is reaped by the sender
    mid-send, so it doubles as a cleanup pass. Admin-only (a diagnostic, not a member surface).
    """
    from core.push_admin import PushStatus, TestSendResult, send_test_push, status_for
    from hub.forms import PushTestForm

    if not _viewing_as_admin(request):
        return HttpResponse("Forbidden", status=403)

    status: PushStatus | None = None
    result: TestSendResult | None = None
    target: User | None = None
    form = PushTestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        target = form.cleaned_data["user"]
        if "send" in request.POST:
            result = send_test_push(target, url=request.build_absolute_uri("/"))
        status = status_for(target)
    ctx = _get_hub_context(request)
    return render(
        request,
        "hub/push_test.html",
        {**ctx, "form": form, "status": status, "result": result, "target": target},
    )


@login_required
def hub_admin_tools(request: HttpRequest) -> HttpResponse:
    """The admin tools hub: announcements, orientations, members, activity, notifications, settings.

    Open to anyone whose elevated perms unlock a tool (admin, guild lead/staff, or instructor);
    each card shows only to whoever can use it. View-as-aware for actual admins — an admin
    previewing as a plain member is bounced home. Others are bounced home too.
    """
    member = _get_member(request)
    if not _can_use_admin_tools(request, member):
        return redirect("hub_home")
    is_admin = _viewing_as_admin(request)
    can_orient = is_admin or (member is not None and (member.is_guild_lead or member.is_guild_staff))
    ctx = _get_hub_context(request)
    return render(
        request,
        "hub/admin_tools.html",
        {
            **ctx,
            "tool_announcements": _can_compose(request, member),
            "tool_orientations": can_orient,
            "tool_manage_members": is_admin,
            "tool_activity": is_admin,
            "tool_notifications": is_admin,
            "tool_site_settings": is_admin,
            "tool_push_test": is_admin,
            # Quickstart guide links — shown to whoever the guide is for. The
            # instructor card includes the teaching unlock, not just the public
            # Instructor role, so new teachers find their map too.
            "guide_guild_lead": can_orient,
            "guide_instructor": is_admin
            or (member is not None and (member.is_instructor or member.can_create_classes)),
        },
    )


@login_required
@require_POST
def hub_compose_save_draft(request: HttpRequest) -> HttpResponse:
    """HTMX: upsert the draft. Valid → toast + OOB (draft_pk + list); invalid → error toast, no row."""
    from hub.forms import AnnouncementComposeForm
    from membership.models import AnnouncementDraft

    raw = request.POST.get("audience") or ""
    forbidden = _compose_audience_forbidden(request, raw)
    if forbidden is not None:
        return forbidden
    draft_pk = request.POST.get("draft_pk") or ""
    instance = None
    if draft_pk:
        instance = get_object_or_404(AnnouncementDraft, pk=draft_pk, author=request.user, sent_at__isnull=True)
    form = AnnouncementComposeForm(request.POST, **_compose_form_kwargs(request))
    if not form.is_valid():
        response = HttpResponse(status=204)
        trigger_toast(response, _compose_first_error(form), "error")
        return response
    draft = AnnouncementDraft.save_from_form(form, cast(User, request.user), instance=instance)
    response = render(
        request,
        "hub/partials/_compose_save_result.html",
        {"draft": draft, "drafts": AnnouncementDraft.objects.for_user(cast(User, request.user))},
    )
    trigger_toast(response, "Draft saved.")
    return response


@login_required
@require_POST
def hub_compose_send(request: HttpRequest) -> HttpResponse:
    """Full-page POST: re-check the audience server-side, persist the draft, send, then redirect."""
    from hub.forms import AnnouncementComposeForm
    from membership.models import AnnouncementDraft

    raw = request.POST.get("audience") or ""
    forbidden = _compose_audience_forbidden(request, raw)
    if forbidden is not None:
        return forbidden
    draft_pk = request.POST.get("draft_pk") or ""
    instance = None
    if draft_pk:
        instance = get_object_or_404(AnnouncementDraft, pk=draft_pk, author=request.user, sent_at__isnull=True)
    form = AnnouncementComposeForm(request.POST, require_body=True, **_compose_form_kwargs(request))
    if not form.is_valid():
        locked, locked_label, heading = _compose_lock(raw, bool(request.POST.get("lock")))
        return _render_compose(
            request, form=form, draft=instance, locked=locked, locked_label=locked_label, compose_heading=heading
        )
    draft = AnnouncementDraft.save_from_form(form, cast(User, request.user), instance=instance)
    emailed, total = draft.send()
    messages.success(request, f"Announcement sent to {total} recipient(s).")
    return redirect("hub_compose")


@login_required
@require_POST
def hub_compose_delete_draft(request: HttpRequest, draft_pk: int) -> HttpResponse:
    """HTMX (confirm modal): delete an unsent draft you own, then swap the refreshed list + toast."""
    from membership.models import AnnouncementDraft

    draft = get_object_or_404(AnnouncementDraft, pk=draft_pk, author=request.user, sent_at__isnull=True)
    draft.delete()
    response = render(
        request,
        "hub/partials/_compose_drafts_list.html",
        {"drafts": AnnouncementDraft.objects.for_user(cast(User, request.user))},
    )
    trigger_toast(response, "Draft deleted.")
    return response


@login_required
def guild_emails_save(request: HttpRequest, pk: int) -> HttpResponse:
    """Save the guild's two follow-up emails from the Announcements/Emails tab. Editor only.

    The editors are an in-page section of the Announcements/Emails tab on ``guild_edit``,
    so a GET just sends the viewer there. A POST validates and saves the six email fields
    (enable-requires-subject+body, stamping each email's ``*_updated_at``), then redirects
    back to the tab; an invalid POST re-renders the full guild edit page with the email
    form's errors.
    """
    from hub.forms import GuildEmailsForm
    from membership.models import GuildOrientationSettings

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden
    announcements_tab = f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=announcements"
    if request.method != "POST":
        return redirect(announcements_tab)

    settings_obj, _ = GuildOrientationSettings.objects.get_or_create(guild=guild)
    form = GuildEmailsForm(request.POST, instance=settings_obj)
    if form.is_valid():
        form.save()
        messages.success(request, "Guild emails saved.")
        return redirect(announcements_tab)

    ctx = _guild_edit_context(request, guild, emails_form=form)
    return render(request, "hub/guild_edit.html", ctx)


@login_required
@require_POST
def guild_announcement_delete(request: HttpRequest, pk: int, announcement_pk: int) -> HttpResponse:
    """Delete a guild announcement. Editor only.

    The companion *create* endpoint fires the ``guild.announcement`` notification to
    the guild's members (see :meth:`membership.models.GuildAnnouncement.notify_members`).
    Deleting does not notify.
    """
    from membership.models import GuildAnnouncement

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden
    get_object_or_404(GuildAnnouncement, pk=announcement_pk, guild=guild).delete()
    messages.success(request, "Announcement deleted.")
    return redirect("hub_guild_edit", pk=guild.pk)


@login_required
@require_POST
def guild_faq_save(request: HttpRequest, pk: int) -> HttpResponse:
    """Save the guild's FAQ rows from their own form on the FAQ & Links tab. Editor only.

    The FAQ section is its own ``<form>`` (it can't be nested in the main edit form), so it
    persists here independently and redirects back to the same tab with a Django message.
    """
    from hub.forms import GuildFAQItemFormSet

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden
    formset = GuildFAQItemFormSet(request.POST, request.FILES, instance=guild, prefix="faq")
    if formset.is_valid():
        formset.save()
        messages.success(request, "FAQ saved.")
    else:
        messages.error(request, "Couldn't save the FAQ — check the highlighted fields.")
    return redirect(f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=content")


@login_required
@require_POST
def guild_links_save(request: HttpRequest, pk: int) -> HttpResponse:
    """Save the guild's Links rows from their own form on the FAQ & Links tab. Editor only."""
    from hub.forms import GuildLinkFormSet

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden
    formset = GuildLinkFormSet(request.POST, instance=guild, prefix="links")
    if formset.is_valid():
        formset.save()
        messages.success(request, "Links saved.")
    else:
        messages.error(request, "Couldn't save the links — check the highlighted fields.")
    return redirect(f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=content")


@login_required
def guild_mailing_list_save(request: HttpRequest, pk: int) -> HttpResponse:
    """Save the guild's custom mailing-list addresses from the Announcements/Emails tab. Editor only.

    A GET just sends the viewer to the tab. A POST validates the inline formset and saves,
    then redirects back to the tab; an invalid POST re-renders the full guild edit page with
    the bound formset so the row errors show inline (mirrors ``guild_emails_save``, not
    ``guild_links_save``, so the typed input is preserved), re-opening the Announcements tab.
    """
    from hub.forms import GuildMailingListFormSet

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden
    announcements_tab = f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=announcements"
    if request.method != "POST":
        return redirect(announcements_tab)

    formset = GuildMailingListFormSet(request.POST, instance=guild, prefix="mailing_list")
    if formset.is_valid():
        formset.save()
        messages.success(request, "Mailing list saved.")
        return redirect(announcements_tab)

    ctx = _guild_edit_context(request, guild, mailing_list_formset=formset)
    ctx["active_tab"] = "announcements"
    return render(request, "hub/guild_edit.html", ctx)


@login_required
@require_POST
def guild_mailing_list_import(request: HttpRequest, pk: int) -> HttpResponse:
    """Import custom mailing-list addresses from an uploaded CSV / text file. Editor only.

    Decodes the upload and hands it to :meth:`GuildMailingListEmail.import_from_text`, which
    parses it leniently (newlines/commas, optional 2nd column = label), skipping invalid
    tokens, addresses already on the list, and member-collisions. Flashes the outcome summary
    and returns to the Announcements tab.
    """
    from membership.models import GuildMailingListEmail

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden
    announcements_tab = f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=announcements"
    upload = request.FILES.get("import_file")
    if upload is None:
        messages.error(request, "Choose a CSV or text file to import.")
        return redirect(announcements_tab)

    raw_text = upload.read().decode("utf-8", errors="ignore")
    result = GuildMailingListEmail.import_from_text(guild, raw_text)
    if result.created_any:
        messages.success(request, result.summary)
    else:
        messages.error(request, result.summary)
    return redirect(announcements_tab)


# ── Space & Org Info page ────────────────────────────────────────────────────


def spaces(request: HttpRequest) -> HttpResponse:
    """Public Spaces page — the interactive map (tab 1) and the full space listings (tab 2).

    Public-read like ``guild_detail`` (no ``@login_required``): a floor plan and a list of
    studios carry no member PII, so they are safe on the guest surface too. Marker placement
    is admin-only via ``org_map_edit``.

    Both tabs render the same published :class:`~membership.models.Floorplan` set and share
    one Alpine component, so the chosen floor follows you between them. Until a floor is
    published, the legacy single-image lightbox stands in for the map and there is nothing
    to list — the page then shows only that fallback.
    """
    page = OrgInfoPage.load()
    return render(
        request,
        "hub/spaces.html",
        {
            **_get_hub_context(request),
            **_space_map_context(request),
            # Only the legacy fallback image is still read here; the prose moved to the wiki.
            "page": page,
            "can_edit": _viewing_as_admin(request),
            # ?tab=listings deep-links the second tab; anything else is the map.
            "active_tab": "listings" if request.GET.get("tab") == "listings" else "map",
        },
    )


def help_page(request: HttpRequest) -> HttpResponse:
    """Public Help landing — category grid, search, Josh's reference blocks, FAQ, resources.

    Public-read for the same reason it always was: org-wide reference content, no member
    PII. Editing is admin-only via ``help_edit``. Every category card lists its published
    guides directly (one ``Prefetch`` into ``landing_articles`` — no per-category queries);
    uncategorized published guides render in the permanent "All guides" fallback list, so
    nothing vanishes between migrate and seed. ``legacy_anchor_map`` covers the old
    ``/help/#slug`` deep links — filtered to targets that exist and are published,
    resolved to full article URLs for the inline redirect JS.
    """
    from membership.help_content import LEGACY_SLUG_MAP, UNLISTED_SLUGS

    if not SiteConfiguration.load().help_page_enabled:
        return redirect("hub_home")

    page = OrgInfoPage.load()
    org_ct = ContentType.objects.get_for_model(OrgInfoPage)
    live_targets = {
        a.slug: a
        for a in WikiArticle.objects.published()
        .filter(slug__in=set(LEGACY_SLUG_MAP.values()))
        .select_related("category")
    }
    legacy_anchor_map = {
        old: live_targets[new].get_absolute_url() for old, new in LEGACY_SLUG_MAP.items() if new in live_targets
    }
    # Guided-tours aside card (Spec C §6.5) — authenticated members only; the page
    # itself is public-read, so no @login_required here.
    from core.tours import help_card_rows

    member = getattr(request.user, "member", None) if request.user.is_authenticated else None
    tour_rows = help_card_rows(member) if member is not None else None
    return render(
        request,
        "hub/help.html",
        {
            **_get_hub_context(request),
            "page": page,
            "categories": (
                HelpCategory.objects.with_published_counts()
                .nonempty()
                .landing_ranked()
                .prefetch_related(
                    Prefetch(
                        "articles",
                        queryset=(
                            WikiArticle.objects.published()
                            .exclude(slug__in=UNLISTED_SLUGS)
                            # get_absolute_url reads article.category — join it here so
                            # the card links stay inside the single prefetch query.
                            .select_related("category")
                            .order_by("sort_order", "pk")
                        ),
                        to_attr="landing_articles",
                    )
                )
            ),
            "uncategorized": (
                WikiArticle.objects.published()
                .filter(category__isnull=True)
                .exclude(slug__in=UNLISTED_SLUGS)
                .order_by("sort_order", "pk")
            ),
            "legacy_anchor_map": legacy_anchor_map,
            "tour_rows": tour_rows,
            "faq_items": page.faq_items.all(),
            "links": page.links.all(),
            "can_edit": _viewing_as_admin(request),
            "org_ct_id": org_ct.pk,
        },
    )


def help_category(request: HttpRequest, category_slug: str) -> HttpResponse:
    """Public category browse — the published guides in one category, in ``(sort_order, pk)`` order.

    Admins additionally see the category's drafts, flagged with a Draft badge. Unlisted
    guides never appear here (their canonical URL is the only way in, by design).
    """
    from membership.help_content import UNLISTED_SLUGS

    if not SiteConfiguration.load().help_page_enabled:
        return redirect("hub_home")

    category = get_object_or_404(HelpCategory, slug=category_slug)
    articles = category.articles.filter(is_published=True).exclude(slug__in=UNLISTED_SLUGS).order_by("sort_order", "pk")
    is_admin = _viewing_as_admin(request)
    drafts = (
        category.articles.filter(is_published=False).order_by("sort_order", "pk")
        if is_admin
        else WikiArticle.objects.none()
    )
    return render(
        request,
        "hub/help_category.html",
        {
            **_get_hub_context(request),
            "category": category,
            "articles": articles,
            "drafts": drafts,
            "can_edit": is_admin,
        },
    )


def help_article(request: HttpRequest, category_slug: str, article_slug: str) -> HttpResponse:
    """Public article page — resolved by article slug alone (globally unique via the page constraint).

    Missing → 404. Unpublished → 404 unless the viewer is an admin (who sees a Draft
    banner). A stale ``category_slug`` (recategorized guide, hand-typed URL) 301s to the
    canonical URL, so old article links never break when a guide moves categories.
    Unlisted articles resolve normally here — the URL is the only way in, by design.
    """
    if not SiteConfiguration.load().help_page_enabled:
        return redirect("hub_home")

    article = WikiArticle.objects.filter(slug=article_slug).select_related("category").first()
    if article is None:
        raise Http404("No guide with that slug.")
    is_admin = _viewing_as_admin(request)
    if not article.is_published and not is_admin:
        raise Http404("No guide with that slug.")
    if category_slug != article.url_category_segment:
        return redirect(article.get_absolute_url(), permanent=True)
    siblings = (
        list(article.category.articles.filter(is_published=True).order_by("sort_order", "pk"))
        if article.category
        else []
    )
    return render(
        request,
        "hub/help_article.html",
        {
            **_get_hub_context(request),
            "article": article,
            "toc": article.toc(),
            "related": article.related_for_display(),
            "previous_article": article.previous_in_category(),
            "next_article": article.next_in_category(),
            "siblings": siblings,
            "has_other_siblings": any(s.pk != article.pk for s in siblings),
            "can_edit": is_admin,
        },
    )


def help_search(request: HttpRequest) -> HttpResponse:
    """Public help search — plain GET ``?q=``, per-term AND across title/body/category name.

    The view builds ``(article, snippet)`` pairs via ``search_snippet``; empty ``q``
    renders the prompt state with the category list as suggestions.
    """
    if not SiteConfiguration.load().help_page_enabled:
        return redirect("hub_home")

    q = request.GET.get("q", "").strip()
    results = [(article, article.search_snippet(q)) for article in WikiArticle.objects.search(q)]
    return render(
        request,
        "hub/help_search.html",
        {
            **_get_hub_context(request),
            "q": q,
            "results": results,
            "categories": HelpCategory.objects.with_published_counts().nonempty().landing_ranked(),
        },
    )


def _org_info_edit_context(
    request: HttpRequest,
    page: OrgInfoPage,
    *,
    form: OrgInfoPageForm | None = None,
    faq_formset: BaseInlineFormSet[Any, Any, Any] | None = None,
    link_formset: BaseInlineFormSet[Any, Any, Any] | None = None,
    article_formset: BaseInlineFormSet[Any, Any, Any] | None = None,
    category_formset: BaseModelFormSet[Any, Any] | None = None,
    active_tab: str | None = None,
) -> dict[str, Any]:
    """Build the render context for the Space & Org Info editor (Content / Map / FAQ & Links / …).

    The main form covers Content + Map; the FAQ, Links, Articles, and Categories formsets each
    save via their own endpoint (they can't nest inside the main form), so they render unbound
    here unless the caller passes a bound one back in — the invalid-save re-render path, which
    keeps field errors visible and the admin's edits intact. ``active_tab`` tells the template
    which tab to open (it wins over the ``?tab=`` query param).
    """
    from hub.forms import HelpCategoryFormSet, OrgFAQItemFormSet, OrgLinkFormSet, WikiArticleFormSet
    from membership.help_content import ARTICLES

    ctx = _get_hub_context(request)
    return {
        **ctx,
        "page": page,
        # Slugs owned by the seed pipeline — the Articles tab flags these rows with an
        # overwrite warning (a deploy's seed_help_center refreshes their text in place).
        "seeded_slugs": {article["slug"] for article in ARTICLES},
        "form": form if form is not None else OrgInfoPageForm(instance=page),
        "faq_formset": faq_formset if faq_formset is not None else OrgFAQItemFormSet(instance=page, prefix="faq"),
        "link_formset": link_formset if link_formset is not None else OrgLinkFormSet(instance=page, prefix="links"),
        "article_formset": (
            article_formset if article_formset is not None else WikiArticleFormSet(instance=page, prefix="articles")
        ),
        "category_formset": (
            category_formset if category_formset is not None else HelpCategoryFormSet(prefix="categories")
        ),
        "is_admin": _viewing_as_admin(request),
        "active_tab": active_tab,
    }


@login_required
def help_edit(request: HttpRequest) -> HttpResponse:
    """Edit the Help page (GET + main-form POST). Admin only.

    Content and Map are in the single main form; FAQ and Links save via their own endpoints.
    One editor still covers both because ``OrgInfoPage`` is one row: the Map tab here is only
    the *legacy* fallback image, while live marker placement lives in ``org_map_edit``.
    """
    forbidden = _require_admin(request)
    if forbidden is not None:
        return forbidden
    page = OrgInfoPage.load()
    if request.method == "POST":
        form = OrgInfoPageForm(request.POST, request.FILES, instance=page)
        if form.is_valid():
            form.save()
            messages.success(request, "Help page updated.")
            return redirect("hub_help")
        return render(request, "hub/org_info_edit.html", _org_info_edit_context(request, page, form=form))
    return render(request, "hub/org_info_edit.html", _org_info_edit_context(request, page))


@login_required
@require_POST
def org_info_faq_save(request: HttpRequest) -> HttpResponse:
    """Save the org-info FAQ rows from their own form on the FAQ & Links tab. Admin only."""
    from hub.forms import OrgFAQItemFormSet

    forbidden = _require_admin(request)
    if forbidden is not None:
        return forbidden
    page = OrgInfoPage.load()
    formset = OrgFAQItemFormSet(request.POST, request.FILES, instance=page, prefix="faq")
    if formset.is_valid():
        formset.save()
        messages.success(request, "FAQ saved.")
        return redirect(f"{reverse('hub_help_edit')}?tab=faq")
    return render(
        request,
        "hub/org_info_edit.html",
        _org_info_edit_context(request, page, faq_formset=formset, active_tab="faq"),
    )


@login_required
@require_POST
def org_info_links_save(request: HttpRequest) -> HttpResponse:
    """Save the org-info Links rows from their own form on the FAQ & Links tab. Admin only."""
    from hub.forms import OrgLinkFormSet

    forbidden = _require_admin(request)
    if forbidden is not None:
        return forbidden
    page = OrgInfoPage.load()
    formset = OrgLinkFormSet(request.POST, instance=page, prefix="links")
    if formset.is_valid():
        formset.save()
        messages.success(request, "Links saved.")
        return redirect(f"{reverse('hub_help_edit')}?tab=faq")
    return render(
        request,
        "hub/org_info_edit.html",
        _org_info_edit_context(request, page, link_formset=formset, active_tab="faq"),
    )


@login_required
@require_POST
def help_articles_save(request: HttpRequest) -> HttpResponse:
    """Save the Help guides from their own form on the Articles tab. Admin only."""
    from hub.forms import WikiArticleFormSet

    forbidden = _require_admin(request)
    if forbidden is not None:
        return forbidden
    page = OrgInfoPage.load()
    formset = WikiArticleFormSet(request.POST, instance=page, prefix="articles")
    if formset.is_valid():
        formset.save()
        messages.success(request, "Help guides saved.")
        return redirect(f"{reverse('hub_help_edit')}?tab=articles")
    return render(
        request,
        "hub/org_info_edit.html",
        _org_info_edit_context(request, page, article_formset=formset, active_tab="articles"),
    )


@login_required
@require_POST
def help_categories_save(request: HttpRequest) -> HttpResponse:
    """Save the help-center categories from their own form on the Categories tab. Admin only.

    Valid → save + redirect back to the tab. Invalid → re-render the editor with the *bound*
    formset and the Categories tab active, so field errors show inline and no edit is lost.
    """
    from hub.forms import HelpCategoryFormSet

    forbidden = _require_admin(request)
    if forbidden is not None:
        return forbidden
    formset = HelpCategoryFormSet(request.POST, prefix="categories")
    if formset.is_valid():
        formset.save()
        messages.success(request, "Categories saved.")
        return redirect(f"{reverse('hub_help_edit')}?tab=categories")
    page = OrgInfoPage.load()
    return render(
        request,
        "hub/org_info_edit.html",
        _org_info_edit_context(request, page, category_formset=formset, active_tab="categories"),
    )


@login_required
@require_POST
def org_info_floorplan_delete(request: HttpRequest) -> HttpResponse:
    """Clear the floor-plan image and return to the editor's Map tab. Admin only."""
    forbidden = _require_admin(request)
    if forbidden is not None:
        return forbidden
    page = OrgInfoPage.load()
    if page.floorplan_image:
        page.floorplan_image.delete(save=True)
        messages.success(request, "Floor plan removed.")
    return redirect(f"{reverse('hub_help_edit')}?tab=map")


@login_required
def guild_announcement_edit(request: HttpRequest, pk: int, announcement_pk: int) -> HttpResponse:
    """Edit an existing announcement from a modal on the Announcements tab. Editor only.

    GET (HTMX) renders the prefilled form into the modal body. POST validates and, on success,
    swaps the updated row back in (``hx-swap-oob``), fires a success toast, and tells the shared
    modal to close (it has no auto-close on ``htmx:after-request`` — the close must be driven
    from the server). ``published_at`` and ``author`` are never touched on edit.
    """
    from hub.forms import GuildAnnouncementForm
    from membership.models import GuildAnnouncement

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden
    announcement = get_object_or_404(GuildAnnouncement, pk=announcement_pk, guild=guild)

    if request.method == "POST":
        form = GuildAnnouncementForm(request.POST, instance=announcement, guild=guild)
        if form.is_valid():
            # Editing never re-sends, so persist only the editable copy. The post-time
            # send_email toggle and discord_channel picker aren't on the edit form, so a
            # blank value there must not clobber the originally-chosen send options.
            form.save(commit=False)
            announcement.save(update_fields=["title", "body", "expires_at"])
            response = render(
                request,
                "hub/partials/_guild_announcement_row.html",
                {"guild": guild, "a": announcement, "oob": True},
            )
            # One response carries three things: the OOB row swap (body), the success toast
            # (HX-Trigger), and the modal-close event. trigger_toast owns HX-Trigger, so the
            # close-modal event rides HX-Trigger-After-Settle to avoid clobbering the toast.
            trigger_toast(response, "Announcement updated.", "success")
            response["HX-Trigger-After-Settle"] = json.dumps({"close-modal": f"edit-ann-{announcement.pk}"})
            return response
        # Invalid: re-render the modal form with field errors, modal stays open (no close trigger).
        return render(
            request,
            "hub/partials/guild_announcement_edit_form.html",
            {"guild": guild, "announcement": announcement, "form": form},
        )

    form = GuildAnnouncementForm(instance=announcement, guild=guild)
    return render(
        request,
        "hub/partials/guild_announcement_edit_form.html",
        {"guild": guild, "announcement": announcement, "form": form},
    )


# --- Member announcement proposals + reviewer queue -------------------------


def _announcement_review_scope(request: HttpRequest) -> Any:
    """The requester's announcement-review authority.

    Returns ``True`` for an admin (every guild), a ``Guild`` queryset for a lead/staffer
    (their guilds only), or ``None`` when the request may not review.
    """
    if _viewing_as_admin(request):
        return True
    member = _get_member(request)
    if member is not None and member.staffed_guilds.exists():
        return member.staffed_guilds
    return None


def _pending_announcements_for_scope(scope: Any) -> Any:
    """The pending proposals visible to a reviewer ``scope`` (``True`` = admin/all).

    Each returned announcement carries a ``channel_picker_field`` — a bound
    ``discord_channel`` field from a per-guild :class:`GuildAnnouncementDecisionForm` — so the
    approve modal renders the same Discord channel picker the lead's own post form uses, with
    this guild's unconfigured channels disabled.
    """
    from core.models import SiteConfiguration
    from hub.forms import GuildAnnouncementDecisionForm
    from membership.models import GuildAnnouncement

    pending = (
        GuildAnnouncement.objects.awaiting_review().select_related("guild", "submitted_by").order_by("published_at")
    )
    announcements = list(pending if scope is True else pending.filter(guild__in=scope))
    config = SiteConfiguration.load()
    for announcement in announcements:
        form = GuildAnnouncementDecisionForm(guild=announcement.guild, config=config)
        announcement.channel_picker_field = form["discord_channel"]
    return announcements


@login_required
def propose_guild_announcement(request: HttpRequest, pk: int | None = None) -> HttpResponse:
    """Member "Suggest an announcement" page — create a new proposal, or edit/resubmit an
    owned Pending/Changes-requested one.

    Any logged-in member may propose an announcement for any guild; it always goes to the
    guild's leads (or an admin) for review before it posts. Editing a changes-requested
    proposal re-submits it (back to Pending).
    """
    from hub.forms import GuildAnnouncementProposalForm
    from membership.models import GuildAnnouncement

    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    editable_states = [
        GuildAnnouncement.ModerationState.PENDING,
        GuildAnnouncement.ModerationState.CHANGES_REQUESTED,
    ]
    if pk is None:
        announcement = GuildAnnouncement()
        editing = False
        guild_pk = request.GET.get("guild")
        fixed_guild = Guild.objects.filter(pk=guild_pk, is_active=True).first() if guild_pk else None
    else:
        announcement = get_object_or_404(
            GuildAnnouncement, pk=pk, submitted_by=user, moderation_state__in=editable_states
        )
        editing = True
        fixed_guild = announcement.guild

    back_guild = announcement.guild if editing else fixed_guild
    back_url = reverse("hub_guild_detail", args=[back_guild.slug]) if back_guild is not None else reverse("hub_home")

    if request.method == "POST":
        form = GuildAnnouncementProposalForm(request.POST, instance=announcement, fixed_guild=fixed_guild)
        if form.is_valid():
            announcement = form.save(commit=False)
            if not editing:
                announcement.author = user
            else:
                # Persist the edited title/body/guild first — submit_for_review then saves
                # only the moderation fields (update_fields), so the content edits would
                # otherwise be dropped for an already-saved row.
                announcement.save()
            announcement.submit_for_review(submitted_by=user)
            messages.success(
                request,
                "Thanks — your announcement was submitted for review. You'll get a note when a lead or admin responds.",
            )
            return redirect(reverse("hub_guild_detail", args=[announcement.guild.slug]))
    else:
        form = GuildAnnouncementProposalForm(instance=announcement, fixed_guild=fixed_guild)

    ctx = _get_hub_context(request)
    my_proposals = (
        GuildAnnouncement.objects.filter(submitted_by=user)
        .exclude(moderation_state=GuildAnnouncement.ModerationState.PUBLISHED)
        .select_related("guild")
        .order_by("-updated_at")
    )
    return render(
        request,
        "hub/propose_guild_announcement.html",
        {
            **ctx,
            "announcement": announcement,
            "form": form,
            "editing": editing,
            "back_url": back_url,
            "my_proposals": my_proposals,
        },
    )


@login_required
@require_POST
def guild_announcement_withdraw(request: HttpRequest, pk: int) -> HttpResponse:
    """The proposer withdraws (deletes) their own not-yet-posted proposal. POST only."""
    from membership.models import GuildAnnouncement

    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    announcement = get_object_or_404(
        GuildAnnouncement,
        pk=pk,
        submitted_by=user,
        moderation_state__in=[
            GuildAnnouncement.ModerationState.PENDING,
            GuildAnnouncement.ModerationState.CHANGES_REQUESTED,
        ],
    )
    guild_slug = announcement.guild.slug
    announcement.withdraw(by=user)
    messages.success(request, "Proposal withdrawn.")
    return redirect(reverse("hub_guild_detail", args=[guild_slug]))


@login_required
def guild_announcement_review_queue(request: HttpRequest) -> HttpResponse:
    """The reviewer queue — member-proposed announcements a lead/admin can act on."""
    scope = _announcement_review_scope(request)
    if scope is None:
        return HttpResponse("Forbidden", status=403)
    ctx = _get_hub_context(request)
    return render(
        request,
        "hub/guild_announcement_review_queue.html",
        {
            **ctx,
            "pending_announcements": _pending_announcements_for_scope(scope),
            "decision_form": None,
            "open_decision_for": None,
            "open_decision_kind": "",
            "decision_note_value": "",
            "decision_note_error": "",
        },
    )


@login_required
@require_POST
def guild_announcement_review_decision(request: HttpRequest, pk: int) -> HttpResponse:
    """Record a reviewer's decision (approve / request changes / decline) on a proposal.

    Approving also carries the two outbound-channel toggles (email the guild's members /
    post to the guild's Discord); they're persisted before publishing so
    :meth:`GuildAnnouncement.notify_members` honors them.
    """
    from hub.forms import GuildAnnouncementDecisionForm
    from membership.models import GuildAnnouncement, InvalidAnnouncementTransition

    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    scope = _announcement_review_scope(request)
    if scope is None:
        return HttpResponse("Forbidden", status=403)

    # Scoped to the reviewer's authority (a lead can only touch their guilds), but NOT to a
    # moderation state — a stale decision surfaces as a friendly "already handled" via the
    # model guard rather than a bare 404.
    if scope is True:
        announcement = get_object_or_404(GuildAnnouncement, pk=pk)
    else:
        announcement = get_object_or_404(GuildAnnouncement, pk=pk, guild__in=scope)

    # Approve carries its decision in the query string (the approve modal posts no notes);
    # changes/decline post the decision + notes in the body.
    data = request.POST.copy()
    if not data.get("decision"):
        data["decision"] = request.GET.get("decision", "")
    form = GuildAnnouncementDecisionForm(data, guild=announcement.guild)
    if not form.is_valid():
        kind = data.get("decision", "")
        return render(
            request,
            "hub/guild_announcement_review_queue.html",
            {
                **_get_hub_context(request),
                "pending_announcements": _pending_announcements_for_scope(scope),
                "decision_form": form,
                "open_decision_for": announcement.pk,
                "open_decision_kind": kind,
                "decision_note_value": data.get("notes", ""),
                "decision_note_error": " ".join(str(e) for e in form.errors.get("notes", [])),
            },
        )

    decision = form.cleaned_data["decision"]
    notes = form.cleaned_data["notes"]
    try:
        if decision == "approve":
            announcement.approve(
                reviewer=user,
                send_email=form.cleaned_data["send_email"],
                discord_channel=form.cleaned_data["discord_channel"] or None,
            )
            messages.success(request, "Announcement approved and posted.")
        elif decision == "changes":
            announcement.request_changes(reviewer=user, notes=notes)
            messages.success(request, "Sent back to the proposer for changes.")
        else:
            announcement.decline(reviewer=user, notes=notes)
            messages.success(request, "Proposal declined.")
    except InvalidAnnouncementTransition:
        messages.info(request, "That announcement was already handled.")
    return redirect("hub_guild_announcement_review_queue")


@login_required
def guild_meeting_notes(request: HttpRequest, pk: int) -> HttpResponse:
    """Legacy list URL — the meeting-notes list is now an in-page tab on the guild editor.

    Keeps the editor-only gate (so non-staff still get a 403) and then redirects to the tab.
    """
    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden
    return redirect(f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=meeting_notes")


@login_required
def guild_meeting_note_edit(request: HttpRequest, pk: int, note_pk: int | None = None) -> HttpResponse:
    """Add (no ``note_pk``) or edit a meeting note plus its attachment formset. Editor only."""
    from hub.forms import GuildMeetingNoteAttachmentFormSet, GuildMeetingNoteForm
    from membership.models import GuildMeetingNote

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden
    note = get_object_or_404(GuildMeetingNote, pk=note_pk, guild=guild) if note_pk is not None else GuildMeetingNote()

    if request.method == "POST":
        form = GuildMeetingNoteForm(request.POST, instance=note)
        formset = GuildMeetingNoteAttachmentFormSet(request.POST, request.FILES, instance=note, prefix="att")
        if form.is_valid() and formset.is_valid():
            note = form.save(commit=False)
            note.guild = guild
            if note.created_by_id is None:
                note.created_by = request.user
            note.save()
            formset.instance = note
            formset.save()
            messages.success(request, "Meeting notes saved.")
            return redirect(f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=meeting_notes")
    else:
        form = GuildMeetingNoteForm(instance=note)
        formset = GuildMeetingNoteAttachmentFormSet(instance=note, prefix="att")

    ctx = _get_hub_context(request)
    return render(
        request,
        "hub/guild_meeting_note_edit.html",
        {**ctx, "guild": guild, "note": note, "form": form, "attachment_formset": formset},
    )


@login_required
@require_POST
def guild_meeting_note_delete(request: HttpRequest, pk: int, note_pk: int) -> HttpResponse:
    """Delete a meeting note (attachments cascade). POST only, editor only."""
    from membership.models import GuildMeetingNote

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden
    get_object_or_404(GuildMeetingNote, pk=note_pk, guild=guild).delete()
    messages.success(request, "Meeting notes deleted.")
    return redirect(f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=meeting_notes")


@login_required
def guild_events(request: HttpRequest, pk: int) -> HttpResponse:
    """Legacy list URL — the events list is now an in-page tab on the guild editor.

    Keeps the editor-only gate (so non-staff still get a 403) and then redirects to the tab.
    """
    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden
    return redirect(f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=events")


def _event_delete_confirm_message(event: CommunityEvent) -> str:
    """Consequence copy for the edit page's delete confirm modal, varying by publish state."""
    if event.moderation_state == CommunityEvent.ModerationState.PUBLISHED:
        message = (
            "Members will no longer see it on the calendar, and it will be removed from "
            "Google Calendar and Discord. This can't be undone."
        )
    else:
        message = "It'll be removed before it's ever announced. This can't be undone."
    if event.recurrence != CommunityEvent.Recurrence.NONE:
        message += " This removes the whole series."
    return message


@login_required
def guild_event_edit(request: HttpRequest, pk: int, event_pk: int | None = None) -> HttpResponse:
    """Add (no ``event_pk``) or edit a guild event. Editor only.

    Edit/delete fetch the event **scoped to this guild** so a lead of guild A cannot
    mutate guild B's event by supplying B's ``event_pk`` with A's ``pk``. Creating a new
    event announces it once; editing does not re-announce.
    """
    from hub.forms import CommunityEventForm
    from membership.models import CommunityEvent

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden
    event = get_object_or_404(CommunityEvent, pk=event_pk, guild=guild) if event_pk is not None else CommunityEvent()
    is_new = event.pk is None

    if request.method == "POST":
        form = CommunityEventForm(request.POST, instance=event, guild=guild, as_admin=False)
        if form.is_valid():
            event = form.save(commit=False)
            event.guild = guild
            event.event_type = CommunityEvent.EventType.GUILD_MEETING
            if is_new:
                event.created_by = request.user
            event.save()
            # A new event OR a still-SCHEDULED one routes through schedule_or_go_live so a
            # future publish_at parks it and a cleared/back-dated one publishes now (no strand);
            # editing a live event only re-pushes to Google (never re-announces).
            if is_new or event.moderation_state == CommunityEvent.ModerationState.SCHEDULED:
                event.schedule_or_go_live(actor=request.user)
            elif event.moderation_state == CommunityEvent.ModerationState.PUBLISHED:
                event.push_to_google(actor=request.user)
                event.push_to_discord(actor=request.user)
            if event.moderation_state == CommunityEvent.ModerationState.SCHEDULED:
                messages.success(request, f"Event scheduled for {event.publish_at_display}.")
            else:
                messages.success(request, "Event saved.")
            return redirect(f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=events")
    else:
        form = CommunityEventForm(instance=event, guild=guild, as_admin=False)

    ctx = _get_hub_context(request)
    return render(
        request,
        "hub/community_event_edit.html",
        {
            **ctx,
            "guild": guild,
            "event": event,
            "form": form,
            "cancel_url": f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=events",
            "google_sync_enabled": _google_sync_enabled(),
            "delete_url": reverse("hub_guild_event_delete", args=[guild.pk, event.pk]) if event.pk else None,
            "delete_confirm_message": _event_delete_confirm_message(event) if event.pk else None,
        },
    )


@login_required
@require_POST
def guild_event_delete(request: HttpRequest, pk: int, event_pk: int) -> HttpResponse:
    """Delete a guild event. POST only, editor only, fetched guild-scoped."""
    from membership.models import CommunityEvent

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden
    event = get_object_or_404(CommunityEvent, pk=event_pk, guild=guild)
    event.remove_from_google()  # best-effort; must run before the FOG row is gone
    event.remove_from_discord()  # best-effort; must run before the FOG row is gone
    event.delete()
    messages.success(request, "Event deleted.")
    return redirect(f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=events")


@login_required
def event_edit(request: HttpRequest, event_pk: int | None = None) -> HttpResponse:
    """Admin site-wide event authoring (reached from the Community Calendar Events tab)."""
    from hub.forms import CommunityEventForm
    from membership.models import CommunityEvent

    forbidden = _require_admin(request)
    if forbidden is not None:
        return forbidden
    event = get_object_or_404(CommunityEvent, pk=event_pk) if event_pk is not None else CommunityEvent()
    is_new = event.pk is None
    cancel_url = reverse("hub_community_calendar") + "?tab=events"

    if request.method == "POST":
        form = CommunityEventForm(request.POST, instance=event, as_admin=True)
        if form.is_valid():
            event = form.save(commit=False)
            if is_new:
                event.created_by = request.user
            event.save()
            # is_new OR still-SCHEDULED → schedule_or_go_live (park a future publish_at, publish a
            # cleared/back-dated one now); editing a live event only re-pushes to Google.
            if is_new or event.moderation_state == CommunityEvent.ModerationState.SCHEDULED:
                event.schedule_or_go_live(actor=request.user)
            elif event.moderation_state == CommunityEvent.ModerationState.PUBLISHED:
                event.push_to_google(actor=request.user)
                event.push_to_discord(actor=request.user)
            if event.moderation_state == CommunityEvent.ModerationState.SCHEDULED:
                messages.success(request, f"Event scheduled for {event.publish_at_display}.")
            else:
                messages.success(request, "Event saved.")
            return redirect(cancel_url)
    else:
        form = CommunityEventForm(instance=event, as_admin=True)

    ctx = _get_hub_context(request)
    return render(
        request,
        "hub/community_event_edit.html",
        {
            **ctx,
            "event": event,
            "form": form,
            "cancel_url": cancel_url,
            "google_sync_enabled": _google_sync_enabled(),
            "delete_url": reverse("hub_event_delete", args=[event.pk]) if event.pk else None,
            "delete_confirm_message": _event_delete_confirm_message(event) if event.pk else None,
        },
    )


@login_required
@require_POST
def event_delete(request: HttpRequest, event_pk: int) -> HttpResponse:
    """Delete a site-wide event. POST only, admin only."""
    from membership.models import CommunityEvent

    forbidden = _require_admin(request)
    if forbidden is not None:
        return forbidden
    event = get_object_or_404(CommunityEvent, pk=event_pk)
    event.remove_from_google()  # best-effort; must run before the FOG row is gone
    event.remove_from_discord()  # best-effort; must run before the FOG row is gone
    event.delete()
    messages.success(request, "Event deleted.")
    return redirect(reverse("hub_community_calendar") + "?tab=events")


# --- Member event proposals + reviewer queue --------------------------------


@dataclass(frozen=True)
class _ReviewScope:
    """A requester's event-review authority.

    ``can_review`` gates access at all; an ``is_admin`` reviewer covers every guild +
    site-wide, while a lead/staffer covers only their staffed ``guilds``. Built by
    :func:`_reviewer_guild_scope`; apply it via :meth:`scoped` / :meth:`pending`.
    """

    can_review: bool
    is_admin: bool = False
    guilds: Any = None  # a Guild queryset for a lead/staffer; None for an admin or forbidden

    def scoped(self, events: QuerySet) -> QuerySet:
        """Narrow a ``CommunityEvent`` queryset to what this scope may act on."""
        return events if self.is_admin else events.filter(guild__in=self.guilds)

    def pending(self) -> QuerySet:
        """The pending proposals visible to this scope — an empty set when it can't review."""
        from membership.models import CommunityEvent

        if not self.can_review:
            return CommunityEvent.objects.none()
        awaiting = (
            CommunityEvent.objects.awaiting_review().select_related("guild", "submitted_by").order_by("starts_at")
        )
        return self.scoped(awaiting)


def _reviewer_guild_scope(request: HttpRequest) -> _ReviewScope:
    """The requester's event-review authority (admin / capability / lead-scoped / none)."""
    if _viewing_as_admin(request):
        return _ReviewScope(can_review=True, is_admin=True)
    member = _get_member(request)
    if member is not None and member.has_admin_capability(AdminCapability.Capability.EVENTS_APPROVER):
        # A Calendar Administrator reviews every calendar proposal site-wide, like an admin.
        return _ReviewScope(can_review=True, is_admin=True)
    if member is not None and member.staffed_guilds.exists():
        return _ReviewScope(can_review=True, guilds=member.staffed_guilds)
    return _ReviewScope(can_review=False)


@login_required
def propose_event(request: HttpRequest, pk: int | None = None) -> HttpResponse:
    """Member "Propose an event" page — create a new proposal, or edit/resubmit an
    owned Pending/Changes-requested one.

    On create the member-event policy decides the outcome: ``DISABLED`` → 403;
    ``OPEN`` → publish immediately; ``APPROVAL`` → submit to the review queue. Editing
    always re-submits for review (a changes-requested proposal returns to Pending).
    """
    from hub.forms import CommunityEventForm
    from membership.models import CommunityEvent

    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    policy = SiteConfiguration.load().member_event_policy
    Policy = SiteConfiguration.MemberEventPolicy
    editable_states = [
        CommunityEvent.ModerationState.PENDING,
        CommunityEvent.ModerationState.CHANGES_REQUESTED,
    ]
    if pk is None:
        if policy == Policy.DISABLED:
            return HttpResponse("Forbidden", status=403)
        event = CommunityEvent()
        editing = False
    else:
        event = get_object_or_404(CommunityEvent, pk=pk, submitted_by=user, moderation_state__in=editable_states)
        editing = True

    cancel_url = reverse("hub_community_calendar") + "?tab=events"

    if request.method == "POST":
        form = CommunityEventForm(request.POST, instance=event, as_member=True)
        if form.is_valid():
            event = form.save(commit=False)
            published = event.propose(by=user, guild=form.cleaned_data.get("guild"), policy=policy, editing=editing)
            if published:
                messages.success(request, "Your event is live on the Community Calendar.")
            else:
                messages.success(
                    request,
                    "Thanks — your event was submitted for review. You'll get a note when a lead or admin responds.",
                )
            return redirect(cancel_url)
    else:
        form = CommunityEventForm(instance=event, as_member=True)

    ctx = _get_hub_context(request)
    my_proposals = (
        CommunityEvent.objects.filter(submitted_by=user)
        .exclude(moderation_state=CommunityEvent.ModerationState.PUBLISHED)
        .select_related("guild")
        .order_by("-updated_at")
    )
    return render(
        request,
        "hub/propose_event.html",
        {
            **ctx,
            "event": event,
            "form": form,
            "editing": editing,
            "policy": policy,
            "cancel_url": cancel_url,
            "my_proposals": my_proposals,
        },
    )


@login_required
@require_POST
def event_withdraw(request: HttpRequest, pk: int) -> HttpResponse:
    """The proposer withdraws (deletes) their own not-yet-published proposal. POST only."""
    from membership.models import CommunityEvent

    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    event = get_object_or_404(
        CommunityEvent,
        pk=pk,
        submitted_by=user,
        moderation_state__in=[
            CommunityEvent.ModerationState.PENDING,
            CommunityEvent.ModerationState.CHANGES_REQUESTED,
        ],
    )
    event.withdraw(by=user)
    messages.success(request, "Proposal withdrawn.")
    return redirect(reverse("hub_community_calendar") + "?tab=events")


@login_required
def event_review_queue(request: HttpRequest) -> HttpResponse:
    """The reviewer queue — pending proposals a lead/admin can approve, send back, or decline."""
    scope = _reviewer_guild_scope(request)
    if not scope.can_review:
        return HttpResponse("Forbidden", status=403)
    ctx = _get_hub_context(request)
    return render(
        request,
        "hub/event_review_queue.html",
        {
            **ctx,
            "pending_events": scope.pending(),
            "decision_form": None,
            "open_decision_for": None,
            "open_decision_kind": "",
            "decision_note_value": "",
            "decision_note_error": "",
        },
    )


@login_required
@require_POST
def event_review_decision(request: HttpRequest, pk: int) -> HttpResponse:
    """Record a reviewer's decision (approve / request changes / decline) on a proposal."""
    from hub.forms import EventDecisionForm
    from membership.models import CommunityEvent, InvalidEventTransition

    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    scope = _reviewer_guild_scope(request)
    if not scope.can_review:
        return HttpResponse("Forbidden", status=403)

    # Fetch scoped to the reviewer's authority (a lead can only touch their guilds' events),
    # but NOT to a moderation state — a stale decision surfaces as a friendly "already
    # handled" via the model guard rather than a bare 404.
    event = get_object_or_404(scope.scoped(CommunityEvent.objects.all()), pk=pk)

    # Approve carries its decision in the query string (a plain confirm modal posts no
    # notes field); changes/decline post the decision + notes in the body.
    data = request.POST.copy()
    if not data.get("decision"):
        data["decision"] = request.GET.get("decision", "")
    form = EventDecisionForm(data)
    if not form.is_valid():
        kind = data.get("decision", "")
        return render(
            request,
            "hub/event_review_queue.html",
            {
                **_get_hub_context(request),
                "pending_events": scope.pending(),
                "decision_form": form,
                "open_decision_for": event.pk,
                "open_decision_kind": kind,
                "decision_note_value": data.get("notes", ""),
                "decision_note_error": " ".join(str(e) for e in form.errors.get("notes", [])),
            },
        )

    decision = form.cleaned_data["decision"]
    notes = form.cleaned_data["notes"]
    try:
        if decision == "approve":
            event.approve(reviewer=user)
            messages.success(request, "Event approved and published.")
        elif decision == "changes":
            event.request_changes(reviewer=user, notes=notes)
            messages.success(request, "Sent back to the proposer for changes.")
        else:
            event.decline(reviewer=user, notes=notes)
            messages.success(request, "Proposal declined.")
    except InvalidEventTransition:
        messages.info(request, "That event was already handled.")
    return redirect("hub_event_review_queue")


@login_required
@require_POST
def event_retry_sync(request: HttpRequest, pk: int) -> HttpResponse:
    """Re-push a single event to Google now (the "Retry sync now" button on a FAILED sync
    badge). Admin-only; best-effort — records the outcome and reports the new sync state so
    an admin who just fixed the Calendar ID / sharing doesn't wait up to 15 min for the cron.
    """
    from membership.models import CommunityEvent

    forbidden = _require_admin(request)
    if forbidden is not None:
        return forbidden
    event = get_object_or_404(CommunityEvent, pk=pk)
    event.push_to_google()
    event.push_to_discord()  # self-gates off / no-op for studio hours
    if event.sync_state == CommunityEvent.SyncState.SYNCED:
        messages.success(request, "Event synced to Google Calendar.")
    elif event.sync_state == CommunityEvent.SyncState.FAILED:
        messages.error(request, f"Sync failed: {event.sync_error}")
    else:
        messages.info(request, event.sync_error or "Sync is still pending.")
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect(reverse("hub_community_calendar") + "?tab=events")


@login_required
def beta_feedback(request: HttpRequest) -> HttpResponse:
    """Feedback page — users can report bugs, request features, or leave general feedback."""
    ctx = _get_hub_context(request)

    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User

    if request.method == "POST":
        form = BetaFeedbackForm(request.POST)
        if form.is_valid():
            form.send(user=user)
            messages.success(request, "Thanks for your feedback! We'll review it soon.")
            return redirect("hub_beta_feedback")
    else:
        form = BetaFeedbackForm()

    return render(request, "hub/beta_feedback.html", {**ctx, "form": form})


@login_required
@require_http_methods(["GET"])
def tab_detail(request: HttpRequest) -> HttpResponse:
    """My Tab page — shows current balance, pending entries, and saved payment method."""
    if not SiteConfiguration.load().my_tab_enabled:
        messages.info(request, "My Tab isn't available right now.")
        return redirect("home")

    member = _get_member(request)
    ctx = _get_hub_context(request)

    if member is None:
        messages.info(request, "Your account is not linked to a membership.")
        return render(request, "hub/tab_detail.html", {**ctx, "tab": None, "entries": []})

    tab, _created = Tab.objects.get_or_create(member=member)
    entries = tab.entries.pending().select_related("product__guild").order_by("-created_at")

    return render(
        request,
        "hub/tab_detail.html",
        {
            **ctx,
            "tab": tab,
            "entries": entries,
            "next_charge_at": BillingSettings.load().next_charge_at(),
        },
    )


@login_required
@require_POST
def void_tab_entry(request: HttpRequest, entry_pk: int) -> HttpResponse:
    """Remove a pending tab entry. Only the owning member can remove their own entries."""
    from billing.models import TabEntry as TabEntryModel

    member = _get_member(request)
    if member is None:  # pragma: no cover — defensive; signal auto-creates Member on User creation
        return HttpResponse(status=404)

    entry = get_object_or_404(TabEntryModel, pk=entry_pk, tab__member=member)

    try:
        entry.void(user=request.user, reason="Removed by member")  # type: ignore[arg-type]
    except ValueError as e:
        response = HttpResponse(status=400)
        trigger_toast(response, str(e), "error")
        return response

    response = HttpResponse(status=204)
    trigger_toast(response, "Charge removed.", "success")
    return response


@login_required
def tab_history(request: HttpRequest) -> HttpResponse:
    """Tab History page — shows past billing charges with expandable details."""
    if not SiteConfiguration.load().my_tab_enabled:
        messages.info(request, "My Tab isn't available right now.")
        return redirect("home")

    member = _get_member(request)
    ctx = _get_hub_context(request)

    if member is None:
        messages.info(request, "Your account is not linked to a membership.")
        return render(request, "hub/tab_history.html", {**ctx, "charges": []})

    tab, _created = Tab.objects.get_or_create(member=member)
    charges = tab.charges.exclude(status=TabCharge.Status.PENDING).order_by("-created_at").prefetch_related("entries")

    return render(request, "hub/tab_history.html", {**ctx, "charges": charges})


_CALENDAR_PAGE_SIZE = 10
# FOG-native community events render under this source. Reuses the existing --hub-blue
# brand token (not a new color) so they read distinctly from classes/orientation/guild.
_COMMUNITY_CALENDAR_COLOR = "#3d8bd4"


def _calendar_legend_guilds(
    all_events: list[Any], guilds_with_calendars: list[Any], guild: "Guild | None"
) -> list[Any]:
    """Guilds that get a legend/filter toggle for the current calendar view.

    A guild earns a toggle when it owns a guild-colored chip in the window — a class
    row carries its guild even without an iCal URL, so a class-only guild qualifies.
    The Community Calendar (``guild=None``) unions those with every configured-feed
    guild so a returning member's saved toggle persists with no events this window; a
    single guild's page scopes to that guild alone so other guilds never get a dead
    toggle. Sorted by name for a stable legend order.
    """
    event_guilds = {e.guild for e in all_events if e.guild is not None and e.source_key == str(e.guild.pk)}
    if guild is None:
        return sorted(set(guilds_with_calendars) | event_guilds, key=lambda g: g.name)
    scoped_guilds = set(event_guilds)
    if guild.calendar_url:
        scoped_guilds.add(guild)
    return sorted(scoped_guilds, key=lambda g: g.name)


def _get_calendar_context(
    request: HttpRequest,
    week_offset: int = 0,
    month_offset: int = 0,
    event_page: int = 1,
    guild: "Guild | None" = None,
) -> dict[str, Any]:
    """Build context for both the full calendar page and the HTMX partial.

    The "month" view is a rolling 4-week window starting from the current week
    (current week + 3 upcoming weeks). ``month_offset`` shifts that window in
    4-week chunks, so members navigating forward see the next 4 weeks rather
    than jumping to a calendar month boundary.

    Args:
        week_offset: Weeks relative to the current week (negative = past, positive = future).
        month_offset: 4-week chunks relative to the current window (negative = past, positive = future).
        event_page: 1-based page number for the event list (PAGE_SIZE events per page).
    """
    from collections import defaultdict

    from core.models import CalendarFeed, SiteConfiguration
    from membership.models import CalendarEvent, CommunityEvent, Guild

    now = dj_timezone.now()
    today = now.date()

    # Navigated week
    current_week_start = today - timedelta(days=today.weekday())
    week_start = current_week_start + timedelta(weeks=week_offset)
    week_end = week_start + timedelta(days=6)

    # Rolling 4-week window: current week + 3 upcoming weeks (Mon–Sun rows).
    # month_offset shifts the window by 4 weeks so navigation stays aligned to weeks.
    window_start = current_week_start + timedelta(weeks=4 * month_offset)
    window_end = window_start + timedelta(days=27)

    # Fetch only events covering the navigated week and 4-week window
    fetch_from = min(week_start, window_start)
    fetch_to = max(week_end, window_end)

    events_qs = CalendarEvent.objects.filter(start_dt__date__gte=fetch_from, start_dt__date__lte=fetch_to)
    if guild is not None:
        events_qs = events_qs.filter(guild=guild)
    # Echo de-dup: hide the iCal copy of any event FOG itself pushed to Google (the daily
    # read re-imports it as a CalendarEvent whose UID matches our stored google_ical_uid),
    # so a FOG event never shows twice on the calendar.
    events_qs = events_qs.exclude(uid__in=CommunityEvent.objects.pushed().values_list("google_ical_uid", flat=True))
    # CalendarEvent rows, optionally merged with synthetic guild entries (classes/orientations)
    # that duck-type CalendarEvent — hence the Any element type.
    all_events: list[Any] = list(events_qs.select_related("guild", "feed").order_by("start_dt"))
    # Merge FOG-native synthetic entries (they aren't CalendarEvent rows) into BOTH the
    # community calendar (all events) and a guild calendar (that guild's events), then
    # re-sort by start so they interleave with the iCal/class/orientation entries.
    from hub.calendar_entries import community_event_entries, guild_calendar_entries

    if guild is not None:
        synthetic = [
            *guild_calendar_entries(guild, fetch_from, fetch_to),
            *community_event_entries(fetch_from, fetch_to, guild=guild),
        ]
    else:
        synthetic = community_event_entries(fetch_from, fetch_to)
    all_events = sorted([*all_events, *synthetic], key=lambda e: e.start_dt)

    # Week event list: events whose start date falls within the navigated week
    week_events = [e for e in all_events if week_start <= e.start_dt.date() <= week_end]

    # Month-view event list: events whose start date falls within the 4-week window (paginated)
    raw_month_events = [e for e in all_events if window_start <= e.start_dt.date() <= window_end]
    total_pages = max(1, (len(raw_month_events) + _CALENDAR_PAGE_SIZE - 1) // _CALENDAR_PAGE_SIZE)
    event_page = max(1, min(event_page, total_pages))
    page_start = (event_page - 1) * _CALENDAR_PAGE_SIZE
    month_events = raw_month_events[page_start : page_start + _CALENDAR_PAGE_SIZE]

    # Map every event in the 4-week window to its 1-based pagination page so chip
    # clicks for events on a different page can hop pages before scrolling.
    month_event_pages: dict[int, int] = {
        evt.pk: (idx // _CALENDAR_PAGE_SIZE) + 1 for idx, evt in enumerate(raw_month_events)
    }

    guilds_with_calendars = list(Guild.objects.filter(is_active=True, calendar_url__gt="").order_by("name"))

    config = SiteConfiguration.load()
    calendar_feeds = list(CalendarFeed.objects.filter(ical_url__gt=""))
    classes_enabled = config.sync_classes_enabled
    classes_color = config.classes_calendar_color

    source_colors: dict[str, str] = {
        "classes": classes_color,
        "orientation": "#EEB44B",
        "community": _COMMUNITY_CALENDAR_COLOR,
    }
    for feed in calendar_feeds:
        source_colors[f"feed-{feed.pk}"] = feed.color

    legend_guilds = _calendar_legend_guilds(all_events, guilds_with_calendars, guild)
    for g in legend_guilds:
        source_colors[str(g.pk)] = g.calendar_color

    # True when a class in this window has no guild → keep the generic "Other classes"
    # fallback toggle/color for it (a class with a guild groups under that guild instead).
    has_ungrouped_classes = any(e.source_key == "classes" for e in all_events)

    # Group events by date for calendar grid dots
    events_by_date: dict = defaultdict(list)
    for evt in all_events:
        events_by_date[evt.start_dt.date()].append(evt)

    # Week label (e.g. "Apr 14 – 20, 2026" or "Apr 28 – May 4, 2026")
    if week_start.month == week_end.month and week_start.year == week_end.year:
        week_label = f"{week_start.strftime('%b %-d')} – {week_end.strftime('%-d')}, {week_end.year}"
    else:
        week_label = f"{week_start.strftime('%b %-d')} – {week_end.strftime('%b %-d')}, {week_end.year}"

    # Week grid: 7 days starting from navigated Monday
    week_days = [
        {
            "date": week_start + timedelta(days=i),
            "is_today": (week_start + timedelta(days=i)) == today,
            "events": events_by_date.get(week_start + timedelta(days=i), []),
        }
        for i in range(7)
    ]

    # Window label, e.g. "Apr 27 – May 24, 2026" or "Dec 28, 2025 – Jan 24, 2026"
    if window_start.year != window_end.year:
        month_label = f"{window_start.strftime('%b %-d, %Y')} – {window_end.strftime('%b %-d, %Y')}"
    elif window_start.month == window_end.month:
        month_label = f"{window_start.strftime('%b %-d')} – {window_end.strftime('%-d')}, {window_end.year}"
    else:
        month_label = f"{window_start.strftime('%b %-d')} – {window_end.strftime('%b %-d')}, {window_end.year}"

    # 4-week grid: 28 days (Mon–Sun, exactly 4 rows). Every cell is "in window".
    month_headers = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    month_days = []
    for i in range(28):
        d = window_start + timedelta(days=i)
        month_days.append({"date": d, "is_today": d == today, "in_month": True, "events": events_by_date.get(d, [])})

    # The Google-sync flag stays admin-only and gated by both sync switches (the same
    # contract as the wordy badge), so it renders on the calendar list only for a
    # manager when sync is on — never for a plain member.
    sync_flag_visible = _google_sync_enabled() and _viewing_as_admin(request)

    return {
        "week_events": week_events,
        "month_events": month_events,
        "event_page": event_page,
        "event_total_pages": total_pages,
        "sync_flag_visible": sync_flag_visible,
        "guilds_with_calendars": guilds_with_calendars,
        "legend_guilds": legend_guilds,
        "has_ungrouped_classes": has_ungrouped_classes,
        "calendar_feeds": calendar_feeds,
        "classes_enabled": classes_enabled,
        "classes_color": classes_color,
        "community_color": _COMMUNITY_CALENDAR_COLOR,
        "source_colors": source_colors,
        "week_days": week_days,
        "week_label": week_label,
        "week_offset": week_offset,
        "month_days": month_days,
        "month_headers": month_headers,
        "month_label": month_label,
        "month_offset": month_offset,
        "month_event_pages_json": json.dumps(month_event_pages),
        "now": now,
    }


@login_required
@ensure_csrf_cookie
def home(request: HttpRequest) -> HttpResponse:
    """Member Home / Dashboard — the post-login landing page.

    Reads the logged-in member's upcoming items, joined-guild announcements, guild
    shortcuts, and profile-completeness from the ``hub.home`` service. An unlinked
    account (a User with no ``Member``) renders a friendly "not linked yet" state and
    skips the personalized blocks — the hub's established graceful-``None`` pattern.

    ``@ensure_csrf_cookie``: this is the native app's post-login landing page (``/``
    redirects here), and it is where ``static/js/native-push.js`` runs to register the
    device's FCM token. That registration POSTs to ``/push/fcm/register/`` with the CSRF
    token read from the ``csrftoken`` cookie. Django only sets that cookie when a view
    uses it, so without this the WebView had no cookie and every registration POST was
    rejected ("CSRF cookie not set"), leaving native push silently broken. Forcing the
    cookie here (it persists for the rest of the session) lets the token register.
    """
    member = _get_member(request)
    ctx = _get_hub_context(request)
    if member is None:
        return render(request, "hub/home.html", {**ctx, "member": None})
    from core.tours import tour_offer_context
    from hub.home import build_home_context

    return render(
        request,
        "hub/home.html",
        {**ctx, "member": member, **build_home_context(member), **tour_offer_context(request, "member-welcome")},
    )


def community_calendar(request: HttpRequest) -> HttpResponse:
    """Community Calendar page — a Calendar grid tab + an Events list/authoring tab.

    The Events tab is a member-readable upcoming-events list; admins additionally get
    ``+ Add`` / Edit / Delete controls (gated by ``events_can_manage``).
    """
    from django.core.paginator import Paginator

    from hub.calendar_entries import upcoming_calendar_events
    from membership.models import CommunityEvent

    ctx = _get_hub_context(request)
    cal_ctx = _get_calendar_context(request)

    # No default_filters_json here: the Community Calendar persists *disabled*
    # filters client-side, so every legend key — including ones added later —
    # defaults to visible without a seeded enabled-list.
    cal_ctx["events_url"] = reverse("hub_community_calendar_events")

    view_as = getattr(request, "view_as", None)
    is_admin = bool(view_as is not None and view_as.is_admin)
    # The Events tab lists exactly what the grid shows — every feed / general / class
    # event plus every published community event — not just the FOG-native ones (that
    # was the "missing events" bug). Paginated with the hub's standard Paginator.
    events_paginator = Paginator(upcoming_calendar_events(), _CALENDAR_PAGE_SIZE)
    cal_ctx["events_page_obj"] = events_paginator.get_page(request.GET.get("events_page", 1))
    cal_ctx["events_can_manage"] = is_admin
    # Admin-only: site-wide events parked in SCHEDULED are invisible on the public list and
    # aren't in my_proposals (admin direct-creates set created_by, not submitted_by), so an
    # admin could never find them to edit/cancel — surface them in their own section.
    cal_ctx["scheduled_events"] = (
        CommunityEvent.objects.scheduled().site_wide().select_related("guild").order_by("publish_at")
        if is_admin
        else CommunityEvent.objects.none()
    )

    policy = SiteConfiguration.load().member_event_policy
    cal_ctx["member_can_propose"] = policy != SiteConfiguration.MemberEventPolicy.DISABLED
    cal_ctx["google_sync_enabled"] = _google_sync_enabled()

    # Reviewer queue link + count, and the member's own in-flight proposals (Screen A′).
    scope = _reviewer_guild_scope(request)
    cal_ctx["can_review"] = scope.can_review
    cal_ctx["review_pending_count"] = scope.pending().count()
    if request.user.is_authenticated:
        cal_ctx["my_proposals"] = (
            CommunityEvent.objects.filter(submitted_by=request.user)
            .exclude(moderation_state=CommunityEvent.ModerationState.PUBLISHED)
            .select_related("guild")
            .order_by("-updated_at")
        )
    else:
        cal_ctx["my_proposals"] = CommunityEvent.objects.none()
    return render(request, "hub/community_calendar.html", {**ctx, **cal_ctx})


def calendar_events_partial(request: HttpRequest) -> HttpResponse:
    """HTMX partial — returns calendar event HTML straight from the database.

    Sources are refreshed once each morning by the ``sync_all_sources`` cron, so this
    view never fetches upstream — it just reads the stored ``CalendarEvent`` rows.
    """
    try:
        week_offset = max(-52, min(52, int(request.GET.get("week_offset", 0))))
        month_offset = max(-24, min(24, int(request.GET.get("month_offset", 0))))
        event_page = max(1, int(request.GET.get("page", 1)))
    except (ValueError, TypeError):
        week_offset = 0
        month_offset = 0
        event_page = 1
    cal_ctx = _get_calendar_context(request, week_offset=week_offset, month_offset=month_offset, event_page=event_page)
    cal_ctx["events_url"] = reverse("hub_community_calendar_events")
    return render(request, "hub/partials/calendar_content.html", cal_ctx)


def guild_calendar_events_partial(request: HttpRequest, pk: int) -> HttpResponse:
    """HTMX partial — calendar grid/list scoped to one guild (its iCal events plus the
    guild's CMS classes and orientation slots). Drives the Guild Calendar tab's nav."""
    guild = get_object_or_404(Guild, pk=pk)
    try:
        week_offset = max(-52, min(52, int(request.GET.get("week_offset", 0))))
        month_offset = max(-24, min(24, int(request.GET.get("month_offset", 0))))
        event_page = max(1, int(request.GET.get("page", 1)))
    except (ValueError, TypeError):
        week_offset = 0
        month_offset = 0
        event_page = 1
    cal_ctx = _get_calendar_context(
        request, week_offset=week_offset, month_offset=month_offset, event_page=event_page, guild=guild
    )
    cal_ctx["events_url"] = reverse("hub_guild_calendar_events", args=[guild.pk])
    return render(request, "hub/partials/calendar_content.html", cal_ctx)


@login_required
def calendar_export_ics(request: HttpRequest) -> HttpResponse:
    """Download a combined iCal file of all upcoming events."""
    from membership.models import CalendarEvent, CommunityEvent

    now = dj_timezone.now()
    horizon = now + timedelta(days=90)
    # Echo de-dup: exclude the iCal re-import of any event FOG pushed to Google (matched by
    # the stored google_ical_uid), so the export never carries a FOG event twice.
    events = (
        CalendarEvent.objects.filter(start_dt__gte=now, start_dt__lte=horizon)
        .exclude(uid__in=CommunityEvent.objects.pushed().values_list("google_ical_uid", flat=True))
        .select_related("guild")
        .order_by("start_dt")
    )

    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Past Lives Makerspace//Community Calendar//EN",
        "X-WR-CALNAME:Past Lives Community Calendar",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]

    for evt in events:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{evt.uid}",
            f"SUMMARY:{ical_escape(evt.title)}",
        ]
        if evt.all_day:
            lines += [
                f"DTSTART;VALUE=DATE:{evt.start_dt.strftime('%Y%m%d')}",
                f"DTEND;VALUE=DATE:{evt.end_dt.strftime('%Y%m%d')}",
            ]
        else:
            lines += [
                f"DTSTART:{evt.start_dt.strftime('%Y%m%dT%H%M%SZ')}",
                f"DTEND:{evt.end_dt.strftime('%Y%m%dT%H%M%SZ')}",
            ]
        if evt.description:
            lines.append(f"DESCRIPTION:{ical_escape(evt.description[:250])}")
        if evt.location:
            lines.append(f"LOCATION:{evt.location}")
        lines.append("END:VEVENT")

    # FOG-native events build their VEVENT from the shared CommunityEvent.ics_vevent_lines
    # (same lines the per-event .ics uses, so the two never drift). A recurring series
    # emits ONE VEVENT carrying an RRULE the subscriber expands itself. Only PUBLISHED
    # events export (pending/declined proposals never leave FOG).
    for ev in CommunityEvent.objects.published().upcoming().select_related("guild"):
        lines += ev.ics_vevent_lines()

    lines.append("END:VCALENDAR")
    ical_content = "\r\n".join(lines) + "\r\n"

    response = HttpResponse(ical_content, content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="past-lives-calendar.ics"'
    return response


def event_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """The public detail page for a Community Event — the canonical link a QR/flyer/signage
    resolves to. **No login required** so a scanned code opens for anyone.

    Only PUBLISHED events have a page; a pending/changes-requested/declined proposal (or an
    unknown pk) 404s identically via the themed ``404.html`` — no unreviewed proposal ever
    leaks onto a scannable URL, and a missing event never reveals its title.
    """
    from membership.models import CommunityEvent
    from membership.permissions import can_edit_event

    event = get_object_or_404(CommunityEvent.objects.published().select_related("guild"), pk=pk)
    ctx = _get_hub_context(request)
    on_member_surface = getattr(request, "surface", "members") == "members"
    can_edit = on_member_surface and can_edit_event(request, event)
    is_recurring = event.recurrence != CommunityEvent.Recurrence.NONE
    # "Who's coming": names for signed-in viewers, count only for an anonymous QR scan.
    rsvps = list(event.rsvps.select_related("member"))
    member = _get_member(request)
    viewer_rsvped = member is not None and any(rsvp.member_id == member.pk for rsvp in rsvps)
    return render(
        request,
        "hub/event_detail.html",
        {
            **ctx,
            "event": event,
            "can_edit": can_edit,
            "is_recurring": is_recurring,
            # A non-recurring event that has already ended is still viewable; show an honest
            # "already taken place" note. A recurring series is ongoing, so never flag it.
            "show_past_note": not is_recurring and event.ends_at < dj_timezone.now(),
            "rsvps": rsvps,
            "rsvp_count": len(rsvps),
            "viewer_rsvped": viewer_rsvped,
        },
    )


@login_required
@require_POST
def event_rsvp(request: HttpRequest, pk: int) -> HttpResponse:
    """Toggle the signed-in member's RSVP to a published event, then refresh the Discord embed.

    Thin orchestration: the toggle and the best-effort Discord refresh are model methods. An
    unlinked account or a finished non-recurring event is turned away with a friendly message
    (the same "already taken place" gate the page shows), never a 500.
    """
    from membership.models import CommunityEvent, EventRSVP

    event = get_object_or_404(CommunityEvent.objects.published(), pk=pk)
    member = _get_member(request)
    if member is None:
        messages.error(request, "Connect your Past Lives account to RSVP.")
        return redirect("hub_event_detail", pk=pk)
    if event.rsvps_closed:
        messages.info(request, "This event has already taken place.")
        return redirect("hub_event_detail", pk=pk)
    going = event.toggle_rsvp(member, source=EventRSVP.Source.HUB)
    event.refresh_discord_announcement()
    messages.success(request, "You're on the list. See you there." if going else "You're no longer on the list.")
    return redirect("hub_event_detail", pk=pk)


def event_ics(request: HttpRequest, pk: int) -> HttpResponse:
    """The single-event ``.ics`` for the public page's "Add to calendar" button.

    Public (no login) so a flyer scanner can add it to their own calendar; PUBLISHED-only,
    like the page it belongs to.
    """
    from membership.models import CommunityEvent

    event = get_object_or_404(CommunityEvent.objects.published(), pk=pk)
    response = HttpResponse(event.ics_document(), content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="event-{event.pk}.ics"'
    return response


def event_qr(request: HttpRequest, pk: int, fmt: str) -> HttpResponse:
    """Download a published event's public-page QR as SVG (default) or PNG.

    Editor-gated via the shared ``can_edit_event`` check (so it and the "Edit event"
    affordance never drift). An anonymous or non-editor request gets 403 — the download is
    an editor convenience; the public artifact is the page itself.
    """
    from membership.models import CommunityEvent
    from membership.permissions import can_edit_event

    event = get_object_or_404(CommunityEvent.objects.published(), pk=pk)
    if not can_edit_event(request, event):
        return HttpResponse("You don't have access to this event.", status=403)
    if fmt == "svg":
        resp = HttpResponse(event.qr_svg(), content_type="image/svg+xml")
    elif fmt == "png":
        resp = HttpResponse(event.qr_png_bytes(), content_type="image/png")
    else:
        raise Http404
    resp["Content-Disposition"] = f'attachment; filename="event-{event.pk}-qr.{fmt}"'
    return resp


@require_POST
@login_required
def view_as_set(request: HttpRequest) -> JsonResponse:
    """Set the session view-as role.

    Body: ``{"role": "admin"}``. Unknown role names and roles the user does
    not actually hold are rejected so the session can never carry junk or
    grant privileges above what the user already has.
    """
    try:
        payload = json.loads(request.body or b"{}")
        role = payload["role"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return JsonResponse({"error": "Invalid request"}, status=400)

    if role not in ALL_ROLES:
        return JsonResponse({"error": f"Unknown role '{role}'"}, status=400)

    view_as = request.view_as  # type: ignore[attr-defined]
    is_admin = view_as.has_actual("admin")
    if not is_admin and not view_as.has_actual(role):
        return JsonResponse({"error": "Cannot view as a role you don't have"}, status=403)

    request.session[SESSION_ROLE_KEY] = role

    return JsonResponse({"role": role})


@fog_admin_required
def voting_overview(request: HttpRequest) -> HttpResponse:
    """Voting → Overview tab — current-cycle pool stats and live vote leaders (read-only)."""
    from plfog.dashboard import dashboard_callback

    ctx = _get_hub_context(request)
    ctx = dashboard_callback(request, ctx)
    ctx.update(get_cycle_context())
    ctx["active_tab"] = "atglance"
    ctx["pending_results_snapshot"] = FundingSnapshot.most_recent_pending()
    return render(request, "hub/admin/voting_overview.html", ctx)


@fog_admin_required
def voting_history(request: HttpRequest) -> HttpResponse:
    """Voting → Funding History tab — the list of past funding snapshots, newest first."""
    ctx = _get_hub_context(request)
    ctx["snapshots"] = FundingSnapshot.objects.order_by("-snapshot_at")
    ctx["active_tab"] = "history"
    return render(request, "hub/admin/voting_history.html", ctx)


@fog_admin_required
def voting_history_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Voting → Funding History detail — the immutable per-member audit for one snapshot."""
    from membership.vote_analyzer import build_analyzer_context

    snapshot = get_object_or_404(FundingSnapshot, pk=pk)
    ctx = _get_hub_context(request)
    ctx.update(build_analyzer_context(list(snapshot.raw_votes), snapshot=snapshot, get_params=request.GET))
    ctx["active_tab"] = "history"
    return render(request, "hub/admin/voting_history_detail.html", ctx)


@fog_admin_required
def voting_snapshots(request: HttpRequest) -> HttpResponse:
    """Voting → Snapshots tab — the live (draft) analyzer plus the Take-snapshot form."""
    from membership.vote_analyzer import build_analyzer_context, serialize_live_votes

    ctx = _get_hub_context(request)
    ctx.update(build_analyzer_context(serialize_live_votes(), snapshot=None, get_params=request.GET))
    ctx["active_tab"] = "snapshots"
    return render(request, "hub/admin/voting_snapshots.html", ctx)


@fog_admin_required
def voting_settings(request: HttpRequest) -> HttpResponse:
    """Voting → Settings tab — edit the VotingSettings singleton (full-page form + messages)."""
    from hub.forms import VotingSettingsForm
    from membership.models import VotingSettings

    settings_obj = VotingSettings.load()
    form = VotingSettingsForm(request.POST or None, instance=settings_obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Voting settings saved.")
        return redirect("hub_admin_voting_settings")

    ctx = _get_hub_context(request)
    ctx["active_tab"] = "settings"
    ctx["form"] = form
    return render(request, "hub/admin/voting_settings.html", ctx)


@fog_admin_required
@require_POST
def voting_send_results(request: HttpRequest, pk: int) -> HttpResponse:
    """Email this cycle's results to members who voted (HTMX → toast + re-rendered control).

    The admin-confirmed send: ``snapshot.send_results`` loops the frozen votes and
    emails each active voter their personalized allocation + recorded vote. Returns the
    re-rendered Send/Resend control (its new "sent" state) plus an out-of-band swap of
    the Overview "review & send" banner, and a toast — never a Django-messages redirect.
    """
    from membership.models import ResultsAlreadySentError

    snapshot = get_object_or_404(FundingSnapshot, pk=pk)
    resend = request.POST.get("resend") == "1"
    try:
        sent = snapshot.send_results(actor=request.user, resend=resend)
    except ResultsAlreadySentError:
        response = _render_results_send_control(request, snapshot)
        trigger_toast(response, "Those results were already sent.", "error")
        return response

    response = _render_results_send_control(request, snapshot)
    trigger_toast(response, f"Results sent to {sent} member{'' if sent == 1 else 's'}.", "success")
    return response


def _render_results_send_control(request: HttpRequest, snapshot: FundingSnapshot) -> HttpResponse:
    """Render the Send/Resend control + an OOB refresh of the Overview pending banner."""
    return render(
        request,
        "hub/admin/_results_send_control.html",
        {"snapshot": snapshot, "pending_results_snapshot": FundingSnapshot.most_recent_pending(), "oob": True},
    )


@fog_admin_required
@require_POST
def voting_snapshot_take(request: HttpRequest) -> HttpResponse:
    """Commit a snapshot from the current live vote state, then open the new record.

    Filters on the Snapshots tab are analysis-only — the commit always captures
    the full unfiltered live state. Only title and minimum_pool carry over.
    """
    from membership.vote_analyzer import parse_minimum_pool

    title = request.POST.get("title", "").strip()
    minimum_pool = parse_minimum_pool(request.POST.get("minimum_pool"))

    snapshot = FundingSnapshot.take(title=title, minimum_pool=minimum_pool)
    if snapshot is None:
        messages.warning(request, "No votes yet — nothing to snapshot.")
        return redirect("hub_admin_voting_snapshots")

    messages.success(request, f"Snapshot '{snapshot.cycle_label}' created — ${snapshot.funding_pool} pool.")
    return redirect("hub_admin_voting_history_detail", pk=snapshot.pk)


@fog_admin_required
@require_POST
def voting_snapshot_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Hard-delete a snapshot (and its Airtable mirror) and return to the Funding History list."""
    snapshot = get_object_or_404(FundingSnapshot, pk=pk)
    cycle_label = snapshot.cycle_label
    snapshot.delete()
    messages.success(request, f"Deleted snapshot '{cycle_label}'.")
    return redirect("hub_admin_voting_history")


@dataclass(frozen=True)
class PersonRow:
    """A uniform row for the person-centric Manage Members list.

    Both sources — :class:`~membership.models.Member` rows (every one now has a
    user) and ``User`` rows with no Member ("Non-member user") — collapse into this
    so the template renders one table without branching on type.
    """

    name: str
    email: str
    status_display: str  # "" for non-member users (they have no member status)
    role_display: str  # "" for non-member users
    class_count: int  # 0 for non-member users
    classes_url: str  # "" when there are no classes
    edit_url: str
    is_member: bool
    badge_label: str  # "" = no pill (a signed-in member)
    badge_modifier: str  # "neutral" / "danger"
    email_gap_label: str = ""  # only populated for the Missing-email report


def _person_status_badge(*, is_member: bool, has_signed_in: bool) -> tuple[str, str]:
    """The (label, hub-pill modifier) for a person's sign-in status badge."""
    if not is_member:
        return ("Non-member user", "danger")
    if has_signed_in:
        return ("Signed in", "ok")
    return ("Hasn't signed in yet", "neutral")


def _user_primary_email(user: User) -> str:
    """The user's primary allauth EmailAddress — NEVER the User.email mirror.

    Uses the list-view ``_primary_emailaddresses`` prefetch when present (avoids an
    N+1), else a single targeted query. Returns "" when no primary row exists.
    """
    prefetched = getattr(user, "_primary_emailaddresses", None)
    if prefetched is not None:
        return prefetched[0].email if prefetched else ""
    primary = EmailAddress.objects.filter(user_id=user.pk, primary=True).first()
    return primary.email if primary else ""


def _member_person_row(member: Member) -> PersonRow:
    """Build the uniform list row for a Member (prefetch ``user`` to avoid N+1)."""
    user = member.user
    has_signed_in = bool(user and user.last_login)
    badge_label, badge_modifier = ("", "") if has_signed_in else ("Hasn't signed in yet", "neutral")
    classes_url = (
        f"{reverse('classes:admin_classes')}?instructor={member.pk}" if getattr(member, "class_count", 0) else ""
    )
    return PersonRow(
        name=member.full_legal_name or member.display_name or "—",
        email=member.primary_email or "—",
        status_display=member.get_status_display(),
        role_display=member.get_fog_role_display(),
        class_count=getattr(member, "class_count", 0),
        classes_url=classes_url,
        edit_url=reverse("hub_admin_member_edit", args=[member.pk]),
        is_member=True,
        badge_label=badge_label,
        badge_modifier=badge_modifier,
        email_gap_label=member.email_gap_label if hasattr(member, "email_gap") else "",
    )


def _nonmember_person_row(user: User) -> PersonRow:
    """Build the uniform list row for a User with no Member ("Non-member user")."""
    return PersonRow(
        name=user.get_full_name() or user.username or "—",
        email=_user_primary_email(user) or "—",
        status_display="",
        role_display="",
        class_count=0,
        classes_url="",
        edit_url=reverse("hub_admin_user_edit", args=[user.pk]),
        is_member=False,
        badge_label="Non-member user",
        badge_modifier="danger",
    )


class _PersonRowList:
    """A sliceable, countable sequence of :class:`PersonRow` for ``Paginator``.

    Members (ordered by name) come first, then non-member users (ordered by email),
    and ``Paginator`` pages over the union without materializing the whole thing —
    only the requested slice is turned into rows.
    """

    def __init__(self, members_qs: Any, nonmembers_qs: Any) -> None:
        self._members = members_qs
        self._nonmembers = nonmembers_qs
        self._member_count = members_qs.count()
        self._nonmember_count = nonmembers_qs.count()

    def count(self) -> int:
        return self._member_count + self._nonmember_count

    def __len__(self) -> int:
        return self.count()

    def __getitem__(self, item: slice) -> list[PersonRow]:
        start, stop, _step = item.indices(self.count())
        rows: list[PersonRow] = []
        if start < self._member_count:
            member_stop = min(stop, self._member_count)
            rows.extend(_member_person_row(m) for m in self._members[start:member_stop])
        nm_start = max(0, start - self._member_count)
        nm_stop = max(0, stop - self._member_count)
        if nm_stop > nm_start:
            rows.extend(_nonmember_person_row(u) for u in self._nonmembers[nm_start:nm_stop])
        return rows


def _primary_email_prefetch(relation: str) -> Prefetch:
    """A Prefetch of just the primary EmailAddress rows under ``relation``.

    ``to_attr="_primary_emailaddresses"`` is the hook ``Member.primary_email`` and
    :func:`_user_primary_email` both read, so the email column never hits the DB
    per-row and never reads the ``User.email`` mirror.
    """
    return Prefetch(
        relation,
        queryset=EmailAddress.objects.filter(primary=True),
        to_attr="_primary_emailaddresses",
    )


@fog_admin_required
def admin_members(request: HttpRequest) -> HttpResponse:
    """Person-centric Manage Members list — members + non-member users, unioned.

    Members (every one provisioned → has a user) are listed first, then ``User``
    rows with no Member ("Non-member user", superusers excluded so the owner's admin
    login isn't listed). Member-only filters (status/role/type/email) narrow to
    members and hide non-member users; search matches both.
    """
    from django.core.paginator import Paginator

    from membership.forms import AddMemberForm, InviteMemberForm

    ctx = _get_hub_context(request)
    status_filter = request.GET.get("status", "active")
    role_filter = request.GET.get("role", "")
    type_filter = request.GET.get("type", "")
    email_filter = request.GET.get("email", "")
    search = request.GET.get("q", "").strip()

    members = (
        Member.objects.select_related("user", "membership_plan")
        .annotate(class_count=Count("classes", distinct=True))
        .prefetch_related(_primary_email_prefetch("user__emailaddress_set"))
        .order_by("full_legal_name")
    )
    if status_filter and status_filter != "all":
        members = members.filter(status=status_filter)
    if role_filter:
        members = members.filter(fog_role=role_filter)
    if type_filter:
        members = members.filter(member_type=type_filter)
    if search:
        members = members.filter(
            Q(full_legal_name__icontains=search)
            | Q(preferred_name__icontains=search)
            | Q(user__email__icontains=search)
            | Q(discord_handle__icontains=search)
        )

    missing_count = members.missing_email().count()  # emailless within the current filters
    if email_filter == "missing":
        members = members.missing_email()  # page rows now carry has_email + email_gap

    # Non-member users join the list only when no member-only filter is narrowing it.
    # The default status ("active") and "all" are non-narrowing so the default view
    # shows everyone; any other status, or a role/type/missing-email filter, hides
    # them (they have no such fields). Search still matches their email.
    member_only_filter_active = bool(
        role_filter or type_filter or email_filter == "missing" or status_filter not in ("", "all", "active")
    )
    nonmembers = User.objects.none()
    if not member_only_filter_active:
        nonmembers = (
            User.objects.filter(member__isnull=True, is_superuser=False)
            .prefetch_related(_primary_email_prefetch("emailaddress_set"))
            .order_by("email", "username")
        )
        if search:
            nonmembers = nonmembers.filter(
                Q(emailaddress__email__icontains=search) | Q(username__icontains=search)
            ).distinct()

    person_list: Any = _PersonRowList(members, nonmembers)
    paginator = Paginator(person_list, 50)
    page = paginator.get_page(request.GET.get("page", 1))
    return render(
        request,
        "hub/admin/members.html",
        {
            **ctx,
            "page": page,
            "status_filter": status_filter,
            "role_filter": role_filter,
            "type_filter": type_filter,
            "email_filter": email_filter,
            "missing_count": missing_count,
            "member_only_filter_active": member_only_filter_active,
            "search": search,
            "status_choices": Member.Status.choices,
            "role_choices": Member.FogRole.choices,
            "type_choices": Member.MemberType.choices,
            "invite_form": InviteMemberForm(),
            "add_form": AddMemberForm(),
            **_invites_panel_context(),
        },
    )


@fog_admin_required
def admin_member_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Hub-native tabbed edit page for a single Member (Details, Permissions, Notifications, Emails).

    Three independent save forms, dispatched by a hidden ``form_id``: the Details form
    (role + profile), the Permissions capability toggles, and the Notifications tab's
    matrix (so an admin can edit a member's notification preferences for them).
    """
    from core.events import settings_matrix

    member = get_object_or_404(Member, pk=pk)
    permissions_url = f"{reverse('hub_admin_member_edit', args=[member.pk])}?tab=permissions"

    if request.method == "POST":
        form_id = request.POST.get("form_id")
        if form_id == "capabilities":
            cap_form = MemberCapabilitiesForm(request.POST)
            if cap_form.is_valid():
                member.sync_admin_capabilities(cap_form.selected(), granted_by=cast(User, request.user))
                messages.success(request, "Saved admin capabilities.")
            return redirect(permissions_url)
        if form_id == "notifications":
            target = member.user
            if target is not None:
                settings_matrix.save_matrix(target, request.POST)
                messages.success(request, "Saved notification settings.")
            return redirect(permissions_url)
        form = MemberAdminEditForm(request.POST, instance=member)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.save()
            obj.apply_admin_role(form.cleaned_data["role"])
            display = obj.full_legal_name or obj.primary_email or f"member #{obj.pk}"
            messages.success(request, f"Saved {display}.")
            return redirect("hub_admin_members")
    else:
        form = MemberAdminEditForm(instance=member)

    user = member.user
    has_signed_in = bool(user and user.last_login)
    status_label, status_modifier = _person_status_badge(is_member=True, has_signed_in=has_signed_in)
    email_rows = (
        _email_rows(
            user,
            owner_pk=member.pk,
            set_primary="hub_admin_member_email_set_primary",
            toggle="hub_admin_member_email_toggle_verified",
            remove="hub_admin_member_email_remove",
        )
        if user
        else []
    )
    # Permissions tab: capability toggles (always) + this member's notification matrix
    # (only when they have a linked account to hold preferences on).
    cap_form = MemberCapabilitiesForm(initial=MemberCapabilitiesForm.initial_for(member))
    notif_matrix = notif_channels = notif_channel_labels = None
    if user is not None:
        notif_matrix = settings_matrix.build_matrix(user)
        notif_channels = [(c, settings_matrix.CHANNEL_LABELS[c]) for c in settings_matrix.visible_channels(user)]
        notif_channel_labels = {channel.value: label for channel, label in notif_channels}
    ctx = _get_hub_context(request)
    return render(
        request,
        "hub/admin/member_edit.html",
        {
            **ctx,
            "is_member": True,
            "member": member,
            "form": form,
            "capabilities_form": cap_form,
            "notif_matrix": notif_matrix,
            "notif_channels": notif_channels,
            "notif_channel_labels": notif_channel_labels,
            "person_name": member.full_legal_name or member.display_name or "Member",
            "primary_email": member.primary_email,
            "has_signed_in": has_signed_in,
            "has_user": member.user_id is not None,
            "status_label": status_label,
            "status_modifier": status_modifier,
            "email_rows": email_rows,
            "email_add_form": _email_add_form(member),
            "email_add_url": reverse("hub_admin_member_email_add", args=[member.pk]),
            "send_login_invite_url": reverse("hub_admin_member_send_login_invite", args=[member.pk]),
        },
    )


@fog_admin_required
@require_POST
def admin_member_teaching_set(request: HttpRequest, pk: int) -> HttpResponse:
    """Grant or revoke the Instructor permission from the member edit Permissions tab.

    Grants/revokes the public instructor page AND teaching access together (the unified
    Instructor toggle). Full-page POST + Django message, matching this page's sibling actions.
    ``action`` must be ``grant`` or ``revoke``; anything else is a 400 (never
    reachable from the UI). Revoke's consequences are named in the confirm modal.
    """
    member = get_object_or_404(Member, pk=pk)
    action = request.POST.get("action", "")
    if action not in ("grant", "revoke"):
        return HttpResponseBadRequest("Unknown action.")
    assert request.user.is_authenticated  # fog_admin_required guarantees a real User
    # None = a superuser acting without a linked Member (emergency access).
    admin_member = Member.objects.filter(user=request.user).first()
    display = member.display_name or member.full_legal_name or f"member #{member.pk}"
    if action == "grant":
        member.grant_instructor(granted_by=admin_member)
        messages.success(request, f"Made {display} an instructor (public page + teaching access).")
    else:
        member.revoke_instructor(revoked_by=admin_member)
        messages.success(request, f"Removed instructor access for {display}.")
    return redirect(f"{reverse('hub_admin_member_edit', args=[member.pk])}?tab=permissions")


@fog_admin_required
def admin_user_edit(request: HttpRequest, user_pk: int) -> HttpResponse:
    """Edit page for a non-member User — same tabbed shell, "non-member user" mode.

    Details is read-only identity (no ``MemberAdminEditForm``); the Emails tab
    manages the user's allauth ``EmailAddress`` rows via the user-keyed endpoints.
    A user who actually has a Member is bounced to the member edit page so the two
    routes never disagree about which page owns a person.
    """
    user = get_object_or_404(User, pk=user_pk)
    member = Member.objects.filter(user=user).first()
    if member is not None:
        return redirect("hub_admin_member_edit", pk=member.pk)

    has_signed_in = bool(user.last_login)
    status_label, status_modifier = _person_status_badge(is_member=False, has_signed_in=has_signed_in)
    ctx = _get_hub_context(request)
    return render(
        request,
        "hub/admin/member_edit.html",
        {
            **ctx,
            "is_member": False,
            "target_user": user,
            "person_name": user.get_full_name() or user.username,
            "primary_email": _user_primary_email(user),
            "has_signed_in": has_signed_in,
            "has_user": True,
            "status_label": status_label,
            "status_modifier": status_modifier,
            "email_rows": _email_rows(
                user,
                owner_pk=user.pk,
                set_primary="hub_admin_user_email_set_primary",
                toggle="hub_admin_user_email_toggle_verified",
                remove="hub_admin_user_email_remove",
            ),
            "email_add_form": _user_email_add_form(user),
            "email_add_url": reverse("hub_admin_user_email_add", args=[user.pk]),
        },
    )


@fog_admin_required
@require_POST
def admin_member_send_login_invite(request: HttpRequest, pk: int) -> HttpResponse:
    """POST-only (HTMX) — email a not-signed-in member a first-time sign-in link.

    Calls the Phase-1 ``Member.send_login_invite`` (a distinct path from the
    "already a member" invite guard). Returns 204 + a toast either way.
    """
    member = get_object_or_404(Member, pk=pk)
    response = HttpResponse(status=204)
    try:
        member.send_login_invite()
    except ValueError as exc:
        trigger_toast(response, str(exc), "error")
        return response
    trigger_toast(response, f"Login invite sent to {member.primary_email}.", "success")
    return response


def _email_rows(user: User, *, owner_pk: int, set_primary: str, toggle: str, remove: str) -> list[dict[str, Any]]:
    """A user's allauth EmailAddress rows (primary first) with per-row action URLs.

    Shared by the member-keyed and user-keyed edit pages so the Emails tab renders
    one list regardless of whether the person is a Member or a non-member User.
    """
    return [
        {
            "obj": ea,
            "set_primary_url": reverse(set_primary, args=[owner_pk, ea.pk]),
            "toggle_url": reverse(toggle, args=[owner_pk, ea.pk]),
            "remove_url": reverse(remove, args=[owner_pk, ea.pk]),
        }
        for ea in EmailAddress.objects.filter(user=user).order_by("-primary", "email")
    ]


def _email_add_form(member: Member, data: Any = None) -> Any:
    """AddEmailAliasForm bound to the member's user, or None if no linked user."""
    if member.user_id is None:
        return None
    from membership.forms import AddEmailAliasForm

    return AddEmailAliasForm(data, user=member.user)


def _user_email_add_form(user: User, data: Any = None) -> Any:
    """AddEmailAliasForm bound to a non-member User."""
    from membership.forms import AddEmailAliasForm

    return AddEmailAliasForm(data, user=user)


def _apply_alias_action(request: HttpRequest, user: User, email_pk: int, action: Any) -> None:
    """Run an ``email_aliases`` mutation on one of the user's addresses and flash it."""
    alias = get_object_or_404(EmailAddress, pk=email_pk, user=user)
    for level, msg in action(alias):
        getattr(messages, level)(request, msg)


def _email_member_or_redirect(pk: int) -> tuple[Member | None, HttpResponse | None]:
    """Fetch the member for an email action; redirect back to edit if no linked user."""
    member = get_object_or_404(Member, pk=pk)
    if member.user_id is None:
        return None, redirect("hub_admin_member_edit", pk=member.pk)
    return member, None


@fog_admin_required
@require_POST
def admin_member_email_add(request: HttpRequest, pk: int) -> HttpResponse:
    """POST-only — add a verified, non-primary email alias to a member from the hub edit page."""
    from membership import email_aliases

    member, early = _email_member_or_redirect(pk)
    if member is None:
        return cast(HttpResponse, early)

    form = _email_add_form(member, request.POST)
    if form.is_valid():
        for level, msg in email_aliases.add_alias(member.user, form.cleaned_data["email"]):
            getattr(messages, level)(request, msg)
    else:
        # AddEmailAliasForm has a single required ``email`` field, so an invalid
        # form always carries an email error.
        messages.error(request, form.errors["email"][0])
    return redirect("hub_admin_member_edit", pk=member.pk)


@fog_admin_required
@require_POST
def admin_member_email_remove(request: HttpRequest, pk: int, email_pk: int) -> HttpResponse:
    """POST-only — remove an email alias from a member (with the lock-out safety rules)."""
    from membership import email_aliases

    member, early = _email_member_or_redirect(pk)
    if member is None:
        return cast(HttpResponse, early)

    _apply_alias_action(request, cast(User, member.user), email_pk, email_aliases.remove_alias)
    return redirect("hub_admin_member_edit", pk=member.pk)


@fog_admin_required
@require_POST
def admin_member_email_set_primary(request: HttpRequest, pk: int, email_pk: int) -> HttpResponse:
    """POST-only — promote a verified alias to the member's primary email."""
    from membership import email_aliases

    member, early = _email_member_or_redirect(pk)
    if member is None:
        return cast(HttpResponse, early)

    _apply_alias_action(request, cast(User, member.user), email_pk, email_aliases.set_primary)
    return redirect("hub_admin_member_edit", pk=member.pk)


@fog_admin_required
@require_POST
def admin_member_email_toggle_verified(request: HttpRequest, pk: int, email_pk: int) -> HttpResponse:
    """POST-only — flip the verified flag on a member's email alias."""
    from membership import email_aliases

    member, early = _email_member_or_redirect(pk)
    if member is None:
        return cast(HttpResponse, early)

    _apply_alias_action(request, cast(User, member.user), email_pk, email_aliases.toggle_verified)
    return redirect("hub_admin_member_edit", pk=member.pk)


def _email_user_or_redirect(user_pk: int) -> tuple[User | None, HttpResponse | None]:
    """Fetch the non-member User for an email action; bounce to member edit if linked."""
    user = get_object_or_404(User, pk=user_pk)
    member = Member.objects.filter(user=user).first()
    if member is not None:
        return None, redirect("hub_admin_member_edit", pk=member.pk)
    return user, None


@fog_admin_required
@require_POST
def admin_user_email_add(request: HttpRequest, user_pk: int) -> HttpResponse:
    """POST-only — add a verified, non-primary alias to a non-member User."""
    from membership import email_aliases

    user, early = _email_user_or_redirect(user_pk)
    if user is None:
        return cast(HttpResponse, early)

    form = _user_email_add_form(user, request.POST)
    if form.is_valid():
        for level, msg in email_aliases.add_alias(user, form.cleaned_data["email"]):
            getattr(messages, level)(request, msg)
    else:
        messages.error(request, form.errors["email"][0])
    return redirect("hub_admin_user_edit", user_pk=user.pk)


@fog_admin_required
@require_POST
def admin_user_email_remove(request: HttpRequest, user_pk: int, email_pk: int) -> HttpResponse:
    """POST-only — remove an alias from a non-member User (with the lock-out safety rules)."""
    from membership import email_aliases

    user, early = _email_user_or_redirect(user_pk)
    if user is None:
        return cast(HttpResponse, early)

    _apply_alias_action(request, user, email_pk, email_aliases.remove_alias)
    return redirect("hub_admin_user_edit", user_pk=user.pk)


@fog_admin_required
@require_POST
def admin_user_email_set_primary(request: HttpRequest, user_pk: int, email_pk: int) -> HttpResponse:
    """POST-only — promote a verified alias to a non-member User's primary email."""
    from membership import email_aliases

    user, early = _email_user_or_redirect(user_pk)
    if user is None:
        return cast(HttpResponse, early)

    _apply_alias_action(request, user, email_pk, email_aliases.set_primary)
    return redirect("hub_admin_user_edit", user_pk=user.pk)


@fog_admin_required
@require_POST
def admin_user_email_toggle_verified(request: HttpRequest, user_pk: int, email_pk: int) -> HttpResponse:
    """POST-only — flip the verified flag on a non-member User's email alias."""
    from membership import email_aliases

    user, early = _email_user_or_redirect(user_pk)
    if user is None:
        return cast(HttpResponse, early)

    _apply_alias_action(request, user, email_pk, email_aliases.toggle_verified)
    return redirect("hub_admin_user_edit", user_pk=user.pk)


def _invites_panel_context() -> dict[str, Any]:
    """Split the invites card into its default rows and the collapsed expired ones.

    ``invites`` are what show by default (pending + recently-accepted); ``expired_invites``
    are un-accepted invites past the expiry window, hidden behind a count so the panel
    isn't a wall of dead invites. Both derive from the one ``for_management_panel`` query,
    partitioned in Python via the cheap ``is_expired`` property (no extra DB hits).
    """
    from core.models import Invite

    panel = list(Invite.objects.for_management_panel())
    return {
        "invites": [invite for invite in panel if not invite.is_expired],
        "expired_invites": [invite for invite in panel if invite.is_expired],
    }


def _render_invites_panel(request: HttpRequest) -> HttpResponse:
    """Render the swappable outstanding-invites panel with a fresh queryset."""
    return render(request, "hub/admin/_invites_panel.html", _invites_panel_context())


@fog_admin_required
@require_POST
def admin_member_invite(request: HttpRequest) -> HttpResponse:
    """Send a single invite (HTMX) — re-renders the invites panel plus a toast.

    Reuses InviteMemberForm for validation and Invite.create_and_send for the work.
    On success returns the refreshed panel (200, swaps #invites-list); on a validation
    error or a create_and_send ValueError returns 204 so HTMX makes no swap and the
    form stays open, carrying the message as an error toast.
    """
    from core.models import Invite
    from membership.forms import InviteMemberForm

    form = InviteMemberForm(request.POST)
    if not form.is_valid():
        response = HttpResponse(status=204)
        trigger_toast(response, str(form.errors["email"][0]), "error")
        return response

    email = form.cleaned_data["email"]
    try:
        Invite.create_and_send(email=email, invited_by=request.user)
    except ValueError as exc:
        response = HttpResponse(status=204)
        trigger_toast(response, str(exc), "error")
        return response

    response = _render_invites_panel(request)
    trigger_toast(response, f"Invite sent to {email}.", "success")
    return response


@fog_admin_required
@require_POST
def admin_member_create(request: HttpRequest) -> HttpResponse:
    """Create a member directly (HTMX), no invite and no email.

    Mirrors the invite flow's gating and POST-only shape. On a valid form the member
    is created and we hand the browser a full navigation back to the members list
    (``HX-Redirect``) carrying a success message, so the new person shows up in the
    table straight away. On a validation error we re-render just the form partial
    (200, swaps itself) so the typed values and field errors survive.
    """
    from membership.forms import AddMemberForm

    form = AddMemberForm(request.POST)
    if not form.is_valid():
        return render(request, "hub/admin/_add_member_form.html", {"add_form": form})

    member = form.create_member()
    messages.success(request, f"Added {member.display_name} to the roster.")
    response = HttpResponse(status=204)
    response["HX-Redirect"] = reverse("hub_admin_members")
    return response


@fog_admin_required
@require_POST
def admin_invite_resend(request: HttpRequest, pk: int) -> HttpResponse:
    """Re-fire the invite email for an un-accepted invite (HTMX) — refreshed panel + toast."""
    from core.models import Invite

    invite = get_object_or_404(Invite, pk=pk)
    if not invite.is_pending:
        response = HttpResponse(status=204)
        trigger_toast(response, "That invite was already accepted.", "error")
        return response

    invite.send_invite_email()
    response = _render_invites_panel(request)
    trigger_toast(response, f"Invite resent to {invite.email}.", "success")
    return response


@fog_admin_required
@require_POST
def admin_invite_revoke(request: HttpRequest, pk: int) -> HttpResponse:
    """Revoke an un-accepted invite (full-page POST from confirm_modal) — redirect + message."""
    from core.models import Invite

    invite = get_object_or_404(Invite, pk=pk)
    email = invite.email
    try:
        invite.revoke()
        messages.success(request, f"Revoked the invite for {email}.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("hub_admin_members")


@fog_admin_required
@require_POST
def admin_invite_clear_expired(request: HttpRequest) -> HttpResponse:
    """Revoke every expired invite at once (HTMX) — refreshed panel + toast."""
    from core.models import Invite

    count = Invite.objects.clear_expired()
    response = _render_invites_panel(request)
    noun = "invite" if count == 1 else "invites"
    trigger_toast(response, f"Cleared {count} expired {noun}.", "success")
    return response


def _legacy_instructor_sync_status() -> tuple[list[dict[str, object]], int]:
    """Return instructor match stats for the CMS tab.

    For each member with an instructor slug, count how many ClassOfferings came
    from the legacy CMS (legacy_cms_id set) and have that member linked as instructor.
    """
    from classes.models import ClassOffering

    from membership.models import Member as MemberModel

    rows = []
    for member in MemberModel.objects.filter(instructor_slug__gt="").order_by("full_legal_name"):
        matched = ClassOffering.objects.filter(
            legacy_cms_id__gt="",
            instructor=member,
        ).count()
        rows.append(
            {
                "instructor": member,
                "matched": matched,
            }
        )
    # Also count unmatched offerings (has legacy_cms_id but instructor is null)
    unmatched = ClassOffering.objects.filter(legacy_cms_id__gt="", instructor__isnull=True).count()
    return rows, unmatched


def _activated_member_count() -> int:
    """How many *activated* members a sitewide announcement reaches right now.

    Exactly the ALL_ACTIVE_MEMBERS resolver audience (active members who have signed in
    and carry a usable email), so the preview's count matches who actually gets it.
    """
    from core.events import resolvers
    from core.events.registry import Recipients

    return len(resolvers.resolve(Recipients.ALL_ACTIVE_MEMBERS, {}))


def _release_announcement_initial() -> dict[str, str]:
    """Prefilled subject / preheader / intro for a fresh Release-mode draft."""
    from core.release_email import current_line_entries
    from plfog.version import VERSION

    entries = current_line_entries(VERSION)
    latest_title = str(entries[0]["title"]) if entries else "What's new"
    return {
        "subject": f"Heads-Up: New Member Portal Features — {latest_title}",
        "preheader": latest_title,
        "intro": "<p>We've just shipped an update — here's what's new.</p>",
    }


def _send_release_announcement(request: HttpRequest, subject: str, html: str, text: str, summary: str) -> int:
    """Fire the release-update ``site_announcement`` to activated members; return the count.

    A per-channel EMAIL override on the spine, Discord suppressed (the GitHub Action
    auto-posts on merge), and a **timestamp-unique** period (NOT version-keyed) so an admin
    who caught a bad card can send a corrected version and have it actually deliver.
    """
    from core.events.channels import Channel, Message
    from core.events.emit import emit

    site_url = request.build_absolute_uri("/")
    email = Message(title=subject, body=text, url=site_url, html_body=html, trigger_kind="site_announcement")
    result = emit(
        "site_announcement",
        actor=request.user if request.user.is_authenticated else None,
        context={
            "member_name": "there",
            "announcement_title": subject,
            "announcement_body": summary,
            "site_url": site_url,
        },
        url=site_url,
        period=f"site:{dj_timezone.now():%Y%m%d%H%M%S%f}",
        messages={Channel.EMAIL: email},
        suppress_broadcast=True,
    )
    return result.recipient_count


def _handle_release_announcement_action(
    request: HttpRequest, action: str
) -> tuple[HttpResponse | None, ReleaseAnnouncementForm, dict[str, object] | None]:
    """Process a Release-mode Announcements POST. Returns ``(redirect_or_none, form, preview)``.

    ``announce_send`` (valid) redirects; ``announce_preview`` / ``announce_test`` (valid)
    re-render with the assembled preview (test additionally sends a copy to the admin and
    flashes a success message — a direct send, never the spine). An invalid form re-renders
    with errors.
    """
    from core.html_sanitize import render_rich_email_text
    from core.release_email import render_release_email, send_release_test

    form = ReleaseAnnouncementForm(request.POST)
    if not form.is_valid():
        return None, form, None

    admin_user = cast(User, request.user)  # @fog_admin_required guarantees an authed admin
    subject = form.cleaned_data["subject"]
    preheader = form.cleaned_data["preheader"]
    intro = form.cleaned_data["intro"]
    cards = form.cleaned_cards()
    html, text = render_release_email(form.version, subject=subject, preheader=preheader, intro=intro, cards=cards)

    if action == "announce_send":
        summary = render_rich_email_text(intro).strip() or "See what's new at Past Lives."
        count = _send_release_announcement(request, subject, html, text, summary)
        messages.success(request, f"Release update sent to {count} member(s).")
        return redirect(f"{reverse('hub_admin_site_settings')}?tab=announcements"), form, None

    if action == "announce_test":
        send_release_test(admin_user, html, text, subject)
        messages.success(request, f"Test sent to {admin_user.email} — check your inbox.")

    preview: dict[str, object] = {
        "html": html,
        "text": text,
        "count": _activated_member_count(),
        "subject": subject,
        "preheader": preheader,
    }
    return None, form, preview


def _discord_editor_querysets() -> tuple[Any, Any]:
    """Querysets backing the Discord tab's emoji map (D2) and per-guild role table (D3)."""
    from membership.models import DiscordGuildEmoji

    return DiscordGuildEmoji.objects.all(), Guild.objects.filter(is_active=True).order_by("name")


def _automation_jobstate_queryset() -> Any:
    """State rows for the toggleable jobs — non-toggleable ``bill_tabs`` is Run-now only, so it
    has no editable toggle (Decision 2). Seeds any missing rows first so the formset always has
    one to bind (feeds pattern; the panel is always in the DOM)."""
    from core.models import ScheduledJobState
    from core.scheduled_jobs import SCHEDULED_JOBS

    ScheduledJobState.objects.sync_registry()
    toggleable_keys = [job.key for job in SCHEDULED_JOBS if job.toggleable]
    return ScheduledJobState.objects.filter(task_key__in=toggleable_keys)


def _build_automation_rows(formset: Any) -> list[dict[str, Any]]:
    """Pair every registry job with its bound toggle form (matched by ``task_key``, never by
    position — §11 #4) and its latest run, so the Automations panel loops once."""
    from core.models import ScheduledTaskRun
    from core.scheduled_jobs import SCHEDULED_JOBS

    forms_by_key = {form.instance.task_key: form for form in formset.forms}
    latest = ScheduledTaskRun.objects.latest_per_task()
    return [{"job": job, "form": forms_by_key.get(job.key), "last_run": latest.get(job.key)} for job in SCHEDULED_JOBS]


def _bind_jobstate_formset(request: HttpRequest) -> tuple[Any, bool]:
    """Bind the Automations toggle formset when its management form is posted, else build an
    unbound one over the synced rows. Returns ``(formset, was_posted)``."""
    queryset = _automation_jobstate_queryset()
    if "jobstates-TOTAL_FORMS" in request.POST:
        return ScheduledJobStateFormSet(request.POST, queryset=queryset, prefix="jobstates"), True
    return ScheduledJobStateFormSet(queryset=queryset, prefix="jobstates"), False


def _save_jobstate_formset(formset: Any, was_posted: bool) -> None:
    """Save the Automations toggles independently of the main settings save, so a jobstate issue
    can never block another tab from saving (§11 #11)."""
    if was_posted and formset.is_valid():
        formset.save()


def _resolve_automation_context(bound_formset: Any) -> tuple[list[dict[str, Any]], Any]:
    """The Automations tab context: reuse a bound formset from a failed save (preserving typed
    toggle state), else a fresh one over the synced rows. Returns ``(rows, formset)``."""
    formset = bound_formset or ScheduledJobStateFormSet(queryset=_automation_jobstate_queryset(), prefix="jobstates")
    return _build_automation_rows(formset), formset


def _persist_automation_toggles(request: HttpRequest, config: Any) -> None:
    """Persist the Automations-tab toggle edits that ride along a Run-now POST (Decision 7):
    clicking Run now must not silently discard an unsaved toggle flip.

    Explicitly saves ONLY the Automations toggles — the per-job jobstate formset and the
    legacy-CMS-sync checkbox — never the full ``SiteSettingsForm``. The run_job path must not
    write the settings singleton: the bill_tabs confirm modal posts only ``run_job`` + csrf (no
    management form), so the ``jobstates-TOTAL_FORMS`` marker is absent and this is a no-op; an
    Automations run-now posts the shared form but we persist just its toggles and ignore every
    other field. Never blocks or messages."""
    if "jobstates-TOTAL_FORMS" not in request.POST:
        return
    formset = ScheduledJobStateFormSet(request.POST, queryset=_automation_jobstate_queryset(), prefix="jobstates")
    if formset.is_valid():
        formset.save()
    # The legacy CMS sync toggle sits on the same Automations form — persist just that one
    # checkbox field (present = on) without binding or writing the rest of the singleton.
    config.legacy_cms_sync_enabled = "legacy_cms_sync_enabled" in request.POST
    config.save(update_fields=["legacy_cms_sync_enabled"])


def _handle_run_job(request: HttpRequest, config: Any) -> HttpResponse:
    """Run one scheduled job now (Decision 1/4/7). Resolves the job from the registry — an unknown
    key errors with no dispatch — persists any unsaved toggle edits first, then runs the command
    with NO ``--force`` inside ``record_run``. A raising command becomes a FAILED run + an error
    message, never a 500 (mirrors the legacy ``sync_now`` handler)."""
    from django.core.management import call_command

    from core.scheduled_jobs import JOBS_BY_KEY, Trigger, record_run

    key = request.POST.get("run_job", "")
    redirect_url = f"{reverse('hub_admin_site_settings')}?tab=automations"
    job = JOBS_BY_KEY.get(key)
    if job is None:
        messages.error(request, f"Unknown automation '{key}'.")
        return redirect(redirect_url)

    _persist_automation_toggles(request, config)
    try:
        with record_run(job.key, trigger=Trigger.MANUAL, actor=request.user if request.user.is_authenticated else None):
            call_command(job.command)
        messages.success(request, f"Ran {job.name}.")
    except Exception as exc:  # noqa: BLE001 — surface any command failure as a message, never a 500
        messages.error(request, f"Ran {job.name} — failed: {exc}")
    return redirect(redirect_url)


# The Site Settings fields that drive the pinned #important-info Discord post; changing any
# of them on save re-syncs the post in place.
_INFO_POST_FIELDS = frozenset({"discord_info_channel_id", "discord_info_message_id", "discord_info_links_content"})


def _sync_info_post_if_changed(request: HttpRequest, form: SiteSettingsForm) -> None:
    """Push the #important-info pinned post after a save that touched its fields.

    Runs only when one of :data:`_INFO_POST_FIELDS` actually changed (an unrelated tab's
    save never calls Discord). A Discord failure surfaces loudly as an admin-facing error
    message — the local content is already saved, so the admin can just hit Save again.
    """
    from core.integrations.discord_channel import DiscordChannelError

    from hub.discord_info_post import sync_info_post

    if not _INFO_POST_FIELDS & set(form.changed_data):
        return
    try:
        sync_info_post()
    except DiscordChannelError as exc:
        messages.error(
            request,
            f"Settings saved, but updating the pinned #important-info Discord post failed: {exc}",
        )


def _save_site_settings(
    request: HttpRequest, config: Any, feed_queryset: Any, active_tab: str
) -> tuple[HttpResponse | None, SiteSettingsForm, Any, Any, Any, Any]:
    """Bind + save the settings form, calendar formset, and (Discord tab only) the emoji
    map + per-guild role formsets. Returns ``(redirect_or_none, form, feed_formset,
    emoji_formset, role_formset)``.

    The Discord formsets are bound + validated + saved ONLY when the Discord tab posted
    (``submitted_tab == "discord"``) so saving any other tab never requires the Discord
    management forms; on a Discord validation error the bound formsets are returned so no
    typed value is lost.
    """
    emoji_queryset, role_queryset = _discord_editor_querysets()
    is_discord = request.POST.get("submitted_tab") == "discord"
    form = SiteSettingsForm(request.POST, instance=config)
    feed_formset = CalendarFeedFormSet(request.POST, queryset=feed_queryset, prefix="feeds")
    # The Automations toggle formset rides the shared form (its panel is always in the DOM). Bind
    # it when posted; it's saved independently below so a jobstate hiccup can never block another
    # tab's save (§11 #11).
    jobstate_formset, jobstate_posted = _bind_jobstate_formset(request)
    if is_discord:
        emoji_formset = DiscordGuildEmojiFormSet(request.POST, queryset=emoji_queryset, prefix="emoji")
        role_formset = GuildRoleFormSet(request.POST, queryset=role_queryset, prefix="guildroles")
    else:
        emoji_formset = DiscordGuildEmojiFormSet(queryset=emoji_queryset, prefix="emoji")
        role_formset = GuildRoleFormSet(queryset=role_queryset, prefix="guildroles")

    discord_ok = (emoji_formset.is_valid() and role_formset.is_valid()) if is_discord else True
    if form.is_valid() and feed_formset.is_valid() and discord_ok:
        form.save()
        _sync_info_post_if_changed(request, form)
        instances = feed_formset.save(commit=False)
        for obj in feed_formset.deleted_objects:
            obj.delete()
        for inst in instances:
            # Skip blank "+ Add" rows the user never filled in.
            if not inst.name and not inst.ical_url:
                continue
            inst.save()
        if is_discord:
            emoji_instances: list[Any] = emoji_formset.save(commit=False)
            for emoji_obj in emoji_formset.deleted_objects:
                emoji_obj.delete()
            for emoji_inst in emoji_instances:
                # Skip blank "+ Add" rows the user never filled in.
                if not emoji_inst.emoji:
                    continue
                emoji_inst.save()
            role_formset.save()
        _save_jobstate_formset(jobstate_formset, jobstate_posted)
        messages.success(request, "Site settings saved.")
        target_tab = request.POST.get("submitted_tab", active_tab)
        return (
            redirect(f"{reverse('hub_admin_site_settings')}?tab={target_tab}"),
            form,
            feed_formset,
            emoji_formset,
            role_formset,
            jobstate_formset,
        )
    return None, form, feed_formset, emoji_formset, role_formset, jobstate_formset


@fog_admin_required
def admin_site_settings(request: HttpRequest) -> HttpResponse:
    """Admin site settings — edit the SiteConfiguration singleton and its calendar feeds.

    Tabs: ``general``, ``calendar``, ``legacy-cms``, ``features`` (the My Tab/Payments
    and class-registration kill switches), ``automations`` (the scheduled-job dashboard —
    ON/OFF toggles + Run now, from the shared job registry), and ``announcements`` (a
    sitewide announcement composer with a preview-then-send step). The Calendar tab owns a
    ``CalendarFeedFormSet`` so admins can add/remove iCal feeds inline.
    """
    from core.models import CalendarFeed, SiteConfiguration

    config = SiteConfiguration.load()
    active_tab = request.GET.get("tab", "general")
    allowed_tabs = {
        "general",
        "calendar",
        "legacy-cms",
        "automations",
        "announcements",
        "features",
        "discord",
        "slideshow",
        "emails",
    }
    if active_tab not in allowed_tabs:
        active_tab = "general"

    feed_queryset = CalendarFeed.objects.all()
    release_mode = False
    release_form: ReleaseAnnouncementForm | None = None
    release_preview: dict[str, object] | None = None
    jobstate_formset: Any = None

    if request.method == "POST":
        action = request.POST.get("action")
        # Handle "Sync Now" action — separate from the settings form
        if action == "sync_now":
            from classes.import_service import sync_legacy_cms

            try:
                count = sync_legacy_cms()
                messages.success(request, f"Synced {count} offering(s) from the legacy CMS.")
            except Exception as exc:
                messages.error(request, f"Sync failed: {exc}")
            url = reverse("hub_admin_site_settings")
            return redirect(f"{url}?tab=legacy-cms")

        # Automations "Run now" — a named submitter on the shared form (generic buttons) or the
        # teleported bill_tabs confirm modal (carries run_job as a hidden field). Handled before
        # the settings-form save, like sync_now; it persists toggle edits then runs (Decision 1/7).
        if "run_job" in request.POST:
            return _handle_run_job(request, config)

        # Announcements tab — the Release composer (preview-then-send), separate from the
        # settings form. The plain sitewide composer moved to the /announcements/compose/
        # wizard, so only the release mode (mode=release) is handled here now.
        if action in ("announce_preview", "announce_send", "announce_test"):
            active_tab = "announcements"
            release_mode = True
            form = SiteSettingsForm(instance=config)
            feed_formset = CalendarFeedFormSet(queryset=feed_queryset, prefix="feeds")
            emoji_queryset, role_queryset = _discord_editor_querysets()
            emoji_formset = DiscordGuildEmojiFormSet(queryset=emoji_queryset, prefix="emoji")
            role_formset = GuildRoleFormSet(queryset=role_queryset, prefix="guildroles")
            response, release_form, release_preview = _handle_release_announcement_action(request, action)
            if response is not None:
                return response
            # else fall through to render (preview, or send with validation errors)
        else:
            response, form, feed_formset, emoji_formset, role_formset, jobstate_formset = _save_site_settings(
                request, config, feed_queryset, active_tab
            )
            if response is not None:
                return response
    else:
        form = SiteSettingsForm(instance=config)
        feed_formset = CalendarFeedFormSet(queryset=feed_queryset, prefix="feeds")
        emoji_queryset, role_queryset = _discord_editor_querysets()
        emoji_formset = DiscordGuildEmojiFormSet(queryset=emoji_queryset, prefix="emoji")
        role_formset = GuildRoleFormSet(queryset=role_queryset, prefix="guildroles")
        # "Draft from latest release" — enter Release mode with a prefilled draft.
        if request.GET.get("draft") == "release":
            release_mode = True
            release_form = ReleaseAnnouncementForm(initial=_release_announcement_initial())
            active_tab = "announcements"

    instructor_sync_rows, legacy_cms_unmatched = _legacy_instructor_sync_status()
    from membership.models import SlideshowSlide, SlideshowZone

    zone_formset = SlideshowZoneFormSet(queryset=SlideshowZone.objects.all(), prefix="zones")
    slide_formset = SlideshowSlideFormSet(queryset=SlideshowSlide.objects.all(), prefix="slides")

    # Automations tab: reuse the bound formset from a failed save (preserves typed toggle state),
    # else build a fresh one over the synced rows. Rows pair each registry job with its form + last run.
    automation_rows, jobstate_formset = _resolve_automation_context(jobstate_formset)

    from core.events.email_catalogue import build_email_catalogue

    ctx = _get_hub_context(request)
    return render(
        request,
        "hub/admin/site_settings.html",
        {
            **ctx,
            "form": form,
            "email_catalogue": build_email_catalogue(),
            "feed_formset": feed_formset,
            "emoji_formset": emoji_formset,
            "role_formset": role_formset,
            "zone_formset": zone_formset,
            "slide_formset": slide_formset,
            "active_tab": active_tab,
            "classes_color_field": form["classes_calendar_color"],
            "sync_classes_field": form["sync_classes_enabled"],
            "legacy_cms_sync_field": form["legacy_cms_sync_enabled"],
            "instructor_sync_rows": instructor_sync_rows,
            "legacy_cms_unmatched": legacy_cms_unmatched,
            "config": config,
            "release_mode": release_mode,
            "release_form": release_form,
            "release_preview": release_preview,
            "automation_rows": automation_rows,
            "jobstate_formset": jobstate_formset,
        },
    )


@fog_admin_required
@require_POST
def admin_slideshow_zones_save(request: HttpRequest) -> HttpResponse:
    """Save the Slideshow tab's Zones editor (its own form, outside the settings form)."""
    from membership.models import SlideshowZone

    formset = SlideshowZoneFormSet(request.POST, queryset=SlideshowZone.objects.all(), prefix="zones")
    if formset.is_valid():
        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for inst in instances:
            # Skip a blank "+ Add" row the user never filled in.
            if not inst.name:
                continue
            inst.save()
        messages.success(request, "Zones saved.")
    else:
        messages.error(request, "Couldn't save the zones — check the highlighted fields.")
    return redirect(f"{reverse('hub_admin_site_settings')}?tab=slideshow")


@fog_admin_required
@require_POST
def admin_slideshow_slides_save(request: HttpRequest) -> HttpResponse:
    """Save the Slideshow tab's Slides editor (its own multipart form, outside the settings form)."""
    from membership.models import SlideshowSlide

    formset = SlideshowSlideFormSet(request.POST, request.FILES, queryset=SlideshowSlide.objects.all(), prefix="slides")
    if formset.is_valid():
        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for inst in instances:
            # Skip a blank "+ Add" row (no title, image, or announcement).
            if not (inst.title or inst.image or inst.announcement_id):
                continue
            inst.save()
        messages.success(request, "Slides saved.")
    else:
        messages.error(request, "Couldn't save the slides — check the highlighted fields.")
    return redirect(f"{reverse('hub_admin_site_settings')}?tab=slideshow")


# ── Interactive space map ────────────────────────────────────────────────────


@dataclass(frozen=True)
class _MapReviewScope:
    """A requester's authority over space requests.

    ``can_review`` gates the queue at all; an ``is_admin`` reviewer sees every lease and
    cubby ask, while a guild lead/staffer sees only cubby asks for spaces their guilds
    sublet. Mirrors :class:`_ReviewScope` on the events queue.
    """

    can_review: bool
    is_admin: bool = False
    guilds: Any = None  # a Guild queryset for a lead/staffer; None for an admin or forbidden

    def scoped(self, requests: SpaceRequestQuerySet) -> SpaceRequestQuerySet:
        """Narrow a ``SpaceRequest`` queryset to what this scope may act on.

        Callers gate on ``can_review`` before they get here (the queue and the decision
        endpoint both 403 first), exactly like ``_ReviewScope.scoped``.
        """
        return requests.for_scope(True if self.is_admin else self.guilds)

    def pending(self) -> SpaceRequestQuerySet:
        """The pending requests visible to this scope — empty when it can't review."""
        from membership.models import SpaceRequest

        if not self.can_review:
            return cast(SpaceRequestQuerySet, SpaceRequest.objects.none())
        awaiting = (
            SpaceRequest.objects.pending()
            .select_related("requester", "space", "space__sublet_guild", "hotspot")
            .order_by("created_at")
        )
        return self.scoped(awaiting)


def _map_reviewer_scope(request: HttpRequest) -> _MapReviewScope:
    """The requester's space-request review authority (admin / capability / lead-scoped / none)."""
    if _viewing_as_admin(request):
        return _MapReviewScope(can_review=True, is_admin=True)
    member = _get_member(request) if request.user.is_authenticated else None
    if member is not None and member.has_admin_capability(AdminCapability.Capability.SPACE_APPROVER):
        # A Space & Cubby Administrator reviews every space request site-wide, like an admin.
        return _MapReviewScope(can_review=True, is_admin=True)
    if member is not None and member.staffed_guilds.exists():
        return _MapReviewScope(can_review=True, guilds=member.staffed_guilds)
    return _MapReviewScope(can_review=False)


def _space_map_context(request: HttpRequest) -> dict[str, Any]:
    """Everything the read map + its accessible list need, in a fixed number of queries.

    Published floors with their markers prefetched (``for_map`` kills the per-marker
    Space/Guild lookups), plus the viewer's own open requests so a marker, its detail
    panel, and its list row can all show the same "pending" state.
    """
    from membership.models import Floorplan, MapHotspot, SpaceRequest

    floorplans = list(
        Floorplan.objects.published().prefetch_related(Prefetch("hotspots", queryset=MapHotspot.objects.for_map()))
    )
    member = _get_member(request) if request.user.is_authenticated else None
    my_requests: list[Any] = []
    if member is not None:
        my_requests = list(SpaceRequest.objects.pending().filter(requester=member).select_related("space", "hotspot"))
    scope = _map_reviewer_scope(request)
    return {
        "floorplans": floorplans,
        "pending_space_ids": [r.space_id for r in my_requests],
        "my_space_requests": my_requests,
        "map_can_review": scope.can_review,
        "map_review_pending_count": scope.pending().count(),
    }


def _hotspot_detail_context(request: HttpRequest, hotspot: Any) -> dict[str, Any]:
    """Viewer-specific state for one marker's detail panel.

    Answers the three questions the CTA branches on: is there already an open request,
    is the viewer allowed to make one, and (if not) why not — so the panel can offer a
    log-in link or an explanation instead of a dead disabled button.
    """
    from hub.forms import SpaceRequestForm
    from membership.models import SpaceRequest

    member = _get_member(request) if request.user.is_authenticated else None
    open_request = None
    if member is not None and hotspot.space_id:
        open_request = SpaceRequest.objects.pending().filter(requester=member, space_id=hotspot.space_id).first()
    is_active_member = member is not None and member.status == Member.Status.ACTIVE
    can_request = hotspot.is_requestable and is_active_member and open_request is None
    return {
        "hotspot": hotspot,
        "open_request": open_request,
        "viewer_is_member": member is not None,
        "viewer_is_active": is_active_member,
        "can_request": can_request,
        # Always an unbound form when a request is possible, so the panel renders the
        # field through components/form_field.html rather than hand-rolled markup.
        "request_form": (SpaceRequestForm(hotspot=hotspot, member=cast(Member, member)) if can_request else None),
        "request_form_open": False,
    }


def map_hotspot_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """HTMX GET — one marker's detail panel, rendered into the shared modal body.

    Public-read, exactly like the map it opens from.
    """
    from membership.models import MapHotspot

    hotspot = get_object_or_404(MapHotspot.objects.for_map(), pk=pk)
    return render(request, "hub/partials/_space_detail.html", _hotspot_detail_context(request, hotspot))


@login_required
@require_POST
def space_request_create(request: HttpRequest, pk: int) -> HttpResponse:
    """A member asks for the space behind marker ``pk``. HTMX POST from the detail panel.

    On success the response carries three swaps so the marker, the detail panel, and the
    accessible list row can never disagree about whether a request is pending.
    """
    from hub.forms import SpaceRequestForm
    from membership.models import MapHotspot

    hotspot = get_object_or_404(MapHotspot.objects.for_map(), pk=pk)
    member = _get_member(request)
    if member is None:
        return HttpResponse("Forbidden", status=403)

    form = SpaceRequestForm(request.POST, hotspot=hotspot, member=member)
    if not form.is_valid():
        ctx = _hotspot_detail_context(request, hotspot)
        return render(
            request,
            "hub/partials/_space_detail.html",
            {**ctx, "can_request": True, "request_form": form, "request_form_open": True},
        )
    space_request = form.save()

    ctx = _hotspot_detail_context(request, hotspot)
    response = render(
        request,
        "hub/partials/_space_detail.html",
        {
            **ctx,
            "swap_marker": True,
            "pending_space_ids": [hotspot.space_id],
            "my_space_requests": [space_request],
        },
    )
    trigger_toast(response, "Request sent — you'll hear back soon.", "success")
    return response


@login_required
@require_POST
def space_request_withdraw(request: HttpRequest, pk: int) -> HttpResponse:
    """The requester pulls back their own pending ask. POST only, full-page redirect."""
    from membership.models import InvalidSpaceRequestTransition, SpaceRequest

    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    member = _get_member(request)
    if member is None:
        return HttpResponse("Forbidden", status=403)
    space_request = get_object_or_404(SpaceRequest, pk=pk, requester=member)
    try:
        space_request.withdraw(by=user)
        messages.success(request, "Request withdrawn.")
    except InvalidSpaceRequestTransition:
        messages.info(request, "That request was already handled.")
    return redirect("hub_spaces")


@login_required
def space_request_review_queue(request: HttpRequest) -> HttpResponse:
    """The reviewer queue — space requests a lead/admin can approve or decline."""
    scope = _map_reviewer_scope(request)
    if not scope.can_review:
        return HttpResponse("Forbidden", status=403)
    ctx = _get_hub_context(request)
    return render(
        request,
        "hub/space_request_review_queue.html",
        {
            **ctx,
            "pending_requests": scope.pending(),
            "open_decision_for": None,
            "decision_note_value": "",
            "decision_note_error": "",
        },
    )


@login_required
@require_POST
def space_request_review_decision(request: HttpRequest, pk: int) -> HttpResponse:
    """Record a reviewer's decision (approve / decline) on a space request."""
    from hub.forms import SpaceRequestDecisionForm
    from membership.models import InvalidSpaceRequestTransition, SpaceRequest

    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    scope = _map_reviewer_scope(request)
    if not scope.can_review:
        return HttpResponse("Forbidden", status=403)

    # Scoped to the reviewer's authority but NOT to a state — a stale decision surfaces
    # the model guard as a friendly "already handled", never a bare 404.
    space_request = get_object_or_404(scope.scoped(SpaceRequest.objects.all()), pk=pk)

    # Approve carries its decision in the query string (the confirm modal posts no note);
    # decline posts the decision plus its required note in the body.
    data = request.POST.copy()
    if not data.get("decision"):
        data["decision"] = request.GET.get("decision", "")
    form = SpaceRequestDecisionForm(data)
    if not form.is_valid():
        return render(
            request,
            "hub/space_request_review_queue.html",
            {
                **_get_hub_context(request),
                "pending_requests": scope.pending(),
                "open_decision_for": space_request.pk,
                "decision_note_value": data.get("notes", ""),
                "decision_note_error": " ".join(str(e) for e in form.errors.get("notes", [])),
            },
        )

    try:
        if form.cleaned_data["decision"] == "approve":
            space_request.approve(reviewer=user)
            messages.success(request, "Request approved.")
        else:
            space_request.decline(reviewer=user, notes=form.cleaned_data["notes"])
            messages.success(request, "Request declined.")
    except InvalidSpaceRequestTransition:
        messages.info(request, "That request was already handled.")
    return redirect("hub_space_request_review_queue")


def _org_map_edit_context(request: HttpRequest, *, selected: Any = None) -> dict[str, Any]:
    """Render context for the admin placement editor (Floors + Placement tabs).

    Both formsets save through their own endpoint (the FAQ/Links idiom), so both are
    unbound here. ``selected`` is the floor whose markers the Placement tab is editing —
    the first floor unless the query string names another.
    """
    from hub.forms import FloorplanFormSet, MapHotspotFormSet
    from membership.models import Floorplan, MapHotspot, Space

    floors = list(Floorplan.objects.all().prefetch_related("hotspots"))
    if selected is None and floors:
        requested = request.GET.get("floor", "")
        selected = next((f for f in floors if str(f.pk) == requested), floors[0])
    ctx = _get_hub_context(request)
    return {
        **ctx,
        "floors": floors,
        "selected_floor": selected,
        "floor_formset": FloorplanFormSet(queryset=Floorplan.objects.all(), prefix="floors"),
        "hotspot_formset": (MapHotspotFormSet(instance=selected, prefix="markers") if selected is not None else None),
        "hotspots": (list(MapHotspot.objects.for_map().filter(floorplan=selected)) if selected is not None else []),
        "space_count": Space.objects.count(),
        "max_upload_bytes": settings.MAX_UPLOAD_IMAGE_BYTES,
    }


@login_required
def org_map_edit(request: HttpRequest) -> HttpResponse:
    """The admin's map editor — upload floors, then drop each space onto one. Admin only."""
    forbidden = _require_admin(request)
    if forbidden is not None:
        return forbidden
    return render(request, "hub/org_map_edit.html", _org_map_edit_context(request))


@login_required
@require_POST
def org_map_floors_save(request: HttpRequest) -> HttpResponse:
    """Save the Floors tab's rows from their own form. Admin only."""
    from hub.forms import FloorplanFormSet
    from membership.models import Floorplan

    forbidden = _require_admin(request)
    if forbidden is not None:
        return forbidden
    formset = FloorplanFormSet(request.POST, request.FILES, queryset=Floorplan.objects.all(), prefix="floors")
    if formset.is_valid():
        formset.save()
        messages.success(request, "Floors saved.")
    else:
        messages.error(request, "Couldn't save the floors — check the highlighted fields.")
    return redirect(f"{reverse('hub_org_map_edit')}?tab=floors")


@login_required
@require_POST
def org_map_floor_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Delete one floor and the markers on it. Admin only.

    A floor with markers can't go through the formset's plain DELETE-flip without an
    admin realising the cascade, so the editor routes it here from a confirm modal.
    """
    from membership.models import Floorplan

    forbidden = _require_admin(request)
    if forbidden is not None:
        return forbidden
    floor = get_object_or_404(Floorplan, pk=pk)
    floor.delete()
    messages.success(request, f"{floor.name} deleted.")
    return redirect(f"{reverse('hub_org_map_edit')}?tab=floors")


@login_required
@require_POST
def map_hotspots_save(request: HttpRequest) -> HttpResponse:
    """Save the Placement tab's marker rows. Admin only.

    Structural fields only — the formset never carries ``x/y/w/h``, so saving here can
    never move a marker an admin just dragged (that is ``map_hotspot_position``'s job).
    """
    from hub.forms import MapHotspotFormSet
    from membership.models import Floorplan

    forbidden = _require_admin(request)
    if forbidden is not None:
        return forbidden
    floor = get_object_or_404(Floorplan, pk=request.POST.get("floor_id") or 0)
    formset = MapHotspotFormSet(request.POST, instance=floor, prefix="markers")
    if formset.is_valid():
        formset.save()
        messages.success(request, "Markers saved.")
    else:
        messages.error(request, "Couldn't save the markers — check the highlighted fields.")
    return redirect(f"{reverse('hub_org_map_edit')}?tab=placement&floor={floor.pk}")


@login_required
@require_POST
def map_hotspot_position(request: HttpRequest, pk: int) -> HttpResponse:
    """JSON endpoint — store ONE marker's dragged position. Admin only.

    Mirrors ``hub_hero_adjust``: a permission-gated JSON POST that writes only the
    coordinate columns. Bounds live in :class:`MapHotspotPositionForm`, so an
    off-the-edge drag comes back as a 400 the editor shows as an error toast.
    """
    from hub.forms import MapHotspotPositionForm
    from membership.models import MapHotspot

    if not _viewing_as_admin(request):
        return JsonResponse({"error": "Forbidden"}, status=403)
    hotspot = get_object_or_404(MapHotspot, pk=pk)
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Send a JSON body."}, status=400)
    form = MapHotspotPositionForm(payload, hotspot=hotspot)
    if not form.is_valid():
        return JsonResponse({"error": form.error_message()}, status=400)
    form.apply()
    return JsonResponse(
        {
            "status": "ok",
            "x": float(hotspot.x),
            "y": float(hotspot.y),
            "w": float(hotspot.w) if hotspot.w is not None else None,
            "h": float(hotspot.h) if hotspot.h is not None else None,
        }
    )


def _push_space_status_to_airtable(space: Any) -> bool:
    """Push a Space's status to Airtable. Returns ``False`` when the push failed.

    Airtable is the system of record for ``Space.status``, so a local status change must be
    written back or the next pull reverts it. ``sync_space_to_airtable`` already swallows its
    own errors (Airtable is a secondary store), so a failure surfaces as ``None`` while sync is
    enabled; the extra try/except guards the unlikely case of it raising anyway. Either way the
    caller keeps the local change — an Airtable outage never fails the request.
    """
    from airtable_sync.service import sync_space_to_airtable

    try:
        record_id = sync_space_to_airtable(space)
    except Exception:
        logger.exception("Airtable push crashed for space %s", space.space_id)
        return False
    if settings.AIRTABLE_SYNC_ENABLED and record_id is None:
        logger.error("Airtable push returned no record for space %s", space.space_id)
        return False
    return True


@login_required
@require_POST
def map_hotspot_status(request: HttpRequest, pk: int) -> HttpResponse:
    """JSON endpoint — set the status of the Space behind marker ``pk``. Admin only.

    The edit map's click-to-set-status control. Writes ``Space.status`` locally, then pushes
    back to Airtable (the system of record). An Airtable outage keeps the local change and
    returns a non-fatal warning rather than 500ing — the marker recolors either way, and the
    value may revert on the next pull. Facility/info markers have no space and are rejected.
    """
    from membership.models import MapHotspot, Space

    if not _viewing_as_admin(request):
        return JsonResponse({"error": "Forbidden"}, status=403)
    hotspot = get_object_or_404(MapHotspot.objects.select_related("space"), pk=pk)
    space = hotspot.space
    if space is None:
        return JsonResponse({"error": "This marker has no space to set a status on."}, status=400)
    target = request.POST.get("status", "")
    if target not in Space.Status.values:
        return JsonResponse({"error": "Unknown status."}, status=400)
    space.status = target
    space.save(update_fields=["status"])
    airtable_ok = _push_space_status_to_airtable(space)
    payload: dict[str, Any] = {
        "status": "ok",
        "space_status": space.status,
        "availability_class": space.status,
        "status_display": space.get_status_display(),
    }
    if not airtable_ok:
        payload["warning"] = "Saved here, but the Airtable push failed — it may revert on the next sync."
    return JsonResponse(payload)


def _render_marker_editor(request: HttpRequest, hotspot: Any, form: Any, *, new_marker: Any = None) -> HttpResponse:
    """Render the click-a-tile modal form. ``new_marker`` appends its tile to the map (OOB)."""
    return render(
        request,
        "hub/partials/_marker_edit_form.html",
        {"hotspot": hotspot, "form": form, "new_marker": new_marker},
    )


def _apply_marker_status(request: HttpRequest, space: Any, target: str) -> None:
    """Set a space's status from the marker modal and push it to Airtable (the system of record).

    A no-op when the status hasn't changed. An Airtable outage keeps the local change and warns the
    admin rather than failing the save — same posture as ``map_hotspot_status``.
    """
    if space.status == target:
        return
    space.status = target
    space.save(update_fields=["status"])
    if not _push_space_status_to_airtable(space):
        messages.warning(request, "Status saved here, but the Airtable push failed — it may revert on the next sync.")


@login_required
def map_hotspot_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Open or save one marker's editor — the map's click-a-tile modal. Admin only.

    GET returns the modal form. A valid POST saves the marker's fields and, for a space-bound
    marker, applies the chosen status; it answers with the re-rendered tile (an ``hx-swap-oob``
    replacement so the map recolors and relabels) and an ``HX-Trigger`` that closes the modal. An
    invalid POST re-renders the form with its errors so the modal stays open. Coordinates are never
    touched here — dragging owns them (``map_hotspot_position``).
    """
    from hub.forms import MapHotspotEditForm
    from membership.models import MapHotspot

    forbidden = _require_admin(request)
    if forbidden is not None:
        return forbidden
    hotspot = get_object_or_404(MapHotspot.objects.select_related("space", "floorplan"), pk=pk)
    if request.method != "POST":
        return _render_marker_editor(request, hotspot, MapHotspotEditForm(instance=hotspot))
    form = MapHotspotEditForm(request.POST, instance=hotspot)
    if not form.is_valid():
        return _render_marker_editor(request, hotspot, form)
    hotspot = form.save()
    if hotspot.space_id and form.cleaned_data["status"]:
        _apply_marker_status(request, hotspot.space, form.cleaned_data["status"])
    response = render(request, "hub/partials/_editor_marker.html", {"h": hotspot, "oob_swap": "true"})
    response["HX-Trigger"] = "close-marker-edit"
    return response


@login_required
@require_POST
def map_hotspot_create(request: HttpRequest) -> HttpResponse:
    """Drop a new marker on a floor and open its editor. Admin only.

    The map-first "+ Add a marker": creates a centred info pin, then answers with the editor form
    (into the modal) plus an ``hx-swap-oob`` copy of the new tile appended to the drawn canvas. The
    admin sets its kind, linked space, and label in the modal, and drags it into place.
    """
    from hub.forms import MapHotspotEditForm
    from membership.models import Floorplan, MapHotspot

    forbidden = _require_admin(request)
    if forbidden is not None:
        return forbidden
    floor = get_object_or_404(Floorplan, pk=request.POST.get("floor_id") or 0)
    hotspot = MapHotspot.objects.create(
        floorplan=floor,
        kind=MapHotspot.Kind.INFO,
        shape=MapHotspot.Shape.PIN,
        label="New marker",
        x=Decimal("50.00"),
        y=Decimal("50.00"),
    )
    return _render_marker_editor(request, hotspot, MapHotspotEditForm(instance=hotspot), new_marker=hotspot)


@login_required
@require_POST
def map_hotspot_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Delete one marker from the map. Admin only.

    The modal's Delete: removes the marker and answers with an ``hx-swap-oob`` delete for its tile
    plus an ``HX-Trigger`` that closes the modal. The floor and its other markers are untouched.
    """
    from membership.models import MapHotspot

    forbidden = _require_admin(request)
    if forbidden is not None:
        return forbidden
    hotspot = get_object_or_404(MapHotspot, pk=pk)
    hotspot.delete()
    response = render(request, "hub/partials/_editor_marker_deleted.html", {"pk": pk})
    response["HX-Trigger"] = "close-marker-edit"
    return response
