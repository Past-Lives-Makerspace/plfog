from django.urls import path

from . import views

urlpatterns = [
    path("payment-method/setup/", views.setup_payment_method, name="billing_setup_payment_method"),
    path("api/setup-intent/", views.create_setup_intent_api, name="billing_create_setup_intent"),
    path("payment-method/confirm/", views.confirm_setup, name="billing_confirm_setup"),
    path("payment-method/remove/", views.remove_payment_method, name="billing_remove_payment_method"),
    path("webhooks/stripe/", views.stripe_webhook, name="billing_stripe_webhook"),
    path("admin/dashboard/", views.admin_tab_dashboard, name="billing_admin_dashboard"),
    path("admin/add-entry/", views.admin_add_tab_entry, name="billing_admin_add_entry"),
    path("admin/save-settings/", views.billing_admin_save_settings, name="billing_admin_save_settings"),
    path("admin/retry-charge/<int:charge_pk>/", views.billing_admin_retry_charge, name="billing_admin_retry_charge"),
    path("admin/tab/<int:tab_pk>/detail/", views.billing_admin_tab_detail_api, name="billing_admin_tab_detail_api"),
    path("admin/payments/table/", views.billing_admin_payments_table, name="billing_admin_payments_table"),
    path("admin/payments/export/csv/", views.admin_payments_csv, name="billing_admin_payments_csv"),
    path(
        "admin/refunds/<int:refund_pk>/retry/",
        views.payment_refund_retry,
        name="billing_payment_refund_retry",
    ),
    path(
        "admin/orientations/<int:booking_pk>/refund/form/",
        views.payment_orientation_refund_form,
        name="billing_orientation_refund_form",
    ),
    path(
        "admin/orientations/<int:booking_pk>/refund/",
        views.payment_orientation_refund,
        name="billing_orientation_refund",
    ),
    path("admin/reports/", views.admin_reports, name="billing_admin_reports"),
    path("admin/reports/export/csv/", views.admin_reports_csv, name="billing_admin_reports_csv"),
    path(
        "admin/connect-platform/test/",
        views.billing_test_platform_connection,
        name="billing_test_platform_connection",
    ),
    path(
        "admin/connect-platform/save/",
        views.billing_save_connect_platform,
        name="billing_save_connect_platform",
    ),
]
