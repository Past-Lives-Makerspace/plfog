"""Views for the Classes app — admin tabs, public portal, instructor profile pages."""

from __future__ import annotations

import json
from functools import wraps
from typing import TYPE_CHECKING, Any, Callable

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.core.paginator import Paginator
from django.db.models import Count, F, Min, Q
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

if TYPE_CHECKING:
    from membership.models import Member

from classes.emails import (
    send_admin_registration_notification,
    send_class_review_decision,
    send_class_review_requests,
    send_instructor_registration_notification,
    send_registration_confirmation,
    send_waitlist_joined_confirmation,
)
from classes.table import prepare_table
from classes.forms import (
    CategoryForm,
    ClassOfferingForm,
    ClassReviewDecisionForm,
    ClassSessionFormSet,
    ClassSettingsForm,
    DiscountCodeForm,
    TeachClassOfferingForm,
    TeachProfileForm,
    RegistrationForm,
    RegistrationQuestionForm,
)
from classes.models import (
    Category,
    ClassApproval,
    ClassImage,
    ClassOffering,
    ClassSettings,
    CmsActivity,
    DiscountCode,
    Registration,
    RegistrationQuestion,
)
from core.models import SiteConfiguration

_ViewFunc = Callable[..., HttpResponse]


def _browsable_classes() -> Any:
    """Published, non-private classes with at least one upcoming session.

    Flexible-scheduling classes are always included (no fixed session dates).
    Classes whose last session has already passed are excluded.
    Ordered by soonest upcoming session; flexible/TBA classes sort to the end.
    """
    now = timezone.now()
    return (
        ClassOffering.objects.public()
        .filter(Q(scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE) | Q(sessions__starts_at__gte=now))
        .select_related("category", "instructor")
        .prefetch_related("sessions")
        .annotate(first_session_at=Min("sessions__starts_at", filter=Q(sessions__starts_at__gte=now)))
        .order_by(F("first_session_at").asc(nulls_last=True), "title")
        .distinct()
    )


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

    return qs


def public_list(request: HttpRequest) -> HttpResponse:
    """Public portal — hero + sticky category filter + grouped class cards.

    Supports HTMX partial swaps: when the request carries an ``HX-Request``
    header, returns just the results grid so the filter form can update the
    page in place without a full reload. The querystring is the source of
    truth for filter state; ``hx-push-url`` keeps the URL shareable.
    """
    settings_obj = ClassSettings.load()

    selected_category_slug = request.GET.get("category", "").strip()
    selected_instructor_slugs = [s for s in request.GET.getlist("instructor") if s]
    members_only = request.GET.get("members_only") == "1"
    free_only = request.GET.get("free") == "1"
    upcoming_only = request.GET.get("upcoming") == "1"

    classes_qs = _apply_browse_filters(_browsable_classes(), request)

    # Category chips and per-category counts always reflect the unfiltered
    # universe of browsable classes so users can see what else is out there.
    category_counts: dict[int, int] = {}
    for offering in _browsable_classes():
        category_counts[offering.category_id] = category_counts.get(offering.category_id, 0) + 1
    categories = [cat for cat in Category.objects.all() if category_counts.get(cat.id)]
    for cat in categories:
        cat.class_count = category_counts[cat.id]  # type: ignore[attr-defined]

    # Instructors-for-filter: members who teach at least one browsable class.
    from membership.models import Member as MemberModel

    instructors_for_filter = list(
        MemberModel.objects.filter(classes__in=_browsable_classes(), instructor_slug__gt="")
        .distinct()
        .order_by("full_legal_name")
    )

    paginator = Paginator(classes_qs, 25)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    # Strip 'page' from the querystring so pagination links can append it cleanly.
    filter_qs = request.GET.copy()
    filter_qs.pop("page", None)
    filter_querystring = filter_qs.urlencode()

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

    context = {
        "settings_obj": settings_obj,
        "site_config": SiteConfiguration.load(),
        "categories": categories,
        "selected_category_slug": selected_category_slug,
        "selected_instructor_slugs": selected_instructor_slugs,
        "instructors_for_filter": instructors_for_filter,
        "min_price": request.GET.get("min_price", ""),
        "max_price": request.GET.get("max_price", ""),
        "members_only": members_only,
        "free_only": free_only,
        "upcoming_only": upcoming_only,
        "active_filter_count": active_filter_count,
        "page_obj": page_obj,
        "paginator": paginator,
        "filter_querystring": filter_querystring,
        "total_classes": paginator.count,
        "total_instructors": classes_qs.values("instructor_id").exclude(instructor_id__isnull=True).distinct().count(),
        "total_categories": len(categories),
    }

    # HTMX partial: return just the results grid so the filter form can swap
    # in place without rerendering hero + filter chrome.
    if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted"):
        return render(request, "classes/public/_list_results.html", context)
    return render(request, "classes/public/list.html", context)


