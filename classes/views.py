"""Views for the Classes app — admin tabs, public portal, instructor profile pages."""

from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING, Any, Callable

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import Count, Min, Q
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

if TYPE_CHECKING:
    from membership.models import Member

from classes.emails import send_registration_confirmation
from classes.forms import (
    CategoryForm,
    ClassOfferingForm,
    ClassSessionFormSet,
    ClassSettingsForm,
    DiscountCodeForm,
    InstructorClassOfferingForm,
    InstructorProfileForm,
    PromoteUserToInstructorForm,
    RegistrationForm,
)
from classes.models import Category, ClassOffering, ClassSettings, DiscountCode, Instructor, Registration
from core.models import SiteConfiguration

_ViewFunc = Callable[..., HttpResponse]


def _browsable_classes() -> Any:
    """Published, non-private classes annotated with first upcoming session date.

    Every published, non-private class is shown — the template renders an
    "Upcoming dates TBA" placeholder when no future sessions exist, so a
    just-created class is visible immediately whether or not its schedule is
    finalized. Ordered by category sort, then soonest upcoming session
    (classes with no upcoming session sort to the bottom of each category).
    """
    now = timezone.now()
    return (
        ClassOffering.objects.public()
        .select_related("category", "instructor")
        .prefetch_related("sessions")
        .annotate(first_session_at=Min("sessions__starts_at", filter=Q(sessions__starts_at__gte=now)))
        .order_by("category__sort_order", "category__name", "first_session_at", "title")
    )


def public_list(request: HttpRequest) -> HttpResponse:
    """Public portal — hero + sticky category filter + grouped class cards."""
    settings_obj = ClassSettings.load()
    selected_category_slug = request.GET.get("category", "").strip()
    classes_qs = _browsable_classes()
    if selected_category_slug:
        classes_qs = classes_qs.filter(category__slug=selected_category_slug)
    classes = list(classes_qs)
    # Category chips show only categories with at least one browsable class.
    category_counts: dict[int, int] = {}
    for offering in _browsable_classes():
        category_counts[offering.category_id] = category_counts.get(offering.category_id, 0) + 1
    categories = [cat for cat in Category.objects.all() if category_counts.get(cat.id)]
    for cat in categories:
        cat.class_count = category_counts[cat.id]  # type: ignore[attr-defined]
    # Group classes by category for the rendered sections.
    grouped: dict[int, dict[str, Any]] = {}
    for offering in classes:
        bucket = grouped.setdefault(offering.category_id, {"category": offering.category, "classes": []})
        bucket["classes"].append(offering)
    category_sections = [grouped[cat.id] for cat in categories if cat.id in grouped]
    distinct_instructor_ids = {offering.instructor_id for offering in classes}
    return render(
        request,
        "classes/public/list.html",
        {
            "settings_obj": settings_obj,
            "site_config": SiteConfiguration.load(),
            "categories": categories,
            "selected_category_slug": selected_category_slug,
            "category_sections": category_sections,
            "total_classes": len(classes),
            "total_instructors": len(distinct_instructor_ids),
            "total_categories": len(categories),
        },
    )


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
    member_price_cents = None
    if offering.member_discount_pct:
        member_price_cents = int(offering.price_cents * (100 - offering.member_discount_pct) / 100)
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
        },
    )


def public_instructor(request: HttpRequest, slug: str) -> HttpResponse:
    """Public instructor profile — bio, photo, current + past classes."""
    instructor = get_object_or_404(Instructor, slug=slug, is_active=True)
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
    )

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
            send_registration_confirmation(registration)
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

    member_price_cents = None
    if offering.member_discount_pct:
        member_price_cents = int(offering.price_cents * (100 - offering.member_discount_pct) / 100)

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


