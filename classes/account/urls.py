from __future__ import annotations

from django.urls import path

from classes.account import views

app_name = "account"

urlpatterns = [
    path("", views.OverviewView.as_view(), name="overview"),
    path("history/", views.HistoryView.as_view(), name="history"),
    path("receipts/", views.ReceiptsView.as_view(), name="receipts"),
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("lookup/", views.LookupView.as_view(), name="lookup"),
    path("onboarding/", views.OnboardingStep1View.as_view(), name="onboarding_step1"),
    path("onboarding/2/", views.OnboardingStep2View.as_view(), name="onboarding_step2"),
    path("onboarding/3/", views.OnboardingStep3View.as_view(), name="onboarding_step3"),
    path("onboarding/questions/", views.OnboardingQuestionsView.as_view(), name="onboarding_questions"),
]
