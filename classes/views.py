"""Views for the Classes app — admin tabs, public portal, instructor profile pages."""

from __future__ import annotations

import json
from datetime import timedelta
from functools import wraps
from typing import TYPE_CHECKING, Any, Callable, TypedDict, cast

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.core.paginator import Paginator
from django.db.models import Count, F, IntegerField, Max, Min, OuterRef, Prefetch, Q, QuerySet, Subquery, Sum
from django.db.models.functions import TruncDate
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseForbidden,
    JsonResponse,
    StreamingHttpResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser, User

    from classes.forms import PaymentRefundForm, RegistrationMoveForm
    from classes.models import ClassOfferingQuerySet
    from membership.models import Member

from hub.toast import trigger_toast
from hub.view_as import classes_review_access_required, refund_authority_required

from classes.emails import (
    emit_instructor_new_registration,
    send_admin_registration_notification,
    send_class_review_decision,
    send_class_welcome_email,
    send_class_welcome_email_test,
    send_guild_lead_review_reminder,
    send_registration_confirmation,
    send_waitlist_joined_confirmation,
)
from classes.lifecycle import ADMIN_FACETS, INSTRUCTOR_FACETS, facet_rows, resolve_facet
from classes.questions import prefill_answers
from classes.table import prepare_table
from classes.templatetags.classes_tags import member_price_cents as compute_member_price_cents
from classes.forms import (
    CategoryForm,
    ClassCancelForm,
    ClassChangeRequestForm,
    ClassOfferingForm,
    ClassReviewDecisionForm,
    ClassSessionFormSet,
    ClassSettingsForm,
    DiscountCodeForm,
    InstructorOrientationCompleteForm,
    TeachClassOfferingForm,
    TeachPublishedClassForm,
    TeachWelcomeEmailForm,
    RegistrationForm,
    RegistrationQuestionForm,
    build_class_faq_formset,
)
from classes.models import (
    MAX_GALLERY_IMAGES,
    Category,
    ClassApproval,
    ClassImage,
    ClassOffering,
    ClassSettings,
    CmsActivity,
    DiscountCode,
    Registration,
    RegistrationQuestion,
    readiness_error_text,
    readiness_items,
)
from core.models import SiteConfiguration

_ViewFunc = Callable[..., HttpResponse]

# Timeframe windows for the public catalog's "When" filter. Keys are the raw
# GET values; values are the horizon in days. "all"/absent/unknown → no upper
# bound (all upcoming). Kept in one named place so the three magic numbers
# don't scatter across the view and templates.
WITHIN_DAYS = {"30": 30, "90": 90, "180": 180}

# Soft, non-blocking suggestion shown after a class submits with fewer than three
# gallery photos. The hard requirement (own hero image plus at least one gallery
# photo) lives on the model as ``ClassOffering.has_submittable_image``; this is
# only encouragement to add more.
_PHOTO_NUDGE_MESSAGE = "Classes with 3 or more photos get more sign-ups — consider adding a few more."


def _browsable_classes() -> Any:
    """Published, non-private classes still open for booking, soonest first.

    Delegates the date gate to ``ClassOffering.objects.bookable()``: flexible
    classes always qualify; a dated class (single or series) drops off the
    instant its first session starts, so a part-finished series is never listed.
    """
    return ClassOffering.objects.bookable().select_related("category", "instructor").prefetch_related("sessions")


def _bookable_run_options(offering: Any) -> list[Any]:
    """Still-bookable date-sets of this class, including the current run.

    Powers the register-page dropdown for switching runs. Returns an empty list
    unless there's an actual choice (more than one bookable run in the group).
    Each returned offering carries a ``spots_left`` attribute for its label.
    """
    runs = list(
        ClassOffering.objects.bookable().filter(grouping_key=offering.grouping_key).prefetch_related("sessions")
    )
    if len(runs) <= 1:
        return []
    run_spots = ClassOffering.objects.filter(pk__in=[r.pk for r in runs]).spots_remaining_map()
    for run in runs:
        run.spots_left = run_spots.get(run.pk, run.capacity)
    return runs


class _CatalogGroup:
    """One public catalog card: a class plus every date it is offered on.

    ``representative`` supplies the shared display chrome (title, image, price,
    instructor); ``members`` are the individual dated offerings, each still its
    own bookable unit with its own capacity. Built from offerings already sorted
    by soonest upcoming session, so the first member seen is the representative
    and members stay date-ordered.
    """

    def __init__(self, representative: Any) -> None:
        self.representative = representative
        self.members = [representative]

    @property
    def date_count(self) -> int:
        return len(self.members)

    @property
    def is_multi(self) -> bool:
        return len(self.members) > 1


def _grouped_catalog(offerings: Any) -> list[_CatalogGroup]:
    """Collapse offerings sharing a grouping key into one card, preserving order."""
    groups: dict[str, _CatalogGroup] = {}
    order: list[str] = []
    for offering in offerings:
        key = offering.grouping_key or f"solo:{offering.pk}"
        group = groups.get(key)
        if group is None:
            groups[key] = _CatalogGroup(offering)
            order.append(key)
        else:
            group.members.append(offering)
    return [groups[key] for key in order]


def _coerce_dollars_to_cents(raw: str | None) -> int:
    """Parse a form-submitted dollar amount into cents. Empty/invalid → 0."""
    if not raw:
        return 0
    try:
        return int(round(float(raw) * 100))
    except (TypeError, ValueError):
        return 0


def _apply_browse_filters(qs: Any, request: HttpRequest) -> Any:
    """Apply all GET-param browse filters to the class listing queryset."""
    slug = request.GET.get("category", "").strip()
    if slug:
        qs = qs.filter(category__slug=slug)

    guild_slug = request.GET.get("guild", "").strip()
    if guild_slug:
        qs = qs.filter(category__guild__slug=guild_slug)

    instructor_slugs = [s for s in request.GET.getlist("instructor") if s]
    if instructor_slugs:
        qs = qs.filter(instructor__instructor_slug__in=instructor_slugs)

    min_price = _coerce_dollars_to_cents(request.GET.get("min_price"))
    if min_price > 0:
        qs = qs.filter(price_cents__gte=min_price)

    max_price = _coerce_dollars_to_cents(request.GET.get("max_price"))
    if max_price > 0:
        qs = qs.filter(price_cents__lte=max_price)

    if request.GET.get("members_only") == "1":
        qs = qs.filter(member_discount_pct__gt=0)
    if request.GET.get("free") == "1":
        qs = qs.filter(price_cents=0)
    if request.GET.get("upcoming") == "1":
        qs = qs.exclude(first_session_at__isnull=True)

    within = request.GET.get("within", "").strip()
    if within in WITHIN_DAYS:
        horizon = timezone.now() + timedelta(days=WITHIN_DAYS[within])
        # Keep flexible/undated classes in every window — they mirror bookable()'s
        # own rule that a flexible class always qualifies (it has no fixed date to
        # fall outside the window). A bare `first_session_at__lte` would silently
        # drop them, since bookable() annotates first_session_at as NULL for them.
        qs = qs.filter(Q(first_session_at__lte=horizon) | Q(scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE))

    return qs


def public_list(request: HttpRequest) -> HttpResponse:
    """Public portal — hero + sticky category filter + grouped class cards.

    Supports HTMX partial swaps: when the request carries an ``HX-Request``
    header, returns just the results grid so the filter form can update the
    page in place without a full reload. The querystring is the source of
    truth for filter state; ``hx-push-url`` keeps the URL shareable.
    """
    settings_obj = ClassSettings.load()

    from membership.models import Guild

    selected_category_slug = request.GET.get("category", "").strip()
    # Resolve the guild behind ?guild=<slug> for the active-filter heading and empty state.
    # Fail-soft, mirroring the raw ?category= filter: an unknown slug leaves selected_guild
    # None (the filter still yields zero rows → generic empty copy) rather than 404-ing.
    selected_guild_slug = request.GET.get("guild", "").strip()
    selected_guild = Guild.objects.filter(slug=selected_guild_slug).first() if selected_guild_slug else None
    selected_instructor_slugs = [s for s in request.GET.getlist("instructor") if s]
    members_only = request.GET.get("members_only") == "1"
    free_only = request.GET.get("free") == "1"
    upcoming_only = request.GET.get("upcoming") == "1"
    selected_within = request.GET.get("within", "all")
    selected_within_days = WITHIN_DAYS.get(selected_within)

    classes_qs = _apply_browse_filters(_browsable_classes(), request)
    catalog_groups = _grouped_catalog(classes_qs)

    # Category chips and per-category counts always reflect the unfiltered
    # universe of browsable classes so users can see what else is out there.
    # Counts are per-group (distinct grouping keys) so they match the collapsed
    # cards rather than the raw, deduplicated offering rows.
    keys_by_category: dict[int, set[str]] = {}
    for offering in _browsable_classes():
        key = offering.grouping_key or f"solo:{offering.pk}"
        keys_by_category.setdefault(offering.category_id, set()).add(key)
    category_counts: dict[int, int] = {cat_id: len(keys) for cat_id, keys in keys_by_category.items()}
    # Show every guild type in the catalog, even those with no bookable classes right
    # now, so members can see the full range of guilds. Zero-class types get a count of
    # 0 (reflected in the "Guild Types" hero stat and the guild-type filter dropdown).
    # Mirror the demo gate from ClassOfferingQuerySet.public(): when demo classes are
    # hidden, hide demo-slug guild types too. The old "count > 0" filter hid them only
    # as a side effect (they had zero bookable classes), so listing all categories
    # unconditionally would leak [DEMO] guild types into the public catalog.
    category_qs = Category.objects.all()
    if not SiteConfiguration.load().display_demo_classes:
        category_qs = category_qs.exclude(slug__startswith="demo-")
    categories = list(category_qs)
    for cat in categories:
        cat.class_count = category_counts.get(cat.id, 0)  # type: ignore[attr-defined]

    # Instructors-for-filter: members who teach at least one browsable class.
    from membership.models import Member as MemberModel

    instructors_for_filter = list(
        MemberModel.objects.filter(classes__in=_browsable_classes(), instructor_slug__gt="")
        .distinct()
        .order_by("full_legal_name")
    )

    paginator = Paginator(catalog_groups, 25)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    # Attach per-date seat counts to the members shown on this page in one query,
    # so each date row can display its own "N left" without an N+1.
    page_member_pks = [member.pk for group in page_obj for member in group.members]
    spots_map = ClassOffering.objects.filter(pk__in=page_member_pks).spots_remaining_map()
    for group in page_obj:
        for member in group.members:
            member.spots_left = spots_map.get(member.pk, member.capacity)

    # Strip 'page' from the querystring so pagination links can append it cleanly.
    filter_qs = request.GET.copy()
    filter_qs.pop("page", None)
    filter_querystring = filter_qs.urlencode()

    # Same querystring minus 'within' — powers the empty-state "Show all upcoming"
    # escape, which widens the timeframe while preserving every other filter.
    no_within = filter_qs.copy()
    no_within.pop("within", None)
    filter_querystring_no_within = no_within.urlencode()

    active_filter_count = sum(
        1
        for v in (
            selected_instructor_slugs,
            request.GET.get("min_price"),
            request.GET.get("max_price"),
            members_only,
            free_only,
            upcoming_only,
        )
        if v
    )

    # HTMX partial: return just the results grid so the filter form can swap
    # in place without rerendering hero + filter chrome. Computed once so the
    # OOB hero-count block renders only on HTMX responses (never as a stray
    # duplicate inside the embedded include on a full page load).
    is_htmx = bool(request.headers.get("HX-Request") and not request.headers.get("HX-Boosted"))

    context = {
        "settings_obj": settings_obj,
        "site_config": SiteConfiguration.load(),
        "categories": categories,
        "selected_category_slug": selected_category_slug,
        "selected_guild_slug": selected_guild_slug,
        "selected_guild": selected_guild,
        "selected_instructor_slugs": selected_instructor_slugs,
        "instructors_for_filter": instructors_for_filter,
        "min_price": request.GET.get("min_price", ""),
        "max_price": request.GET.get("max_price", ""),
        "members_only": members_only,
        "free_only": free_only,
        "upcoming_only": upcoming_only,
        "selected_within": selected_within,
        "selected_within_days": selected_within_days,
        "active_filter_count": active_filter_count,
        "page_obj": page_obj,
        "paginator": paginator,
        "filter_querystring": filter_querystring,
        "filter_querystring_no_within": filter_querystring_no_within,
        "is_htmx": is_htmx,
        # The hero "Classes" tile is driven by paginator.count (see the template);
        # Guilds and Instructors both reflect the full browsable universe so they
        # stay stable while the live "Classes" count tracks the current filter.
        "total_instructors": len(instructors_for_filter),
        "total_categories": len(categories),
    }

    if is_htmx:
        return render(request, "classes/public/_list_results.html", context)
    return render(request, "classes/public/list.html", context)


def public_category(request: HttpRequest, slug: str) -> HttpResponse:
    """Public category landing — same layout as list, pre-filtered."""
    category = get_object_or_404(Category, slug=slug)
    mutable_get = request.GET.copy()
    mutable_get["category"] = category.slug
    request.GET = mutable_get  # type: ignore[assignment]  # .copy() returns a mutable QueryDict; stub types the attr immutable
    return public_list(request)


def public_class_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """Full class detail page — schedule, info grid, (future: registration form)."""
    offering = get_object_or_404(
        ClassOffering.objects.public().select_related("category", "instructor").prefetch_related("sessions"),
        slug=slug,
    )
    settings_obj = ClassSettings.load()
    # Member price reads off the SALE base so every quoted member number
    # matches what compute_final_price_cents will actually charge.
    member_price_cents = compute_member_price_cents(offering.sale_price_cents, offering.member_discount_pct)
    now = timezone.now()
    upcoming_sessions = list(offering.sessions.filter(starts_at__gte=now).order_by("starts_at"))
    # A series is its full set of dates; a single class is its one date. Show every
    # session for a series (so a started one still reads as the N-session series it
    # is, with past dates marked) and just the dated session for a single.
    schedule_sessions = list(offering.sessions.order_by("starts_at")) if offering.is_series else upcoming_sessions

    # Other dates this same class is offered on, so the visitor can switch dates
    # without hunting through the catalog. Only runs you can still book are shown
    # (a started run is dropped), and each keeps its own seats.
    sibling_offerings: list[Any] = []
    if offering.grouping_key:
        sibling_offerings = list(
            ClassOffering.objects.bookable()
            .filter(grouping_key=offering.grouping_key)
            .exclude(pk=offering.pk)
            .select_related("instructor")
            .prefetch_related("sessions")
        )
        sib_spots = ClassOffering.objects.filter(pk__in=[s.pk for s in sibling_offerings]).spots_remaining_map()
        for sibling in sibling_offerings:
            sibling.spots_left = sib_spots.get(sibling.pk, sibling.capacity)

    # Only classes you could still sign up for — never surface a run whose dates
    # have already passed. ``bookable()`` drops any dated class once its first
    # session begins (and keeps flexible, arrange-with-instructor ones), already
    # ordered soonest-first.
    related_offerings = list(
        ClassOffering.objects.bookable()
        .filter(category=offering.category)
        .exclude(pk=offering.pk)
        .select_related("instructor")[:3]
    )
    from hub.view_as import ROLE_ADMIN, ROLE_GUILD_OFFICER
    from membership.permissions import can_edit_category as can_edit_category_perm
    from membership.permissions import can_edit_class, is_effective_staff

    view_as = getattr(request, "view_as", None)
    # is_admin reflects the *actual* role — used for banners/admin links on the page.
    is_admin = view_as is not None and (view_as.has_actual(ROLE_ADMIN) or view_as.has_actual(ROLE_GUILD_OFFICER))

    member = getattr(request.user, "member", None)
    is_instructor = member is not None and offering.instructor_id == member.pk

    # One shared rule decides edit rights: admin/officer, the lead of the
    # category's guild (FK only), or the instructor. view_as preview is honored.
    can_edit_offering = can_edit_class(request, offering)
    edit_url = None
    if can_edit_offering:
        if is_effective_staff(request):
            edit_url = reverse("classes:admin_class_edit", kwargs={"pk": offering.pk})
        else:
            # Instructors and guild leads manage the class from the teaching portal.
            edit_url = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})

    # If the class has NO specific image (real or legacy), it falls back to the category image.
    has_no_class_image = not offering.image and not offering.legacy_image_url
    can_edit_category = bool(
        has_no_class_image and offering.category.hero_image and can_edit_category_perm(request, offering.category)
    )

    offering_ct = ContentType.objects.get_for_model(ClassOffering)
    category_ct = ContentType.objects.get_for_model(Category)

    return render(
        request,
        "classes/public/detail.html",
        {
            "offering": offering,
            "offering_ct_id": offering_ct.pk,
            "category_ct_id": category_ct.pk,
            "can_edit_offering": can_edit_offering,
            "can_edit_category": can_edit_category,
            "edit_url": edit_url,
            "is_admin": is_admin,
            "is_instructor": is_instructor,
            "view_as": view_as,
            "settings_obj": settings_obj,
            "site_config": SiteConfiguration.load(),
            "upcoming_sessions": upcoming_sessions,
            "schedule_sessions": schedule_sessions,
            "is_bookable": offering.is_bookable,
            "now": now,
            "member_price_cents": member_price_cents,
            "spots_remaining": offering.spots_remaining,
            "related_offerings": related_offerings,
            "sibling_offerings": sibling_offerings,
        },
    )


def public_instructor(request: HttpRequest, slug: str) -> HttpResponse:
    """Public instructor profile — bio, photo, current + past classes."""
    from membership.models import Member as MemberModel

    instructor = get_object_or_404(MemberModel, instructor_slug=slug, status=MemberModel.Status.ACTIVE)
    now = timezone.now()
    current_classes = (
        ClassOffering.objects.public()  # type: ignore[misc]  # django-stubs can't see annotate() aliases
        .filter(instructor=instructor)
        .prefetch_related("sessions")
        .annotate(first_session_at=Min("sessions__starts_at", filter=Q(sessions__starts_at__gte=now)))
        .filter(Q(first_session_at__isnull=False) | Q(scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE))
        .order_by("first_session_at", "title")
    )
    past_classes = (
        ClassOffering.objects.filter(instructor=instructor, status=ClassOffering.Status.ARCHIVED)
        .select_related("category")
        .order_by("-updated_at")
    )
    return render(
        request,
        "classes/public/instructor.html",
        {
            "instructor": instructor,
            "current_classes": current_classes,
            "past_classes": past_classes,
            "settings_obj": ClassSettings.load(),
            "site_config": SiteConfiguration.load(),
        },
    )