def public_category(request: HttpRequest, slug: str) -> HttpResponse:
    """Public category landing — same layout as list, pre-filtered."""
    category = get_object_or_404(Category, slug=slug)
    request.GET = request.GET.copy()
    request.GET["category"] = category.slug
    return public_list(request)


def public_class_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """Full class detail page — schedule, info grid, (future: registration form)."""
    offering = get_object_or_404(
        ClassOffering.objects.public().select_related("category", "instructor").prefetch_related("sessions"),
        slug=slug,
    )
    settings_obj = ClassSettings.load()
    member_price_cents = offering.member_price_cents
    upcoming_sessions = list(offering.sessions.filter(starts_at__gte=timezone.now()).order_by("starts_at"))
    related_offerings = list(
        ClassOffering.objects.public()
        .filter(category=offering.category)
        .exclude(pk=offering.pk)
        .select_related("instructor")
        .order_by("-created_at")[:3]
    )
    return render(
        request,
        "classes/public/detail.html",
        {
            "offering": offering,
            "settings_obj": settings_obj,
            "site_config": SiteConfiguration.load(),
            "upcoming_sessions": upcoming_sessions,
            "member_price_cents": member_price_cents,
            "spots_remaining": offering.spots_remaining,
            "related_offerings": related_offerings,
        },
    )


