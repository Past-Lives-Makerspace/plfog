"""Core app URL configuration."""

from django.urls import path

from . import views

urlpatterns = [
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
]
