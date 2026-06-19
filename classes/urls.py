from django.urls import path
from django.views.generic.base import RedirectView

from classes import views, views_legacy_image

app_name = "classes"

urlpatterns = [
    # Public portal
    path("", views.public_list, name="public_list"),
    path("category/<slug:slug>/", views.public_category, name="public_category"),
    path("instructors/<slug:slug>/", views.public_instructor, name="public_instructor"),
    # Self-serve registration management (token-based, no auth)
    path("my/<str:token>/", views.my_registration, name="my_registration"),
    path("my/<str:token>/cancel/", views.my_registration_cancel, name="my_registration_cancel"),
    # Teaching portal (member self-serve for instructors)
    path("teach/", views.teach_overview, name="teach_overview"),
    path("teach/classes/", views.teach_dashboard, name="teach_dashboard"),
    path("teach/classes/new/", views.teach_class_create, name="teach_class_create"),
    path("teach/classes/<int:pk>/edit/", views.teach_class_edit, name="teach_class_edit"),
    path("teach/classes/<int:pk>/submit/", views.teach_class_submit, name="teach_class_submit"),
    path("teach/classes/<int:pk>/", views.teach_class_detail, name="teach_class_detail"),
    path(
        "teach/classes/<int:pk>/registrations/",
        views.teach_class_registrations,
        name="teach_class_registrations",
    ),
    path(
        "teach/classes/<int:pk>/registrations/export/",
        views.teach_class_export,
        name="teach_class_export",
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
    path("teach/profile/", views.teach_profile, name="teach_profile"),
    # Legacy 301 redirects — old /classes/instructor/... links in emails + bookmarks
    path("instructor/", RedirectView.as_view(pattern_name="classes:teach_overview", permanent=True)),
    path("instructor/<path:subpath>", RedirectView.as_view(url="/classes/teach/%(subpath)s", permanent=True)),
    # Admin — /classes/admin/ is the Overview dashboard; the classes list moves to /admin/classes/.
    path("admin/", views.admin_overview, name="admin_overview"),
    path("admin/classes/", views.admin_classes, name="admin_classes"),
    path("admin/new/", views.admin_class_create, name="admin_class_create"),
    path("admin/<int:pk>/", views.admin_class_detail, name="admin_class_detail"),
    path("admin/<int:pk>/registrations/", views.admin_class_registrations, name="admin_class_registrations"),
    path("admin/<int:pk>/registrations/export/", views.admin_class_export, name="admin_class_export"),
    path("admin/<int:pk>/waitlist/", views.admin_class_waitlist, name="admin_class_waitlist"),
    path("admin/<int:pk>/discount-codes/", views.admin_class_discount_codes, name="admin_class_discount_codes"),
    path("admin/<int:pk>/preview/", views.class_preview, name="class_preview"),
    path("admin/<int:pk>/edit/", views.admin_class_edit, name="admin_class_edit"),
    path("admin/<int:pk>/email/", views.admin_class_email, name="admin_class_email"),
    path("admin/<int:pk>/approve/", views.admin_class_approve, name="admin_class_approve"),
    path("admin/<int:pk>/review/", views.admin_class_review, name="admin_class_review"),
    # Tokenized review page — emailed reviewers act without a hub login.
    path("review/<str:token>/", views.class_review, name="class_review"),
    path("admin/<int:pk>/archive/", views.admin_class_archive, name="admin_class_archive"),
    path("admin/<int:pk>/duplicate/", views.admin_class_duplicate, name="admin_class_duplicate"),
    path("admin/<int:pk>/delete/", views.admin_class_delete, name="admin_class_delete"),
    path("admin/<int:pk>/hero/upload/", views.admin_class_hero_upload, name="admin_class_hero_upload"),
    path("admin/<int:pk>/images/upload/", views.admin_class_image_upload, name="admin_class_image_upload"),
    path("admin/<int:pk>/images/reorder/", views.admin_class_image_reorder, name="admin_class_image_reorder"),
    path("admin/images/<int:pk>/delete/", views.admin_class_image_delete, name="admin_class_image_delete"),
    path("admin/images/<int:pk>/alt/", views.admin_class_image_alt, name="admin_class_image_alt"),
    path("admin/categories/", views.admin_categories, name="admin_categories"),
    path("admin/categories/new/", views.admin_category_create, name="admin_category_create"),
    path("admin/categories/<int:pk>/edit/", views.admin_category_edit, name="admin_category_edit"),
    path("admin/categories/<int:pk>/delete/", views.admin_category_delete, name="admin_category_delete"),
    path("admin/activity/", views.admin_activity, name="admin_activity"),
    path("admin/registrations/", views.admin_registrations, name="admin_registrations"),
    path("admin/registrations/<int:pk>/", views.admin_registration_detail, name="admin_registration_detail"),
    path("admin/registrations/<int:pk>/cancel/", views.admin_registration_cancel, name="admin_registration_cancel"),
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
    # Legacy CMS image proxy — must come before the bare slug catch-all below.
    path("_legacy-image/", views_legacy_image.legacy_image, name="legacy_image"),
    # Public registration — must come before the bare slug catch-all below.
    path("<slug:slug>/register/", views.register, name="register"),
    path("<slug:slug>/register/success/", views.register_success, name="register_success"),
    path("<slug:slug>/register/cancelled/", views.register_cancelled, name="register_cancelled"),
    # Public class detail — keep last so admin/, category/, instructors/, my/ win.
    path("<slug:slug>/", views.public_class_detail, name="public_class_detail"),
]