def public_instructor(request: HttpRequest, slug: str) -> HttpResponse:
    """Public instructor profile — bio, photo, current + past classes."""
    from membership.models import Member as MemberModel

    instructor = get_object_or_404(MemberModel, instructor_slug=slug, status=MemberModel.Status.ACTIVE)
    now = timezone.now()
    current_classes = (
        ClassOffering.objects.public()
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
    """Verified Member matching this email, or None."""
    from membership.models import Member

    return (
        Member.objects.filter(
            user__emailaddress__email__iexact=email,
            user__emailaddress__verified=True,
        )
        .distinct()
        .first()
    )


def _registration_initial_for_user(user: "AbstractBaseUser | AnonymousUser | None") -> dict[str, str]:
    """Pre-fill values pulled from the logged-in user's Member record."""
    if not user or not user.is_authenticated:
        return {}
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
    settings_obj = ClassSettings.load()

    # Waitlist intent: ?waitlist=1 (offered when the class is sold out) routes
    # to the no-charge waitlist branch below. Forced on automatically when the
    # class has no spots left so we never hide the option from a registrant
    # who lands here from a stale link.
    is_waitlist = request.GET.get("waitlist") == "1" or offering.spots_remaining <= 0

    # Two-pass form: first POST validates email so we can detect a member
    # before computing price, then re-binds to surface the discounted total.
    # GET requests pre-fill from the logged-in user's Member record when present.
    bound_email = (request.POST.get("email") or "").strip() if request.method == "POST" else ""
    member = _member_for_email(bound_email) if bound_email else None
    initial = {} if request.method == "POST" else _registration_initial_for_user(request.user)

    form = RegistrationForm(
        request.POST or None,
        offering=offering,
        settings_obj=settings_obj,
        member=member,
        client_ip=_client_ip(request),
        initial=initial,
        is_waitlist=is_waitlist,
    )

    if request.method == "POST" and form.is_valid() and is_waitlist:
        # The form sets status=WAITLISTED on save (see RegistrationForm.save), so the
        # WAITLIST_JOINED activity is logged at creation time.
        registration = form.save()
        send_waitlist_joined_confirmation(registration)
        messages.success(
            request,
            f"You're on the waitlist for {offering.title}. We'll email you if a spot opens.",
        )
        return redirect("classes:my_registration", token=registration.self_serve_token)

    if request.method == "POST" and form.is_valid():
        registration = form.save()
        final_price = form.compute_final_price_cents()

        if final_price == 0:
            # Free class — confirm + email immediately, no Stripe round-trip.
            registration.status = Registration.Status.CONFIRMED
            registration.confirmed_at = timezone.now()
            registration.amount_paid_cents = 0
            registration.save(update_fields=["status", "confirmed_at", "amount_paid_cents"])
            if registration.discount_code_id:
                _bump_discount_use_count(registration.discount_code_id)
                _log_discount_redeemed(registration)
            send_registration_confirmation(registration)
            send_instructor_registration_notification(registration)
            _instructor = registration.class_offering.instructor
            if _instructor is not None and _instructor.user is not None:
                from core import notifications

                notifications.dispatch(
                    "instructor_new_registration",
                    [_instructor.user],
                    title="New registration",
                    body=registration.class_offering.title,
                    url="/classes/teach/",
                )
            send_admin_registration_notification(registration)
            from classes.services.mailchimp_subscribe import subscribe_registration

            subscribe_registration(registration)
            return redirect("classes:register_success", slug=offering.slug)

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

        try:
            checkout = stripe_utils.create_class_checkout_session(
                amount_cents=final_price,
                product_name=offering.title,
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

    member_price_cents = offering.member_price_cents

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
    can_self_cancel = registration.status in {
        Registration.Status.PENDING,
        Registration.Status.CONFIRMED,
        Registration.Status.WAITLISTED,
    } and (not upcoming_sessions or upcoming_sessions[0].starts_at > timezone.now())
    return render(
        request,
        "classes/public/my_registration.html",
        {
            "registration": registration,
            "offering": offering,
            "upcoming_sessions": upcoming_sessions,
            "can_self_cancel": can_self_cancel,
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
    registration.cancel(reason="self-serve")
    messages.success(request, "Your registration is cancelled.")
    return redirect("classes:my_registration", token=token)


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
        payload={"code": registration.discount_code.code},
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


def teaching_member_required(view_func: _ViewFunc) -> _ViewFunc:
    """Decorator: Teaching portal access — any active logged-in member may teach."""

    @wraps(view_func)
    @login_required
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        from membership.models import Member as MemberModel

        member = MemberModel.objects.filter(user=request.user, status=MemberModel.Status.ACTIVE).first()
        if member is None:
            return HttpResponseForbidden("An active member account is required to access the teaching portal.")
        request.teaching_member = member  # type: ignore[attr-defined]
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


@teaching_member_required
def teach_dashboard(request: HttpRequest) -> HttpResponse:
    """My classes — list view for the logged-in teaching member."""
    teaching_member: Member = request.teaching_member  # type: ignore[attr-defined]
    classes = (
        ClassOffering.objects.for_instructor(teaching_member)
        .select_related("category")
        .annotate(registration_count=Count("registrations"))
        .order_by("-created_at")
    )
    return render(
        request,
        "classes/teach/classes_list.html",
        {
            "active_tab": "classes",
            "instructor": teaching_member,
            "classes": classes,
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
        offering.add_gallery_images(request.FILES.getlist("gallery_images"))
        submit_now = request.POST.get("action") == "submit"
        if submit_now:
            offering.submit_for_review()
            messages.success(request, f"Submitted “{offering.title}” for admin review.")
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
        ClassOffering.objects.filter(instructor=teaching_member).prefetch_related("gallery_images"),
        pk=pk,
    )
    if offering.status in {ClassOffering.Status.PUBLISHED, ClassOffering.Status.ARCHIVED}:
        messages.info(request, "Published and archived classes can only be edited by an admin.")
        return redirect("classes:teach_dashboard")
    form = TeachClassOfferingForm(
        request.POST or None, request.FILES or None, instance=offering, teaching_member=teaching_member
    )
    formset = ClassSessionFormSet(request.POST or None, instance=offering, prefix="sessions")
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        offering = form.save()
        formset.save()
        submit_now = request.POST.get("action") == "submit"
        if submit_now and offering.status == ClassOffering.Status.DRAFT:
            offering.submit_for_review()
            messages.success(request, f"Submitted “{offering.title}” for admin review.")
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
    )


@teaching_member_required
def teach_class_submit(request: HttpRequest, pk: int) -> HttpResponse:
    """Transition a draft to 'pending review'."""
    teaching_member: Member = request.teaching_member  # type: ignore[attr-defined]
    offering = get_object_or_404(ClassOffering.objects.filter(instructor=teaching_member), pk=pk)
    if request.method == "POST" and offering.status == ClassOffering.Status.DRAFT:
        approvals = offering.submit_for_review()
        send_class_review_requests(offering, approvals)
        roles_label = " + ".join(a.get_role_display() for a in approvals)
        messages.success(
            request,
            f"Submitted “{offering.title}” for review by {roles_label}.",
        )
    return redirect("classes:teach_dashboard")


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
        messages.error(request, first_error)
        return redirect("classes:teach_registrations")
    message = form.send()
    messages.success(
        request,
        f"Sent ‘{message.subject}’ to {message.recipient_count} recipient(s).",
    )
    return redirect("classes:teach_registrations")


@teaching_member_required
def teach_discount_codes(request: HttpRequest) -> HttpResponse:
    """Discount codes — managed by teaching members from the Teaching portal.

    Codes are currently shared across all classes (no per-instructor
    ownership on the DiscountCode model), so any teaching member sees every code.
    If that becomes a problem, add an ``owner`` FK and filter here.
    """
    teaching_member: Member = request.teaching_member  # type: ignore[attr-defined]
    codes = DiscountCode.objects.all()
    return render(
        request,
        "classes/teach/discount_codes.html",
        {
            "active_tab": "discount_codes",
            "instructor": teaching_member,
            "codes": codes,
        },
    )


@teaching_member_required
def teach_discount_code_create(request: HttpRequest) -> HttpResponse:
    teaching_member: Member = request.teaching_member  # type: ignore[attr-defined]
    scoped_to: ClassOffering | None = None
    raw_class = request.GET.get("class") or request.POST.get("class")
    if raw_class:
        try:
            scoped_to = ClassOffering.objects.filter(instructor=teaching_member).get(pk=int(raw_class))
        except (ClassOffering.DoesNotExist, ValueError, TypeError):
            scoped_to = None
    form = DiscountCodeForm(request.POST or None, scoped_to=scoped_to, created_by=request.user)
    if request.method == "POST" and form.is_valid():
        code = form.save(commit=False)
        # Class-scoped codes created by the offering's own teaching member are
        # auto-approved by DiscountCodeForm.save. Other teaching member codes
        # (global, or scoped to another instructor's class) need admin review.
        if scoped_to is None:
            code.is_approved = False
        if scoped_to is not None and not code.class_offering_id:
            code.class_offering = scoped_to
        if not code.created_by_id:
            code.created_by = request.user
        code.save()
        if code.is_approved:
            messages.success(request, "Discount code created and active for this class.")
        else:
            messages.success(request, "Discount code created — an admin will review and approve it.")
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
def teach_discount_code_edit(request: HttpRequest, pk: int) -> HttpResponse:
    teaching_member: Member = request.teaching_member  # type: ignore[attr-defined]
    code = get_object_or_404(DiscountCode, pk=pk)
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
def teach_discount_code_delete(request: HttpRequest, pk: int) -> HttpResponse:
    code = get_object_or_404(DiscountCode, pk=pk)
    if request.method == "POST":
        code.delete()
        messages.success(request, "Discount code deleted.")
    return redirect("classes:teach_discount_codes")


@teaching_member_required
def teach_profile(request: HttpRequest) -> HttpResponse:
    teaching_member: Member = request.teaching_member  # type: ignore[attr-defined]
    form = TeachProfileForm(request.POST or None, request.FILES or None, instance=teaching_member)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profile updated.")
        return redirect("classes:teach_profile")
    return render(
        request,
        "classes/teach/profile.html",
        {"active_tab": "profile", "instructor": teaching_member, "form": form},
    )


@login_required
def class_preview(request: HttpRequest, pk: int) -> HttpResponse:
    """Preview the public detail page for any class — including drafts.

    Access: the assigned instructor (owner) OR any actual admin. The view
    renders ``classes/public/detail.html`` but skips the ``status=published``
    filter so drafts/pending can be reviewed before going live. A banner is
    rendered at the top so it's clear this is a preview.
    """
    offering = get_object_or_404(
        ClassOffering.objects.select_related("category", "instructor").prefetch_related("sessions", "gallery_images"),
        pk=pk,
    )
    from membership.models import Member as MemberModel

    view_as = getattr(request, "view_as", None)
    is_admin = view_as is not None and view_as.has_actual("admin")
    user_member = MemberModel.objects.filter(user=request.user).first()
    is_owner = user_member is not None and offering.instructor_id == user_member.pk
    if not (is_admin or is_owner):
        return HttpResponseForbidden("You can only preview your own classes.")
    settings_obj = ClassSettings.load()
    member_price_cents = offering.member_price_cents
    upcoming_sessions = list(offering.sessions.filter(starts_at__gte=timezone.now()).order_by("starts_at"))
    return render(
        request,
        "classes/public/detail.html",
        {
            "offering": offering,
            "settings_obj": settings_obj,
            "site_config": SiteConfiguration.load(),
            "upcoming_sessions": upcoming_sessions,
            "member_price_cents": member_price_cents,
            "spots_remaining": offering.spots_remaining,
            "is_preview": True,
        },
    )


@classes_admin_access_required
def admin_classes(request: HttpRequest) -> HttpResponse:
    valid_statuses = {choice.value for choice in ClassOffering.Status}
    status_filter = request.GET.get("status", "").strip()
    if status_filter not in valid_statuses:
        status_filter = ""
    instructor_filter = request.GET.get("instructor", "").strip()

    base = ClassOffering.objects.select_related("instructor", "category").annotate(
        registration_count=Count("registrations")
    )
    qs = base.filter(status=status_filter) if status_filter else base
    if instructor_filter:
        qs = qs.filter(instructor_id=instructor_filter)

    status_counts = {row["status"]: row["count"] for row in base.values("status").annotate(count=Count("pk"))}
    filters = [("", "All", base.count())] + [
        (choice.value, choice.label, status_counts.get(choice.value, 0)) for choice in ClassOffering.Status
    ]
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
            "status_filters": filters,
            "selected_status": status_filter,
            "instructors": instructors,
            "selected_instructor": instructor_filter,
            **table,
        },
    )


@classes_admin_access_required
def admin_class_create(request: HttpRequest) -> HttpResponse:
    form = ClassOfferingForm(request.POST or None, request.FILES or None)
    session_formset = ClassSessionFormSet(request.POST or None, prefix="sessions")
    if request.method == "POST" and form.is_valid() and session_formset.is_valid():
        offering = form.save(commit=False)
        offering.status = ClassOffering.Status.PUBLISHED
        offering.save()
        session_formset.instance = offering
        session_formset.save()
        offering.add_gallery_images(request.FILES.getlist("gallery_images"))
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


@classes_admin_access_required
def admin_class_edit(request: HttpRequest, pk: int) -> HttpResponse:
    offering = get_object_or_404(ClassOffering.objects.prefetch_related("gallery_images", "sessions"), pk=pk)
    form = ClassOfferingForm(request.POST or None, request.FILES or None, instance=offering)
    session_formset = ClassSessionFormSet(request.POST or None, instance=offering, prefix="sessions")
    if request.method == "POST" and form.is_valid() and session_formset.is_valid():
        form.save()
        session_formset.save()
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
        },
    )


