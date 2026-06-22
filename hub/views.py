"""Views for the member hub."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, TypedDict, cast

from django.utils import timezone as dj_timezone

from allauth.account.models import EmailAddress
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Prefetch, Q
from django.http import HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST, require_http_methods

from billing.exceptions import NoPaymentMethodError, TabLimitExceededError, TabLockedError
from billing.models import BillingSettings, Tab, TabCharge
from classes.models import Category, ClassOffering
from core.models import HeroCropMixin
from hub.view_as import ALL_ROLES, SESSION_ROLE_KEY, fog_admin_required
from hub.forms import (
    BetaFeedbackForm,
    CalendarFeedFormSet,
    EmailPreferencesForm,
    GuildEditForm,
    MemberAdminEditForm,
    ProfileSettingsForm,
    SiteSettingsForm,
    VotePreferenceForm,
)
from hub.toast import trigger_toast
from membership.cycle import get_cycle_context
from membership.models import FundingSnapshot, Guild, Member, VotePreference
from membership.permissions import can_edit_category as _can_edit_category
from membership.permissions import can_edit_class as _can_edit_offering
from membership.permissions import can_edit_guild as _can_edit_guild


class VoteStanding(TypedDict, total=False):
    guild_name: str
    total_points: int
    bar_pct: float


def _get_hub_context(request: HttpRequest) -> dict[str, Any]:
    """Build common sidebar context for all hub pages."""
    guilds = Guild.objects.order_by("name")
    initials = ""
    photo_url = ""
    if request.user.is_authenticated:
        member: Member | None = getattr(request.user, "member", None)
        if member is not None:
            initials = member.initials
            if member.profile_photo:
                photo_url = member.profile_photo.url
    return {
        "guilds": guilds,
        "user_initials": initials,
        "user_profile_photo_url": photo_url,
    }


def _get_member(request: HttpRequest) -> Member | None:
    """Get the Member for the logged-in user, or None.

    Callers must be decorated with @login_required.
    """
    member: Member | None = getattr(request.user, "member", None)
    return member


@login_required
def guild_voting(request: HttpRequest) -> HttpResponse:
    """Guild voting page — members submit or update their persistent guild preferences."""
    member = _get_member(request)
    ctx = _get_hub_context(request)
    cycle_ctx = get_cycle_context()

    preference: VotePreference | None = None
    if member is not None:
        preference = getattr(member, "vote_preference", None)

    latest_snapshot = FundingSnapshot.objects.order_by("-snapshot_at").first()
    since = latest_snapshot.snapshot_at if latest_snapshot else None

    # Live vote standings: tally points from all current VotePreference records
    vote_standings = _compute_live_standings()
    new_vote_standings = _compute_new_votes_since(since)

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
            VotePreference.objects.update_or_create(
                member=member,
                defaults={
                    "guild_1st": form.cleaned_data["guild_1st"],
                    "guild_2nd": form.cleaned_data["guild_2nd"],
                    "guild_3rd": form.cleaned_data["guild_3rd"],
                },
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


def _compute_live_standings() -> list[VoteStanding]:
    """Tally live vote points from current VotePreference records.

    Only counts votes from members with a linked User — members imported from
    Airtable who never signed up to the app are excluded. See
    ``VotePreferenceQuerySet.from_signed_up_members``.

    Returns a list of dicts sorted by total points descending:
        [{"guild_name": str, "total_points": int, "bar_pct": float}, ...]
    """
    signed_up_1st = Q(first_choice_votes__member__user__isnull=False)
    signed_up_2nd = Q(second_choice_votes__member__user__isnull=False)
    signed_up_3rd = Q(third_choice_votes__member__user__isnull=False)
    # distinct=True is essential: annotating three reverse-FK Counts on the same
    # queryset cross-joins first/second/third_choice_votes, so without distinct
    # each Count is multiplied by the other two. A guild with 1/2/3 first/second/
    # third-place votes would show 6/6/6 and score 60 points instead of 17.
    guilds = Guild.objects.filter(is_active=True).annotate(
        first=Count("first_choice_votes", filter=signed_up_1st, distinct=True),
        second=Count("second_choice_votes", filter=signed_up_2nd, distinct=True),
        third=Count("third_choice_votes", filter=signed_up_3rd, distinct=True),
    )

    results: list[VoteStanding] = []
    for g in guilds:
        points = g.first * 5 + g.second * 3 + g.third * 2
        if points > 0:
            results.append(VoteStanding(guild_name=g.name, total_points=points))

    if not results:
        return []

    results.sort(key=lambda x: x["total_points"], reverse=True)
    max_points = results[0]["total_points"]
    for r in results:
        r["bar_pct"] = round(r["total_points"] / max_points * 100, 1)
    return results


def _compute_new_votes_since(since: datetime | None) -> list[VoteStanding]:
    """Tally points from VotePreferences updated after ``since``.

    Represents the "new votes this month" view — votes cast or changed since
    the last snapshot was taken. If ``since`` is None (no prior snapshot),
    every signed-up vote is considered new.
    """
    first_q = Q(first_choice_votes__member__user__isnull=False)
    second_q = Q(second_choice_votes__member__user__isnull=False)
    third_q = Q(third_choice_votes__member__user__isnull=False)
    if since is not None:
        first_q &= Q(first_choice_votes__updated_at__gt=since)
        second_q &= Q(second_choice_votes__updated_at__gt=since)
        third_q &= Q(third_choice_votes__updated_at__gt=since)

    # See note on distinct=True in _compute_live_standings — same cross-join
    # multiplication applies here.
    guilds = Guild.objects.filter(is_active=True).annotate(
        first=Count("first_choice_votes", filter=first_q, distinct=True),
        second=Count("second_choice_votes", filter=second_q, distinct=True),
        third=Count("third_choice_votes", filter=third_q, distinct=True),
    )

    results: list[VoteStanding] = []
    for g in guilds:
        points = g.first * 5 + g.second * 3 + g.third * 2
        if points > 0:
            results.append(VoteStanding(guild_name=g.name, total_points=points))

    if not results:
        return []

    results.sort(key=lambda x: x["total_points"], reverse=True)
    max_points = results[0]["total_points"]
    for r in results:
        r["bar_pct"] = round(r["total_points"] / max_points * 100, 1)
    return results


def member_directory(request: HttpRequest) -> HttpResponse:
    """Member directory page — lists all active members.

    Prefetches each member's primary allauth ``EmailAddress`` so
    ``Member.primary_email`` stays O(1) per member instead of firing a query
    on every template access. See the three-email-store note on
    ``Member.primary_email`` and docs/superpowers/specs/2026-04-07-user-email-aliases-design.md.
    """
    ctx = _get_hub_context(request)
    current_member = _get_member(request)
    view_as = getattr(request, "view_as", None)
    is_admin = view_as is not None and view_as.is_admin
    must_show = (
        Q(fog_role=Member.FogRole.ADMIN)
        | Q(fog_role=Member.FogRole.GUILD_OFFICER)
        | Q(led_guilds__isnull=False)
        | Q(instructor_slug__gt="")
    )
    member_qs = Member.objects.filter(status=Member.Status.ACTIVE).distinct()
    if not is_admin:
        member_qs = member_qs.filter(Q(show_in_directory=True) | must_show)
    guild_filter = request.GET.get("guild", "")
    if guild_filter.isdigit():
        member_qs = member_qs.filter(guild_memberships__guild_id=int(guild_filter))
    members = (
        member_qs.select_related("membership_plan", "user")
        .prefetch_related(
            Prefetch(
                "user__emailaddress_set",
                queryset=EmailAddress.objects.filter(primary=True),
                to_attr="_primary_emailaddresses",
            ),
            "guild_memberships__guild",
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
            "guilds": Guild.objects.filter(is_active=True).order_by("name"),
            "guild_filter": guild_filter,
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
    if model_class not in [Guild, Category, ClassOffering]:
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
    """A short 'what's happening' feed for a guild: recent joins, announcements, and new classes.

    Synthesized from existing rows (no new activity table) and merged newest-first.
    """
    from classes.models import ClassOffering

    items: list[dict[str, Any]] = []
    for membership in guild.memberships.select_related("member").order_by("-joined_at")[:limit]:
        items.append({"when": membership.joined_at, "text": f"{membership.member.display_name} joined the guild"})
    for announcement in guild.announcements.active().order_by("-published_at")[:limit]:
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


def guild_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Guild detail page — shows about text, active products, and cart interface."""
    from billing.forms import CONTEXT_MEMBER_GUILD_PAGE, TabItemForm, build_product_split_formset
    from billing.models import Product

    guild = get_object_or_404(
        Guild.objects.select_related("featured_class__instructor").prefetch_related("products__splits__guild"),
        pk=pk,
    )
    ctx = _get_hub_context(request)
    products = guild.products.order_by("name").prefetch_related("splits__guild")
    member = _get_member(request)

    tab: Tab | None = None
    if member is not None:
        tab, _created = Tab.objects.get_or_create(member=member)

    eyop_form = TabItemForm(context=CONTEXT_MEMBER_GUILD_PAGE, user=request.user, guild=guild)

    can_edit_this_guild = _can_edit_guild(request, guild)
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
    links = guild.links.all()
    announcements = guild.announcements.active()[:5]
    roster = guild.roster_members() if guild.show_members else None
    is_member_of_guild = member is not None and guild.memberships.filter(member=member).exists()

    from classes.models import ClassOffering

    guild_classes = ClassOffering.objects.filter(category__guild=guild)
    member_count = guild.memberships.count()
    class_count = guild_classes.filter(status=ClassOffering.Status.PUBLISHED).count()
    upcoming_classes = guild_classes.bookable().select_related("instructor")[:4]
    published_classes = (
        guild_classes.filter(status=ClassOffering.Status.PUBLISHED)
        .select_related("instructor", "category")
        .order_by("title")
    )
    calendar = _get_calendar_context(request, guild=guild)
    pulse = _guild_pulse(guild)

    from membership.models import GuildOrientationSettings

    orientation = GuildOrientationSettings.objects.filter(guild=guild).first()
    orientation_booking = member.active_orientation_for(guild) if member is not None else None
    is_oriented = member.is_oriented_for(guild) if member is not None else False
    show_orientation = orientation is not None and orientation.is_enabled
    orientation_slots = (
        list(guild.orientation_slots.upcoming().order_by("starts_at")[:8])
        if orientation is not None
        and show_orientation
        and not is_oriented
        and orientation_booking is None
        and not orientation.is_closed
        else []
    )

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
            "roster": roster,
            "member": member,
            "is_member_of_guild": is_member_of_guild,
            "member_count": member_count,
            "class_count": class_count,
            "upcoming_classes": upcoming_classes,
            "published_classes": published_classes,
            "calendar": calendar,
            "pulse": pulse,
            "orientation": orientation,
            "orientation_booking": orientation_booking,
            "is_oriented": is_oriented,
            "show_orientation": show_orientation,
            "orientation_slots": orientation_slots,
        },
    )


