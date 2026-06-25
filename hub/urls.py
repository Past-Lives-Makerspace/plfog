from django.urls import path
from django.views.generic import RedirectView

from . import notification_views, views

urlpatterns = [
    path("guilds/voting/", views.guild_voting, name="hub_guild_voting"),
    path("guilds/voting/history/", views.snapshot_history, name="hub_snapshot_history"),
    path("guilds/voting/history/<int:pk>/", views.snapshot_detail, name="hub_snapshot_detail"),
    path("members/", views.member_directory, name="hub_member_directory"),
    path("guilds/<int:pk>/", views.guild_detail, name="hub_guild_detail"),
    path("guilds/<int:pk>/edit/", views.guild_edit, name="hub_guild_edit"),
    path("hero-adjust/", views.hub_hero_adjust, name="hub_hero_adjust"),
    path("guilds/<int:pk>/banner/delete/", views.guild_banner_delete, name="hub_guild_banner_delete"),
    path("guilds/<int:pk>/orientation/edit/", views.guild_orientation_edit, name="hub_guild_orientation_edit"),
    path("guilds/<int:pk>/staff/add/", views.guild_staff_add, name="hub_guild_staff_add"),
    path(
        "guilds/<int:pk>/staff/<int:staff_pk>/remove/",
        views.guild_staff_remove,
        name="hub_guild_staff_remove",
    ),
    path(
        "guilds/<int:pk>/orientation/slots/add/",
        views.guild_orientation_slot_add,
        name="hub_guild_orientation_slot_add",
    ),
    path(
        "guilds/<int:pk>/orientation/slots/<int:slot_pk>/cancel/",
        views.guild_orientation_slot_cancel,
        name="hub_guild_orientation_slot_cancel",
    ),
    path("guilds/<int:pk>/orientation/", views.orientation_info, name="hub_orientation_info"),
    path("guilds/<int:pk>/calendar/events/", views.guild_calendar_events_partial, name="hub_guild_calendar_events"),
    path(
        "guilds/<int:pk>/orientation/request-custom/",
        views.guild_orientation_request_custom,
        name="hub_guild_orientation_request_custom",
    ),
    path("orientation/slots/<int:slot_pk>/book/", views.orientation_book, name="hub_orientation_book"),
    path("orientation/bookings/<int:booking_pk>/respond/", views.orientation_respond, name="hub_orientation_respond"),
    path(
        "orientation/bookings/<int:booking_pk>/lead-cancel/",
        views.orientation_lead_cancel,
        name="hub_orientation_lead_cancel",
    ),
    path(
        "orientation/bookings/<int:booking_pk>/cancel/",
        views.orientation_cancel_mine,
        name="hub_orientation_cancel_mine",
    ),
    path("orientation/act/<str:token>/", views.orientation_action, name="hub_orientation_action"),
    path("orientations/", views.orientations_dashboard, name="hub_orientations_dashboard"),
    path("orientations/export/", views.orientations_export, name="hub_orientations_export"),
    path("orientations/add-member/", views.orientation_add_member, name="hub_orientation_add_member"),
    path(
        "orientations/bookings/<int:booking_pk>/toggle-completed/",
        views.orientation_toggle_completed,
        name="hub_orientation_toggle_completed",
    ),
    path("guilds/<int:pk>/join/", views.guild_join, name="hub_guild_join"),
    path("guilds/<int:pk>/leave/", views.guild_leave, name="hub_guild_leave"),
    path(
        "guilds/<int:pk>/images/<int:image_pk>/delete/",
        views.guild_image_delete,
        name="hub_guild_image_delete",
    ),
    path(
        "guilds/<int:pk>/images/upload/",
        views.guild_image_upload,
        name="hub_guild_image_upload",
    ),
    path(
        "guilds/<int:pk>/images/reorder/",
        views.guild_image_reorder,
        name="hub_guild_image_reorder",
    ),
    path(
        "guilds/<int:pk>/images/<int:image_pk>/alt/",
        views.guild_image_alt_update,
        name="hub_guild_image_alt",
    ),
    path(
        "guilds/<int:pk>/announcements/new/",
        views.guild_announcement_create,
        name="hub_guild_announcement_create",
    ),
    path(
        "guilds/<int:pk>/announcements/<int:announcement_pk>/delete/",
        views.guild_announcement_delete,
        name="hub_guild_announcement_delete",
    ),
    path(
        "guilds/<int:pk>/announcements/<int:announcement_pk>/edit/",
        views.guild_announcement_edit,
        name="hub_guild_announcement_edit",
    ),
    path("guilds/<int:pk>/faq/save/", views.guild_faq_save, name="hub_guild_faq_save"),
    path("guilds/<int:pk>/links/save/", views.guild_links_save, name="hub_guild_links_save"),
    path("guilds/<int:pk>/meeting-notes/", views.guild_meeting_notes, name="hub_guild_meeting_notes"),
    path(
        "guilds/<int:pk>/meeting-notes/add/",
        views.guild_meeting_note_edit,
        name="hub_guild_meeting_note_add",
    ),
    path(
        "guilds/<int:pk>/meeting-notes/<int:note_pk>/edit/",
        views.guild_meeting_note_edit,
        name="hub_guild_meeting_note_edit",
    ),
    path(
        "guilds/<int:pk>/meeting-notes/<int:note_pk>/delete/",
        views.guild_meeting_note_delete,
        name="hub_guild_meeting_note_delete",
    ),
    path("guilds/<int:pk>/cart/confirm/", views.guild_cart_confirm, name="hub_guild_cart_confirm"),
    path("guilds/<int:pk>/eyop-form/", views.guild_eyop_form, name="hub_guild_eyop_form"),
    path(
        "guilds/<int:pk>/products/add/",
        views.guild_product_create,
        name="hub_guild_product_create",
    ),
    path(
        "guilds/<int:pk>/products/<int:product_pk>/edit/",
        views.guild_product_update,
        name="hub_guild_product_update",
    ),
    path(
        "guilds/<int:pk>/products/<int:product_pk>/delete/",
        views.guild_product_delete,
        name="hub_guild_product_delete",
    ),
    path("settings/", views.user_settings, name="hub_user_settings"),
    path(
        "settings/profile-photo/delete/",
        views.profile_photo_delete,
        name="hub_profile_photo_delete",
    ),
    # Old settings routes redirect to the tabbed User Settings page.
    path(
        "settings/profile/",
        RedirectView.as_view(pattern_name="hub_user_settings", query_string=False, permanent=False),
        name="hub_profile_settings",
    ),
    path("feedback/", views.beta_feedback, name="hub_beta_feedback"),
    path("tab/", views.tab_detail, name="hub_tab_detail"),
    path("tab/history/", views.tab_history, name="hub_tab_history"),
    path("tab/void/<int:entry_pk>/", views.void_tab_entry, name="hub_void_tab_entry"),
    path("calendar/", views.community_calendar, name="hub_community_calendar"),
    path("calendar/events/", views.calendar_events_partial, name="hub_community_calendar_events"),
    path("calendar/export.ics", views.calendar_export_ics, name="hub_calendar_export_ics"),
    path("view-as/set/", views.view_as_set, name="hub_view_as_set"),
    path("manage/voting/", views.admin_voting_dashboard, name="hub_admin_voting_dashboard"),
    path("manage/members/", views.admin_members, name="hub_admin_members"),
    path("manage/members/invite/", views.admin_member_invite, name="hub_admin_member_invite"),
    path("manage/members/<int:pk>/edit/", views.admin_member_edit, name="hub_admin_member_edit"),
    path(
        "manage/members/<int:pk>/emails/add/",
        views.admin_member_email_add,
        name="hub_admin_member_email_add",
    ),
    path(
        "manage/members/<int:pk>/emails/<int:email_pk>/remove/",
        views.admin_member_email_remove,
        name="hub_admin_member_email_remove",
    ),
    path(
        "manage/members/<int:pk>/emails/<int:email_pk>/set-primary/",
        views.admin_member_email_set_primary,
        name="hub_admin_member_email_set_primary",
    ),
    path(
        "manage/members/<int:pk>/emails/<int:email_pk>/toggle-verified/",
        views.admin_member_email_toggle_verified,
        name="hub_admin_member_email_toggle_verified",
    ),
    path("manage/site-settings/", views.admin_site_settings, name="hub_admin_site_settings"),
    # --- Notification copy catalogue (design §2.3 + §2.4, Decision 6) ---
    path("manage/notifications/", notification_views.catalogue, name="hub_admin_notifications"),
    path(
        "manage/notifications/<str:event_key>/<str:channel>/edit/",
        notification_views.edit_copy,
        name="hub_admin_notification_edit",
    ),
    path(
        "manage/notifications/<str:event_key>/<str:channel>/preview/",
        notification_views.preview_copy,
        name="hub_admin_notification_preview",
    ),
    path(
        "manage/notifications/<str:event_key>/<str:channel>/revert/<int:version_id>/",
        notification_views.revert_copy,
        name="hub_admin_notification_revert",
    ),
    path(
        "manage/notifications/<str:event_key>/discord/",
        notification_views.edit_discord_route,
        name="hub_admin_notification_discord",
    ),
]