def instructor_required(view_func: _ViewFunc) -> _ViewFunc:
    """Decorator: Teaching portal access.

    Active instructors pass through. Admins without an Instructor record get
    bounced to the Classes admin Instructors tab — they have a superset view
    over there, and the Teaching portal is per-instructor so there's no
    meaningful admin-scoped equivalent.
    """

    @wraps(view_func)
    @login_required
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        instructor = Instructor.objects.filter(user=request.user, is_active=True).first()
        if instructor is None:
            view_as = getattr(request, "view_as", None)
            if view_as is not None and view_as.has_actual("admin"):
                messages.info(
                    request,
                    "You don't have an Instructor profile — manage instructors here instead.",
                )
                return redirect("classes:admin_instructors")
            return HttpResponseForbidden("Instructor access required.")
        request.instructor = instructor  # type: ignore[attr-defined]
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


@instructor_required
def instructor_dashboard(request: HttpRequest) -> HttpResponse:
    """My classes — list view for the logged-in instructor."""
    instructor: Instructor = request.instructor  # type: ignore[attr-defined]
    classes = (
        ClassOffering.objects.for_instructor(instructor)
        .select_related("category")
        .annotate(registration_count=Count("registrations"))
        .order_by("-created_at")
    )
    return render(
        request,
        "classes/instructor/classes_list.html",
        {
            "active_tab": "classes",
            "instructor": instructor,
            "classes": classes,
        },
    )


def _render_instructor_class_form(
    request: HttpRequest,
    *,
    form: InstructorClassOfferingForm,
    formset: Any,
    instructor: Instructor,
    mode: str,
    offering: ClassOffering | None = None,
) -> HttpResponse:
    return render(
        request,
        "classes/instructor/class_form.html",
        {
            "active_tab": "classes",
            "instructor": instructor,
            "form": form,
            "formset": formset,
            "mode": mode,
            "offering": offering,
        },
    )


@instructor_required
def instructor_class_create(request: HttpRequest) -> HttpResponse:
    instructor: Instructor = request.instructor  # type: ignore[attr-defined]
    form = InstructorClassOfferingForm(request.POST or None, request.FILES or None, instructor=instructor)
    formset = ClassSessionFormSet(request.POST or None, prefix="sessions")
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        offering = form.save()
        formset.instance = offering
        formset.save()
        submit_now = request.POST.get("action") == "submit"
        if submit_now:
            offering.submit_for_review()
            messages.success(request, f"Submitted “{offering.title}” for admin review.")
        else:
            messages.success(request, f"Saved draft “{offering.title}”.")
        return redirect("classes:instructor_class_edit", pk=offering.pk)
    return _render_instructor_class_form(request, form=form, formset=formset, instructor=instructor, mode="create")