def _require_can_edit_guild(request: HttpRequest, guild: Guild) -> HttpResponse | None:
    """Return a 403 response if the user cannot edit ``guild``, else None."""
    if not _can_edit_guild(request, guild):
        return HttpResponse("Forbidden", status=403)
    return None


@login_required
def guild_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Full guild edit page (GET) + handler (POST). Admin, officer, or this guild's lead only."""
    from hub.forms import GuildAnnouncementForm, GuildFAQItemFormSet, GuildLinkFormSet

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden

    if request.method == "POST":
        form = GuildEditForm(request.POST, request.FILES, instance=guild)
        faq_formset = GuildFAQItemFormSet(request.POST, instance=guild, prefix="faq")
        link_formset = GuildLinkFormSet(request.POST, instance=guild, prefix="links")
        if form.is_valid() and faq_formset.is_valid() and link_formset.is_valid():
            form.save()
            faq_formset.save()
            link_formset.save()
            guild.add_gallery_images(request.FILES.getlist("gallery_images"))

            messages.success(request, "Guild page updated.")
            if request.POST.get("after") == "edit":
                return redirect("hub_guild_edit", pk=guild.pk)
            return redirect("hub_guild_detail", pk=guild.pk)
    else:
        form = GuildEditForm(instance=guild)
        faq_formset = GuildFAQItemFormSet(instance=guild, prefix="faq")
        link_formset = GuildLinkFormSet(instance=guild, prefix="links")

    ctx = _get_hub_context(request)
    return render(
        request,
        "hub/guild_edit.html",
        {
            **ctx,
            "guild": guild,
            "form": form,
            "faq_formset": faq_formset,
            "link_formset": link_formset,
            "announcement_form": GuildAnnouncementForm(),
        },
    )