def _client_ip(request: HttpRequest) -> str:
    """Best-effort client IP, honoring X-Forwarded-For when behind a proxy."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _member_for_email(email: str) -> "Member | None":
    """Verified Member matching this email, or None.

    Thin wrapper over the shared :func:`membership.selectors.member_for_verified_email`
    selector (the Discord link flow uses the same lookup); behavior is unchanged.
    """
    from membership.selectors import member_for_verified_email

    return member_for_verified_email(email)


def _registration_initial_for_user(user: "AbstractBaseUser | AnonymousUser | None") -> dict[str, str]:
    """Pre-fill values pulled from the logged-in user's Member record."""
    if not user or not user.is_authenticated:
        return {}
    user = cast("AbstractUser", user)
    member = getattr(user, "member", None)
    if member is None:
        return {"email": user.email or ""}
    name = (member.preferred_name or member.full_legal_name or user.get_full_name() or "").strip()
    first_name, _, last_name = name.partition(" ")
    return {
        "first_name": first_name or user.first_name or "",
        "last_name": last_name.strip() or user.last_name or "",
        "email": member.primary_email or user.email or "",
        "phone": member.phone or "",
        "pronouns": member.pronouns or "",
    }


def _cache_registration_to_profile(request: HttpRequest, registration: Registration) -> None:
    """Seed the logged-in user's profile from their registration answers (no-op for guests)."""
    if not request.user.is_authenticated:
        return
    from core.models import UserProfile

    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    profile.cache_from_registration(registration)
    # Remember the custom-question answers so the next registration pre-fills them.
    answers = {a.question_id: a.answer_text for a in registration.custom_answers.all()}
    if answers:
        profile.set_custom_answers(answers)


def _register_prefill(request: HttpRequest) -> tuple[str, dict[int, str], bool, dict[str, str]]:
    """Resolve the email + pre-fill inputs for the registration form.

    On POST the email comes from the submitted data; on GET an ``?email=`` query
    (sent by the email field's HTMX recall) lets a returning guest's saved answers
    pre-fill. Returns ``(bound_email, custom_answers_initial, answers_prefilled,
    field_initial)`` — the field initial pre-fills standard fields from the
    logged-in user's Member record (GET only).
    """
    if request.method == "POST":
        bound_email = (request.POST.get("email") or "").strip()
    else:
        bound_email = (request.GET.get("email") or "").strip()
    custom_answers_initial, answers_prefilled = prefill_answers(request.user, bound_email)
    field_initial = {} if request.method == "POST" else _registration_initial_for_user(request.user)
    if bound_email and request.method != "POST":
        field_initial.setdefault("email", bound_email)
    return bound_email, custom_answers_initial, answers_prefilled, field_initial


def _stale_claim_link_redirect(request: HttpRequest, offering: ClassOffering) -> HttpResponse | None:
    """Claim-link collision guard: bounce a stale ``?waitlist_token=`` click, or ``None``.

    A manually promoted person may still hold an un-clicked auto claim link. If
    their registration already holds a seat (CONFIRMED / PENDING), a stale claim
    click must never create a duplicate registration row — redirect to their
    self-serve page. A CANCELLED / REFUNDED registrant falls through to the
    register form: they were told they're out (removal email) and may
    legitimately sign up again.
    """
    waitlist_token = request.GET.get("waitlist_token", "")
    if not waitlist_token:
        return None
    claiming = Registration.objects.filter(self_serve_token=waitlist_token, class_offering=offering).first()
    if claiming is None or claiming.status not in (Registration.Status.CONFIRMED, Registration.Status.PENDING):
        return None
    messages.success(request, "Good news! You're already in this class.")
    return redirect("classes:my_registration", token=claiming.self_serve_token)


def _confirm_free_registration(request: HttpRequest, registration: Registration) -> HttpResponse:
    """Free class — confirm + email immediately, no Stripe round-trip.

    Attributes the confirmation to the acting user (registrant) so the audit
    feed records who confirmed, not "System".
    """
    registration._acting_user = (
        request.user
        if request.user.is_authenticated
        else (registration.member.user if registration.member and registration.member.user else None)
    )
    registration.status = Registration.Status.CONFIRMED
    registration.confirmed_at = timezone.now()
    registration.amount_paid_cents = 0
    registration.save(update_fields=["status", "confirmed_at", "amount_paid_cents"])
    if registration.discount_code_id:
        _bump_discount_use_count(registration.discount_code_id)
        _log_discount_redeemed(registration)
    send_registration_confirmation(registration)
    send_class_welcome_email(registration)
    emit_instructor_new_registration(registration)
    send_admin_registration_notification(registration)
    from classes.services.mailchimp_subscribe import subscribe_registration

    # Subscribe BEFORE account creation: derive_tags decides
    # `first-time-student` by asking whether this email is already a known
    # member, and ensure_account_for_registration is what makes it one.
    # The profile opt-in stamp is mirrored afterwards, inside that call.
    subscribe_registration(registration)
    from core.services.guest_account import ensure_account_for_registration

    ensure_account_for_registration(registration)
    return redirect("classes:register_success", slug=registration.class_offering.slug)


def register(request: HttpRequest, slug: str) -> HttpResponse:
    """Public registration form — collects info, signs waivers, kicks off Stripe Checkout.

    Free classes (price_cents == 0 after discounts) confirm immediately and
    skip Stripe. Paid classes redirect to a Stripe Checkout Session; the
    webhook handler flips the registration to CONFIRMED on success.
    """
    offering = get_object_or_404(
        ClassOffering.objects.public().select_related("category", "instructor"),
        slug=slug,
    )

    stale_claim = _stale_claim_link_redirect(request, offering)
    if stale_claim is not None:
        return stale_claim

    settings_obj = ClassSettings.load()

    # You can't join a class once it has started — a series can't be entered
    # part-way through. Send late arrivals back to the detail page with a note.
    if not offering.is_bookable:
        messages.info(request, "Registration has closed for this class — it has already started.")
        return redirect("classes:public_class_detail", slug=offering.slug)

    # Site-wide kill switch (Site Settings → Features). When class registration is
    # off, refuse sign-ups regardless of the hidden button (defense in depth).
    from core.models import SiteConfiguration

    site_config = SiteConfiguration.load()
    if not site_config.class_registration_enabled:
        messages.info(
            request,
            site_config.class_registration_disabled_note or "Online registration is currently unavailable.",
        )
        return redirect("classes:public_class_detail", slug=offering.slug)

    # Waitlist intent: ?waitlist=1 (offered when the class is sold out) routes
    # to the no-charge waitlist branch below. Forced on automatically when the
    # class has no spots left so we never hide the option from a registrant
    # who lands here from a stale link.
    is_waitlist = request.GET.get("waitlist") == "1" or offering.spots_remaining <= 0

    # Two-pass form: first POST validates email so we can detect a member
    # before computing price, then re-binds to surface the discounted total.
    bound_email, custom_answers_initial, answers_prefilled, initial = _register_prefill(request)
    member = _member_for_email(bound_email) if bound_email else None

    form = RegistrationForm(
        request.POST or None,
        offering=offering,
        settings_obj=settings_obj,
        member=member,
        client_ip=_client_ip(request),
        initial=initial,
        is_waitlist=is_waitlist,
        user=request.user,
        custom_answers_initial=custom_answers_initial,
    )

    if request.method == "POST" and form.is_valid() and is_waitlist:
        # The form sets status=WAITLISTED on save (see RegistrationForm.save), so the
        # WAITLIST_JOINED activity is logged at creation time.
        registration = form.save()
        _cache_registration_to_profile(request, registration)
        send_waitlist_joined_confirmation(registration)
        messages.success(
            request,
            f"You're on the waitlist for {offering.title}. We'll email you if a spot opens.",
        )
        return redirect("classes:my_registration", token=registration.self_serve_token)

    if request.method == "POST" and form.is_valid():
        registration = form.save()
        _cache_registration_to_profile(request, registration)
        final_price = form.compute_final_price_cents()

        if final_price == 0:
            return _confirm_free_registration(request, registration)

        # Paid class — kick off Stripe Checkout.
        from billing import stripe_utils

        success_url = (
            request.build_absolute_uri(reverse("classes:register_success", kwargs={"slug": offering.slug}))
            + f"?reg={registration.self_serve_token}"
        )
        cancel_url = (
            request.build_absolute_uri(reverse("classes:register_cancelled", kwargs={"slug": offering.slug}))
            + f"?reg={registration.self_serve_token}"
        )

        # A series is still ONE line item at the series price — only the Stripe
        # line-item name gains a "(N-session series)" suffix so the buyer's
        # receipt reads clearly. No loop, no fan-out: one Registration, one
        # Checkout Session, one charge, one seat (Option A).
        product_name = offering.title
        if offering.is_series and offering.series_session_count > 1:
            product_name = f"{offering.title} ({offering.series_session_count}-session series)"
        # Label-only marker so the Stripe receipt reflects the sale (precedent:
        # the series suffix above). The amount already carries the sale price.
        if offering.sale_is_active:
            product_name = f"{product_name} (Sale)"

        try:
            checkout = stripe_utils.create_class_checkout_session(
                amount_cents=final_price,
                product_name=product_name,
                customer_email=registration.email,
                success_url=success_url,
                cancel_url=cancel_url,
                metadata={
                    "registration_id": str(registration.pk),
                    "class_slug": offering.slug,
                    "kind": "class_registration",
                },
                idempotency_key=f"class-checkout-reg-{registration.pk}",
            )
        except Exception:
            registration.delete()  # roll back the half-created registration
            raise

        registration.stripe_session_id = checkout["id"]
        registration.amount_paid_cents = final_price  # provisional; webhook is canonical
        registration.save(update_fields=["stripe_session_id", "amount_paid_cents"])
        return redirect(checkout["url"])

    # Member price reads off the SALE base so every quoted member number
    # matches what compute_final_price_cents will actually charge.
    member_price_cents = compute_member_price_cents(offering.sale_price_cents, offering.member_discount_pct)
    upcoming_sessions = list(offering.sessions.filter(starts_at__gte=timezone.now()).order_by("starts_at"))

    run_options = _bookable_run_options(offering)

    return render(
        request,
        "classes/public/register.html",
        {
            "offering": offering,
            "form": form,
            "settings_obj": settings_obj,
            "site_config": SiteConfiguration.load(),
            "member_price_cents": member_price_cents,
            "spots_remaining": offering.spots_remaining,
            "is_waitlist": is_waitlist,
            "upcoming_sessions": upcoming_sessions,
            "run_options": run_options,
            "answers_prefilled": answers_prefilled,
        },
    )


def register_success(request: HttpRequest, slug: str) -> HttpResponse:
    """Landing page after successful checkout — webhook does the real work."""
    offering = get_object_or_404(
        ClassOffering.objects.public().select_related("category", "instructor"),
        slug=slug,
    )
    return render(
        request,
        "classes/public/register_success.html",
        {
            "offering": offering,
            "settings_obj": ClassSettings.load(),
            "site_config": SiteConfiguration.load(),
        },
    )


def register_cancelled(request: HttpRequest, slug: str) -> HttpResponse:
    """User backed out of Stripe Checkout — clean up the unpaid registration."""
    offering = get_object_or_404(
        ClassOffering.objects.public().select_related("category", "instructor"),
        slug=slug,
    )
    token = request.GET.get("reg", "").strip()
    if token:
        Registration.objects.filter(
            self_serve_token=token,
            status=Registration.Status.PENDING,
            class_offering=offering,
        ).delete()
    return render(
        request,
        "classes/public/register_cancelled.html",
        {
            "offering": offering,
            "settings_obj": ClassSettings.load(),
            "site_config": SiteConfiguration.load(),
        },
    )


def my_registration(request: HttpRequest, token: str) -> HttpResponse:
    """Self-serve registration page — no auth, identified by the unguessable token."""
    registration = get_object_or_404(
        Registration.objects.select_related("class_offering", "class_offering__instructor"),
        self_serve_token=token,
    )
    offering = registration.class_offering
    upcoming_sessions = list(offering.sessions.filter(starts_at__gte=timezone.now()).order_by("starts_at"))
    class_cancelled = offering.status == ClassOffering.Status.CANCELLED
    can_self_cancel = (
        registration.status
        in {
            Registration.Status.PENDING,
            Registration.Status.CONFIRMED,
            Registration.Status.WAITLISTED,
        }
        and (not upcoming_sessions or upcoming_sessions[0].starts_at > timezone.now())
        and not class_cancelled
    )
    return render(
        request,
        "classes/public/my_registration.html",
        {
            "registration": registration,
            "offering": offering,
            "upcoming_sessions": upcoming_sessions,
            "can_self_cancel": can_self_cancel,
            "class_cancelled": class_cancelled,
            "paid_banner": request.GET.get("paid") == "1",
            "settings_obj": ClassSettings.load(),
            "site_config": SiteConfiguration.load(),
        },
    )


def my_registration_cancel(request: HttpRequest, token: str) -> HttpResponse:
    """Self-cancel a registration. Refunds aren't automated — admins handle them."""
    registration = get_object_or_404(Registration, self_serve_token=token)
    if request.method != "POST":
        return redirect("classes:my_registration", token=token)
    if registration.status not in {
        Registration.Status.PENDING,
        Registration.Status.CONFIRMED,
        Registration.Status.WAITLISTED,
    }:
        messages.info(request, "This registration is already cancelled.")
        return redirect("classes:my_registration", token=token)
    registration.cancel(
        reason="self-serve",
        actor=request.user if request.user.is_authenticated else None,
    )
    messages.success(request, "Your registration is cancelled.")
    return redirect("classes:my_registration", token=token)


def my_registration_pay(request: HttpRequest, token: str) -> HttpResponse:
    """Token-rails pay page for a promoted registration's outstanding balance.

    GET renders the page and NEVER creates a Stripe session (mail-scanner
    prefetch must not mint Checkout sessions); a settled or inactive
    registration redirects to the self-serve page with a state-aware message.
    POST creates the Checkout session for the full balance and redirects to
    Stripe's hosted page.
    """
    from classes.forms import STRIPE_MIN_CHARGE_CENTS

    registration = get_object_or_404(
        Registration.objects.select_related("class_offering", "class_offering__instructor"),
        self_serve_token=token,
    )
    if not registration.is_unpaid:
        if registration.status in (Registration.Status.CANCELLED, Registration.Status.REFUNDED):
            messages.info(request, "This registration is no longer active.")
        else:
            messages.info(request, "Nothing owed. You're all set.")
        return redirect("classes:my_registration", token=token)
    offering = registration.class_offering
    balance = registration.balance_due_cents

    def _render_pay_page(under_minimum: bool) -> HttpResponse:
        return render(
            request,
            "classes/public/registration_pay.html",
            {
                "registration": registration,
                "offering": offering,
                "upcoming_sessions": list(
                    offering.sessions.filter(starts_at__gte=timezone.now()).order_by("starts_at")
                ),
                "balance_due_dollars": f"{balance / 100:.2f}",
                "payment_in_flight": bool(registration.stripe_session_id),
                "under_minimum": under_minimum,
                "settings_obj": ClassSettings.load(),
                "site_config": SiteConfiguration.load(),
            },
        )

    if request.method != "POST":
        return _render_pay_page(under_minimum=False)
    if balance < STRIPE_MIN_CHARGE_CENTS:
        return _render_pay_page(under_minimum=True)
    from billing import stripe_utils

    success_url = request.build_absolute_uri(reverse("classes:my_registration", kwargs={"token": token})) + "?paid=1"
    cancel_url = request.build_absolute_uri(reverse("classes:my_registration_pay", kwargs={"token": token}))
    checkout = stripe_utils.create_class_checkout_session(
        amount_cents=balance,
        product_name=f"{offering.title} (balance)",
        customer_email=registration.email,
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={
            "registration_id": str(registration.pk),
            "class_slug": offering.slug,
            "kind": "class_payment_link",
        },
        idempotency_key=f"class-paylink-reg-{registration.pk}-{balance}",
    )
    registration.stripe_session_id = checkout["id"]
    registration.save(update_fields=["stripe_session_id"])
    return redirect(checkout["url"])


def _log_discount_redeemed(registration: Registration) -> None:
    """Append a DISCOUNT_CODE_REDEEMED activity row when a discount applied."""
    from classes import activity
    from classes.models import CmsActivity

    if not registration.discount_code_id:
        return
    activity.log(
        CmsActivity.Kind.DISCOUNT_CODE_REDEEMED,
        class_offering=registration.class_offering,
        registration=registration,
        payload={"code": registration.discount_code.code},  # type: ignore[union-attr]  # discount_code_id guard ensures non-None
    )


def _bump_discount_use_count(discount_code_id: int) -> None:
    """Atomic +1 on use_count — called on confirmed registration."""
    from django.db.models import F

    DiscountCode.objects.filter(pk=discount_code_id).update(use_count=F("use_count") + 1)