@instructor_required
def instructor_class_edit(request: HttpRequest, pk: int) -> HttpResponse:
    instructor: Instructor = request.instructor  # type: ignore[attr-defined]
    offering = get_object_or_404(
        ClassOffering.objects.filter(instructor=instructor),
        pk=pk,
    )
    if offering.status in {ClassOffering.Status.PUBLISHED, ClassOffering.Status.ARCHIVED}:
        messages.info(request, "Published and archived classes can only be edited by an admin.")
        return redirect("classes:instructor_dashboard")
    form = InstructorClassOfferingForm(
        request.POST or None, request.FILES or None, instance=offering, instructor=instructor
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
        return redirect("classes:instructor_class_edit", pk=offering.pk)
    return _render_instructor_class_form(
        request, form=form, formset=formset, instructor=instructor, mode="edit", offering=offering
    )


@instructor_required
def instructor_class_submit(request: HttpRequest, pk: int) -> HttpResponse:
    """Transition a draft to 'pending review'."""
    instructor: Instructor = request.instructor  # type: ignore[attr-defined]
    offering = get_object_or_404(ClassOffering.objects.filter(instructor=instructor), pk=pk)
    if request.method == "POST" and offering.status == ClassOffering.Status.DRAFT:
        offering.submit_for_review()
        messages.success(request, f"Submitted “{offering.title}” for admin review.")
    return redirect("classes:instructor_dashboard")


@instructor_required
def instructor_registrations(request: HttpRequest) -> HttpResponse:
    instructor: Instructor = request.instructor  # type: ignore[attr-defined]
    registrations = (
        Registration.objects.filter(class_offering__instructor=instructor)
        .select_related("class_offering", "member")
        .order_by("-registered_at")
    )
    return render(
        request,
        "classes/instructor/registrations.html",
        {
            "active_tab": "registrations",
            "instructor": instructor,
            "registrations": registrations,
        },
    )


@instructor_required
def instructor_discount_codes(request: HttpRequest) -> HttpResponse:
    """Discount codes — managed by instructors from the Teaching portal.

    Codes are currently shared across all classes (no per-instructor
    ownership on the DiscountCode model), so any instructor sees every code.
    If that becomes a problem, add an ``owner`` FK and filter here.
    """
    instructor: Instructor = request.instructor  # type: ignore[attr-defined]
    codes = DiscountCode.objects.all()
    return render(
        request,
        "classes/instructor/discount_codes.html",
        {
            "active_tab": "discount_codes",
            "instructor": instructor,
            "codes": codes,
        },
    )


@instructor_required
def instructor_discount_code_create(request: HttpRequest) -> HttpResponse:
    instructor: Instructor = request.instructor  # type: ignore[attr-defined]
    form = DiscountCodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Discount code created.")
        return redirect("classes:instructor_discount_codes")
    return render(
        request,
        "classes/instructor/discount_code_form.html",
        {"active_tab": "discount_codes", "instructor": instructor, "form": form, "mode": "create"},
    )


@instructor_required
def instructor_discount_code_edit(request: HttpRequest, pk: int) -> HttpResponse:
    instructor: Instructor = request.instructor  # type: ignore[attr-defined]
    code = get_object_or_404(DiscountCode, pk=pk)
    form = DiscountCodeForm(request.POST or None, instance=code)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Discount code updated.")
        return redirect("classes:instructor_discount_codes")
    return render(
        request,
        "classes/instructor/discount_code_form.html",
        {"active_tab": "discount_codes", "instructor": instructor, "form": form, "code": code, "mode": "edit"},
    )


@instructor_required
def instructor_discount_code_delete(request: HttpRequest, pk: int) -> HttpResponse:
    code = get_object_or_404(DiscountCode, pk=pk)
    if request.method == "POST":
        code.delete()
        messages.success(request, "Discount code deleted.")
    return redirect("classes:instructor_discount_codes")


@instructor_required
def instructor_profile(request: HttpRequest) -> HttpResponse:
    instructor: Instructor = request.instructor  # type: ignore[attr-defined]
    form = InstructorProfileForm(request.POST or None, request.FILES or None, instance=instructor)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profile updated.")
        return redirect("classes:instructor_profile")
    return render(
        request,
        "classes/instructor/profile.html",
        {"active_tab": "profile", "instructor": instructor, "form": form},
    )


@classes_admin_access_required
def admin_classes(request: HttpRequest) -> HttpResponse:
    valid_statuses = {choice.value for choice in ClassOffering.Status}
    status_filter = request.GET.get("status", "").strip()
    if status_filter not in valid_statuses:
        status_filter = ""
    base = ClassOffering.objects.select_related("instructor", "category").annotate(
        registration_count=Count("registrations")
    )
    classes = base.filter(status=status_filter) if status_filter else base
    classes = classes.order_by("-created_at")
    status_counts = {row["status"]: row["count"] for row in base.values("status").annotate(count=Count("pk"))}
    filters = [("", "All", base.count())] + [
        (choice.value, choice.label, status_counts.get(choice.value, 0)) for choice in ClassOffering.Status
    ]
    return render(
        request,
        "classes/admin/classes_list.html",
        {
            "active_tab": "classes",
            "classes": classes,
            "pending_count": ClassOffering.objects.pending_review().count(),
            "status_filters": filters,
            "selected_status": status_filter,
        },
    )


@classes_admin_access_required
def admin_class_create(request: HttpRequest) -> HttpResponse:
    form = ClassOfferingForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        offering = form.save(commit=False)
        offering.status = ClassOffering.Status.PUBLISHED
        offering.save()
        messages.success(request, f"{offering.title} is published.")
        return redirect("classes:admin_classes")
    return render(
        request,
        "classes/admin/class_form.html",
        {"active_tab": "classes", "form": form, "mode": "create"},
    )


@classes_admin_access_required
def admin_class_edit(request: HttpRequest, pk: int) -> HttpResponse:
    offering = get_object_or_404(ClassOffering, pk=pk)
    form = ClassOfferingForm(request.POST or None, request.FILES or None, instance=offering)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Class updated.")
        return redirect("classes:admin_class_detail", pk=offering.pk)
    return render(
        request,
        "classes/admin/class_form.html",
        {"active_tab": "classes", "form": form, "offering": offering, "mode": "edit"},
    )


@classes_admin_access_required
def admin_class_detail(request: HttpRequest, pk: int) -> HttpResponse:
    offering = get_object_or_404(ClassOffering, pk=pk)
    return render(
        request,
        "classes/admin/class_detail.html",
        {"active_tab": "classes", "offering": offering},
    )


@classes_admin_access_required
def admin_class_approve(request: HttpRequest, pk: int) -> HttpResponse:
    offering = get_object_or_404(ClassOffering, pk=pk)
    if request.method == "POST":
        offering.approve(request.user)
        messages.success(request, f"{offering.title} is published.")
    return redirect("classes:admin_class_detail", pk=offering.pk)


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
        messages.success(request, f"Deleted “{title}”.")
        return redirect("classes:admin_classes")
    return redirect("classes:admin_class_detail", pk=offering.pk)


@classes_admin_access_required
def admin_categories(request: HttpRequest) -> HttpResponse:
    categories = Category.objects.all()
    return render(
        request,
        "classes/admin/categories.html",
        {"active_tab": "categories", "categories": categories},
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
def admin_instructors(request: HttpRequest) -> HttpResponse:
    instructors = Instructor.objects.select_related("user").annotate(class_count=Count("classes"))
    return render(
        request,
        "classes/admin/instructors.html",
        {"active_tab": "instructors", "instructors": instructors},
    )


@classes_admin_access_required
def admin_instructor_promote(request: HttpRequest) -> HttpResponse:
    """Add an existing User as an Instructor — grants the role to a member account."""
    form = PromoteUserToInstructorForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        instructor = form.save()
        messages.success(request, f"{instructor.display_name} is now an instructor.")
        return redirect("classes:admin_instructors")
    return render(
        request,
        "classes/admin/instructor_promote.html",
        {"active_tab": "instructors", "form": form},
    )


@classes_admin_access_required
def admin_registrations(request: HttpRequest) -> HttpResponse:
    registrations = Registration.objects.select_related("class_offering", "member").order_by("-registered_at")
    return render(
        request,
        "classes/admin/registrations.html",
        {"active_tab": "registrations", "registrations": registrations},
    )


@classes_admin_access_required
def admin_registration_detail(request: HttpRequest, pk: int) -> HttpResponse:
    registration = get_object_or_404(
        Registration.objects.select_related("class_offering", "member").prefetch_related("waivers"),
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
    codes = DiscountCode.objects.all()
    return render(
        request,
        "classes/admin/discount_codes.html",
        {"active_tab": "discount_codes", "codes": codes},
    )


@classes_admin_access_required
def admin_discount_code_create(request: HttpRequest) -> HttpResponse:
    form = DiscountCodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Discount code created.")
        return redirect("classes:admin_discount_codes")
    return render(
        request,
        "classes/admin/discount_code_form.html",
        {"active_tab": "discount_codes", "form": form, "mode": "create"},
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