@login_required
def guild_orientation_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Config editor: orientation settings + recurring availability rules. Editors only."""
    from hub.forms import GuildOrientationSettingsForm, OrientationAvailabilityFormSet, OrientationSlotForm
    from membership.models import GuildOrientationSettings

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden
    settings_obj, _ = GuildOrientationSettings.objects.get_or_create(guild=guild)

    if request.method == "POST":
        form = GuildOrientationSettingsForm(request.POST, instance=settings_obj)
        rule_formset = OrientationAvailabilityFormSet(request.POST, instance=guild, prefix="rules")
        if form.is_valid() and rule_formset.is_valid():
            form.save()
            rule_formset.save()
            messages.success(request, "Orientation settings updated.")
            return redirect("hub_guild_orientation_edit", pk=guild.pk)
    else:
        form = GuildOrientationSettingsForm(instance=settings_obj)
        rule_formset = OrientationAvailabilityFormSet(instance=guild, prefix="rules")

    ctx = _get_hub_context(request)
    return render(
        request,
        "hub/orientation_settings.html",
        {
            **ctx,
            "guild": guild,
            "form": form,
            "rule_formset": rule_formset,
            "slot_form": OrientationSlotForm(),
            "upcoming_slots": guild.orientation_slots.upcoming(),
        },
    )


@login_required
@require_POST
def guild_orientation_slot_add(request: HttpRequest, pk: int) -> HttpResponse:
    """POST-only — add a one-off orientation slot to this guild. Editors only."""
    from hub.forms import OrientationSlotForm
    from membership.models import OrientationSlot

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden

    form = OrientationSlotForm(request.POST)
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
    return redirect("hub_guild_orientation_edit", pk=guild.pk)


@login_required
@require_POST
def guild_orientation_slot_cancel(request: HttpRequest, pk: int, slot_pk: int) -> HttpResponse:
    """POST-only — cancel a one-off or generated orientation slot (and its bookings). Editors only."""
    from membership import orientations

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden

    slot = get_object_or_404(guild.orientation_slots, pk=slot_pk)
    orientations.cancel_slot(slot, reason=request.POST.get("reason", ""))
    messages.success(request, "Orientation slot cancelled.")
    return redirect("hub_guild_orientation_edit", pk=guild.pk)


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
        return redirect("hub_guild_detail", pk=slot.guild_id)
    try:
        orientations.request_orientation(slot, member, note=request.POST.get("note", ""))
        messages.success(
            request,
            "Orientation requested! Check your email for the details — it's not official until the guild lead confirms.",
        )
    except OrientationError as exc:
        messages.error(request, str(exc))
    return redirect("hub_guild_detail", pk=slot.guild_id)


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
    forbidden = _require_can_edit_guild(request, booking.guild)
    if forbidden is not None:
        return forbidden

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "confirm":
            orientations.confirm_orientation(booking)
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
    forbidden = _require_can_edit_guild(request, booking.guild)
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
    return redirect("hub_guild_detail", pk=booking.guild_id)


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
    """True for admins and any guild lead — they may view the orientations dashboard."""
    view_as = getattr(request, "view_as", None)
    if view_as is not None and view_as.has_actual("admin"):
        return True
    member = _get_member(request)
    return member is not None and member.is_guild_lead


def _manageable_slots(request: HttpRequest) -> Any:
    """Upcoming slots this request may add members to: all for admins, own-guild for leads."""
    from membership.models import OrientationSlot

    qs = OrientationSlot.objects.upcoming().select_related("guild")
    view_as = getattr(request, "view_as", None)
    if view_as is not None and view_as.has_actual("admin"):
        return qs
    member = _get_member(request)
    if member is None:
        return OrientationSlot.objects.none()
    return qs.filter(guild__guild_lead=member)


def _filter_orientations(request: HttpRequest, bookings: Any) -> Any:
    """Apply the dashboard's guild / scope / status / completed / date-range filters."""
    member = _get_member(request)
    guild_filter = request.GET.get("guild", "")
    if guild_filter.isdigit():
        bookings = bookings.filter(guild_id=int(guild_filter))
    if request.GET.get("scope") == "mine" and member is not None:
        bookings = bookings.filter(guild__guild_lead=member)
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

    base = OrientationBooking.objects.select_related("slot", "guild", "member", "oriented_by")
    table = prepare_table(
        request,
        _filter_orientations(request, base),
        search_fields=["member__full_legal_name", "member__preferred_name", "guild__name"],
        default_sort="slot__starts_at",
        default_dir="desc",
    )
    upcoming = (
        OrientationBooking.objects.upcoming().select_related("slot", "guild", "member").order_by("slot__starts_at")[:25]
    )
    view_as = getattr(request, "view_as", None)
    member = _get_member(request)
    return render(
        request,
        "hub/orientations_dashboard.html",
        {
            **_get_hub_context(request),
            **table,
            "upcoming": upcoming,
            "guilds": Guild.objects.filter(is_active=True).order_by("name"),
            "statuses": OrientationBooking.Status.choices,
            "add_member_form": OrientationAddMemberForm(slot_queryset=_manageable_slots(request)),
            "is_admin": view_as is not None and view_as.has_actual("admin"),
            "my_member_id": member.pk if member is not None else None,
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
    forbidden = _require_can_edit_guild(request, booking.guild)
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

    return redirect("hub_guild_detail", pk=guild.pk)


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

    return redirect("hub_guild_detail", pk=guild.pk)


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
    return redirect("hub_guild_detail", pk=guild.pk)


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


@login_required
def user_settings(request: HttpRequest) -> HttpResponse:
    """Tabbed user settings page — Profile + Emails (manage addresses + preferences).

    Two forms POST to this endpoint, disambiguated by the ``form_id`` hidden field:
    ``profile`` (member info) and ``email_prefs`` (notification toggles). Email
    address management (add, primary, verify, remove) POSTs to allauth's
    ``account_email`` URL, which is overridden in ``plfog.urls`` to redirect back
    here after each action.
    """
    from allauth.account.forms import AddEmailForm
    from allauth.account.models import EmailAddress

    ctx = _get_hub_context(request)
    member = _get_member(request)

    profile_form: ProfileSettingsForm | None
    if request.method == "POST" and request.POST.get("form_id") == "profile":
        if member is None:
            messages.error(request, "Your account is not linked to a membership.")
            return redirect("hub_user_settings")
        profile_form = ProfileSettingsForm(request.POST, request.FILES, instance=member)
        if profile_form.is_valid():
            profile_form.save()
            from core.models import SiteActivity

            SiteActivity.log(SiteActivity.Kind.PROFILE_UPDATED, actor=request.user, target=member)
            messages.success(request, "Profile updated.")
            return redirect(f"{request.path}?tab=profile")
    elif member is not None:
        profile_form = ProfileSettingsForm(instance=member)
    else:
        profile_form = None

    prefs_form: EmailPreferencesForm
    if request.method == "POST" and request.POST.get("form_id") == "email_prefs":
        prefs_form = EmailPreferencesForm(request.POST)
        if prefs_form.is_valid():
            messages.success(request, "Email preferences updated.")
            return redirect(f"{request.path}?tab=emails")
    else:
        prefs_form = EmailPreferencesForm(initial={"voting_results": True})

    user: User = request.user  # type: ignore[assignment]  # @login_required guarantees User
    if request.method == "POST" and request.POST.get("form_id") == "notifications":
        from core import triggers
        from core.models import NotificationPreference

        is_instructor = bool(member and member.is_instructor)
        for t in triggers.for_member(is_instructor=is_instructor, is_staff=user.is_staff):
            NotificationPreference.objects.update_or_create(
                user=user,
                trigger=t.key,
                defaults={
                    "push_enabled": request.POST.get(f"push_{t.key}") == "on",
                    "email_enabled": request.POST.get(f"email_{t.key}") == "on",
                },
            )
        messages.success(request, "Notification preferences updated.")
        return redirect(f"{request.path}?tab=notifications")

    add_email_form = AddEmailForm(user=request.user)
    email_addresses = list(EmailAddress.objects.filter(user=request.user).order_by("-primary", "email"))
    primary_email = next((ea for ea in email_addresses if ea.primary), None)
    primary_verified_json = "true" if primary_email is None or primary_email.verified else "false"

    # Whitelist the tab param — it flows into an Alpine x-data JS expression, so
    # HTML escaping alone isn't enough to stop a payload like ?tab='+alert(1)+'.
    tab_param = request.GET.get("tab", "profile")
    active_tab = tab_param if tab_param in {"profile", "emails", "notifications"} else "profile"

    if member is None and request.method == "GET" and not request.GET.get("tab"):
        messages.info(request, "Your account is not linked to a membership.")

    from core import triggers as _triggers
    from core.models import NotificationPreference as _NP

    notif_groups = _triggers.by_category(is_instructor=bool(member and member.is_instructor), is_staff=user.is_staff)
    notif_prefs = {p.trigger: p for p in _NP.objects.filter(user=user)}

    return render(
        request,
        "hub/user_settings.html",
        {
            **ctx,
            "member": member,
            "profile_form": profile_form,
            "prefs_form": prefs_form,
            "add_email_form": add_email_form,
            "email_addresses": email_addresses,
            "primary_verified_json": primary_verified_json,
            "active_tab": active_tab,
            "notif_groups": notif_groups,
            "notif_prefs": notif_prefs,
        },
    )


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
def guild_banner_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Clear a guild's banner image and redirect back to the guild page."""
    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden
    if guild.banner_image:
        guild.banner_image.delete(save=True)
        messages.success(request, "Banner removed.")
    return redirect("hub_guild_detail", pk=guild.pk)


@login_required
@require_POST
def guild_join(request: HttpRequest, pk: int) -> HttpResponse:
    """Current member joins this guild (idempotent)."""
    from membership import orientations
    from membership.models import GuildMembership

    guild = get_object_or_404(Guild, pk=pk)
    member = _get_member(request)
    if member is not None:
        _membership, created = GuildMembership.objects.get_or_create(guild=guild, member=member)
        if created:
            orientations.member_joined_guild(guild, member)
        messages.success(request, f"You joined {guild.name}.")
    return redirect("hub_guild_detail", pk=guild.pk)


@login_required
@require_POST
def guild_leave(request: HttpRequest, pk: int) -> HttpResponse:
    """Current member leaves this guild."""
    from membership.models import GuildMembership

    guild = get_object_or_404(Guild, pk=pk)
    member = _get_member(request)
    if member is not None:
        GuildMembership.objects.filter(guild=guild, member=member).delete()
        messages.success(request, f"You left {guild.name}.")
    return redirect("hub_guild_detail", pk=guild.pk)


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


@login_required
@require_POST
def guild_announcement_create(request: HttpRequest, pk: int) -> HttpResponse:
    """Post a new announcement to a guild from the edit page. Editor only."""
    from hub.forms import GuildAnnouncementForm

    guild = get_object_or_404(Guild, pk=pk)
    forbidden = _require_can_edit_guild(request, guild)
    if forbidden is not None:
        return forbidden
    form = GuildAnnouncementForm(request.POST)
    if form.is_valid():
        announcement = form.save(commit=False)
        announcement.guild = guild
        announcement.author = request.user
        announcement.save()
        messages.success(request, "Announcement posted.")
    else:
        messages.error(request, "Couldn't post the announcement — add a title and body.")
    return redirect("hub_guild_edit", pk=guild.pk)


@login_required
@require_POST
def guild_announcement_delete(request: HttpRequest, pk: int, announcement_pk: int) -> HttpResponse:
    """Delete a guild announcement. Editor only.

    The companion *create*/publish endpoint (which fires the ``guild_announcement``
    notification) is deferred until Plan 2's ``core.notifications`` lands — see DEFERRED.md.
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
def beta_feedback(request: HttpRequest) -> HttpResponse:
    """Beta feedback page — users can report bugs, request features, or leave general feedback."""
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
    member = _get_member(request)
    ctx = _get_hub_context(request)

    if member is None:
        messages.info(request, "Your account is not linked to a membership.")
        return render(request, "hub/tab_history.html", {**ctx, "charges": []})

    tab, _created = Tab.objects.get_or_create(member=member)
    charges = tab.charges.exclude(status=TabCharge.Status.PENDING).order_by("-created_at").prefetch_related("entries")

    return render(request, "hub/tab_history.html", {**ctx, "charges": charges})


_CALENDAR_PAGE_SIZE = 10


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
    from membership.models import CalendarEvent, Guild

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
    all_events = list(events_qs.select_related("guild", "feed").order_by("start_dt"))

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

    source_colors: dict[str, str] = {"classes": classes_color}
    for feed in calendar_feeds:
        source_colors[f"feed-{feed.pk}"] = feed.color
    for g in guilds_with_calendars:
        source_colors[str(g.pk)] = g.calendar_color

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

    return {
        "week_events": week_events,
        "month_events": month_events,
        "event_page": event_page,
        "event_total_pages": total_pages,
        "guilds_with_calendars": guilds_with_calendars,
        "calendar_feeds": calendar_feeds,
        "classes_enabled": classes_enabled,
        "classes_color": classes_color,
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


def community_calendar(request: HttpRequest) -> HttpResponse:
    """Community Calendar page — upcoming events from all guild and general calendars."""
    ctx = _get_hub_context(request)
    cal_ctx = _get_calendar_context(request)

    default_filters = []
    for feed in cal_ctx["calendar_feeds"]:
        default_filters.append(f"feed-{feed.pk}")
    if cal_ctx["classes_enabled"]:
        default_filters.append("classes")
    for g in cal_ctx["guilds_with_calendars"]:
        default_filters.append(str(g.pk))

    cal_ctx["default_filters_json"] = json.dumps(default_filters).replace('"', '\\"')
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
    return render(request, "hub/partials/calendar_content.html", cal_ctx)


def _ical_escape(value: str) -> str:
    """Escape special characters per RFC 5545 §3.3.11."""
    value = value.replace("\\", "\\\\")
    value = value.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    value = value.replace(";", "\\;").replace(",", "\\,")
    return value


@login_required
def calendar_export_ics(request: HttpRequest) -> HttpResponse:
    """Download a combined iCal file of all upcoming events."""
    from membership.models import CalendarEvent

    now = dj_timezone.now()
    horizon = now + timedelta(days=90)
    events = (
        CalendarEvent.objects.filter(start_dt__gte=now, start_dt__lte=horizon)
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
            f"SUMMARY:{_ical_escape(evt.title)}",
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
            lines.append(f"DESCRIPTION:{_ical_escape(evt.description[:250])}")
        if evt.location:
            lines.append(f"LOCATION:{evt.location}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    ical_content = "\r\n".join(lines) + "\r\n"

    response = HttpResponse(ical_content, content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="past-lives-calendar.ics"'
    return response


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
def admin_voting_dashboard(request: HttpRequest) -> HttpResponse:
    """Admin voting dashboard — pool stats, vote leaders, snapshot actions."""
    from plfog.dashboard import dashboard_callback

    ctx = _get_hub_context(request)
    ctx = dashboard_callback(request, ctx)
    return render(request, "hub/admin/voting_dashboard.html", ctx)


@fog_admin_required
def admin_members(request: HttpRequest) -> HttpResponse:
    """Admin members management — paginated list with search + status/role/type filters."""
    from django.core.paginator import Paginator
    from django.db.models import Count, Q

    ctx = _get_hub_context(request)
    status_filter = request.GET.get("status", "active")
    role_filter = request.GET.get("role", "")
    type_filter = request.GET.get("type", "")
    search = request.GET.get("q", "").strip()

    qs = (
        Member.objects.select_related("user", "membership_plan")
        .annotate(class_count=Count("classes", distinct=True))
        .order_by("full_legal_name")
    )
    if status_filter and status_filter != "all":
        qs = qs.filter(status=status_filter)
    if role_filter:
        qs = qs.filter(fog_role=role_filter)
    if type_filter:
        qs = qs.filter(member_type=type_filter)
    if search:
        qs = qs.filter(
            Q(full_legal_name__icontains=search)
            | Q(preferred_name__icontains=search)
            | Q(user__email__icontains=search)
            | Q(discord_handle__icontains=search)
        )

    paginator = Paginator(qs, 50)
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
            "search": search,
            "status_choices": Member.Status.choices,
            "role_choices": Member.FogRole.choices,
            "type_choices": Member.MemberType.choices,
        },
    )


@fog_admin_required
def admin_member_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Hub-native edit form for a single Member."""
    member = get_object_or_404(Member, pk=pk)

    if request.method == "POST":
        form = MemberAdminEditForm(request.POST, instance=member)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.save()
            obj.apply_admin_role(form.cleaned_data["role"])
            display = obj.full_legal_name or (obj.user.email if obj.user else f"member #{obj.pk}")
            messages.success(request, f"Saved {display}.")
            return redirect("hub_admin_members")
    else:
        form = MemberAdminEditForm(instance=member)

    ctx = _get_hub_context(request)
    return render(request, "hub/admin/member_edit.html", {**ctx, "form": form, "member": member})


def _legacy_instructor_sync_status() -> tuple[list[dict[str, object]], int]:
    """Return instructor match stats for the Legacy CMS tab.

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


@fog_admin_required
def admin_site_settings(request: HttpRequest) -> HttpResponse:
    """Admin site settings — edit the SiteConfiguration singleton and its calendar feeds.

    The page exposes three tabs (``general``, ``calendar``, and ``legacy-cms``). The Calendar tab
    owns a ``CalendarFeedFormSet`` so admins can add/remove iCal feeds inline.
    """
    from core.models import CalendarFeed, SiteConfiguration

    config = SiteConfiguration.load()
    active_tab = request.GET.get("tab", "general")
    if active_tab not in {"general", "calendar", "legacy-cms"}:
        active_tab = "general"

    feed_queryset = CalendarFeed.objects.all()

    if request.method == "POST":
        # Handle "Sync Now" action — separate from the settings form
        if request.POST.get("action") == "sync_now":
            from classes.import_service import sync_legacy_cms

            try:
                count = sync_legacy_cms()
                messages.success(request, f"Synced {count} offering(s) from the legacy CMS.")
            except Exception as exc:
                messages.error(request, f"Sync failed: {exc}")
            url = reverse("hub_admin_site_settings")
            return redirect(f"{url}?tab=legacy-cms")

        form = SiteSettingsForm(request.POST, instance=config)
        feed_formset = CalendarFeedFormSet(request.POST, queryset=feed_queryset, prefix="feeds")
        if form.is_valid() and feed_formset.is_valid():
            form.save()
            instances = feed_formset.save(commit=False)
            for obj in feed_formset.deleted_objects:
                obj.delete()
            for inst in instances:
                # Skip blank "+ Add" rows the user never filled in.
                if not inst.name and not inst.ical_url:
                    continue
                inst.save()
            messages.success(request, "Site settings saved.")
            target_tab = request.POST.get("submitted_tab", active_tab)
            url = reverse("hub_admin_site_settings")
            return redirect(f"{url}?tab={target_tab}")
    else:
        form = SiteSettingsForm(instance=config)
        feed_formset = CalendarFeedFormSet(queryset=feed_queryset, prefix="feeds")

    instructor_sync_rows, legacy_cms_unmatched = _legacy_instructor_sync_status()
    ctx = _get_hub_context(request)
    return render(
        request,
        "hub/admin/site_settings.html",
        {
            **ctx,
            "form": form,
            "feed_formset": feed_formset,
            "active_tab": active_tab,
            "classes_color_field": form["classes_calendar_color"],
            "sync_classes_field": form["sync_classes_enabled"],
            "legacy_cms_sync_field": form["legacy_cms_sync_enabled"],
            "instructor_sync_rows": instructor_sync_rows,
            "legacy_cms_unmatched": legacy_cms_unmatched,
            "config": config,
        },
    )
