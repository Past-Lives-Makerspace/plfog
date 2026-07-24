from django.urls import path
from django.views.generic import RedirectView

from . import discord_views, notification_views, views

urlpatterns = [
    path("guilds/voting/", views.guild_voting, name="hub_guild_voting"),
    path("guilds/voting/history/", views.snapshot_history, name="hub_snapshot_history"),
    path("guilds/voting/history/<int:pk>/", views.snapshot_detail, name="hub_snapshot_detail"),
    path("members/", views.member_directory, name="hub_member_directory"),
    # Public guild directory — the guilds.pastlives.app front door (also reachable on FOG).
    path("guilds/", views.guild_directory, name="hub_guild_directory"),
    # Old numeric guild URLs (already shared in Discord/emails) 301 → the slug URL.
    path("guilds/<int:pk>/", views.guild_detail_redirect, name="hub_guild_detail_by_id"),
    path("guilds/<slug:slug>/", views.guild_detail, name="hub_guild_detail"),
    path("guilds/<int:pk>/edit/", views.guild_edit, name="hub_guild_edit"),
    path("guilds/<int:pk>/qr.<str:fmt>/", views.guild_qr_download, name="hub_guild_qr"),
    path("guilds/<int:pk>/flyer/", views.guild_flyer, name="hub_guild_flyer"),
    path("guilds/<int:pk>/delete/", views.guild_delete, name="hub_guild_delete"),
    path("hero-adjust/", views.hub_hero_adjust, name="hub_hero_adjust"),
    path("guilds/<int:pk>/banner/delete/", views.guild_banner_delete, name="hub_guild_banner_delete"),
    path("guilds/<int:pk>/orientation/edit/", views.guild_orientation_edit, name="hub_guild_orientation_edit"),
    path(
        "guilds/<int:pk>/orientation/hours/save/",
        views.guild_orientation_hours_save,
        name="hub_guild_orientation_hours_save",
    ),
    path(
        "guilds/<int:pk>/studio-hours/save/",
        views.guild_studio_hours_save,
        name="hub_guild_studio_hours_save",
    ),
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
        "guilds/<int:pk>/announcements/<int:announcement_pk>/delete/",
        views.guild_announcement_delete,
        name="hub_guild_announcement_delete",
    ),
    path(
        "guilds/<int:pk>/announcements/<int:announcement_pk>/edit/",
        views.guild_announcement_edit,
        name="hub_guild_announcement_edit",
    ),
    # Announcement compose wizard (admins: site-wide; guild leads/staff: their guilds).
    path("announcements/compose/", views.hub_compose, name="hub_compose"),
    path("announcements/compose/preview/", views.hub_compose_preview, name="hub_compose_preview"),
    path("announcements/compose/count/", views.hub_compose_count, name="hub_compose_count"),
    path("announcements/compose/test/", views.hub_compose_test, name="hub_compose_test"),
    path("announcements/compose/save/", views.hub_compose_save_draft, name="hub_compose_save_draft"),
    path("announcements/compose/send/", views.hub_compose_send, name="hub_compose_send"),
    path("announcements/compose/<int:draft_pk>/", views.hub_compose, name="hub_compose_resume"),
    path(
        "announcements/compose/<int:draft_pk>/delete/",
        views.hub_compose_delete_draft,
        name="hub_compose_delete_draft",
    ),
    # Member-proposed announcements (any logged-in member proposes; a lead/admin reviews).
    path(
        "announcements/propose/",
        views.propose_guild_announcement,
        name="hub_guild_announcement_propose",
    ),
    path(
        "announcements/propose/<int:pk>/edit/",
        views.propose_guild_announcement,
        name="hub_guild_announcement_propose_edit",
    ),
    path(
        "announcements/<int:pk>/withdraw/",
        views.guild_announcement_withdraw,
        name="hub_guild_announcement_withdraw",
    ),
    path(
        "announcements/review/",
        views.guild_announcement_review_queue,
        name="hub_guild_announcement_review_queue",
    ),
    path(
        "announcements/review/<int:pk>/decision/",
        views.guild_announcement_review_decision,
        name="hub_guild_announcement_review_decision",
    ),
    path("guilds/<int:pk>/emails/save/", views.guild_emails_save, name="hub_guild_emails_save"),
    path("guilds/<int:pk>/faq/save/", views.guild_faq_save, name="hub_guild_faq_save"),
    path("guilds/<int:pk>/links/save/", views.guild_links_save, name="hub_guild_links_save"),
    path(
        "guilds/<int:pk>/mailing-list/save/",
        views.guild_mailing_list_save,
        name="hub_guild_mailing_list_save",
    ),
    path(
        "guilds/<int:pk>/mailing-list/import/",
        views.guild_mailing_list_import,
        name="hub_guild_mailing_list_import",
    ),
    # Spaces page — the map (tab 1) and the full space listings (tab 2).
    path("spaces/", views.spaces, name="hub_spaces"),
    # Interactive space map — public-read map + admin placement editor + request review.
    path("spaces/map/edit/", views.org_map_edit, name="hub_org_map_edit"),
    path("spaces/map/floors/save/", views.org_map_floors_save, name="hub_org_map_floors_save"),
    path("spaces/map/floors/<int:pk>/delete/", views.org_map_floor_delete, name="hub_org_map_floor_delete"),
    path("spaces/map/markers/save/", views.map_hotspots_save, name="hub_map_hotspots_save"),
    path("spaces/map/markers/create/", views.map_hotspot_create, name="hub_map_hotspot_create"),
    path("spaces/map/markers/<int:pk>/position/", views.map_hotspot_position, name="hub_map_hotspot_position"),
    path("spaces/map/markers/<int:pk>/status/", views.map_hotspot_status, name="hub_map_hotspot_status"),
    path("spaces/map/markers/<int:pk>/edit/", views.map_hotspot_edit, name="hub_map_hotspot_edit"),
    path("spaces/map/markers/<int:pk>/delete/", views.map_hotspot_delete, name="hub_map_hotspot_delete"),
    path("spaces/map/markers/<int:pk>/", views.map_hotspot_detail, name="hub_map_hotspot_detail"),
    path("spaces/map/markers/<int:pk>/request/", views.space_request_create, name="hub_space_request_create"),
    path("spaces/requests/<int:pk>/withdraw/", views.space_request_withdraw, name="hub_space_request_withdraw"),
    path("spaces/requests/review/", views.space_request_review_queue, name="hub_space_request_review_queue"),
    path(
        "spaces/requests/review/<int:pk>/decision/",
        views.space_request_review_decision,
        name="hub_space_request_review_decision",
    ),
    # Help — how the app works: intro, guides, parking, who-to-contact, FAQ, code of conduct, resources.
    path("help/", views.help_page, name="hub_help"),
    path("help/edit/", views.help_edit, name="hub_help_edit"),
    path("help/floorplan/delete/", views.org_info_floorplan_delete, name="hub_org_info_floorplan_delete"),
    path("help/faq/save/", views.org_info_faq_save, name="hub_org_info_faq_save"),
    path("help/links/save/", views.org_info_links_save, name="hub_org_info_links_save"),
    path("help/articles/save/", views.help_articles_save, name="hub_help_articles_save"),
    # Legacy: /info/ was the combined Space & Org Info page. Space-request emails and Discord
    # posts already carry /info/#hotspot-N links, so this must keep resolving — to the map.
    path(
        "info/",
        RedirectView.as_view(pattern_name="hub_spaces", permanent=True),
        name="hub_org_info_legacy",
    ),
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
    path("guilds/<int:pk>/events/", views.guild_events, name="hub_guild_events"),
    path("guilds/<int:pk>/events/add/", views.guild_event_edit, name="hub_guild_event_add"),
    path(
        "guilds/<int:pk>/events/<int:event_pk>/edit/",
        views.guild_event_edit,
        name="hub_guild_event_edit",
    ),
    path(
        "guilds/<int:pk>/events/<int:event_pk>/delete/",
        views.guild_event_delete,
        name="hub_guild_event_delete",
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
    path("welcome/dismiss/", views.welcome_dismiss, name="hub_welcome_dismiss"),
    path("settings/onboarding/dismiss/", views.onboarding_dismiss, name="hub_onboarding_dismiss"),
    path("settings/", views.user_settings, name="hub_user_settings"),
    path(
        "settings/profile-photo/delete/",
        views.profile_photo_delete,
        name="hub_profile_photo_delete",
    ),
    path("settings/skills/add/", views.skill_add, name="hub_skill_add"),
    path("settings/skills/<int:skill_pk>/remove/", views.skill_remove, name="hub_skill_remove"),
    path("settings/skills/suggest/", views.skill_suggest, name="hub_skill_suggest"),
    path("settings/guilds/<int:pk>/", views.guild_membership_set, name="hub_guild_membership_set"),
    # Discord account-linking for the per-member Discord DM notification channel.
    path("settings/discord/connect/", discord_views.discord_connect, name="hub_discord_connect"),
    path("settings/discord/callback/", discord_views.discord_callback, name="hub_discord_callback"),
    path("settings/discord/disconnect/", discord_views.discord_disconnect, name="hub_discord_disconnect"),
    # Anon-allowed low-friction link (posted in Discord): click once → linked + guilds set up.
    path("discord/link/", discord_views.discord_link_start, name="hub_discord_link_start"),
    path("discord/link/callback/", discord_views.discord_link_callback, name="hub_discord_link_callback"),
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
    path("home/", views.home, name="hub_home"),
    path("calendar/", views.community_calendar, name="hub_community_calendar"),
    path("calendar/events/", views.calendar_events_partial, name="hub_community_calendar_events"),
    path("calendar/export.ics", views.calendar_export_ics, name="hub_calendar_export_ics"),
    # Admin site-wide event authoring — the LIST is the Events tab on the calendar.
    path("events/add/", views.event_edit, name="hub_event_add"),
    path("events/<int:event_pk>/edit/", views.event_edit, name="hub_event_edit"),
    path("events/<int:event_pk>/delete/", views.event_delete, name="hub_event_delete"),
    # Member event proposals + reviewer queue.
    path("events/propose/", views.propose_event, name="hub_propose_event"),
    path("events/propose/<int:pk>/edit/", views.propose_event, name="hub_propose_event_edit"),
    path("events/<int:pk>/withdraw/", views.event_withdraw, name="hub_event_withdraw"),
    path("events/<int:pk>/retry-sync/", views.event_retry_sync, name="hub_event_retry_sync"),
    path("events/review/", views.event_review_queue, name="hub_event_review_queue"),
    path("events/review/<int:pk>/decision/", views.event_review_decision, name="hub_event_review_decision"),
    # Public per-event pages + QR (bare-pk route can't shadow the siblings above: it
    # won't match the literal events/add/ nor any deeper events/<int>/<segment>/ path).
    path("events/<int:pk>/", views.event_detail, name="hub_event_detail"),
    path("events/<int:pk>/event.ics", views.event_ics, name="hub_event_ics"),
    path("events/<int:pk>/qr.<str:fmt>/", views.event_qr, name="hub_event_qr"),
    path("view-as/set/", views.view_as_set, name="hub_view_as_set"),
    path("manage/voting/", views.voting_overview, name="hub_admin_voting_overview"),
    path("manage/voting/history/", views.voting_history, name="hub_admin_voting_history"),
    path("manage/voting/history/<int:pk>/", views.voting_history_detail, name="hub_admin_voting_history_detail"),
    path(
        "manage/voting/history/<int:pk>/delete/",
        views.voting_snapshot_delete,
        name="hub_admin_voting_snapshot_delete",
    ),
    path("manage/voting/snapshots/", views.voting_snapshots, name="hub_admin_voting_snapshots"),
    path("manage/voting/snapshots/take/", views.voting_snapshot_take, name="hub_admin_voting_snapshot_take"),
    path(
        "manage/voting/history/<int:pk>/send-results/",
        views.voting_send_results,
        name="hub_admin_voting_send_results",
    ),
    path("manage/voting/settings/", views.voting_settings, name="hub_admin_voting_settings"),
    path("manage/members/", views.admin_members, name="hub_admin_members"),
    path("manage/members/invite/", views.admin_member_invite, name="hub_admin_member_invite"),
    path(
        "manage/members/invites/<int:pk>/resend/",
        views.admin_invite_resend,
        name="hub_admin_invite_resend",
    ),
    path(
        "manage/members/invites/<int:pk>/revoke/",
        views.admin_invite_revoke,
        name="hub_admin_invite_revoke",
    ),
    path("manage/members/<int:pk>/edit/", views.admin_member_edit, name="hub_admin_member_edit"),
    path(
        "manage/members/<int:pk>/send-login-invite/",
        views.admin_member_send_login_invite,
        name="hub_admin_member_send_login_invite",
    ),
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
    # Non-member users (a User with no Member — e.g. a book.* class registrant).
    # Keyed on user_pk because they have no Member pk (Review fix #5).
    path("manage/users/<int:user_pk>/edit/", views.admin_user_edit, name="hub_admin_user_edit"),
    path(
        "manage/users/<int:user_pk>/emails/add/",
        views.admin_user_email_add,
        name="hub_admin_user_email_add",
    ),
    path(
        "manage/users/<int:user_pk>/emails/<int:email_pk>/remove/",
        views.admin_user_email_remove,
        name="hub_admin_user_email_remove",
    ),
    path(
        "manage/users/<int:user_pk>/emails/<int:email_pk>/set-primary/",
        views.admin_user_email_set_primary,
        name="hub_admin_user_email_set_primary",
    ),
    path(
        "manage/users/<int:user_pk>/emails/<int:email_pk>/toggle-verified/",
        views.admin_user_email_toggle_verified,
        name="hub_admin_user_email_toggle_verified",
    ),
    path("manage/site-settings/", views.admin_site_settings, name="hub_admin_site_settings"),
    path(
        "manage/site-settings/slideshow/zones/save/",
        views.admin_slideshow_zones_save,
        name="hub_admin_slideshow_zones_save",
    ),
    path(
        "manage/site-settings/slideshow/slides/save/",
        views.admin_slideshow_slides_save,
        name="hub_admin_slideshow_slides_save",
    ),
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
