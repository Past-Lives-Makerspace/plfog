"""Core app URL configuration."""

from django.urls import path

from . import views

urlpatterns = [
    # Cross-surface session relay (SSO between members and book surfaces)
    path("auth/relay/", views.relay_issue, name="relay_issue"),
    path("auth/relay/accept/", views.relay_accept, name="relay_accept"),
    # Health check
    path("health/", views.health_check, name="health_check"),
    # Home page
    path("", views.home, name="home"),
    # Clear pending login stage and restart
    path("accounts/restart-login/", views.restart_login, name="restart_login"),
    # Find account by name
    path("accounts/find-account/", views.find_account, name="find_account"),
    # Public newsletter signup (Mailchimp)
    path("newsletter/", views.newsletter_signup, name="newsletter_signup"),
    # Service worker (served with Service-Worker-Allowed header)
    path("sw.js", views.service_worker, name="service_worker"),
    # WebPush endpoints
    path("webpush/vapid-key/", views.vapid_key, name="webpush_vapid_key"),
    path("webpush/subscribe/", views.subscribe, name="webpush_subscribe"),
    path("webpush/unsubscribe/", views.unsubscribe, name="webpush_unsubscribe"),
    # Staff activity dashboard
    path("manage/activity/", views.site_activity, name="manage_activity"),
    # Notification bell feed
    path("notifications/", views.notification_feed, name="notification_feed"),
    path("notifications/unread-count/", views.notification_unread_count, name="notification_unread_count"),
    path("notifications/<int:pk>/read/", views.notification_read, name="notification_read"),
    path("notifications/read-all/", views.notification_read_all, name="notification_read_all"),
]