def admin_required(view_func: _ViewFunc) -> _ViewFunc:
    """Decorator: only admins (via request.view_as) may access."""

    @wraps(view_func)
    @login_required
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        view_as = getattr(request, "view_as", None)
        if view_as is None or not view_as.is_admin:
            return HttpResponseForbidden("Admin access required.")
        return view_func(request, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


def active_member_required(view_func: _ViewFunc) -> _ViewFunc:
    """Decorator: any active logged-in member — guards the orientation pages.

    Exactly the pre-unlock teaching gate: login → active Member or 403 → set
    ``request.teaching_member``. The orientation views use this (not
    ``teaching_member_required``) so a *locked* member can still reach the page
    that unlocks them.
    """

    @wraps(view_func)
    @login_required
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        from membership.models import Member as MemberModel

        assert request.user.is_authenticated  # @login_required guarantees a real User
        member = MemberModel.objects.filter(user=request.user, status=MemberModel.Status.ACTIVE).first()
        if member is None:
            return HttpResponseForbidden("An active member account is required to access the teaching portal.")
        request.teaching_member = member  # type: ignore[attr-defined]
        return view_func(request, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


def teaching_member_required(view_func: _ViewFunc) -> _ViewFunc:
    """Decorator: Teaching portal access — active members who completed the instructor orientation.

    Non-members and inactive members keep the 403 (orientation can't fix an
    inactive account). An *active* member who hasn't unlocked teaching is 302'd
    to the orientation page instead, so a locked click lands on the explainer and
    never a dead end (Spec D §5). This redirect is now the main way in: the sidebar
    stopped carrying a recruiting entry, so the remaining entry points (the Class
    Catalog's Manage My Classes, the guild pages' Teach a Class) all arrive here
    locked and rely on it.
    """

    @wraps(view_func)
    @login_required
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        from membership.models import Member as MemberModel

        assert request.user.is_authenticated  # @login_required guarantees a real User
        member = MemberModel.objects.filter(user=request.user, status=MemberModel.Status.ACTIVE).first()
        if member is None:
            return HttpResponseForbidden("An active member account is required to access the teaching portal.")
        if not member.can_create_classes:
            return redirect("classes:teach_orientation")
        request.teaching_member = member  # type: ignore[attr-defined]
        return view_func(request, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


def instructor_discount_codes_required(view_func: _ViewFunc) -> _ViewFunc:
    """Decorator: gate instructor self-service discount codes behind the site flag.

    Layered under ``teaching_member_required`` — teaching access alone isn't enough
    while the site has instructor discount codes switched off (the default). A
    soft-launch kill switch is not an authorization failure, so a direct URL hit
    gets the same treatment as every other feature-flag gate (``tab_detail``,
    ``class_register``): an info message and a redirect, never a 403. The Classes
    admin discount views never pass through here — admins are unaffected.
    """

    @wraps(view_func)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not SiteConfiguration.load().instructor_discount_codes_enabled:
            messages.info(request, "Discount codes are managed by admins. Ask an admin if you need one for your class.")
            return redirect("classes:teach_dashboard")
        return view_func(request, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


def classes_admin_access_required(view_func: _ViewFunc) -> _ViewFunc:
    """Decorator: Classes admin tabs are admin-only.

    Authorization checks the user's *actual* admin role (not the view-as
    preview) so an admin previewing as Instructor/Guest still reaches the
    admin pages when they navigate back. Instructors manage their own
    discount codes and registrations from the Teaching portal instead.
    """

    @wraps(view_func)
    @login_required
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        view_as = getattr(request, "view_as", None)
        if view_as is None or not view_as.has_actual("admin"):
            return HttpResponseForbidden("Classes admin access requires admin privileges.")
        return view_func(request, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


def classes_registrations_access_required(view_func: _ViewFunc) -> _ViewFunc:
    """Decorator: the consolidated registrations list/export is open to admins and
    to instructors/guild-leads, who see only their own classes' registrations.

    Admins reach it via their actual admin role (preview-independent). Everyone
    else needs at least one class they can edit (``editable_by``) — a plain member
    with no classes is forbidden. Mutating a registration (cancel / move / refund)
    stays admin-only via ``classes_admin_access_required``.
    """

    @wraps(view_func)
    @login_required
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        view_as = getattr(request, "view_as", None)
        if view_as is not None and view_as.has_actual("admin"):
            return view_func(request, *args, **kwargs)
        member = getattr(request.user, "member", None)
        if member is not None and ClassOffering.objects.editable_by(member).exists():
            return view_func(request, *args, **kwargs)
        return HttpResponseForbidden("You don't have access to registrations.")

    return wrapper  # type: ignore[return-value]


def _scoped_registrations(request: HttpRequest) -> QuerySet[Registration]:
    """Registrations this request may see.

    Admins and guild officers see every registration; everyone else sees only
    those for classes they can edit (a class they instruct, or one in a guild
    they lead). Callers are gated by ``classes_registrations_access_required``,
    so a non-admin here always has a linked member.
    """
    qs = Registration.objects.select_related("class_offering", "member")
    view_as = getattr(request, "view_as", None)
    if view_as is not None and view_as.has_actual("admin"):
        return qs
    return qs.filter(class_offering__in=ClassOffering.objects.editable_by(request.user.member))


def _filter_registrations(request: HttpRequest, qs: QuerySet[Registration]) -> QuerySet[Registration]:
    """Apply the optional ``status``, ``class``, ``instructor``, and ``mine`` GET filters.

    ``instructor`` mirrors the ``admin_classes`` pattern: validated as an int and
    silently ignored when bogus. ``mine=1`` narrows to registrations of classes the
    real logged-in user teaches or authored (via ``ClassOffering.hosted_by``). The
    CSV export reuses this, so every filter — including ``mine`` — applies there for free.
    """
    status = request.GET.get("status", "")
    if status in Registration.Status.values:
        qs = qs.filter(status=status)
    raw_class = request.GET.get("class", "")
    if raw_class.isdigit():
        qs = qs.filter(class_offering_id=int(raw_class))
    raw_instructor = request.GET.get("instructor", "")
    if raw_instructor.isdigit():
        qs = qs.filter(class_offering__instructor_id=int(raw_instructor))
    if request.GET.get("mine", "") == "1":
        # "Mine" is defined once, on the queryset — reuse it via class_offering__in
        # rather than inlining a second copy of the Q. A memberless viewer gets
        # qs.none(): hosted_by(None) would match NULL-instructor/NULL-author classes
        # and leak their registrations (including through the CSV export).
        own_member = getattr(request.user, "member", None)
        qs = (
            qs.filter(class_offering__in=ClassOffering.objects.hosted_by(own_member))
            if own_member is not None
            else qs.none()
        )
    return qs


def _orientation_context(member: Member, form: InstructorOrientationCompleteForm) -> dict[str, Any]:
    """Context for the orientation page — the seeded article, the guide link, the form.

    A missing seed must fail soft on the page (placeholder copy; the completion
    card still works so the gate is never un-passable) but loudly in the logs.
    """
    import logging

    from membership.models import WikiArticle

    article = WikiArticle.objects.published().filter(slug="instructor-orientation").first()
    if article is None:
        logging.getLogger(__name__).warning(
            "Instructor-orientation article is not seeded — run `manage.py seed_help_center`."
        )
    guide = WikiArticle.objects.published().filter(slug="become-an-instructor").select_related("category").first()
    return {"member": member, "article": article, "guide": guide, "form": form}


@active_member_required
def teach_orientation(request: HttpRequest) -> HttpResponse:
    """The instructor orientation page — the teaching gate's landing (Spec D §6).

    Locked members see the explainer banner + content + the acknowledge/unlock
    card; unlocked members get the completed state with the content still
    readable (it stays the reference page).
    """
    member: Member = request.teaching_member  # type: ignore[attr-defined]
    return render(
        request, "classes/teach/orientation.html", _orientation_context(member, InstructorOrientationCompleteForm())
    )


@active_member_required
@require_POST
def teach_orientation_complete(request: HttpRequest) -> HttpResponse:
    """Handle the "Unlock teaching" submit — form-enforced acknowledge, then unlock."""
    member: Member = request.teaching_member  # type: ignore[attr-defined]
    form = InstructorOrientationCompleteForm(request.POST)
    if not form.is_valid():
        return render(request, "classes/teach/orientation.html", _orientation_context(member, form))
    member.complete_instructor_orientation()
    messages.success(request, "Teaching unlocked — welcome, instructor.")
    return redirect("classes:teach_overview")


@teaching_member_required
def teach_overview(request: HttpRequest) -> HttpResponse:
    """Teaching dashboard: the teaching member's drafts, classes awaiting review,
    recent sign-ups, and active waitlists. No money, no approvals — they submit
    classes, they don't approve them. Empty state nudges a first class."""
    teaching_member: Member = request.teaching_member  # type: ignore[attr-defined]
    my_classes = ClassOffering.objects.for_instructor(teaching_member)
    now = timezone.now()

    # Bounced drafts (a reviewer asked for changes or declined) lead the attention list
    # with the reviewer's note and a Fix and resubmit button; plain drafts follow; then
    # the classes in review, each carrying its stage badge. Every row is annotated so
    # the badge resolves with no per-row queries.
    # ``approvals`` is prefetched so a bounced row's note (the latest bouncing row) costs
    # no query per row.
    attention_base = my_classes.with_lifecycle_inputs().select_related("category__guild").prefetch_related("approvals")
    bounced = attention_base.filter(status=ClassOffering.Status.DRAFT, bounced=True).order_by("-updated_at")  # type: ignore[misc]  # django-stubs can't see annotate() aliases
    drafts = attention_base.filter(status=ClassOffering.Status.DRAFT, bounced=False).order_by("-updated_at")  # type: ignore[misc]  # django-stubs can't see annotate() aliases
    pending = attention_base.filter(status=ClassOffering.Status.PENDING).order_by("created_at")
    week_end = now + timedelta(days=7)
    upcoming_classes = (
        my_classes.filter(  # type: ignore[misc]  # django-stubs can't see annotate() aliases
            status=ClassOffering.Status.PUBLISHED,
            sessions__starts_at__gte=now,
            sessions__starts_at__lt=week_end,
        )
        .annotate(
            next_session_at=Min(
                "sessions__starts_at",
                filter=Q(sessions__starts_at__gte=now, sessions__starts_at__lt=week_end),
            )
        )
        .select_related("category")
        .distinct()
        .order_by("next_session_at")
    )
    waitlist_classes = (
        my_classes.annotate(  # type: ignore[misc]  # django-stubs can't see annotate() aliases
            waiting=Count(
                "registrations",
                filter=Q(registrations__status=Registration.Status.WAITLISTED),
            )
        )
        .filter(waiting__gt=0)
        .order_by("-waiting")
    )
    recent_registrations = (
        Registration.objects.filter(class_offering__instructor=teaching_member)
        .select_related("class_offering")
        .order_by("-registered_at")[:8]
    )

    bounced_rows = list(bounced)
    stats = {
        "published": my_classes.filter(status=ClassOffering.Status.PUBLISHED).count(),
        "pending": pending.count(),
        "drafts": drafts.count(),
        "bounced": len(bounced_rows),
        "total_signups": Registration.objects.filter(
            class_offering__instructor=teaching_member, status=Registration.Status.CONFIRMED
        ).count(),
    }
    stats["attention"] = stats["drafts"] + stats["bounced"] + stats["pending"]

    is_guild_lead = teaching_member.is_guild_lead
    guild_lead_pending = _guild_lead_review_queue(teaching_member) if is_guild_lead else []
    # Classes this lead already approved that now wait on the admin gate — kept
    # visible so a stage-one approval doesn't make the class vanish on them.
    guild_lead_awaiting_admin = (
        list(
            ClassOffering.objects.awaiting_admin_validation(teaching_member)
            .select_related("category", "instructor")
            .order_by("created_at")
        )
        if is_guild_lead
        else []
    )

    return render(
        request,
        "classes/teach/overview.html",
        {
            "active_tab": "overview",
            "instructor": teaching_member,
            "bounced_classes": bounced_rows,
            "drafts": drafts,
            "pending_classes": pending,
            "upcoming_classes": upcoming_classes,
            "waitlist_classes": waitlist_classes,
            "recent_registrations": recent_registrations,
            "has_classes": my_classes.exists(),
            "stats": stats,
            "is_guild_lead": is_guild_lead,
            "guild_lead_pending": guild_lead_pending,
            "guild_lead_awaiting_admin": guild_lead_awaiting_admin,
        },
    )


def _guild_lead_review_queue(member: Member) -> list[dict]:
    """Build the guild-lead review queue for ``member``'s teaching dashboard.

    Each entry pairs a pending class with the token of its undecided
    ``GUILD_LEAD`` approval so the panel can link straight to the tokenized
    review page — guild leads have no admin access, so the token is their door.
    """
    offerings = (
        ClassOffering.objects.awaiting_guild_lead(member)
        .select_related("category", "instructor")
        .prefetch_related("approvals")
        .order_by("created_at")
    )
    queue: list[dict] = []
    for offering in offerings:
        gl_row = next(
            (a for a in offering.approvals.all() if a.role == ClassApproval.Role.GUILD_LEAD and not a.decision),
            None,
        )
        if gl_row is not None:
            queue.append({"offering": offering, "token": gl_row.token})
    return queue


@teaching_member_required
def teach_dashboard(request: HttpRequest) -> HttpResponse:
    """My classes — list view for the logged-in teaching member, faceted by lifecycle."""
    teaching_member: Member = request.teaching_member  # type: ignore[attr-defined]
    base = (
        ClassOffering.objects.for_instructor(teaching_member)
        .with_lifecycle_inputs()
        .select_related("category__guild")
        # The badge note reads the latest bouncing row; prefetching keeps that off the per-row path.
        .prefetch_related("approvals")
        # distinct=True so the sessions join behind the lifecycle inputs never inflates the tally.
        .annotate(registration_count=Count("registrations", distinct=True))
    )
    facet = resolve_facet(INSTRUCTOR_FACETS, request.GET.get("facet", "").strip())
    classes = facet.apply(base).order_by("-created_at")  # type: ignore[arg-type]  # annotated queryset keeps its aliases
    facets = facet_rows(INSTRUCTOR_FACETS, base, facet, lambda key: f"?facet={key}" if key else "?")  # type: ignore[arg-type]
    return render(
        request,
        "classes/teach/classes_list.html",
        {
            "active_tab": "classes",
            "instructor": teaching_member,
            "classes": classes,
            "facets": facets,
            "selected_facet": facet,
            "has_any_classes": base.exists(),
        },
    )


def _render_teach_class_form(
    request: HttpRequest,
    *,
    form: TeachClassOfferingForm,
    formset: Any,
    teaching_member: Member,
    mode: str,
    offering: ClassOffering | None = None,
    faq_formset: Any = None,
) -> HttpResponse:
    sessions_data: list[dict] = []
    if formset.is_bound:
        for i in range(int(request.POST.get("sessions-TOTAL_FORMS", "0"))):
            starts = request.POST.get(f"sessions-{i}-starts_at", "")
            ends = request.POST.get(f"sessions-{i}-ends_at", "")
            pk = request.POST.get(f"sessions-{i}-id", "")
            delete = request.POST.get(f"sessions-{i}-DELETE", "")
            if starts and ends:
                sessions_data.append({"id": pk, "starts_at": starts, "ends_at": ends, "DELETE": bool(delete)})
    elif offering and offering.pk:
        for s in offering.sessions.order_by("starts_at"):
            sessions_data.append(
                {
                    "id": s.pk,
                    "starts_at": s.starts_at.strftime("%Y-%m-%dT%H:%M"),
                    "ends_at": s.ends_at.strftime("%Y-%m-%dT%H:%M"),
                }
            )

    # The pipeline card ("Where Your Class Is") and the readiness card ("Ready to
    # Submit?") need a saved class; the create form shows neither.
    saved = offering if offering is not None and offering.pk else None
    return render(
        request,
        "classes/teach/class_form.html",
        {
            "active_tab": "classes",
            "instructor": teaching_member,
            "form": form,
            "formset": formset,
            "sessions_json": json.dumps(sessions_data),
            "initial_forms": formset.initial_form_count() if hasattr(formset, "initial_form_count") else 0,
            "mode": mode,
            "offering": offering,
            "faq_formset": faq_formset,
            "pipeline": saved.review_pipeline() if saved is not None else None,
            "readiness": saved.readiness() if saved is not None else None,
            **(_teach_gallery_context(saved) if saved is not None else {}),
        },
    )


@teaching_member_required
def teach_class_create(request: HttpRequest) -> HttpResponse:
    teaching_member: Member = request.teaching_member  # type: ignore[attr-defined]
    form = TeachClassOfferingForm(request.POST or None, request.FILES or None, teaching_member=teaching_member)
    formset = ClassSessionFormSet(request.POST or None, prefix="sessions")
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        offering = form.save()
        formset.instance = offering
        formset.save()
        offering.finalize_recurring_slug()
        try:
            offering.add_gallery_images(request.FILES.getlist("gallery_images"))
        except ValidationError as exc:
            offering.delete()  # roll back the half-created offering
            form.add_error(None, exc.messages[0])
        else:
            submit_now = request.POST.get("action") == "submit"
            if submit_now:
                try:
                    offering.submit_for_review()
                except ValidationError as exc:
                    messages.error(request, exc.messages[0])
                else:
                    messages.success(request, _submitted_message(offering))
                    if offering.needs_photo_nudge:
                        messages.info(request, _PHOTO_NUDGE_MESSAGE)
            else:
                messages.success(request, f"Saved draft ‘{offering.title}’.")
            return redirect("classes:teach_class_edit", pk=offering.pk)
    return _render_teach_class_form(
        request,
        form=form,
        formset=formset,
        teaching_member=teaching_member,
        mode="create",
    )


@teaching_member_required
def teach_class_edit(request: HttpRequest, pk: int) -> HttpResponse:
    teaching_member: Member = request.teaching_member  # type: ignore[attr-defined]
    offering = get_object_or_404(
        ClassOffering.objects.editable_by(teaching_member).prefetch_related("gallery_images"),
        pk=pk,
    )
    if offering.status in {ClassOffering.Status.CANCELLED, ClassOffering.Status.ARCHIVED}:
        messages.info(request, "Cancelled and archived classes can only be edited by an admin.")
        return redirect("classes:teach_dashboard")
    if offering.status == ClassOffering.Status.PUBLISHED:
        # A live class gets the light-edit form on the same URL: content only, no re-review.
        return _teach_published_class_edit(request, offering, teaching_member)
    form = TeachClassOfferingForm(
        request.POST or None, request.FILES or None, instance=offering, teaching_member=teaching_member
    )
    formset = ClassSessionFormSet(request.POST or None, instance=offering, prefix="sessions")
    faq_formset = build_class_faq_formset(request.POST or None, offering)
    if request.method == "POST" and form.is_valid() and formset.is_valid() and faq_formset.is_valid():
        offering = form.save()  # type: ignore[assignment]  # django-stubs infers an annotated row type for offering
        formset.save()
        faq_formset.save()
        submit_now = request.POST.get("action") == "submit"
        if submit_now and offering.status == ClassOffering.Status.DRAFT:
            try:
                offering.submit_for_review()
            except ValidationError as exc:
                messages.error(request, exc.messages[0])
            else:
                messages.success(request, _submitted_message(offering))
                if offering.needs_photo_nudge:
                    messages.info(request, _PHOTO_NUDGE_MESSAGE)
        else:
            messages.success(request, "Class updated.")
        return redirect("classes:teach_class_edit", pk=offering.pk)
    return _render_teach_class_form(
        request,
        form=form,
        formset=formset,
        teaching_member=teaching_member,
        mode="edit",
        offering=offering,
        faq_formset=faq_formset,
    )


def _teach_published_class_edit(request: HttpRequest, offering: ClassOffering, teaching_member: Member) -> HttpResponse:
    """The published class edit page: light fields + FAQ + gallery, structural facts locked.

    Keeps ``teach_class_edit``'s ``editable_by`` scope (guild staff who can edit a draft can
    make light edits too). Saves with one Save button and no submit: nothing to review.
    """
    form = TeachPublishedClassForm(request.POST or None, instance=offering)
    faq_formset = build_class_faq_formset(request.POST or None, offering)
    if request.method == "POST" and form.is_valid() and faq_formset.is_valid():
        form.save()
        faq_formset.save()
        messages.success(request, "Class updated.")
        return redirect("classes:teach_class_detail", pk=offering.pk)
    return render(
        request,
        "classes/teach/class_form_published.html",
        {
            "active_tab": "classes",
            "instructor": teaching_member,
            "form": form,
            "faq_formset": faq_formset,
            "offering": offering,
            "sessions": list(offering.sessions.order_by("starts_at")),
            "change_form": ClassChangeRequestForm(),
            # Request a change posts through the instructor-only scope, so only the
            # instructor sees it; guild staff making light edits get the page without it.
            "is_own_class": offering.instructor_id == teaching_member.pk,
            **_teach_gallery_context(offering, with_hero=False),
        },
    )


@teaching_member_required
def teach_class_duplicate_run(request: HttpRequest, pk: int) -> HttpResponse:
    """Offer one of my classes on another set of dates — clones it as a grouped draft run."""
    teaching_member: Member = request.teaching_member  # type: ignore[attr-defined]
    offering = get_object_or_404(ClassOffering.objects.filter(instructor=teaching_member), pk=pk)
    if request.method == "POST":
        run = offering.duplicate_as_new_run()
        messages.success(request, "New date-set added as a draft. Add its dates, then submit for review.")
        return redirect("classes:teach_class_edit", pk=run.pk)
    return redirect("classes:teach_class_edit", pk=offering.pk)


@teaching_member_required
def teach_class_submit(request: HttpRequest, pk: int) -> HttpResponse:
    """Transition a draft to 'pending review'."""
    teaching_member: Member = request.teaching_member  # type: ignore[attr-defined]
    offering = get_object_or_404(ClassOffering.objects.filter(instructor=teaching_member), pk=pk)
    if request.method == "POST" and offering.status == ClassOffering.Status.DRAFT:
        try:
            offering.submit_for_review()
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
            return redirect("classes:teach_class_edit", pk=offering.pk)
        messages.success(request, _submitted_message(offering))
        if offering.needs_photo_nudge:
            messages.info(request, _PHOTO_NUDGE_MESSAGE)
    return redirect("classes:teach_dashboard")


def _submitted_message(offering: ClassOffering) -> str:
    """The honest submit message, naming who actually reviews first (create, edit, and submit paths)."""
    return f"Submitted “{offering.title}” for review by {offering.first_gate_label}."


@teaching_member_required
def teach_registrations(request: HttpRequest) -> HttpResponse:
    teaching_member: Member = request.teaching_member  # type: ignore[attr-defined]
    offerings = (
        ClassOffering.objects.for_instructor(teaching_member)
        .annotate(registration_count=Count("registrations"))
        .order_by("-created_at")
    )
    class_groups = []
    for offering in offerings:
        regs = (
            Registration.objects.filter(class_offering=offering)
            .select_related("member")
            .prefetch_related("custom_answers__question")
            .order_by("-registered_at")
        )
        class_groups.append({"offering": offering, "registrations": list(regs)})
    return render(
        request,
        "classes/teach/registrations.html",
        {
            "active_tab": "registrations",
            "instructor": teaching_member,
            "class_groups": class_groups,
        },
    )


@teaching_member_required
@require_POST
def teach_registrations_email(request: HttpRequest) -> HttpResponse:
    """Send a manual email to selected registrants of one of the teaching member’s classes.

    Submitted as a POST from the registrations table; on success bounces back
    with a flash message so the teaching member sees the confirmation inline.
    """
    from classes.forms import TeachEmailForm

    teaching_member: Member = request.teaching_member  # type: ignore[attr-defined]
    form = TeachEmailForm(request.POST, teaching_member=teaching_member)
    if not form.is_valid():
        first_error = next(iter(form.errors.values()))[0] if form.errors else "Couldn’t send the message."
        messages.error(request, str(first_error))
        return redirect("classes:teach_registrations")
    message = form.send()
    messages.success(
        request,
        f"Sent ‘{message.subject}’ to {message.recipient_count} recipient(s).",
    )
    return redirect("classes:teach_registrations")


@teaching_member_required
@instructor_discount_codes_required
def teach_discount_codes(request: HttpRequest) -> HttpResponse:
    """Discount codes for the Teaching portal.

    Splits codes into the instructor's own (editable, keyed off the ``created_by``
    audit FK) and the site-wide admin codes — ``class_offering`` null and created
    by someone else, e.g. the PLMEMBERS member discount. Site-wide codes are shown
    read-only here; admins manage them from the Classes admin.
    """
    teaching_member: Member = request.teaching_member  # type: ignore[attr-defined]
    own_codes = DiscountCode.objects.filter(created_by=request.user).order_by("code")
    sitewide_codes = (
        DiscountCode.objects.filter(class_offering__isnull=True).exclude(created_by=request.user).order_by("code")
    )
    return render(
        request,
        "classes/teach/discount_codes.html",
        {
            "active_tab": "discount_codes",
            "instructor": teaching_member,
            "own_codes": own_codes,
            "sitewide_codes": sitewide_codes,
            # Resolve the acting user's approval capability once (one Member query),
            # reused per row in the template — avoids an N+1 across the code list.
            "approver": DiscountCode.approver_for(request.user),
        },
    )


@teaching_member_required
@instructor_discount_codes_required
def teach_discount_code_create(request: HttpRequest) -> HttpResponse:
    teaching_member: Member = request.teaching_member  # type: ignore[attr-defined]
    assert request.user.is_authenticated  # @teaching_member_required guarantees a real User
    scoped_to: ClassOffering | None = None
    raw_class = request.GET.get("class") or request.POST.get("class")
    if raw_class:
        try:
            scoped_to = ClassOffering.objects.filter(instructor=teaching_member).get(pk=int(raw_class))
        except (ClassOffering.DoesNotExist, ValueError, TypeError):
            scoped_to = None
    form = DiscountCodeForm(request.POST or None, scoped_to=scoped_to, created_by=request.user)
    if request.method == "POST" and form.is_valid():
        # Every new code starts unapproved (the model default) — a teaching
        # member with the self-approve permission can approve their own; otherwise
        # an admin reviews it.
        code = form.save(commit=False)
        if scoped_to is not None and not code.class_offering_id:
            code.class_offering = scoped_to
        if not code.created_by_id:
            code.created_by = request.user
        code.save()
        messages.success(request, "Discount code created — it needs approval before it's active.")
        if scoped_to is not None:
            return redirect("classes:teach_class_edit", pk=scoped_to.pk)
        return redirect("classes:teach_discount_codes")
    return render(
        request,
        "classes/teach/discount_code_form.html",
        {
            "active_tab": "discount_codes",
            "instructor": teaching_member,
            "form": form,
            "mode": "create",
            "scoped_to": scoped_to,
        },
    )


@teaching_member_required
@instructor_discount_codes_required
def teach_discount_code_edit(request: HttpRequest, pk: int) -> HttpResponse:
    teaching_member: Member = request.teaching_member  # type: ignore[attr-defined]
    # Instructors may only edit codes they created; site-wide / admin codes are
    # read-only here (scoping to created_by makes a foreign code 404, not just hidden).
    code = get_object_or_404(DiscountCode, pk=pk, created_by=request.user)
    form = DiscountCodeForm(request.POST or None, instance=code)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Discount code updated.")
        return redirect("classes:teach_discount_codes")
    return render(
        request,
        "classes/teach/discount_code_form.html",
        {"active_tab": "discount_codes", "instructor": teaching_member, "form": form, "code": code, "mode": "edit"},
    )


@teaching_member_required
@instructor_discount_codes_required
def teach_discount_code_delete(request: HttpRequest, pk: int) -> HttpResponse:
    # Only the instructor who created a code may delete it; site-wide / admin codes 404.
    code = get_object_or_404(DiscountCode, pk=pk, created_by=request.user)
    if request.method == "POST":
        code.delete()
        messages.success(request, "Discount code deleted.")
    return redirect("classes:teach_discount_codes")


@teaching_member_required
@instructor_discount_codes_required
@require_POST
def teach_discount_code_approve(request: HttpRequest, pk: int) -> HttpResponse:
    """Approve one of the teaching member's own pending codes from the Teaching portal.

    Reachable only by a member who holds ``can_self_approve_discounts`` (or an
    admin) for a code they created — authorization is enforced by
    ``DiscountCode.can_be_approved_by``, so a member without the permission, or
    one acting on someone else's code, gets a 403.
    """
    code = get_object_or_404(DiscountCode, pk=pk)
    if not code.can_be_approved_by(request.user):
        return HttpResponseForbidden("You don't have permission to approve this discount code.")
    code.approve(request.user)
    messages.success(request, f"Discount code {code.code} approved.")
    return redirect("classes:teach_discount_codes")


def _teach_class_or_404(request: HttpRequest, pk: int) -> ClassOffering:
    """Scope a per-class Workspace lookup to the logged-in teaching member's own class."""
    teaching_member: Member = request.teaching_member  # type: ignore[attr-defined]
    return get_object_or_404(ClassOffering.objects.filter(instructor=teaching_member), pk=pk)


def _render_teach_class_overview(
    request: HttpRequest,
    offering: ClassOffering,
    *,
    cancel_form: ClassCancelForm | None = None,
    change_form: ClassChangeRequestForm | None = None,
) -> HttpResponse:
    """The instructor workspace Overview: pipeline card, summary, and the action row by state.

    The Cancel class and Request a change modals are server-rendered inline; a bound,
    invalid form re-renders the page with that modal open and the error inside it.
    """
    return render(
        request,
        "classes/teach/class_overview.html",
        {
            "active_tab": "classes",
            "active_subtab": "overview",
            "instructor": request.teaching_member,  # type: ignore[attr-defined]
            "offering": offering,
            "lifecycle": offering.lifecycle,
            "pipeline": offering.review_pipeline(),
            "cancel_form": cancel_form or ClassCancelForm(),
            "change_form": change_form or ClassChangeRequestForm(),
            "paid_registration_count": offering.paid_registration_count,
            **_class_workspace_counts(offering),
        },
    )


@teaching_member_required
def teach_class_detail(request: HttpRequest, pk: int) -> HttpResponse:
    return _render_teach_class_overview(request, _teach_class_or_404(request, pk))


@teaching_member_required
@require_POST
def teach_class_withdraw(request: HttpRequest, pk: int) -> HttpResponse:
    """Take back a submission in review: the class goes back to draft, reviewers stop seeing it."""
    offering = _teach_class_or_404(request, pk)
    try:
        offering.withdraw_submission(actor=cast("User", request.user))
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Submission withdrawn.")
    return redirect("classes:teach_class_detail", pk=offering.pk)


@teaching_member_required
@require_POST
def teach_class_cancel(request: HttpRequest, pk: int) -> HttpResponse:
    """Cancel my own live class with a reason: registrants are told; refunds stay with the admins."""
    offering = _teach_class_or_404(request, pk)
    form = ClassCancelForm(request.POST)
    if not form.is_valid():
        return _render_teach_class_overview(request, offering, cancel_form=form)
    had_paid = offering.paid_registration_count > 0
    try:
        offering.cancel(cast("User", request.user), form.cleaned_data["reason"])
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("classes:teach_class_detail", pk=offering.pk)
    message = "Class cancelled. Everyone registered has been told."
    if had_paid:
        message += " An admin will handle refunds."
    messages.success(request, message)
    return redirect("classes:teach_class_detail", pk=offering.pk)


@teaching_member_required
@require_POST
def teach_class_request_change(request: HttpRequest, pk: int) -> HttpResponse:
    """Ask the admins to change a live class's title, dates, price, or capacity."""
    offering = _teach_class_or_404(request, pk)
    form = ClassChangeRequestForm(request.POST)
    if not form.is_valid():
        return _render_teach_class_overview(request, offering, change_form=form)
    teaching_member: Member = request.teaching_member  # type: ignore[attr-defined]
    try:
        offering.request_change(teaching_member, form.cleaned_data["note"])
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("classes:teach_class_detail", pk=offering.pk)
    messages.success(request, "Sent to the admins.")
    return redirect("classes:teach_class_detail", pk=offering.pk)


def _refunds_prefetch() -> Prefetch:
    """Refunds prefetch with the issuer joined — the card/history templates read initiated_by names."""
    from billing.models import PaymentRefund

    return Prefetch("refunds", queryset=PaymentRefund.objects.select_related("initiated_by"))


def _roster_registrations(offering: ClassOffering) -> QuerySet[Registration]:
    """The roster queryset for one class, annotated for the shared row partial.

    ``promoted_email_sent`` is one ``Exists()`` subquery on the event spine's
    delivery ledger (the ``reg:{pk}:promoted`` period) so the "No email sent yet"
    chip costs no per-row query.
    """
    from django.db.models import CharField, Exists, Value
    from django.db.models.functions import Cast, Concat

    from core.models import EventDelivery

    promoted_delivery = EventDelivery.objects.filter(
        event_key="waitlist_promoted",
        period=Concat(Value("reg:"), Cast(OuterRef("pk"), output_field=CharField()), Value(":promoted")),
    )
    return (
        offering.registrations.select_related("member")
        .prefetch_related("custom_answers__question", _refunds_prefetch())
        .annotate(promoted_email_sent=Exists(promoted_delivery))
        .order_by("-registered_at")
    )


def _claim_email_will_fire(offering: ClassOffering) -> bool:
    """Whether removing a seat-holder right now would fire an auto claim-link email.

    True only when the removal frees a seat that leaves ``spots_remaining > 0``
    after the cancel (an over-full class can free a seat and still be full) AND an
    un-notified WAITLISTED row exists. Computed once per page for the remove
    modals' conditional copy.
    """
    held = offering.registrations.filter(
        status__in=[Registration.Status.CONFIRMED, Registration.Status.PENDING]
    ).count()
    if held - 1 >= offering.capacity:
        return False
    return offering.registrations.filter(
        status=Registration.Status.WAITLISTED, waitlist_notified_at__isnull=True
    ).exists()


def _registration_move_form(request: HttpRequest, offering: ClassOffering) -> "RegistrationMoveForm | None":
    """The roster tab's audience-scoped move-student form, or ``None`` for viewers who can't move.

    Actual admins (preview-independent) may move a student into any upcoming
    class; the class's own instructor only into other bookable classes they
    instruct. Everyone else (guild leads reach rosters via ``editable_by``)
    gets no move affordance. ``auto_id=False`` because the same form renders
    once per roster row — auto ids would collide across the per-row modals.
    """
    from classes.forms import RegistrationMoveForm

    view_as = getattr(request, "view_as", None)
    if view_as is not None and view_as.has_actual("admin"):
        return RegistrationMoveForm(current=offering, auto_id=False)
    member = getattr(request.user, "member", None)
    if member is not None and offering.instructor_id == member.pk:
        return RegistrationMoveForm(current=offering, instructor=member, auto_id=False)
    return None


def _teach_registrations_context(request: HttpRequest, offering: ClassOffering) -> dict[str, Any]:
    """Shared context for the roster (registrations) tabs and the ``refund-done`` table partial."""
    from hub.view_as import has_refund_authority

    move_form = _registration_move_form(request, offering)
    return {
        "offering": offering,
        "registrations": _roster_registrations(offering),
        "viewer_has_refund_authority": has_refund_authority(request),
        "can_manage": True,
        "can_move": move_form is not None,
        "move_form": move_form,
        "claim_email_will_fire": _claim_email_will_fire(offering),
    }


def _waitlist_context(request: HttpRequest, offering: ClassOffering) -> dict[str, Any]:
    """Shared context for the waitlist tabs (teach + admin) with the action modals' inputs."""
    waitlist_registrations = list(
        offering.registrations.filter(status=Registration.Status.WAITLISTED)
        .select_related("member", "discount_code")
        .order_by("registered_at")
    )
    return {
        "offering": offering,
        "waitlist_registrations": waitlist_registrations,
        "can_manage": True,
        "spots_remaining": offering.spots_remaining,
    }


@teaching_member_required
def teach_class_registrations(request: HttpRequest, pk: int) -> HttpResponse:
    offering = _teach_class_or_404(request, pk)
    return render(
        request,
        "classes/teach/class_registrations.html",
        {
            "active_tab": "classes",
            "active_subtab": "registrations",
            "instructor": request.teaching_member,  # type: ignore[attr-defined]
            **_teach_registrations_context(request, offering),
            **_class_workspace_counts(offering),
        },
    )


@teaching_member_required
def teach_class_registrations_table(request: HttpRequest, pk: int) -> HttpResponse:
    """The registrations table alone — re-fetched by the ``refund-done`` refresh container."""
    offering = _teach_class_or_404(request, pk)
    return render(
        request,
        "classes/teach/partials/class_registrations_table.html",
        _teach_registrations_context(request, offering),
    )


@teaching_member_required
@require_POST
def teach_class_email(request: HttpRequest, pk: int) -> HttpResponse:
    """Send a manual email to selected registrants of one of the teaching member's classes.

    POST-only sibling of ``teach_registrations_email``, scoped to a single
    class via ``_teach_class_or_404`` so it slots into the per-class
    Workspace. Bounces back to the Registrations tab with a flash message on
    both success and validation error.
    """
    from classes.forms import TeachEmailForm

    offering = _teach_class_or_404(request, pk)
    form = TeachEmailForm(request.POST, teaching_member=request.teaching_member)  # type: ignore[attr-defined]
    # Bound recipients to THIS class only — the form otherwise spans all of the
    # teaching member's classes, which would let one class's tab email another class's
    # registrants (and mis-anchor the audit record).
    field = form.fields["registration_ids"]
    field.queryset = field.queryset.filter(class_offering=offering)  # type: ignore[attr-defined]
    if not form.is_valid():
        first_error = next(iter(form.errors.values()))[0] if form.errors else "Couldn’t send the message."
        messages.error(request, str(first_error))
        return redirect("classes:teach_class_registrations", pk=offering.pk)
    message = form.send()
    messages.success(
        request,
        f"Sent ‘{message.subject}’ to {message.recipient_count} recipient(s).",
    )
    return redirect("classes:teach_class_registrations", pk=offering.pk)


@teaching_member_required
def teach_class_waitlist(request: HttpRequest, pk: int) -> HttpResponse:
    offering = _teach_class_or_404(request, pk)
    return render(
        request,
        "classes/teach/class_waitlist.html",
        {
            "active_tab": "classes",
            "active_subtab": "waitlist",
            "instructor": request.teaching_member,  # type: ignore[attr-defined]
            **_waitlist_context(request, offering),
            **_class_workspace_counts(offering),
        },
    )


@teaching_member_required
@instructor_discount_codes_required
def teach_class_discount_codes(request: HttpRequest, pk: int) -> HttpResponse:
    offering = _teach_class_or_404(request, pk)
    codes = DiscountCode.objects.filter(Q(class_offering=offering) | Q(class_offering__isnull=True)).order_by("code")
    return render(
        request,
        "classes/teach/class_discount_codes.html",
        {
            "active_tab": "classes",
            "active_subtab": "discount_codes",
            "instructor": request.teaching_member,  # type: ignore[attr-defined]
            "offering": offering,
            "codes": codes,
            # Resolve the acting user's approval capability once (one Member query),
            # reused per row in the template — avoids an N+1 across the code list.
            "approver": DiscountCode.approver_for(request.user),
            **_class_workspace_counts(offering),
        },
    )


@teaching_member_required
def teach_class_emails(request: HttpRequest, pk: int) -> HttpResponse:
    """Author the per-class welcome email. Editable by the instructor, the guild's lead, or an admin."""
    teaching_member: Member = request.teaching_member  # type: ignore[attr-defined]
    offering = get_object_or_404(ClassOffering.objects.editable_by(teaching_member), pk=pk)
    form = TeachWelcomeEmailForm(request.POST or None, instance=offering)
    if request.method == "POST" and form.is_valid():
        offering = form.save()
        if "send_test" in request.POST:
            send_class_welcome_email_test(offering, request.user.email)
            messages.success(request, f"Saved — and sent a test to {request.user.email}.")
        else:
            messages.success(request, "Welcome email saved.")
        return redirect("classes:teach_class_emails", pk=offering.pk)
    return render(
        request,
        "classes/teach/class_emails.html",
        {
            "active_tab": "classes",
            "active_subtab": "emails",
            "instructor": teaching_member,
            "offering": offering,
            "form": form,
            **_class_workspace_counts(offering),
        },
    )


@teaching_member_required
def teach_profile(request: HttpRequest) -> HttpResponse:
    """The portal's Profile tab: when the public instructor page goes live, and where to edit it.

    The bio and photo themselves are edited on the hub Profile settings (the
    Instructor tab there); this page states the public page's status and links across.
    """
    from classes.emails import _absolute_url

    teaching_member: Member = request.teaching_member  # type: ignore[attr-defined]
    public_url = (
        _absolute_url(reverse("classes:public_instructor", kwargs={"slug": teaching_member.instructor_slug}))
        if teaching_member.instructor_slug
        else ""
    )
    return render(
        request,
        "classes/teach/profile.html",
        {
            "active_tab": "profile",
            "instructor": teaching_member,
            "public_profile_url": public_url,
            "profile_settings_url": reverse("hub_user_settings") + "?tab=profile",
        },
    )


def _render_class_preview(
    request: HttpRequest,
    offering: ClassOffering,
    *,
    is_admin: bool = False,
    is_instructor: bool = False,
    can_edit_offering: bool = False,
    edit_url: str | None = None,
) -> HttpResponse:
    """Render the public class detail page in preview mode (drafts included).

    Shared by the login-gated owner/admin preview and the token-authorized
    reviewer preview. ``is_preview`` makes the public template show its
    "preview" banner and bypass the published-only gating.
    """
    upcoming_sessions = list(offering.sessions.filter(starts_at__gte=timezone.now()).order_by("starts_at"))
    return render(
        request,
        "classes/public/detail.html",
        {
            # ``?framed=1`` drops the hub sidebar and every topbar so the review page's
            # iframe shows the class page itself, not a page nested inside another page.
            # Read here rather than in a context processor: only the preview is framed,
            # and no other surface should be strippable by a query parameter.
            "is_framed": request.GET.get("framed") == "1",
            "offering": offering,
            "can_edit_offering": can_edit_offering,
            "edit_url": edit_url,
            "is_admin": is_admin,
            "is_instructor": is_instructor,
            "settings_obj": ClassSettings.load(),
            "site_config": SiteConfiguration.load(),
            "upcoming_sessions": upcoming_sessions,
            "member_price_cents": compute_member_price_cents(offering.sale_price_cents, offering.member_discount_pct),
            "spots_remaining": offering.spots_remaining,
            "is_preview": True,
        },
    )


@login_required
@xframe_options_sameorigin
def class_preview(request: HttpRequest, pk: int) -> HttpResponse:
    """Preview the public detail page for any class — including drafts.

    Access: the assigned instructor (owner), any actual admin, or a CMS
    Administrator (CLASS_APPROVER holder — their review page embeds this
    preview). The view renders ``classes/public/detail.html`` but skips the
    ``status=published`` filter so drafts/pending can be reviewed before going
    live. A banner is rendered at the top so it's clear this is a preview.
    """
    offering = get_object_or_404(
        ClassOffering.objects.select_related("category", "instructor").prefetch_related("sessions", "gallery_images"),
        pk=pk,
    )
    from membership.models import Member as MemberModel

    from hub.view_as import ROLE_ADMIN, ROLE_GUILD_OFFICER

    view_as = getattr(request, "view_as", None)
    # Admins and Officers always get edit rights for heroes.
    is_admin = view_as is not None and (view_as.has_actual(ROLE_ADMIN) or view_as.has_actual(ROLE_GUILD_OFFICER))

    assert request.user.is_authenticated  # @login_required guarantees a real User
    user_member = MemberModel.objects.filter(user=request.user).first()
    is_instructor = user_member is not None and offering.instructor_id == user_member.pk

    from membership.models import AdminCapability

    is_class_approver = user_member is not None and user_member.has_admin_capability(
        AdminCapability.Capability.CLASS_APPROVER
    )
    # The owning instructor, the lead of the category's guild, any admin, or a
    # CMS Administrator (whose review page embeds this preview) may preview.
    if not (is_admin or is_class_approver or (user_member is not None and user_member.can_edit_class(offering))):
        return HttpResponseForbidden("You can only preview your own classes.")

    edit_url = None
    if is_admin:
        edit_url = reverse("classes:admin_class_edit", kwargs={"pk": offering.pk})
    elif user_member is not None and user_member.can_edit_class(offering):
        # Instructors and guild leads manage the class from the teaching portal.
        # A CMS Administrator gets no edit link — they review, they don't edit.
        edit_url = reverse("classes:teach_class_edit", kwargs={"pk": offering.pk})
    return _render_class_preview(
        request,
        offering,
        is_admin=is_admin,
        is_instructor=is_instructor,
        can_edit_offering=True,
        edit_url=edit_url,
    )


@xframe_options_sameorigin
def class_review_preview(request: HttpRequest, token: str) -> HttpResponse:
    """Token-authorized student-eye preview of a class under review (no login).

    Lets a guild lead or admin who opened the emailed review link see exactly
    how the public class page will look once published. Read-only — the token
    already proves the visitor is a designated reviewer, so no edit affordances
    are shown. Framed same-origin inside the review page.
    """
    approval = get_object_or_404(ClassApproval, token=token)
    offering = get_object_or_404(
        ClassOffering.objects.select_related("category", "instructor").prefetch_related("sessions", "gallery_images"),
        pk=approval.class_offering_id,
    )
    return _render_class_preview(request, offering)


class OverviewRange(TypedDict):
    """One selectable lookback window for the overview metrics."""

    key: str
    label: str
    days: int | None


# Lookback windows for the overview's registration-driven metrics. Each key maps
# to a span of days; "all" drops the lower bound so every registration counts.
OVERVIEW_RANGES: list[OverviewRange] = [
    {"key": "3", "label": "Last 3 days", "days": 3},
    {"key": "7", "label": "Last 7 days", "days": 7},
    {"key": "30", "label": "Last 30 days", "days": 30},
    {"key": "90", "label": "Last 90 days", "days": 90},
    {"key": "all", "label": "All time", "days": None},
]
OVERVIEW_DEFAULT_RANGE = "7"


@classes_admin_access_required
def admin_overview(request: HttpRequest) -> HttpResponse:
    """Admin dashboard: the approvals queue, classes happening this week, waitlist
    pressure, recent registrations, recent activity, and at-a-glance stats.

    The metric panels (stat tiles, recent sign-ups, trend chart) honor a
    ``?range=`` lookback window so the numbers can be scoped; the approvals,
    upcoming-classes, and waitlist panels always reflect current state."""
    now = timezone.now()

    ranges_by_key = {r["key"]: r for r in OVERVIEW_RANGES}
    requested_range = request.GET.get("range", OVERVIEW_DEFAULT_RANGE)
    if requested_range not in ranges_by_key:
        requested_range = OVERVIEW_DEFAULT_RANGE
    selected_range = ranges_by_key[requested_range]
    range_days = selected_range["days"]
    range_start = now - timedelta(days=range_days) if range_days is not None else None

    registrations = Registration.objects.all()
    if range_start is not None:
        registrations = registrations.filter(registered_at__gte=range_start)

    # Two-stage queue: what waits on the admin (PENDING with no open guild-lead gate,
    # including a PENDING class with zero rows) and what is still with a guild lead.
    waiting_on_you = list(
        ClassOffering.objects.awaiting_admin().select_related("instructor", "category").order_by("created_at")
    )
    with_guild_leads = _with_guild_leads_queue(now)

    week_end = now + timedelta(days=7)
    upcoming_classes = (
        ClassOffering.objects.filter(  # type: ignore[misc]  # django-stubs can't see annotate() aliases
            status=ClassOffering.Status.PUBLISHED,
            sessions__starts_at__gte=now,
            sessions__starts_at__lt=week_end,
        )
        .annotate(
            next_session_at=Min(
                "sessions__starts_at",
                filter=Q(sessions__starts_at__gte=now, sessions__starts_at__lt=week_end),
            )
        )
        .select_related("instructor")
        .distinct()
        .order_by("next_session_at")
    )

    waitlist_classes = (
        ClassOffering.objects.annotate(  # type: ignore[misc]  # django-stubs can't see annotate() aliases
            waiting=Count(
                "registrations",
                filter=Q(registrations__status=Registration.Status.WAITLISTED),
            )
        )
        .filter(waiting__gt=0)
        .select_related("instructor")
        .order_by("-waiting")
    )

    recent_registrations = registrations.select_related("class_offering").order_by("-registered_at")[:8]
    recent_activity = CmsActivity.objects.select_related("class_offering", "registration", "actor").order_by(
        "-created_at"
    )[:8]

    # Daily registration series across the window, bounded so long ranges stay legible.
    chart_days = min(range_days or 30, 90)
    chart_start = (now - timedelta(days=chart_days - 1)).date()
    counts = {
        row["day"]: row["c"]
        for row in Registration.objects.filter(registered_at__date__gte=chart_start)
        .annotate(day=TruncDate("registered_at"))
        .values("day")
        .annotate(c=Count("pk"))
    }
    reg_by_day = [
        {"date": chart_start + timedelta(days=i), "count": counts.get(chart_start + timedelta(days=i), 0)}
        for i in range(chart_days)
    ]

    confirmed = registrations.filter(status=Registration.Status.CONFIRMED)
    stats = {
        "awaiting_you": len(waiting_on_you),
        "with_leads": len(with_guild_leads),
        "pending": len(waiting_on_you) + len(with_guild_leads),
        "new_regs": registrations.count(),
        "active_registrations": confirmed.count(),
        "collected": confirmed.aggregate(total=Sum("amount_paid_cents"))["total"] or 0,
    }

    return render(
        request,
        "classes/admin/overview.html",
        {
            "active_tab": "overview",
            "waiting_on_you": waiting_on_you,
            "with_guild_leads": with_guild_leads,
            "upcoming_classes": upcoming_classes,
            "waitlist_classes": waitlist_classes,
            "recent_registrations": recent_registrations,
            "recent_activity": recent_activity,
            "reg_by_day": reg_by_day,
            "reg_by_day_max": max((d["count"] for d in reg_by_day), default=0),
            "stats": stats,
            "range_options": OVERVIEW_RANGES,
            "selected_range": selected_range,
        },
    )


class _GuildLeadQueueRow(TypedDict):
    """One "With Guild Leads" row on the admin overview."""

    offering: ClassOffering
    row: ClassApproval
    lead: Any
    days_waiting: int
    leadless: bool


def _with_guild_leads_queue(now: Any) -> list[_GuildLeadQueueRow]:
    """Every PENDING class whose guild-lead gate is open, with who holds it and for how long.

    ``leadless`` marks a guild whose lead and staff have all gone (nobody to remind), so
    the row offers "Review it yourself" instead of Remind lead.
    """
    from classes.emails import _guild_leadership_recipients

    offerings = (
        ClassOffering.objects.awaiting_guild_lead_any()
        .select_related("instructor", "category__guild__guild_lead")
        .prefetch_related("approvals")
        .order_by("created_at")
    )
    queue: list[_GuildLeadQueueRow] = []
    for offering in offerings:
        gate = next(
            (a for a in offering.approvals.all() if a.role == ClassApproval.Role.GUILD_LEAD and not a.decision),
            None,
        )
        if gate is None:
            continue
        guild = offering.category.guild
        queue.append(
            {
                "offering": offering,
                "row": gate,
                "lead": guild.guild_lead if guild is not None else None,
                "days_waiting": max(0, (now - gate.created_at).days),
                "leadless": not _guild_leadership_recipients(guild),
            }
        )
    return queue


@classes_review_access_required
def admin_classes(request: HttpRequest) -> HttpResponse:
    facet = resolve_facet(ADMIN_FACETS, request.GET.get("status", "").strip())
    status_filter = facet.key
    instructor_filter = request.GET.get("instructor", "").strip()

    # For grouped classes (same title+category on multiple dates), show only the
    # lowest-pk representative. Solo classes (blank grouping_key) always show.
    _group_rep_pk = (
        ClassOffering.objects.filter(
            grouping_key=OuterRef("grouping_key"),
            grouping_key__gt="",
        )
        .order_by("pk")
        .values("pk")[:1]
    )
    _group_size = (
        ClassOffering.objects.filter(
            grouping_key=OuterRef("grouping_key"),
            grouping_key__gt="",
        )
        .values("grouping_key")
        .annotate(_c=Count("pk"))
        .values("_c")
    )

    base = (
        ClassOffering.objects.select_related("instructor", "category__guild")
        .with_lifecycle_inputs()
        # The badge note reads the latest bouncing row; prefetching keeps that off the per-row path.
        .prefetch_related("approvals")
        .annotate(
            # distinct=True so the sessions join below doesn't inflate the registration tally.
            registration_count=Count("registrations", distinct=True),
            first_session=Min("sessions__starts_at"),
            last_session=Max("sessions__starts_at"),
            _group_rep_pk=Subquery(_group_rep_pk),
            group_size=Subquery(_group_size, output_field=IntegerField()),
        )
        .filter(Q(grouping_key="") | Q(pk=F("_group_rep_pk")))
    )
    qs = facet.apply(base)  # type: ignore[arg-type]  # annotated queryset keeps its aliases
    if instructor_filter:
        qs = qs.filter(instructor_id=instructor_filter)  # type: ignore[misc]  # Django coerces the str PK at query time

    # "My Classes": classes I teach or authored. Always available; "me" is the real
    # logged-in user's member even under a view-as preview. A bogus mine value is
    # off (matching how the sibling filters ignore junk). The memberless guard is
    # load-bearing: hosted_by(None) would match every NULL-instructor/NULL-author class.
    mine_active = request.GET.get("mine", "") == "1"
    own_member = getattr(request.user, "member", None)
    if mine_active:
        # hosted_by / none() return the base queryset type; qs carries annotate() aliases.
        qs = qs.hosted_by(own_member) if own_member is not None else qs.none()  # type: ignore[assignment]

    # mine_count is global — all statuses, ignoring q and the Instructor dropdown —
    # to match how the facet-chip counts ignore the search box and each other.
    mine_count = base.hosted_by(own_member).count() if own_member is not None else 0

    # Every view-computed URL starts from a normalized copy of the GET params: a
    # bogus mine value (anything but "1") is stripped, not echoed, so cruft never
    # rides along on subsequent links.
    normalized = request.GET.copy()
    if normalized.get("mine", "") != "1":
        normalized.pop("mine", None)

    def _url_without(*drop: str, **add: str) -> str:
        params = normalized.copy()
        for key in ("page", *drop):
            params.pop(key, None)
        for key, value in add.items():
            params[key] = value
        return params.urlencode()

    mine_toggle_url = _url_without("mine") if mine_active else _url_without(mine="1")
    # Lifecycle facet chips (All, Needs review, With guild lead, ...), each counted
    # against the ungrouped base so the numbers ignore the search box and each other.
    status_filters = [
        (row.url, row.label, row.count, row.is_selected)
        for row in facet_rows(
            ADMIN_FACETS,
            base,  # type: ignore[arg-type]  # annotated queryset keeps its aliases
            facet,
            lambda key: "?" + _url_without("status", **({"status": key} if key else {})),
        )
    ]
    search_clear_url = _url_without("q")
    _search_preserved = normalized.copy()
    for key in ("q", "page"):
        _search_preserved.pop(key, None)
    search_preserved_fields = list(_search_preserved.items())
    mine_clear_url = _url_without("mine")
    instructor_clear_url = _url_without("instructor")

    from membership.models import Member as MemberModel

    instructors = MemberModel.objects.filter(instructor_slug__gt="").order_by("full_legal_name")
    table = prepare_table(
        request,
        qs,
        search_fields=["title", "instructor__full_legal_name", "instructor__preferred_name", "category__name"],
        default_sort="created_at",
        default_dir="desc",
    )
    return render(
        request,
        "classes/admin/classes_list.html",
        {
            "active_tab": "classes",
            "pending_count": ClassOffering.objects.pending_review().count(),
            "status_filters": status_filters,
            "selected_status": status_filter,
            "instructors": instructors,
            "selected_instructor": instructor_filter,
            "mine_active": mine_active,
            "mine_count": mine_count,
            "mine_toggle_url": mine_toggle_url,
            "mine_clear_url": mine_clear_url,
            "instructor_clear_url": instructor_clear_url,
            "search_preserved_fields": search_preserved_fields,
            "search_clear_url": search_clear_url,
            **table,
        },
    )


def _create_form_readiness(form: ClassOfferingForm, session_formset: Any, gallery_files: list[Any]) -> list[Any]:
    """The readiness checklist for a not-yet-saved admin create, read from the validated forms."""
    data = form.cleaned_data
    now = timezone.now()
    has_future_session = any(
        session.cleaned_data.get("starts_at") is not None
        and session.cleaned_data["starts_at"] >= now
        and not session.cleaned_data.get("DELETE")
        for session in session_formset.forms
        if getattr(session, "cleaned_data", None)
    )
    return readiness_items(
        has_hero=bool(data.get("image")),
        has_gallery=bool(gallery_files),
        description=data.get("description") or "",
        scheduling_model=data["scheduling_model"],
        flexible_note=data.get("flexible_note") or "",
        has_future_session=has_future_session,
        capacity=data.get("capacity") or 0,
    )


def _discard_half_created_offering(offering: ClassOffering) -> None:
    """Roll back an admin create that could not publish: files, activity rows, then the row.

    ``ClassOffering.delete`` alone would leave the hero and gallery objects in storage and
    the ``class_created`` activity rows dangling (their FK is SET_NULL). Files are removed
    only when no other row shares the same storage key (images are content-addressed).
    """
    from core.files import delete_if_unreferenced
    from core.models import SiteActivity

    for gallery_image in list(offering.gallery_images.all()):
        name = gallery_image.image.name
        gallery_image.delete()
        delete_if_unreferenced(ClassImage, "image", name)
    hero_name = offering.image.name if offering.image else ""
    SiteActivity.objects.filter(
        target_ct=ContentType.objects.get_for_model(ClassOffering), target_id=offering.pk
    ).delete()
    CmsActivity.objects.filter(class_offering=offering).delete()
    offering.delete()
    delete_if_unreferenced(ClassOffering, "image", hero_name)


@classes_admin_access_required
def admin_class_create(request: HttpRequest) -> HttpResponse:
    form = ClassOfferingForm(request.POST or None, request.FILES or None)
    session_formset = ClassSessionFormSet(request.POST or None, prefix="sessions")
    if request.method == "POST" and form.is_valid() and session_formset.is_valid():
        gallery_files = request.FILES.getlist("gallery_images")
        # Readiness is checked from the validated form BEFORE anything is written, so an
        # unready class is refused without a hero file, gallery files, or activity rows
        # ever landing. Only the gallery cap (checked inside ``add_gallery_images``) can
        # still refuse after the save; that path rolls everything back.
        preflight = _create_form_readiness(form, session_formset, gallery_files)
        if not all(item.ok for item in preflight):
            form.add_error(None, readiness_error_text(preflight, "publish"))
        else:
            offering = form.save(commit=False)
            offering.status = ClassOffering.Status.DRAFT
            offering.save()
            session_formset.instance = offering
            session_formset.save()
            offering.finalize_recurring_slug()
            try:
                offering.add_gallery_images(gallery_files)
                offering.publish(cast("User", request.user))  # the admin decorator guarantees a logged-in user
            except ValidationError as exc:
                _discard_half_created_offering(offering)
                form.add_error(None, exc.messages[0])
            else:
                messages.success(request, f"{offering.title} is published.")
                return redirect("classes:admin_class_edit", pk=offering.pk)

    sessions_data: list[dict] = []
    if session_formset.is_bound:
        for i in range(int(request.POST.get("sessions-TOTAL_FORMS", "0"))):
            starts = request.POST.get(f"sessions-{i}-starts_at", "")
            ends = request.POST.get(f"sessions-{i}-ends_at", "")
            pk_val = request.POST.get(f"sessions-{i}-id", "")
            delete = request.POST.get(f"sessions-{i}-DELETE", "")
            if starts and ends:
                sessions_data.append({"id": pk_val, "starts_at": starts, "ends_at": ends, "DELETE": bool(delete)})

    return render(
        request,
        "classes/admin/class_form.html",
        {
            "active_tab": "classes",
            "form": form,
            "sessions_json": json.dumps(sessions_data),
            "initial_forms": session_formset.initial_form_count(),
            "mode": "create",
        },
    )


def class_permalink(request: HttpRequest, pk: int) -> HttpResponse:
    """Stable, slug-independent permalink → the class's current public page.

    Class QR codes encode this (not the slug URL directly), so a printed QR keeps working
    after a slug change. A temporary (302) redirect, so scanners always re-resolve to the
    live slug rather than caching an old target.
    """
    offering = get_object_or_404(ClassOffering, pk=pk)
    return redirect("classes:public_class_detail", slug=offering.slug)


def class_qr_download(request: HttpRequest, pk: int, fmt: str) -> HttpResponse:
    """Download a class's public-page QR as SVG (default) or PNG.

    Editor-gated via the shared ``can_edit_class`` check, so it works from either
    portal — an admin, the category guild's lead/staff, or the class's own instructor.
    """
    from membership.permissions import can_edit_class

    offering = get_object_or_404(ClassOffering, pk=pk)
    if not can_edit_class(request, offering):
        return HttpResponseForbidden("You don't have access to this class.")
    if fmt == "svg":
        resp = HttpResponse(offering.qr_svg(), content_type="image/svg+xml")
    elif fmt == "png":
        resp = HttpResponse(offering.qr_png_bytes(), content_type="image/png")
    else:
        raise Http404
    resp["Content-Disposition"] = f'attachment; filename="{offering.slug}-qr.{fmt}"'
    return resp


def class_flyer(request: HttpRequest, pk: int) -> HttpResponse:
    """Print-optimized one-page flyer for a class (instructor/admin → Print → Save as PDF).

    Editor-gated via the shared ``can_edit_class`` check, so it works from either
    portal — an admin, the category guild's lead/staff, or the class's own instructor.
    """
    from membership.permissions import can_edit_class

    offering = get_object_or_404(ClassOffering, pk=pk)
    if not can_edit_class(request, offering):
        return HttpResponseForbidden("You don't have access to this class.")
    return render(request, "classes/class_flyer.html", {"offering": offering, "qr_svg": offering.qr_svg()})


@classes_admin_access_required
def admin_class_edit(request: HttpRequest, pk: int) -> HttpResponse:
    offering = get_object_or_404(ClassOffering.objects.prefetch_related("gallery_images", "sessions"), pk=pk)
    form = ClassOfferingForm(request.POST or None, request.FILES or None, instance=offering)
    session_formset = ClassSessionFormSet(request.POST or None, instance=offering, prefix="sessions")
    faq_formset = build_class_faq_formset(request.POST or None, offering)
    if request.method == "POST" and form.is_valid() and session_formset.is_valid() and faq_formset.is_valid():
        form.save()
        session_formset.save()
        faq_formset.save()
        messages.success(request, "Class updated.")
        return redirect("classes:admin_class_detail", pk=offering.pk)

    sessions_data: list[dict] = []
    if session_formset.is_bound:
        for i in range(int(request.POST.get("sessions-TOTAL_FORMS", "0"))):
            starts = request.POST.get(f"sessions-{i}-starts_at", "")
            ends = request.POST.get(f"sessions-{i}-ends_at", "")
            pk_val = request.POST.get(f"sessions-{i}-id", "")
            delete = request.POST.get(f"sessions-{i}-DELETE", "")
            if starts and ends:
                sessions_data.append({"id": pk_val, "starts_at": starts, "ends_at": ends, "DELETE": bool(delete)})
    else:
        for s in offering.sessions.order_by("starts_at"):
            sessions_data.append(
                {
                    "id": s.pk,
                    "starts_at": s.starts_at.strftime("%Y-%m-%dT%H:%M"),
                    "ends_at": s.ends_at.strftime("%Y-%m-%dT%H:%M"),
                }
            )

    return render(
        request,
        "classes/admin/class_form.html",
        {
            "active_tab": "classes",
            "form": form,
            "offering": offering,
            "sessions_json": json.dumps(sessions_data),
            "initial_forms": session_formset.initial_form_count(),
            "mode": "edit",
            "faq_formset": faq_formset,
        },
    )


def _class_workspace_counts(offering: ClassOffering) -> dict[str, int]:
    """Sub-tab badge counts shared by every per-class Workspace tab."""
    regs = offering.registrations
    return {
        "confirmed_registration_count": regs.filter(
            status__in=[Registration.Status.CONFIRMED, Registration.Status.PENDING]
        ).count(),
        "waitlist_count": regs.filter(status=Registration.Status.WAITLISTED).count(),
    }


def _admin_class_detail_offering(pk: int) -> ClassOffering:
    return get_object_or_404(
        ClassOffering.objects.select_related("instructor", "category__guild")
        .prefetch_related("sessions")
        .annotate(registration_count=Count("registrations")),
        pk=pk,
    )


def _render_admin_class_detail(
    request: HttpRequest, offering: ClassOffering, cancel_form: ClassCancelForm
) -> HttpResponse:
    """The admin workspace Overview: pipeline strip, summary, and the action row by state.

    A bound, invalid ``cancel_form`` re-renders the page with the Cancel class modal open
    and the error inside it (the modal is server-rendered inline, never fetched).
    """
    return render(
        request,
        "classes/admin/class_detail.html",
        {
            "active_tab": "classes",
            "active_subtab": "overview",
            "offering": offering,
            "lifecycle": offering.lifecycle,
            "pipeline": offering.review_pipeline(),
            "cancel_form": cancel_form,
            "archive_blocker": offering.archive_blocker,
            "paid_registration_count": offering.paid_registration_count,
            **_class_workspace_counts(offering),
        },
    )


@classes_review_access_required
def admin_class_detail(request: HttpRequest, pk: int) -> HttpResponse:
    offering = _admin_class_detail_offering(pk)
    return _render_admin_class_detail(request, offering, ClassCancelForm())


@classes_admin_access_required
@require_POST
def admin_class_cancel(request: HttpRequest, pk: int) -> HttpResponse:
    """Cancel a live class with a reason: registrants are emailed, every member gets the bell row."""
    offering = _admin_class_detail_offering(pk)
    form = ClassCancelForm(request.POST)
    if not form.is_valid():
        return _render_admin_class_detail(request, offering, form)
    try:
        offering.cancel(cast("User", request.user), form.cleaned_data["reason"])
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("classes:admin_class_detail", pk=offering.pk)
    messages.success(request, "Class cancelled. Everyone registered has been told.")
    return redirect("classes:admin_class_detail", pk=offering.pk)


@classes_admin_access_required
@require_POST
def admin_class_restore(request: HttpRequest, pk: int) -> HttpResponse:
    """Restore an archived class to a draft. It needs review again before it goes live."""
    offering = get_object_or_404(ClassOffering, pk=pk)
    try:
        offering.restore()
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("classes:admin_class_detail", pk=offering.pk)
    messages.success(request, f"{offering.title} restored to draft. It needs review again before it goes live.")
    return redirect("classes:admin_class_detail", pk=offering.pk)


@classes_admin_access_required
@require_POST
def admin_class_remind_lead(request: HttpRequest, pk: int) -> HttpResponse:
    """Remind lead (HTMX): re-send the open guild-lead review request, once per day, and toast the outcome."""
    offering = get_object_or_404(ClassOffering, pk=pk)
    response = HttpResponse(status=204)
    gate = offering.approvals.filter(role=ClassApproval.Role.GUILD_LEAD, decision="").order_by("-created_at").first()
    if gate is None or offering.status != ClassOffering.Status.PENDING:
        trigger_toast(response, "This class is not waiting on a guild lead.", "error")
        return response
    result = send_guild_lead_review_reminder(gate)
    if result is None:
        trigger_toast(response, "This guild has no lead. Review it yourself.", "error")
    elif result.delivered:
        guild = offering.category.guild
        lead = guild.guild_lead if guild is not None else None
        trigger_toast(response, f"Reminder sent to {lead.display_name if lead is not None else 'the guild leads'}.")
    else:
        trigger_toast(response, "Already reminded today.", "info")
    return response


@classes_admin_access_required
def admin_class_registrations(request: HttpRequest, pk: int) -> HttpResponse:
    offering = get_object_or_404(ClassOffering, pk=pk)
    return render(
        request,
        "classes/admin/class_registrations.html",
        {
            "active_tab": "classes",
            "active_subtab": "registrations",
            **_teach_registrations_context(request, offering),
            **_class_workspace_counts(offering),
        },
    )


@classes_admin_access_required
def admin_class_registrations_table(request: HttpRequest, pk: int) -> HttpResponse:
    """The admin roster table alone — re-fetched by the ``refund-done`` refresh container."""
    offering = get_object_or_404(ClassOffering, pk=pk)
    return render(
        request,
        "classes/teach/partials/class_registrations_table.html",
        _teach_registrations_context(request, offering),
    )


@classes_admin_access_required
def admin_class_waitlist(request: HttpRequest, pk: int) -> HttpResponse:
    offering = get_object_or_404(ClassOffering, pk=pk)
    return render(
        request,
        "classes/admin/class_waitlist.html",
        {
            "active_tab": "classes",
            "active_subtab": "waitlist",
            **_waitlist_context(request, offering),
            **_class_workspace_counts(offering),
        },
    )


@classes_admin_access_required
def admin_class_discount_codes(request: HttpRequest, pk: int) -> HttpResponse:
    offering = get_object_or_404(ClassOffering, pk=pk)
    codes = DiscountCode.objects.filter(Q(class_offering=offering) | Q(class_offering__isnull=True)).order_by("code")
    return render(
        request,
        "classes/admin/class_discount_codes.html",
        {
            "active_tab": "classes",
            "active_subtab": "discount_codes",
            "offering": offering,
            "codes": codes,
            **_class_workspace_counts(offering),
        },
    )


@classes_admin_access_required
def admin_class_emails(request: HttpRequest, pk: int) -> HttpResponse:
    """Author a class's welcome email from the admin class workspace (any class)."""
    offering = get_object_or_404(ClassOffering, pk=pk)
    form = TeachWelcomeEmailForm(request.POST or None, instance=offering)
    if request.method == "POST" and form.is_valid():
        offering = form.save()
        if "send_test" in request.POST:
            send_class_welcome_email_test(offering, request.user.email)
            messages.success(request, f"Saved — and sent a test to {request.user.email}.")
        else:
            messages.success(request, "Welcome email saved.")
        return redirect("classes:admin_class_emails", pk=offering.pk)
    return render(
        request,
        "classes/admin/class_emails.html",
        {
            "active_tab": "classes",
            "active_subtab": "emails",
            "offering": offering,
            "form": form,
            **_class_workspace_counts(offering),
        },
    )


@classes_admin_access_required
@require_POST
def admin_class_email(request: HttpRequest, pk: int) -> HttpResponse:
    from classes.forms import AdminClassEmailForm

    offering = get_object_or_404(ClassOffering, pk=pk)
    sender_member: Member | None = getattr(request.user, "member", None)
    form = AdminClassEmailForm(request.POST, offering=offering)
    if not form.is_valid():
        first_error = next(iter(form.errors.values()))[0] if form.errors else "Couldn't send the message."
        messages.error(request, str(first_error))
        return redirect("classes:admin_class_registrations", pk=pk)
    message = form.send(sender_member=sender_member)
    messages.success(
        request,
        f"Sent '{message.subject}' to {message.recipient_count} recipient(s).",
    )
    return redirect("classes:admin_class_registrations", pk=pk)


@classes_review_access_required
def admin_class_approve(request: HttpRequest, pk: int) -> HttpResponse:
    """Quick-approve from the admin class detail page.

    Records an admin-role decision via ClassApproval. Admin approval is final:
    the offering publishes immediately, closing any still-open guild-lead gate.
    For request-changes / decline with notes, use the dedicated review page
    at /classes/admin/<pk>/review/.
    """
    offering = get_object_or_404(ClassOffering, pk=pk)
    if request.method == "POST":
        try:
            row = offering.approve(request.user)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("classes:admin_class_detail", pk=offering.pk)
        except ValidationError as exc:
            # An unready class (no dates, no photos, ...) never publishes; say why.
            messages.error(request, exc.messages[0])
            return redirect("classes:admin_class_detail", pk=offering.pk)
        send_class_review_decision(offering, row)
        messages.success(request, f"{offering.title} is published.")
    return redirect("classes:admin_class_detail", pk=offering.pk)


_ACTIVITY_GROUPS = {
    "classes": [
        CmsActivity.Kind.CLASS_CREATED,
        CmsActivity.Kind.CLASS_SUBMITTED,
        CmsActivity.Kind.CLASS_APPROVED,
        CmsActivity.Kind.CLASS_CHANGES_REQUESTED,
        CmsActivity.Kind.CLASS_DENIED,
        CmsActivity.Kind.CLASS_PUBLISHED,
        CmsActivity.Kind.CLASS_ARCHIVED,
    ],
    "registrations": [
        CmsActivity.Kind.REGISTRATION_CREATED,
        CmsActivity.Kind.REGISTRATION_CONFIRMED,
        CmsActivity.Kind.REGISTRATION_MARKED_PAID,
        CmsActivity.Kind.PAYMENT_LINK_SENT,
        CmsActivity.Kind.DUPLICATE_PAYMENT,
        CmsActivity.Kind.REGISTRATION_CANCELLED,
        CmsActivity.Kind.REGISTRATION_REFUNDED,
    ],
    "waitlist": [
        CmsActivity.Kind.WAITLIST_JOINED,
        CmsActivity.Kind.WAITLIST_NOTIFIED,
        CmsActivity.Kind.WAITLIST_LEFT,
        CmsActivity.Kind.WAITLIST_PROMOTED,
    ],
    "discount_codes": [
        CmsActivity.Kind.DISCOUNT_CODE_CREATED,
        CmsActivity.Kind.DISCOUNT_CODE_REDEEMED,
    ],
}


@classes_admin_access_required
def admin_activity(request: HttpRequest) -> HttpResponse:
    """Chronological feed of every CMS event for admins.

    Filters: group (classes/registrations/waitlist/discount_codes/all) and
    a free-text search across class title, actor name, and registration
    email. Pagination is 50 rows; older rows fall off the bottom.
    """
    from django.core.paginator import Paginator
    from django.http import QueryDict

    qs = CmsActivity.objects.select_related(
        "class_offering",
        "registration",
        "actor",
    )

    selected_group = request.GET.get("group", "all").strip() or "all"
    if selected_group in _ACTIVITY_GROUPS:
        qs = qs.filter(kind__in=_ACTIVITY_GROUPS[selected_group])

    search = (request.GET.get("q") or "").strip()
    if search:
        qs = qs.filter(
            Q(class_offering__title__icontains=search)
            | Q(actor__email__icontains=search)
            | Q(actor__first_name__icontains=search)
            | Q(actor__last_name__icontains=search)
            | Q(registration__email__icontains=search)
            | Q(registration__first_name__icontains=search)
            | Q(registration__last_name__icontains=search)
        )

    # "When" is the only sortable column; default to Most Recent (newest-first).
    sort_dir = "asc" if request.GET.get("dir") == "asc" else "desc"
    order_prefix = "" if sort_dir == "asc" else "-"
    qs = qs.order_by(f"{order_prefix}created_at")

    base_params = QueryDict(mutable=True)
    if selected_group != "all":
        base_params["group"] = selected_group
    if search:
        base_params["q"] = search

    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get("page") or 1)

    return render(
        request,
        "classes/admin/activity.html",
        {
            "active_tab": "activity",
            "events": page.object_list,
            "page_obj": page,
            "paginator": paginator,
            "selected_group": selected_group,
            "search": search,
            "current_sort": "created_at",
            "current_dir": sort_dir,
            "base_params": base_params.urlencode(),
            "groups": [
                ("all", "All"),
                ("classes", "Classes"),
                ("registrations", "Registrations"),
                ("waitlist", "Waitlist"),
                ("discount_codes", "Discount codes"),
            ],
        },
    )


@classes_review_access_required
def admin_class_review(request: HttpRequest, pk: int) -> HttpResponse:
    """Full reviewer page for admins and CMS Administrators. Mirrors the tokenized public review page."""
    offering = get_object_or_404(ClassOffering, pk=pk)
    return _class_review_view(
        request,
        offering=offering,
        role=ClassApproval.Role.ADMIN,
        token=None,
    )


def class_review(request: HttpRequest, token: str) -> HttpResponse:
    """Tokenized reviewer page — no hub login required.

    Token is single-use per decision; reopening after a decision shows the
    current state. The token identifies the ClassApproval row and therefore
    which role's gate the visitor satisfies.
    """
    approval = ClassApproval.objects.filter(token=token).select_related("class_offering").first()
    if approval is None:
        # A withdraw or resubmit deletes the cycle's rows, so an emailed link can outlive
        # its token. Render the same "not awaiting review" state, naming nothing about
        # the class (the token is the only credential and it no longer resolves).
        return render(request, "classes/admin/class_review_unknown.html", {"active_tab": "classes"})
    return _class_review_view(
        request,
        offering=approval.class_offering,
        role=approval.role,
        token=token,
        approval=approval,
    )


def _class_review_view(
    request: HttpRequest,
    *,
    offering: ClassOffering,
    role: str,
    token: str | None,
    approval: ClassApproval | None = None,
) -> HttpResponse:
    """Shared logic for /classes/admin/<pk>/review/ and /classes/review/<token>/.

    Only a PENDING offering is reviewable: for any other status the page never
    mints an approval row and never accepts a decision POST — it renders a
    plain "not awaiting review" state instead of the form. This keeps a stale
    review link (or a direct URL hit) from publishing a DRAFT, re-publishing an
    ARCHIVED class, or bouncing a live class back to DRAFT.
    """
    is_reviewable = offering.status == ClassOffering.Status.PENDING
    if approval is None:
        approval = offering.approvals.filter(role=role, decision="").order_by("-created_at").first()
        if approval is None and is_reviewable:
            approval = ClassApproval.objects.create(class_offering=offering, role=role)
    settings_obj = ClassSettings.load()
    upcoming_sessions = list(offering.sessions.filter(starts_at__gte=timezone.now()).order_by("starts_at"))
    history_qs = offering.approvals.order_by("-created_at")
    if approval is not None:
        history_qs = history_qs.exclude(pk=approval.pk)
    history = list(history_qs)

    form = ClassReviewDecisionForm(request.POST or None)
    if (
        request.method == "POST"
        and is_reviewable
        and approval is not None
        and not approval.decision
        and form.is_valid()
    ):
        try:
            approval.decide(
                form.cleaned_data["decision"],
                user=request.user if request.user.is_authenticated else None,
                notes=form.cleaned_data.get("notes", ""),
            )
        except ValidationError as exc:
            # The publishing decision refused an unready class; show the failing
            # items as a form error on both the admin and the tokenized page.
            form.add_error(None, exc.messages[0])
        else:
            # refresh from DB to pick up the new state
            approval.refresh_from_db()
            offering.refresh_from_db()
            send_class_review_decision(offering, approval)
            messages.success(request, "Your decision has been recorded. Thanks for reviewing.")
            if token:
                return redirect("classes:class_review", token=token)
            return redirect("classes:admin_class_review", pk=offering.pk)

    readiness = offering.readiness()
    return render(
        request,
        "classes/admin/class_review.html",
        {
            "offering": offering,
            "settings_obj": settings_obj,
            "approval": approval,
            "history": history,
            "form": form,
            "role": role,
            "is_reviewable": is_reviewable,
            "upcoming_sessions": upcoming_sessions,
            "is_tokenized": token is not None,
            "active_tab": "classes",
            "pipeline": offering.review_pipeline(),
            "readiness": readiness,
            "readiness_ready_count": sum(1 for item in readiness if item.ok),
            "is_ready": all(item.ok for item in readiness),
        },
    )


@classes_admin_access_required
def admin_class_archive(request: HttpRequest, pk: int) -> HttpResponse:
    offering = get_object_or_404(ClassOffering, pk=pk)
    if request.method == "POST":
        try:
            offering.archive()
        except ValueError as exc:
            # An upcoming class with active registrations must be cancelled, not hidden.
            messages.error(request, str(exc))
            return redirect("classes:admin_class_detail", pk=offering.pk)
        messages.success(request, f"{offering.title} archived. Nobody was notified.")
        return redirect("classes:admin_classes")
    return redirect("classes:admin_class_detail", pk=offering.pk)


@classes_admin_access_required
def admin_class_duplicate(request: HttpRequest, pk: int) -> HttpResponse:
    offering = get_object_or_404(ClassOffering, pk=pk)
    if request.method == "POST":
        copy = offering.duplicate()
        messages.success(request, "Class duplicated.")
        return redirect("classes:admin_class_edit", pk=copy.pk)
    return redirect("classes:admin_class_detail", pk=offering.pk)


@classes_admin_access_required
def admin_class_duplicate_run(request: HttpRequest, pk: int) -> HttpResponse:
    """Offer this class on another set of dates — clones it as a grouped draft run."""
    offering = get_object_or_404(ClassOffering, pk=pk)
    if request.method == "POST":
        run = offering.duplicate_as_new_run()
        messages.success(request, "New date-set added as a draft. Add its dates, then publish when ready.")
        return redirect("classes:admin_class_edit", pk=run.pk)
    return redirect("classes:admin_class_detail", pk=offering.pk)


@classes_admin_access_required
def admin_class_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Hard-delete a class — only when it has no registrations.

    Classes with any registration history (even cancelled) are refused to
    preserve the audit record; use Archive instead in that case.
    """
    offering = get_object_or_404(ClassOffering, pk=pk)
    if request.method == "POST":
        if offering.registrations.exists():
            messages.error(request, "Can't delete — this class has registrations. Archive it instead.")
            return redirect("classes:admin_class_detail", pk=offering.pk)
        title = offering.title
        offering.delete()
        messages.success(request, f"Deleted ‘{title}’.")
        return redirect("classes:admin_classes")
    return redirect("classes:admin_class_detail", pk=offering.pk)


@classes_admin_access_required
@require_POST
def admin_class_hero_upload(request: HttpRequest, pk: int) -> HttpResponse:
    return _hero_upload(request, get_object_or_404(ClassOffering, pk=pk))


@classes_admin_access_required
@require_POST
def admin_class_image_upload(request: HttpRequest, pk: int) -> HttpResponse:
    return _gallery_upload(request, get_object_or_404(ClassOffering, pk=pk))


@classes_admin_access_required
@require_POST
def admin_class_image_reorder(request: HttpRequest, pk: int) -> HttpResponse:
    return _gallery_reorder(request, get_object_or_404(ClassOffering, pk=pk))


@classes_admin_access_required
@require_POST
def admin_class_image_delete(request: HttpRequest, pk: int) -> HttpResponse:
    return _gallery_delete(get_object_or_404(ClassImage, pk=pk))


@classes_admin_access_required
@require_POST
def admin_class_image_alt(request: HttpRequest, pk: int) -> HttpResponse:
    return _gallery_alt(request, get_object_or_404(ClassImage, pk=pk))


# --- Instructor-scoped image endpoints ----------------------------------------
#
# The hero and gallery components post instantly (drag, drop, reorder, alt text). The
# instructor edit pages point them at these routes, which share the admin handlers
# above but scope the class through the teach portal's ``editable_by`` (the instructor,
# plus guild staff who may edit the draft); anyone else gets a 404, never a 403.


def _teach_editable_offerings(member: Member) -> "ClassOfferingQuerySet":
    """The classes this member may edit photos on: their editable set, minus the closed ones.

    ``teach_class_edit`` bounces cancelled and archived classes to an admin, so the image
    routes behind that page exclude them too. A page gate and a mutation gate that disagree
    are how an instructor ends up curling a surface the UI never offers.
    """
    return ClassOffering.objects.editable_by(member).exclude(
        status__in=[ClassOffering.Status.CANCELLED, ClassOffering.Status.ARCHIVED]
    )


def _teach_editable_offering_or_404(request: HttpRequest, pk: int) -> ClassOffering:
    teaching_member: Member = request.teaching_member  # type: ignore[attr-defined]
    return get_object_or_404(_teach_editable_offerings(teaching_member), pk=pk)


def _teach_editable_image_or_404(request: HttpRequest, pk: int) -> ClassImage:
    teaching_member: Member = request.teaching_member  # type: ignore[attr-defined]
    return get_object_or_404(
        ClassImage.objects.filter(class_offering__in=_teach_editable_offerings(teaching_member)), pk=pk
    )


def _teach_gallery_context(offering: ClassOffering, *, with_hero: bool = True) -> dict[str, str]:
    """The URLs the hero + gallery components post to on the instructor edit pages.

    ``with_hero=False`` for the live-edit page, which renders the gallery but not the hero
    field: shipping a hero URL a page never posts to only invites drift.
    """
    hero = (
        {"hero_upload_url": reverse("classes:teach_class_hero_upload", kwargs={"pk": offering.pk})} if with_hero else {}
    )
    return {
        **hero,
        "gallery_upload_url": reverse("classes:teach_class_image_upload", kwargs={"pk": offering.pk}),
        "gallery_reorder_url": reverse("classes:teach_class_image_reorder", kwargs={"pk": offering.pk}),
        "gallery_image_url_base": teach_image_url_base(),
    }


def teach_image_url_base() -> str:
    """The prefix the per-image delete / alt routes hang off (``<base><id>/delete/``).

    Derived from the route rather than typed out, so re-prefixing the URL include can never
    leave the JS posting to a path that 404s. ``reverse`` is resolver-cached, so calling this
    per render is free.
    """
    return reverse("classes:teach_class_image_delete", kwargs={"pk": 0}).removesuffix("0/delete/")


@teaching_member_required
@require_POST
def teach_class_hero_upload(request: HttpRequest, pk: int) -> HttpResponse:
    return _hero_upload(request, _teach_editable_offering_or_404(request, pk))


@teaching_member_required
@require_POST
def teach_class_image_upload(request: HttpRequest, pk: int) -> HttpResponse:
    return _gallery_upload(request, _teach_editable_offering_or_404(request, pk))


@teaching_member_required
@require_POST
def teach_class_image_reorder(request: HttpRequest, pk: int) -> HttpResponse:
    return _gallery_reorder(request, _teach_editable_offering_or_404(request, pk))


@teaching_member_required
@require_POST
def teach_class_image_delete(request: HttpRequest, pk: int) -> HttpResponse:
    return _gallery_delete(_teach_editable_image_or_404(request, pk))


@teaching_member_required
@require_POST
def teach_class_image_alt(request: HttpRequest, pk: int) -> HttpResponse:
    return _gallery_alt(request, _teach_editable_image_or_404(request, pk))


def _hero_upload(request: HttpRequest, offering: ClassOffering) -> HttpResponse:
    file = request.FILES.get("image")
    if not file:
        return JsonResponse({"error": "No file provided."}, status=400)
    oversize = _oversize_image_error(file)
    if oversize is not None:
        return oversize
    offering.image = file
    offering.hero_crop_x = None
    offering.hero_crop_y = None
    offering.hero_crop_w = None
    offering.hero_crop_h = None
    offering.save()
    return JsonResponse({"url": offering.image.url})


def _oversize_image_error(file: UploadedFile) -> JsonResponse | None:
    """The 400 for an upload over ``MAX_UPLOAD_IMAGE_BYTES``, or None when it fits.

    Shared by the hero and gallery routes so the two cannot drift apart again: the model
    field's ``validate_image_size`` runs only under ``full_clean()``, which the hero path
    does not reach, so the cap has to be enforced here.
    """
    assert file.size is not None  # an uploaded file always reports its size
    if file.size <= settings.MAX_UPLOAD_IMAGE_BYTES:
        return None
    limit_mb = settings.MAX_UPLOAD_IMAGE_BYTES / (1024 * 1024)
    return JsonResponse({"error": f"Image must be {limit_mb:.0f} MB or smaller."}, status=400)


def _gallery_upload(request: HttpRequest, offering: ClassOffering) -> HttpResponse:
    if offering.gallery_images.count() >= MAX_GALLERY_IMAGES:
        return JsonResponse({"error": f"A class can have at most {MAX_GALLERY_IMAGES} images."}, status=400)
    file = request.FILES.get("image")
    if not file:
        return JsonResponse({"error": "No file provided."}, status=400)
    oversize = _oversize_image_error(file)
    if oversize is not None:
        return oversize
    next_order = (offering.gallery_images.order_by("-sort_order").values_list("sort_order", flat=True).first() or 0) + 1
    img = ClassImage(class_offering=offering, image=file, sort_order=next_order)
    img.full_clean()
    img.save()
    return JsonResponse({"id": img.pk, "url": img.image.url, "alt_text": "", "sort_order": img.sort_order})


def _gallery_reorder(request: HttpRequest, offering: ClassOffering) -> HttpResponse:
    try:
        order = json.loads(request.body)["order"]
    except (json.JSONDecodeError, KeyError):
        return JsonResponse({"error": "Invalid payload."}, status=400)
    images = {img.pk: img for img in offering.gallery_images.all()}
    for idx, image_id in enumerate(order):
        if image_id in images:
            images[image_id].sort_order = idx
            images[image_id].save(update_fields=["sort_order"])
    return JsonResponse({"ok": True})


def _gallery_delete(img: ClassImage) -> HttpResponse:
    img.image.delete(save=False)
    img.delete()
    return JsonResponse({"ok": True})


def _gallery_alt(request: HttpRequest, img: ClassImage) -> HttpResponse:
    try:
        alt_text = json.loads(request.body)["alt_text"]
    except (json.JSONDecodeError, KeyError):
        return JsonResponse({"error": "Invalid payload."}, status=400)
    if not isinstance(alt_text, str):
        return JsonResponse({"error": "Invalid payload."}, status=400)
    img.alt_text = alt_text[:255]
    img.save(update_fields=["alt_text"])
    return JsonResponse({"ok": True})


@classes_admin_access_required
def admin_categories(request: HttpRequest) -> HttpResponse:
    table = prepare_table(
        request,
        Category.objects.all(),
        search_fields=["name"],
        default_sort="sort_order",
    )
    return render(
        request,
        "classes/admin/categories.html",
        {"active_tab": "categories", **table},
    )


@classes_admin_access_required
def admin_category_create(request: HttpRequest) -> HttpResponse:
    form = CategoryForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Category created.")
        return redirect("classes:admin_categories")
    return render(
        request,
        "classes/admin/category_form.html",
        {"active_tab": "categories", "form": form, "mode": "create"},
    )


@classes_admin_access_required
def admin_category_edit(request: HttpRequest, pk: int) -> HttpResponse:
    category = get_object_or_404(Category, pk=pk)
    form = CategoryForm(request.POST or None, request.FILES or None, instance=category)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Category updated.")
        return redirect("classes:admin_categories")
    return render(
        request,
        "classes/admin/category_form.html",
        {"active_tab": "categories", "form": form, "category": category, "mode": "edit"},
    )


@classes_admin_access_required
def admin_category_delete(request: HttpRequest, pk: int) -> HttpResponse:
    category = get_object_or_404(Category, pk=pk)
    if request.method == "POST":
        category.delete()
        messages.success(request, "Category deleted.")
    return redirect("classes:admin_categories")


@classes_admin_access_required
def admin_guild_tagging(request: HttpRequest) -> HttpResponse:
    """Bulk re-file legacy offerings from guildless categories into guild-linked ones.

    Every offering imported from the old CMS sits in a generic category
    (``category.guild`` is NULL). This one-time cleanup surface suggests a
    guild-linked category from keywords in each offering's title + description
    and lets staff review and apply them in one pass. POST re-files the chosen
    offerings; anything already moved by someone else is skipped silently.
    """
    guild_categories = list(Category.objects.filter(guild__isnull=False).order_by("name"))

    if request.method == "POST":
        assignments: dict[int, int] = {}
        for key in request.POST:
            if not key.startswith("category_"):
                continue
            value = request.POST[key]
            if not value:
                continue
            try:
                assignments[int(key.removeprefix("category_"))] = int(value)
            except ValueError:
                continue
        applied = ClassOffering.objects.refile_into_guild_categories(assignments)
        noun = "class" if applied == 1 else "classes"
        messages.success(request, f"Re-filed {applied} {noun} into guild categories.")
        return redirect("classes:admin_guild_tagging")

    categories_by_name = {c.name: c for c in guild_categories}
    offerings = (
        ClassOffering.objects.filter(category__guild__isnull=True)
        .select_related("category")
        .order_by("-status", "title")
    )
    rows = [(offering, offering.suggest_guild_category(categories_by_name)) for offering in offerings]
    suggestion_count = sum(1 for _, suggestion in rows if suggestion is not None)
    return render(
        request,
        "classes/admin/guild_tagging.html",
        {
            "active_tab": "categories",
            "rows": rows,
            "guild_categories": guild_categories,
            "total_count": len(rows),
            "suggestion_count": suggestion_count,
        },
    )


@classes_registrations_access_required
def admin_registrations(request: HttpRequest) -> HttpResponse:
    scoped = _scoped_registrations(request)
    table = prepare_table(
        request,
        _filter_registrations(request, scoped),
        search_fields=["first_name", "last_name", "email", "class_offering__title"],
        default_sort="registered_at",
        default_dir="desc",
    )
    class_options = ClassOffering.objects.filter(registrations__in=scoped).distinct().order_by("title")

    # The instructor filter UI is for actual admins only — non-admin visitors are
    # already scoped to their own classes, so a filter that can't widen anything
    # would just confuse.
    view_as = getattr(request, "view_as", None)
    is_actual_admin = view_as is not None and view_as.has_actual("admin")
    instructors = None
    if is_actual_admin:
        from membership.models import Member as MemberModel

        instructors = MemberModel.objects.filter(instructor_slug__gt="").order_by("full_legal_name")

    # "My Classes": registrations for classes the real logged-in user teaches or
    # authored. Always rendered — no instructor_slug gate (that gate was the bug that
    # hid the old toggle) — and "me" is the real user even under a view-as preview.
    # A bogus mine value is off and stripped from the computed URLs so cruft never
    # rides along. The actual filtering (and its memberless guard) lives in
    # _filter_registrations so the CSV export inherits it.
    mine_active = request.GET.get("mine", "") == "1"
    normalized = request.GET.copy()
    if normalized.get("mine", "") != "1":
        normalized.pop("mine", None)
    toggle = normalized.copy()
    toggle.pop("page", None)
    if mine_active:
        toggle.pop("mine", None)
    else:
        toggle["mine"] = "1"
    mine_toggle_url = toggle.urlencode()
    mine_clear = normalized.copy()
    mine_clear.pop("mine", None)
    mine_clear.pop("page", None)
    mine_clear_url = mine_clear.urlencode()
    return render(
        request,
        "classes/admin/registrations.html",
        {
            "active_tab": "registrations",
            "status_choices": Registration.Status.choices,
            "status_filter": request.GET.get("status", ""),
            "class_options": class_options,
            "class_filter": request.GET.get("class", ""),
            "show_instructor_filter": is_actual_admin,
            "instructors": instructors,
            "instructor_filter": request.GET.get("instructor", ""),
            "mine_active": mine_active,
            "mine_toggle_url": mine_toggle_url,
            "mine_clear_url": mine_clear_url,
            **table,
        },
    )


@classes_registrations_access_required
def admin_registrations_export(request: HttpRequest) -> StreamingHttpResponse:
    """Download the filtered, role-scoped registrations list as a CSV."""
    from classes.exports import stream_registrations_query_csv

    registrations = _filter_registrations(request, _scoped_registrations(request))
    return stream_registrations_query_csv(registrations, filename_stem="registrations")


@classes_registrations_access_required
def admin_registration_detail(request: HttpRequest, pk: int) -> HttpResponse:
    from classes.forms import RegistrationMoveForm

    from hub.view_as import has_refund_authority

    registration = get_object_or_404(
        _scoped_registrations(request)
        .select_related("discount_code")
        .prefetch_related("waivers", "custom_answers__question", _refunds_prefetch()),
        pk=pk,
    )
    duplicate_payment = (
        registration.activity.filter(kind=CmsActivity.Kind.DUPLICATE_PAYMENT).order_by("-created_at").first()
    )
    return render(
        request,
        "classes/admin/registration_detail.html",
        {
            "active_tab": "registrations",
            "registration": registration,
            "move_form": RegistrationMoveForm(current=registration.class_offering),
            "viewer_has_refund_authority": has_refund_authority(request),
            "duplicate_payment": duplicate_payment,
        },
    )


@classes_admin_access_required
def admin_registration_cancel(request: HttpRequest, pk: int) -> HttpResponse:
    registration = get_object_or_404(Registration, pk=pk)
    if request.method == "POST":
        actor = request.user if request.user.is_authenticated else None
        registration.cancel(reason=request.POST.get("reason", ""), actor=actor)
        messages.success(request, "Registration cancelled.")
    return redirect("classes:admin_registration_detail", pk=pk)


@classes_admin_access_required
@require_POST
def admin_registration_move(request: HttpRequest, pk: int) -> HttpResponse:
    from classes.forms import RegistrationMoveForm

    registration = get_object_or_404(Registration, pk=pk)
    form = RegistrationMoveForm(request.POST, current=registration.class_offering)
    if form.is_valid():
        actor = request.user if request.user.is_authenticated else None
        registration.move_to(form.cleaned_data["target"], actor=actor)
        messages.success(request, "Registration moved.")
    else:
        messages.error(request, "Could not move registration — pick a valid class.")
    return redirect("classes:admin_registration_detail", pk=pk)


def _render_refund_form(request: HttpRequest, registration: Registration, form: "PaymentRefundForm") -> HttpResponse:
    """Render the shared refund modal body — the retry confirm when the latest attempt failed.

    One partial serves every host (dashboard, CMS detail, teach portal): the
    ``failed`` refund state's only action is Retry (§5.3), so the partial picks
    the variant from the registration's state, not from a host parameter.
    """
    failed_refund = None
    if registration.refund_state == "failed":
        from billing.models import PaymentRefund

        failed_refund = registration.refunds.filter(status=PaymentRefund.Status.FAILED).first()
    return render(
        request,
        "classes/partials/refund_form.html",
        {
            "registration": registration,
            "form": form,
            "failed_refund": failed_refund,
            "first_session_at": registration.class_offering.earliest_session_at,
        },
    )


@refund_authority_required
def admin_registration_refund_form(request: HttpRequest, pk: int) -> HttpResponse:
    """GET partial — the refund modal body, loaded via HTMX by every host page."""
    from classes.forms import PaymentRefundForm

    registration = get_object_or_404(Registration, pk=pk)
    return _render_refund_form(request, registration, PaymentRefundForm(registration=registration))


@refund_authority_required
@require_POST
def admin_registration_refund(request: HttpRequest, pk: int) -> HttpResponse:
    """Issue a real Stripe refund — 204 + toast + ``refund-done`` on success.

    Validation errors re-render the form partial in place. A Stripe rejection is
    loud: an error toast carries Stripe's message and the modal stays open —
    re-rendered in the failed state, whose action is Retry (the FAILED audit row
    is the anchor). A ``REFUNDS`` holder may refund any registration — that is
    what the grant means (§5.6).
    """
    from billing.exceptions import RefundError
    from classes.forms import PaymentRefundForm
    from hub.toast import trigger_client_event, trigger_toast

    registration = get_object_or_404(Registration, pk=pk)
    form = PaymentRefundForm(request.POST, registration=registration)
    if not form.is_valid():
        return _render_refund_form(request, registration, form)
    try:
        refund = registration.issue_refund(
            amount_cents=form.amount_cents,
            reason=form.cleaned_data["reason"],
            actor=request.user,
        )
    except RefundError as exc:
        registration.refresh_from_db()
        response = _render_refund_form(request, registration, PaymentRefundForm(registration=registration))
        trigger_toast(response, f"Refund failed: {exc}", "error")
        return response
    from billing.models import PaymentRefund

    response = HttpResponse(status=204)
    if refund.status == PaymentRefund.Status.SUCCEEDED:
        trigger_toast(response, f"Refunded ${form.cleaned_data['amount']:.2f}.", "success")
    else:
        # Stripe accepted the refund but hasn't settled it; refund.updated will.
        trigger_toast(response, "Refund sent. Stripe is processing it.", "success")
    trigger_client_event(response, "refund-done")
    return response


@classes_registrations_access_required
def admin_registration_refunds_card(request: HttpRequest, pk: int) -> HttpResponse:
    """The detail page's Refunds card — also the ``refund-done`` refresh target."""
    from hub.view_as import has_refund_authority

    registration = get_object_or_404(_scoped_registrations(request).prefetch_related(_refunds_prefetch()), pk=pk)
    return render(
        request,
        "classes/admin/partials/registration_refunds_card.html",
        {
            "registration": registration,
            "viewer_has_refund_authority": has_refund_authority(request),
        },
    )


# --- Roster & waitlist management actions (shared teach + admin surface) -----


def _registration_manageable_or_403(request: HttpRequest, pk: int) -> Registration:
    """Fetch a registration the request may manage, or raise ``PermissionDenied``.

    Actual admins (preview-independent) manage any registration; everyone else
    must have the class in ``ClassOffering.objects.editable_by`` — the same
    population as the read gate: instructors for their own classes, guild
    leads/staff for their guild's classes, guild officers everywhere.
    """
    from django.core.exceptions import PermissionDenied

    registration = get_object_or_404(
        Registration.objects.select_related("class_offering", "member", "discount_code"), pk=pk
    )
    view_as = getattr(request, "view_as", None)
    if view_as is not None and view_as.has_actual("admin"):
        return registration
    member = getattr(request.user, "member", None)
    if (
        member is not None
        and ClassOffering.objects.editable_by(member).filter(pk=registration.class_offering_id).exists()
    ):
        return registration
    raise PermissionDenied("You don't have access to manage this registration.")


def _registration_row_response(request: HttpRequest, registration: Registration) -> HttpResponse:
    """Render the shared roster row partial for one registration (fresh, annotated)."""
    from hub.view_as import has_refund_authority

    offering = registration.class_offering
    reg = _roster_registrations(offering).get(pk=registration.pk)
    return render(
        request,
        "classes/partials/registration_row.html",
        {
            "reg": reg,
            "offering": offering,
            "can_manage": True,
            "can_move": _registration_move_form(request, offering) is not None,
            "viewer_has_refund_authority": has_refund_authority(request),
        },
    )


def _waitlist_row_response(request: HttpRequest, registration: Registration) -> HttpResponse:
    """Render the shared waitlist row partial for one registration (fresh)."""
    return render(
        request,
        "classes/partials/waitlist_row.html",
        {
            "reg": registration,
            "offering": registration.class_offering,
            "can_manage": True,
        },
    )


def _row_response(request: HttpRequest, registration: Registration) -> HttpResponse:
    """The right row partial for the surface that posted — ``row=wl`` targets a waitlist row."""
    if request.POST.get("row") == "wl":
        return _waitlist_row_response(request, registration)
    return _registration_row_response(request, registration)


@login_required
@require_POST
def registration_promote(request: HttpRequest, pk: int) -> HttpResponse:
    """Staff-pick a waitlisted person into the class — CONFIRMED immediately.

    Branches on the COMPUTED ``payment_due_cents`` (not the class's sticker
    price): due 0 → the plain promoted email goes out now; due > 0 → no email
    yet, the response opens the pay-link follow-up modal via ``HX-Trigger``.
    """
    from classes.exceptions import RegistrationStateError
    from hub.toast import trigger_client_event, trigger_toast

    registration = _registration_manageable_or_403(request, pk)
    try:
        registration.promote_from_waitlist(actor=request.user)
    except RegistrationStateError as exc:
        response = _waitlist_row_response(request, registration)
        trigger_toast(response, str(exc), "error")
        return response
    response = _waitlist_row_response(request, registration)
    if registration.payment_due_cents == 0:
        from classes.emails import send_waitlist_promoted

        send_waitlist_promoted(registration)
        trigger_toast(response, f"{registration.first_name} added to the class. Confirmation sent.", "success")
    else:
        trigger_toast(response, f"{registration.first_name} added to the class.", "success")
        trigger_client_event(response, "promote-followup", {"pk": registration.pk})
    return response


@login_required
def registration_promote_followup(request: HttpRequest, pk: int) -> HttpResponse:
    """GET partial — the pay-link follow-up modal body for one just-promoted row."""
    registration = _registration_manageable_or_403(request, pk)
    return render(
        request,
        "classes/partials/promote_followup_body.html",
        {
            "registration": registration,
            "amount_due_dollars": f"{registration.balance_due_cents / 100:.2f}",
        },
    )


@login_required
@require_POST
def registration_promote_notify(request: HttpRequest, pk: int) -> HttpResponse:
    """The follow-up modal's choice endpoint: ``send`` the pay link or ``skip`` to the plain email.

    ``skip`` is a 204 no-op when either promoted email already went out
    (``payment_link_sent_at`` set OR the ``reg:{pk}:promoted`` delivery exists) —
    the modal-close fallback can never stack a second email onto an explicit Send.
    """
    from classes.exceptions import RegistrationStateError
    from core.models import EventDelivery
    from hub.toast import trigger_toast

    registration = _registration_manageable_or_403(request, pk)
    choice = request.POST.get("choice", "")
    if choice == "send":
        from classes.emails import send_payment_link_email

        response = HttpResponse(status=204)
        try:
            send_payment_link_email(registration, actor=request.user)
        except RegistrationStateError as exc:
            trigger_toast(response, str(exc), "error")
            return response
        trigger_toast(response, f"Payment link sent to {registration.email}.", "success")
        return response
    if choice == "skip":
        already_notified = (
            registration.payment_link_sent_at is not None
            or EventDelivery.objects.filter(
                event_key="waitlist_promoted", period=f"reg:{registration.pk}:promoted"
            ).exists()
        )
        if already_notified:
            return HttpResponse(status=204)
        from classes.emails import send_waitlist_promoted

        send_waitlist_promoted(registration)
        response = HttpResponse(status=204)
        trigger_toast(response, "Confirmation sent, no payment link.", "success")
        return response
    return HttpResponse("Unknown choice.", status=400)


@login_required
@require_POST
def registration_send_payment_link(request: HttpRequest, pk: int) -> HttpResponse:
    """Send (or re-send) the payment-link email from a roster row or the detail page."""
    from classes.exceptions import RegistrationStateError
    from classes.emails import send_payment_link_email
    from hub.toast import trigger_toast

    registration = _registration_manageable_or_403(request, pk)
    is_htmx = request.headers.get("HX-Request") == "true"
    try:
        send_payment_link_email(registration, actor=request.user)
    except RegistrationStateError as exc:
        if not is_htmx:
            messages.error(request, str(exc))
            return redirect("classes:admin_registration_detail", pk=pk)
        response = _row_response(request, registration)
        trigger_toast(response, str(exc), "error")
        return response
    if not is_htmx:
        messages.success(request, f"Payment link sent to {registration.email}.")
        return redirect("classes:admin_registration_detail", pk=pk)
    response = _row_response(request, registration)
    trigger_toast(response, f"Payment link sent to {registration.email}.", "success")
    return response


@login_required
@require_POST
def registration_mark_paid(request: HttpRequest, pk: int) -> HttpResponse:
    """Record a by-hand payment (cash, comped) for an unpaid promoted registration."""
    from classes.exceptions import RegistrationStateError
    from hub.toast import trigger_toast

    registration = _registration_manageable_or_403(request, pk)
    is_htmx = request.headers.get("HX-Request") == "true"
    try:
        registration.mark_paid(actor=request.user, note=request.POST.get("note", ""))
    except RegistrationStateError as exc:
        if not is_htmx:
            messages.error(request, str(exc))
            return redirect("classes:admin_registration_detail", pk=pk)
        response = _row_response(request, registration)
        trigger_toast(response, str(exc), "error")
        return response
    if not is_htmx:
        messages.success(request, "Marked paid.")
        return redirect("classes:admin_registration_detail", pk=pk)
    response = _row_response(request, registration)
    trigger_toast(response, "Marked paid.", "success")
    return response


@login_required
@require_POST
def registration_remove(request: HttpRequest, pk: int) -> HttpResponse:
    """Staff-remove a registrant (seat-holder or waitlister) behind the confirm modal."""
    from classes.exceptions import RegistrationStateError
    from hub.toast import trigger_toast

    registration = _registration_manageable_or_403(request, pk)
    was_waitlisted = registration.status == Registration.Status.WAITLISTED
    try:
        registration.remove_by_staff(actor=request.user, reason=request.POST.get("reason", ""))
    except RegistrationStateError as exc:
        response = _row_response(request, registration)
        trigger_toast(response, str(exc), "error")
        return response
    response = _row_response(request, registration)
    where = "waitlist" if was_waitlisted else "class"
    trigger_toast(response, f"{registration.first_name} removed from the {where}.", "success")
    return response


@login_required
@require_POST
def registration_move(request: HttpRequest, pk: int) -> HttpResponse:
    """Move a student to another class from a roster row (teach + admin Registrations tabs).

    Gating is stricter than the other roster actions: actual admins may move
    anyone anywhere upcoming, but a non-admin must be the source class's own
    instructor (``_teach_class_or_404`` semantics — guild leads/officers who can
    otherwise manage the roster get a 403), and their form only offers other
    upcoming classes they instruct, so a crafted POST at someone else's class
    fails validation. Plain POST + redirect: after a move the row belongs to a
    different roster, so an in-place row swap would render a stale table.
    """
    from django.core.exceptions import PermissionDenied

    from classes.forms import RegistrationMoveForm

    registration = get_object_or_404(Registration.objects.select_related("class_offering"), pk=pk)
    source = registration.class_offering
    view_as = getattr(request, "view_as", None)
    is_admin = view_as is not None and view_as.has_actual("admin")
    if is_admin:
        form = RegistrationMoveForm(request.POST, current=source)
    else:
        member = getattr(request.user, "member", None)
        if member is None or source.instructor_id != member.pk:
            raise PermissionDenied("You don't have access to move this registration.")
        form = RegistrationMoveForm(request.POST, current=source, instructor=member)
    if form.is_valid():
        actor = request.user if request.user.is_authenticated else None
        registration.move_to(form.cleaned_data["target"], actor=actor)
        messages.success(request, f"{registration.first_name} moved to {form.cleaned_data['target'].title}.")
    else:
        # Surface the form's own message ("That class is full.", invalid choice) — a
        # generic line would hide why the move bounced.
        first_error = next(iter(form.errors.values()))[0] if form.errors else "Could not move the student."
        messages.error(request, str(first_error))
    if is_admin:
        return redirect("classes:admin_class_registrations", pk=source.pk)
    return redirect("classes:teach_class_registrations", pk=source.pk)


@classes_admin_access_required
def admin_discount_codes(request: HttpRequest) -> HttpResponse:
    table = prepare_table(
        request,
        DiscountCode.objects.all(),
        search_fields=["code", "description"],
        default_sort="code",
    )
    return render(
        request,
        "classes/admin/discount_codes.html",
        {"active_tab": "discount_codes", **table},
    )


@classes_admin_access_required
def admin_discount_code_create(request: HttpRequest) -> HttpResponse:
    scoped_to: ClassOffering | None = None
    raw_class = request.GET.get("class") or request.POST.get("class")
    if raw_class:
        try:
            scoped_to = ClassOffering.objects.get(pk=int(raw_class))
        except (ClassOffering.DoesNotExist, ValueError, TypeError):
            scoped_to = None
    form = DiscountCodeForm(request.POST or None, scoped_to=scoped_to, created_by=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Discount code created.")
        if scoped_to is not None:
            return redirect("classes:admin_class_discount_codes", pk=scoped_to.pk)
        return redirect("classes:admin_discount_codes")
    return render(
        request,
        "classes/admin/discount_code_form.html",
        {
            "active_tab": "discount_codes",
            "form": form,
            "mode": "create",
            "scoped_to": scoped_to,
        },
    )


@classes_admin_access_required
def admin_discount_code_edit(request: HttpRequest, pk: int) -> HttpResponse:
    code = get_object_or_404(DiscountCode, pk=pk)
    form = DiscountCodeForm(request.POST or None, instance=code)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Discount code updated.")
        return redirect("classes:admin_discount_codes")
    return render(
        request,
        "classes/admin/discount_code_form.html",
        {"active_tab": "discount_codes", "form": form, "code": code, "mode": "edit"},
    )


@classes_admin_access_required
def admin_discount_code_delete(request: HttpRequest, pk: int) -> HttpResponse:
    code = get_object_or_404(DiscountCode, pk=pk)
    if request.method == "POST":
        code.delete()
        messages.success(request, "Discount code deleted.")
    return redirect("classes:admin_discount_codes")


@login_required
@require_POST
def admin_discount_code_approve(request: HttpRequest, pk: int) -> HttpResponse:
    """Toggle a code's approval. Admins may approve/un-approve any code; a member
    with the self-approve permission may approve only their own — enforced by
    ``DiscountCode.can_be_approved_by``."""
    code = get_object_or_404(DiscountCode, pk=pk)
    if not code.can_be_approved_by(request.user):
        return HttpResponseForbidden("You don't have permission to approve this discount code.")
    if code.is_approved:
        code.unapprove()
        messages.success(request, f"Discount code {code.code} unapproved.")
    else:
        code.approve(request.user)
        messages.success(request, f"Discount code {code.code} approved.")
    return redirect("classes:admin_discount_codes")


@classes_admin_access_required
def admin_registration_questions(request: HttpRequest) -> HttpResponse:
    table = prepare_table(
        request,
        RegistrationQuestion.objects.all(),
        search_fields=["prompt"],
        default_sort="sort_order",
    )
    return render(
        request,
        "classes/admin/registration_questions.html",
        {"active_tab": "questions", **table},
    )


@classes_admin_access_required
def admin_registration_question_create(request: HttpRequest) -> HttpResponse:
    form = RegistrationQuestionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Registration question created.")
        return redirect("classes:admin_registration_questions")
    return render(
        request,
        "classes/admin/registration_question_form.html",
        {"active_tab": "questions", "form": form, "mode": "create"},
    )


@classes_admin_access_required
def admin_registration_question_edit(request: HttpRequest, pk: int) -> HttpResponse:
    question = get_object_or_404(RegistrationQuestion, pk=pk)
    form = RegistrationQuestionForm(request.POST or None, instance=question)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Registration question updated.")
        return redirect("classes:admin_registration_questions")
    return render(
        request,
        "classes/admin/registration_question_form.html",
        {"active_tab": "questions", "form": form, "question": question, "mode": "edit"},
    )


@classes_admin_access_required
def admin_registration_question_delete(request: HttpRequest, pk: int) -> HttpResponse:
    question = get_object_or_404(RegistrationQuestion, pk=pk)
    if request.method == "POST":
        question.delete()
        messages.success(request, "Registration question deleted.")
    return redirect("classes:admin_registration_questions")


@classes_admin_access_required
def admin_settings_hub(request: HttpRequest) -> HttpResponse:
    """Landing page that groups the rarely-touched config areas."""
    return render(request, "classes/admin/settings_hub.html", {"active_tab": "settings"})


@admin_required
def admin_settings(request: HttpRequest) -> HttpResponse:
    settings_obj = ClassSettings.load()
    form = ClassSettingsForm(request.POST or None, instance=settings_obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Settings saved.")
        return redirect("classes:admin_settings")
    return render(
        request,
        "classes/admin/settings.html",
        {"active_tab": "settings", "form": form},
    )
