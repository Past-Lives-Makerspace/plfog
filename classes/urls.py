from collections.abc import Callable
from typing import Any

from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import path, reverse
from django.views.generic.base import RedirectView

from classes import views, views_legacy_image
from core.features import gate_named

app_name = "classes"


def legacy_class_route(view: Callable[..., HttpResponse], merged_name: str) -> Callable[..., HttpResponse]:
    """One door onto the merged per-class screen for an old ``/classes/admin/<pk>/…`` path.

    A GET is answered with a **302** to the merged URL, query string and all, so a bookmark,
    an old email link and every ``?status=`` / ``?mine=1`` / ``?step=`` filter land where the
    screen now lives. A 302 and not a 301: a permanent redirect is a one-way door ``git
    revert`` cannot close, which is why ``core/middleware.py`` chose 302 for the calendar
    alias too.

    A POST is **dispatched straight to the merged view** rather than redirected.
    ``RedirectView`` maps ``post = get``, and three of these paths take POSTs
    (``admin_class_edit``, ``admin_class_emails``, ``admin_class_duplicate_run``) from a
    composer that posts to the current URL with no ``action`` attribute. An admin who had the
    composer open across the deploy would press Save, get a 302, and lose every session and
    FAQ row without an error — ``static/js/composer_draft.js`` keeps no formset rows. A
    dispatcher removes the question of which verb goes where.

    Args:
        view: The merged view this path now serves.
        merged_name: The merged URL name a GET redirects to.

    Returns:
        A view callable for :func:`django.urls.path`.
    """

    def dispatch(request: HttpRequest, pk: int, *args: Any, **kwargs: Any) -> HttpResponse:
        if request.method == "POST":
            return view(request, pk, *args, **kwargs)
        target = reverse(merged_name, kwargs={"pk": pk})
        query = request.META.get("QUERY_STRING", "")
        return HttpResponseRedirect(f"{target}?{query}" if query else target)

    dispatch.__name__ = f"legacy_{merged_name.rsplit(':', 1)[-1]}"
    dispatch.__qualname__ = dispatch.__name__
    return dispatch


