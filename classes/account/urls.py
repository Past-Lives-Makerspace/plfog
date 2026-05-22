from __future__ import annotations

from django.urls import path
from django.views.generic import TemplateView

app_name = "account"

urlpatterns = [
    path(
        "",
        TemplateView.as_view(template_name="classes/account/_overview_stub.html"),
        name="overview",
    ),
]