@classes_admin_access_required
def admin_class_detail(request: HttpRequest, pk: int) -> HttpResponse:
    offering = get_object_or_404(
        ClassOffering.objects.select_related("instructor", "category")
        .prefetch_related("sessions")
        .annotate(registration_count=Count("registrations")),
        pk=pk,
    )
    registrations = (
        offering.registrations.select_related("member")
        .prefetch_related("custom_answers__question")
        .order_by("-registered_at")
    )
    active_count = registrations.exclude(
        status__in=[Registration.Status.CANCELLED, Registration.Status.REFUNDED],
    ).count()
    waitlist_registrations = list(
        offering.registrations.filter(status=Registration.Status.WAITLISTED).order_by("registered_at")
    )
    return render(
        request,
        "classes/admin/class_detail.html",
        {
            "active_tab": "classes",
            "waitlist_registrations": waitlist_registrations,
            "offering": offering,
            "registrations": registrations,
            "active_registration_count": active_count,
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
        messages.error(request, first_error)
        return redirect("classes:admin_class_detail", pk=pk)
    message = form.send(sender_member=sender_member)
    messages.success(
        request,
        f"Sent '{message.subject}' to {message.recipient_count} recipient(s).",
    )
    return redirect("classes:admin_class_detail", pk=pk)


@classes_admin_access_required
def admin_class_approve(request: HttpRequest, pk: int) -> HttpResponse:
    """Quick-approve from the admin class detail page.

    Records an admin-role decision via ClassApproval; the offering publishes
    only when every required gate (admin + guild lead, if any) is satisfied.
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
        send_class_review_decision(offering, row)
        if offering.status == ClassOffering.Status.PUBLISHED:
            messages.success(request, f"{offering.title} is published.")
        else:
            messages.success(
                request,
                f"Admin approval recorded. Waiting on the remaining reviewer(s) before {offering.title} publishes.",
            )
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
        CmsActivity.Kind.REGISTRATION_CANCELLED,
        CmsActivity.Kind.REGISTRATION_REFUNDED,
    ],
    "waitlist": [
        CmsActivity.Kind.WAITLIST_JOINED,
        CmsActivity.Kind.WAITLIST_NOTIFIED,
        CmsActivity.Kind.WAITLIST_LEFT,
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

    qs = CmsActivity.objects.select_related(
        "class_offering",
        "registration",
        "actor",
    ).order_by("-created_at")

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
            "groups": [
                ("all", "All"),
                ("classes", "Classes"),
                ("registrations", "Registrations"),
                ("waitlist", "Waitlist"),
                ("discount_codes", "Discount codes"),
            ],
        },
    )


@classes_admin_access_required
def admin_class_review(request: HttpRequest, pk: int) -> HttpResponse:
    """Full reviewer page for admins. Mirrors the tokenized public review page."""
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
    approval = get_object_or_404(ClassApproval, token=token)
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
    """Shared logic for /classes/admin/<pk>/review/ and /classes/review/<token>/."""
    if approval is None:
        approval = offering.approvals.filter(role=role, decision="").order_by(
            "-created_at"
        ).first() or ClassApproval.objects.create(class_offering=offering, role=role)
    settings_obj = ClassSettings.load()
    upcoming_sessions = list(offering.sessions.filter(starts_at__gte=timezone.now()).order_by("starts_at"))
    history = list(offering.approvals.exclude(pk=approval.pk).order_by("-created_at"))

    form = ClassReviewDecisionForm(request.POST or None)
    if request.method == "POST" and not approval.decision and form.is_valid():
        approval.decide(
            form.cleaned_data["decision"],
            user=request.user if request.user.is_authenticated else None,
            notes=form.cleaned_data.get("notes", ""),
        )
        # refresh from DB to pick up the new state
        approval.refresh_from_db()
        offering.refresh_from_db()
        send_class_review_decision(offering, approval)
        messages.success(request, "Your decision has been recorded. Thanks for reviewing.")
        if token:
            return redirect("classes:class_review", token=token)
        return redirect("classes:admin_class_review", pk=offering.pk)

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
            "upcoming_sessions": upcoming_sessions,
            "is_tokenized": token is not None,
            "active_tab": "classes",
        },
    )


@classes_admin_access_required
def admin_class_archive(request: HttpRequest, pk: int) -> HttpResponse:
    offering = get_object_or_404(ClassOffering, pk=pk)
    if request.method == "POST":
        offering.archive()
        messages.success(request, f"{offering.title} archived.")
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
    offering = get_object_or_404(ClassOffering, pk=pk)
    file = request.FILES.get("image")
    if not file:
        return JsonResponse({"error": "No file provided."}, status=400)
    offering.image = file
    offering.hero_crop_x = None
    offering.hero_crop_y = None
    offering.hero_crop_w = None
    offering.hero_crop_h = None
    offering.save()
    return JsonResponse({"url": offering.image.url})


@classes_admin_access_required
@require_POST
def admin_class_image_upload(request: HttpRequest, pk: int) -> HttpResponse:
    offering = get_object_or_404(ClassOffering, pk=pk)
    if offering.gallery_images.count() >= 10:
        return JsonResponse({"error": "Maximum 10 gallery images."}, status=400)
    file = request.FILES.get("image")
    if not file:
        return JsonResponse({"error": "No file provided."}, status=400)
    if file.size > 3 * 1024 * 1024:
        return JsonResponse({"error": "Image must be under 3 MB."}, status=400)
    next_order = (offering.gallery_images.order_by("-sort_order").values_list("sort_order", flat=True).first() or 0) + 1
    img = ClassImage(class_offering=offering, image=file, sort_order=next_order)
    img.full_clean()
    img.save()
    return JsonResponse({"id": img.pk, "url": img.image.url, "alt_text": "", "sort_order": img.sort_order})


@classes_admin_access_required
@require_POST
def admin_class_image_reorder(request: HttpRequest, pk: int) -> HttpResponse:
    offering = get_object_or_404(ClassOffering, pk=pk)
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


@classes_admin_access_required
@require_POST
def admin_class_image_delete(request: HttpRequest, pk: int) -> HttpResponse:
    img = get_object_or_404(ClassImage, pk=pk)
    img.image.delete(save=False)
    img.delete()
    return JsonResponse({"ok": True})


@classes_admin_access_required
@require_POST
def admin_class_image_alt(request: HttpRequest, pk: int) -> HttpResponse:
    img = get_object_or_404(ClassImage, pk=pk)
    try:
        alt_text = json.loads(request.body)["alt_text"]
    except (json.JSONDecodeError, KeyError):
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
def admin_registrations(request: HttpRequest) -> HttpResponse:
    table = prepare_table(
        request,
        Registration.objects.select_related("class_offering", "member"),
        search_fields=["first_name", "last_name", "email", "class_offering__title"],
        default_sort="registered_at",
        default_dir="desc",
    )
    return render(
        request,
        "classes/admin/registrations.html",
        {"active_tab": "registrations", **table},
    )


@classes_admin_access_required
def admin_registration_detail(request: HttpRequest, pk: int) -> HttpResponse:
    registration = get_object_or_404(
        Registration.objects.select_related("class_offering", "member", "discount_code").prefetch_related(
            "waivers", "custom_answers__question"
        ),
        pk=pk,
    )
    return render(
        request,
        "classes/admin/registration_detail.html",
        {"active_tab": "registrations", "registration": registration},
    )


@classes_admin_access_required
def admin_registration_cancel(request: HttpRequest, pk: int) -> HttpResponse:
    registration = get_object_or_404(Registration, pk=pk)
    if request.method == "POST":
        registration.cancel(reason=request.POST.get("reason", ""))
        messages.success(request, "Registration cancelled.")
    return redirect("classes:admin_registration_detail", pk=pk)


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
            return redirect("classes:admin_class_detail", pk=scoped_to.pk)
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


@classes_admin_access_required
@require_POST
def admin_discount_code_approve(request: HttpRequest, pk: int) -> HttpResponse:
    code = get_object_or_404(DiscountCode, pk=pk)
    code.is_approved = not code.is_approved
    code.save(update_fields=["is_approved"])
    label = "approved" if code.is_approved else "unapproved"
    messages.success(request, f"Discount code {code.code} {label}.")
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
