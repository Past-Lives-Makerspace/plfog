from django.urls import path

from classes import views

app_name = "classes"

urlpatterns = [
    # Public portal
    path("", views.public_list, name="public_list"),
    path("category/<slug:slug>/", views.public_category, name="public_category"),
    path("instructors/<slug:slug>/", views.public_instructor, name="public_instructor"),
    # Self-serve registration management (token-based, no auth)
    path("my/<str:token>/", views.my_registration, name="my_registration"),
    path("my/<str:token>/cancel/", views.my_registration_cancel, name="my_registration_cancel"),
    # Instructor dashboard
    path("instructor/", views.instructor_dashboard, name="instructor_dashboard"),
    path("instructor/classes/new/", views.instructor_class_create, name="instructor_class_create"),
    path("instructor/classes/<int:pk>/edit/", views.instructor_class_edit, name="instructor_class_edit"),
    path("instructor/classes/<int:pk>/submit/", views.instructor_class_submit, name="instructor_class_submit"),
    path("instructor/registrations/", views.instructor_registrations, name="instructor_registrations"),
    path(
        "instructor/registrations/email/",
        views.instructor_registrations_email,
        name="instructor_registrations_email",
    ),
    path("instructor/discount-codes/", views.instructor_discount_codes, name="instructor_discount_codes"),
    path(
        "instructor/discount-codes/new/", views.instructor_discount_code_create, name="instructor_discount_code_create"
    ),
    path(
        "instructor/discount-codes/<int:pk>/edit/",
        views.instructor_discount_code_edit,
        name="instructor_discount_code_edit",
    ),
    path(
        "instructor/discount-codes/<int:pk>/delete/",
        views.instructor_discount_code_delete,
        name="instructor_discount_code_delete",
    ),
    path("instructor/profile/", views.instructor_profile, name="instructor_profile"),
    # Admin — /classes/admin/ IS the classes list; no double-"classes" path segment.
    path("admin/", views.admin_classes, name="admin_classes"),
    path("admin/new/", views.admin_class_create, name="admin_class_create"),
    path("admin/<int:pk>/", views.admin_class_detail, name="admin_class_detail"),
    path("admin/<int:pk>/preview/", views.class_preview, name="class_preview"),
    path("admin/<int:pk>/edit/", views.admin_class_edit, name="admin_class_edit"),
    path("admin/<int:pk>/approve/", views.admin_class_approve, name="admin_class_approve"),
    path("admin/<int:pk>/archive/", views.admin_class_archive, name="admin_class_archive"),
    path("admin/<int:pk>/duplicate/", views.admin_class_duplicate, name="admin_class_duplicate"),
    path("admin/<int:pk>/delete/", views.admin_class_delete, name="admin_class_delete"),
    path("admin/categories/", views.admin_categories, name="admin_categories"),
    path("admin/categories/new/", views.admin_category_create, name="admin_category_create"),
    path("admin/categories/<int:pk>/edit/", views.admin_category_edit, name="admin_category_edit"),
    path("admin/categories/<int:pk>/delete/", views.admin_category_delete, name="admin_category_delete"),
    path("admin/instructors/", views.admin_instructors, name="admin_instructors"),
    path("admin/instructors/add/", views.admin_instructor_promote, name="admin_instructor_promote"),
    path("admin/registrations/", views.admin_registrations, name="admin_registrations"),
    path("admin/registrations/<int:pk>/", views.admin_registration_detail, name="admin_registration_detail"),
    path("admin/registrations/<int:pk>/cancel/", views.admin_registration_cancel, name="admin_registration_cancel"),
    path("admin/discount-codes/", views.admin_discount_codes, name="admin_discount_codes"),
    path("admin/discount-codes/new/", views.admin_discount_code_create, name="admin_discount_code_create"),
    path("admin/discount-codes/<int:pk>/edit/", views.admin_discount_code_edit, name="admin_discount_code_edit"),
    path("admin/discount-codes/<int:pk>/delete/", views.admin_discount_code_delete, name="admin_discount_code_delete"),
    path("admin/settings/", views.admin_settings, name="admin_settings"),
    # Public registration — must come before the bare slug catch-all below.
    path("<slug:slug>/register/", views.register, name="register"),
    path("<slug:slug>/register/success/", views.register_success, name="register_success"),
    path("<slug:slug>/register/cancelled/", views.register_cancelled, name="register_cancelled"),
    # Public class detail — keep last so admin/, category/, instructors/, my/ win.
    path("<slug:slug>/", views.public_class_detail, name="public_class_detail"),
]
