"""Core app URL configuration."""

from django.urls import path

from . import copy_review_views, views

urlpatterns = [
    # Cross-surface session relay (SSO between members and book surfaces)
    path("auth/relay/", views.relay_issue, name="relay_issue"),
    path("auth/relay/accept/", views.relay_accept, name="relay_accept"),
    # Health check
    path("health/", views.health_check, name="health_check"),
    # Discord Interactions Endpoint URL (slash-command platform). Signed by Discord,
    # csrf-exempt, no login — verified by ed25519 signature, not a session.
    path("discord/interactions/", views.discord_interactions, name="discord_interactions"),
    # Crawler policy — keep search engines out of /admin/ and private areas
    path("robots.txt", views.robots_txt, name="robots_txt"),
    # Home page
    path("", views.home, name="home"),
    # Short, human-typable vanity share URL → 301 to the public guest guild page.
    path("g/<slug:slug>/", views.guild_vanity_redirect, name="guild_vanity"),
    # Clear pending login stage and restart
    path("accounts/restart-login/", views.restart_login, name="restart_login"),
    # Find account by name
    path("accounts/find-account/", views.find_account, name="find_account"),
    # Public newsletter signup (Mailchimp)
    path("newsletter/", views.newsletter_signup, name="newsletter_signup"),
    # Public privacy policy — no login (linked from the app-store listing + footer)
    path("privacy/", views.privacy_policy, name="privacy_policy"),
    # Service worker (served with Service-Worker-Allowed header)
    path("sw.js", views.service_worker, name="service_worker"),
    # WebPush endpoints
    path("webpush/vapid-key/", views.vapid_key, name="webpush_vapid_key"),
    path("webpush/subscribe/", views.subscribe, name="webpush_subscribe"),
    path("webpush/unsubscribe/", views.unsubscribe, name="webpush_unsubscribe"),
    # Native (Capacitor/FCM) push endpoints
    path("push/fcm/register/", views.fcm_register, name="fcm_register"),
    path("push/fcm/unregister/", views.fcm_unregister, name="fcm_unregister"),
    # Biometric login for the native app. `unlock` is the only csrf-exempt one of the
    # three, and only because its caller has no session yet — see the view's comment.
    path("accounts/biometric/enroll/", views.biometric_enroll, name="biometric_enroll"),
    path("accounts/biometric/unlock/", views.biometric_unlock, name="biometric_unlock"),
    path("accounts/biometric/disable/", views.biometric_disable, name="biometric_disable"),
    # Staff activity dashboard
    path("manage/activity/", views.site_activity, name="manage_activity"),
    # Member notifications page
    path("notifications/", views.notification_list, name="notification_list"),
    path("notifications/unread-count/", views.notification_unread_count, name="notification_unread_count"),
    path("notifications/<int:pk>/read/", views.notification_read, name="notification_read"),
    path("notifications/read-all/", views.notification_read_all, name="notification_read_all"),
    # TEMPORARY — remove on/after 2026-08-10. Public, unauthenticated comment API for
    # the copy-review gallery (copy-review.pastlives.space). NOT member-facing; not
    # gated by SurfaceMiddleware (no /copy-review/ prefix in MEMBER_ONLY_PATH_PREFIXES),
    # so it resolves on the public book surface the gallery JS calls cross-origin.
    path("copy-review/comments/", copy_review_views.comments, name="copy_review_comments"),
    path("copy-review/comments/<int:pk>/edit/", copy_review_views.comment_edit, name="copy_review_comment_edit"),
    path("copy-review/comments/<int:pk>/delete/", copy_review_views.comment_delete, name="copy_review_comment_delete"),
    # END TEMPORARY (copy-review comments)
    # Signage slideshow (public kiosk). Registered LAST so any real member route
    # wins — a bare /<zone-slug>/ only falls through to the player when nothing else
    # matched. Both views raise Http404 off the signage surface (see core.views), and
    # the middleware allowlist only permits these names on slideshow.pastlives.space.
    path("<slug:zone_slug>/", views.signage_player, name="signage_player"),
    path("<slug:zone_slug>/deck/", views.signage_deck, name="signage_deck"),
]