urlpatterns = [
    # Public portal
    path("", views.public_list, name="public_list"),
    path("category/<slug:slug>/", views.public_category, name="public_category"),
    path("instructors/<slug:slug>/", views.public_instructor, name="public_instructor"),
    # Self-serve registration management (token-based, no auth)
    path("my/<str:token>/", views.my_registration, name="my_registration"),
    path("my/<str:token>/cancel/", views.my_registration_cancel, name="my_registration_cancel"),
    path("my/<str:token>/pay/", views.my_registration_pay, name="my_registration_pay"),
    # Teaching portal (member self-serve for instructors)
    path("teach/", views.teach_overview, name="teach_overview"),
    # Teach at Past Lives — the marketing page and apply-to-teach front door. Open to
    # any active member, so a locked deep link and an approved instructor both land here.
    path("teach/why/", views.teach_why, name="teach_why"),
    path("teach/apply/", views.teach_apply, name="teach_apply"),
    # The retired self-serve orientation. Teaching is admin-approved now, so its old
    # URL (linked from the Help Center and from old emails) permanently redirects.
    path(
        "teach/orientation/",
        RedirectView.as_view(pattern_name="classes:teach_why", permanent=True),
        name="teach_orientation",
    ),
    path("teach/classes/", views.teach_dashboard, name="teach_dashboard"),
    path("teach/classes/new/", views.teach_class_create, name="teach_class_create"),
    path("teach/classes/<int:pk>/edit/", views.teach_class_edit, name="teach_class_edit"),
    path("teach/classes/<int:pk>/submit/", views.teach_class_submit, name="teach_class_submit"),
    path(
        "teach/classes/<int:pk>/another-date-set/",
        views.teach_class_duplicate_run,
        name="teach_class_duplicate_run",
    ),
    path("teach/classes/<int:pk>/", views.teach_class_detail, name="teach_class_detail"),
    # Instructor-scoped hero + gallery endpoints (the edit pages' instant uploads).
    path("teach/classes/<int:pk>/hero/upload/", views.teach_class_hero_upload, name="teach_class_hero_upload"),
    path("teach/classes/<int:pk>/images/upload/", views.teach_class_image_upload, name="teach_class_image_upload"),
    path("teach/classes/<int:pk>/images/reorder/", views.teach_class_image_reorder, name="teach_class_image_reorder"),
    path("teach/images/<int:pk>/delete/", views.teach_class_image_delete, name="teach_class_image_delete"),
    path("teach/images/<int:pk>/alt/", views.teach_class_image_alt, name="teach_class_image_alt"),
    path("teach/classes/<int:pk>/withdraw/", views.teach_class_withdraw, name="teach_class_withdraw"),
    path("teach/classes/<int:pk>/cancel/", views.teach_class_cancel, name="teach_class_cancel"),
    path("teach/classes/<int:pk>/sale/", views.teach_class_sale, name="teach_class_sale"),
    path(
        "teach/classes/<int:pk>/request-change/",
        views.teach_class_request_change,
        name="teach_class_request_change",
    ),
    path(
        "teach/classes/<int:pk>/registrations/",
        views.teach_class_registrations,
        name="teach_class_registrations",
    ),
    path(
        "teach/classes/<int:pk>/registrations/table/",
        views.teach_class_registrations_table,
        name="teach_class_registrations_table",
    ),
    path(
        "teach/classes/<int:pk>/registrations/email/",
        views.teach_class_email,
        name="teach_class_email",
    ),
    path("teach/classes/<int:pk>/waitlist/", views.teach_class_waitlist, name="teach_class_waitlist"),
    path(
        "teach/classes/<int:pk>/discount-codes/",
        views.teach_class_discount_codes,
        name="teach_class_discount_codes",
    ),
    path("teach/classes/<int:pk>/emails/", views.teach_class_emails, name="teach_class_emails"),
    path("teach/registrations/", views.teach_registrations, name="teach_registrations"),
    path(
        "teach/registrations/email/",
        views.teach_registrations_email,
        name="teach_registrations_email",
    ),
    path("teach/discount-codes/", views.teach_discount_codes, name="teach_discount_codes"),
    path("teach/discount-codes/new/", views.teach_discount_code_create, name="teach_discount_code_create"),
    path(
        "teach/discount-codes/<int:pk>/edit/",
        views.teach_discount_code_edit,
        name="teach_discount_code_edit",
    ),
    path(
        "teach/discount-codes/<int:pk>/delete/",
        views.teach_discount_code_delete,
        name="teach_discount_code_delete",
    ),
    path(
        "teach/discount-codes/<int:pk>/approve/",
        views.teach_discount_code_approve,
        name="teach_discount_code_approve",
    ),
    path("teach/profile/", views.teach_profile, name="teach_profile"),
    # Legacy 301 redirects — old /classes/instructor/... links in emails + bookmarks
    path("instructor/", RedirectView.as_view(pattern_name="classes:teach_overview", permanent=True)),
    path("instructor/<path:subpath>", RedirectView.as_view(url="/classes/teach/%(subpath)s", permanent=True)),
    # Admin — /classes/admin/ is the Overview dashboard; the classes list moves to /admin/classes/.
    path("admin/", views.admin_overview, name="admin_overview"),
    path("admin/classes/", views.admin_classes, name="admin_classes"),
    # Teaching applications queue actions (the overview card).
    path(
        "admin/teaching-applications/<int:pk>/approve/",
        views.admin_teaching_approve,
        name="admin_teaching_approve",
    ),
    path(
        "admin/teaching-applications/<int:pk>/decline/",
        views.admin_teaching_decline,
        name="admin_teaching_decline",
    ),
    path("admin/new/", views.admin_class_create, name="admin_class_create"),
    path("admin/<int:pk>/preview/", views.class_preview, name="class_preview"),
    path("admin/<int:pk>/approve/", views.admin_class_approve, name="admin_class_approve"),
    path("admin/<int:pk>/review/", views.admin_class_review, name="admin_class_review"),
    # Tokenized review page — emailed reviewers act without a hub login.
    path("review/<str:token>/", views.class_review, name="class_review"),
    path("review/<str:token>/preview/", views.class_review_preview, name="class_review_preview"),
    path("admin/<int:pk>/archive/", views.admin_class_archive, name="admin_class_archive"),
    path("admin/<int:pk>/restore/", views.admin_class_restore, name="admin_class_restore"),
    path("admin/<int:pk>/unpublish/", views.admin_class_unpublish, name="admin_class_unpublish"),
    path("admin/<int:pk>/remind-lead/", views.admin_class_remind_lead, name="admin_class_remind_lead"),
    path("admin/<int:pk>/duplicate/", views.admin_class_duplicate, name="admin_class_duplicate"),
    path("admin/<int:pk>/delete/", views.admin_class_delete, name="admin_class_delete"),
    # ── The legacy per-class admin paths ────────────────────────────────────────────────
    #
    # These sixteen had a teaching-portal twin, and the twins are one screen now. The paths
    # and the names stay exactly where they were — every bookmark, every old email link and
    # every ``classes:admin_class_*`` reverse still resolves — but each is a dispatcher onto
    # the merged route rather than a second entrance to it (see ``legacy_class_route``).
    #
    # They sit BELOW ``class_preview`` on purpose: patterns are first-match, and a broader one
    # declared above would swallow it. There is deliberately no ``<path:subpath>`` catch-all
    # here either — it would carry neither ``@xframe_options_sameorigin`` nor the query string,
    # and the preview iframe needs both.
    path(
        "admin/<int:pk>/",
        legacy_class_route(views.teach_class_detail, "classes:teach_class_detail"),
        name="admin_class_detail",
    ),
    path(
        "admin/<int:pk>/registrations/",
        legacy_class_route(views.teach_class_registrations, "classes:teach_class_registrations"),
        name="admin_class_registrations",
    ),
    path(
        "admin/<int:pk>/registrations/table/",
        legacy_class_route(views.teach_class_registrations_table, "classes:teach_class_registrations_table"),
        name="admin_class_registrations_table",
    ),
    path(
        "admin/<int:pk>/waitlist/",
        legacy_class_route(views.teach_class_waitlist, "classes:teach_class_waitlist"),
        name="admin_class_waitlist",
    ),
    path(
        "admin/<int:pk>/discount-codes/",
        legacy_class_route(views.teach_class_discount_codes, "classes:teach_class_discount_codes"),
        name="admin_class_discount_codes",
    ),
    path(
        "admin/<int:pk>/emails/",
        legacy_class_route(views.teach_class_emails, "classes:teach_class_emails"),
        name="admin_class_emails",
    ),
    path(
        "admin/<int:pk>/edit/",
        legacy_class_route(views.teach_class_edit, "classes:teach_class_edit"),
        name="admin_class_edit",
    ),
    path(
        "admin/<int:pk>/email/",
        legacy_class_route(views.teach_class_email, "classes:teach_class_email"),
        name="admin_class_email",
    ),
    path(
        "admin/<int:pk>/cancel/",
        legacy_class_route(views.teach_class_cancel, "classes:teach_class_cancel"),
        name="admin_class_cancel",
    ),
    path(
        "admin/<int:pk>/sale/",
        legacy_class_route(views.teach_class_sale, "classes:teach_class_sale"),
        name="admin_class_sale",
    ),
    path(
        "admin/<int:pk>/another-date-set/",
        legacy_class_route(views.teach_class_duplicate_run, "classes:teach_class_duplicate_run"),
        name="admin_class_duplicate_run",
    ),
    path(
        "admin/<int:pk>/hero/upload/",
        legacy_class_route(views.teach_class_hero_upload, "classes:teach_class_hero_upload"),
        name="admin_class_hero_upload",
    ),
    path(
        "admin/<int:pk>/images/upload/",
        legacy_class_route(views.teach_class_image_upload, "classes:teach_class_image_upload"),
        name="admin_class_image_upload",
    ),
    path(
        "admin/<int:pk>/images/reorder/",
        legacy_class_route(views.teach_class_image_reorder, "classes:teach_class_image_reorder"),
        name="admin_class_image_reorder",
    ),
    path(
        "admin/images/<int:pk>/delete/",
        legacy_class_route(views.teach_class_image_delete, "classes:teach_class_image_delete"),
        name="admin_class_image_delete",
    ),
    path(
        "admin/images/<int:pk>/alt/",
        legacy_class_route(views.teach_class_image_alt, "classes:teach_class_image_alt"),
        name="admin_class_image_alt",
    ),
    path("admin/categories/", views.admin_categories, name="admin_categories"),
    path("admin/categories/guild-tagging/", views.admin_guild_tagging, name="admin_guild_tagging"),
    path("admin/categories/new/", views.admin_category_create, name="admin_category_create"),
    path("admin/categories/<int:pk>/edit/", views.admin_category_edit, name="admin_category_edit"),
    path("admin/categories/<int:pk>/delete/", views.admin_category_delete, name="admin_category_delete"),
    path("admin/activity/", views.admin_activity, name="admin_activity"),
    path("admin/registrations/", views.admin_registrations, name="admin_registrations"),
    path("admin/registrations/export/", views.admin_registrations_export, name="admin_registrations_export"),
    path("admin/registrations/<int:pk>/", views.admin_registration_detail, name="admin_registration_detail"),
    path("admin/registrations/<int:pk>/cancel/", views.admin_registration_cancel, name="admin_registration_cancel"),
    path("admin/registrations/<int:pk>/move/", views.admin_registration_move, name="admin_registration_move"),
    path("admin/registrations/<int:pk>/refund/", views.admin_registration_refund, name="admin_registration_refund"),
    path(
        "admin/registrations/<int:pk>/refund/form/",
        views.admin_registration_refund_form,
        name="admin_registration_refund_form",
    ),
    path(
        "admin/registrations/<int:pk>/refunds-card/",
        views.admin_registration_refunds_card,
        name="admin_registration_refunds_card",
    ),
    # Roster & waitlist management actions (shared teach + admin surface, HTMX POST)
    path("registrations/<int:pk>/remove/", views.registration_remove, name="registration_remove"),
    path("registrations/<int:pk>/move/", views.registration_move, name="registration_move"),
    path("registrations/<int:pk>/promote/", views.registration_promote, name="registration_promote"),
    path(
        "registrations/<int:pk>/promote/followup/",
        views.registration_promote_followup,
        name="registration_promote_followup",
    ),
    path(
        "registrations/<int:pk>/promote/notify/",
        views.registration_promote_notify,
        name="registration_promote_notify",
    ),
    path(
        "registrations/<int:pk>/send-payment-link/",
        views.registration_send_payment_link,
        name="registration_send_payment_link",
    ),
    path("registrations/<int:pk>/mark-paid/", views.registration_mark_paid, name="registration_mark_paid"),
    path("admin/discount-codes/", views.admin_discount_codes, name="admin_discount_codes"),
    path("admin/discount-codes/new/", views.admin_discount_code_create, name="admin_discount_code_create"),
    path("admin/discount-codes/<int:pk>/edit/", views.admin_discount_code_edit, name="admin_discount_code_edit"),
    path("admin/discount-codes/<int:pk>/delete/", views.admin_discount_code_delete, name="admin_discount_code_delete"),
    path(
        "admin/discount-codes/<int:pk>/approve/",
        views.admin_discount_code_approve,
        name="admin_discount_code_approve",
    ),
    path("admin/questions/", views.admin_registration_questions, name="admin_registration_questions"),
    path("admin/questions/new/", views.admin_registration_question_create, name="admin_registration_question_create"),
    path(
        "admin/questions/<int:pk>/edit/",
        views.admin_registration_question_edit,
        name="admin_registration_question_edit",
    ),
    path(
        "admin/questions/<int:pk>/delete/",
        views.admin_registration_question_delete,
        name="admin_registration_question_delete",
    ),
    path("admin/settings/", views.admin_settings_hub, name="admin_settings_hub"),
    path("admin/settings/waivers/", views.admin_settings, name="admin_settings"),
    path(
        "admin/settings/teaching-page/",
        views.admin_teaching_page_settings,
        name="admin_teaching_page_settings",
    ),
    # Legacy CMS image proxy — must come before the bare slug catch-all below.
    path("_legacy-image/", views_legacy_image.legacy_image, name="legacy_image"),
    # Class QR download (editor-gated) — before the bare slug catch-all below.
    path("<int:pk>/qr.<str:fmt>/", views.class_qr_download, name="class_qr"),
    # Printable one-page class flyer (editor-gated) — before the bare slug catch-all below.
    path("<int:pk>/flyer/", views.class_flyer, name="class_flyer"),
    # Stable QR permalink → redirects to the class's current public page (slug-proof).
    path("c/<int:pk>/", views.class_permalink, name="class_permalink"),
    # Public registration — must come before the bare slug catch-all below.
    path("<slug:slug>/register/", views.register, name="register"),
    path("<slug:slug>/register/success/", views.register_success, name="register_success"),
    path("<slug:slug>/register/cancelled/", views.register_cancelled, name="register_cancelled"),
    # Public class detail — keep last so admin/, category/, instructors/, my/ win.
    path("<slug:slug>/", views.public_class_detail, name="public_class_detail"),
]


# ── Feature gate (#405) ───────────────────────────────────────────────────────────────────
# Host a Workshop's family is the RECRUITING invitation and its marketing page, never the
# teaching portal. Exact names, not a "teach_" prefix: that would take the whole portal with it
# and lock every instructor out of their own classes, which is not what a visibility switch is
# for. teach_overview is absent on purpose — it is one route with two faces, and the gate goes
# on its non-instructor branch in classes/views.py so that a locked deep link still redirects
# somewhere real. tests/classes/teach_feature_spec.py pins the set.
GATED_ROUTE_NAMES: list[str] = gate_named(urlpatterns, "teach", ["teach_why", "teach_apply", "teach_orientation"])
