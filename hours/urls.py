from django.urls import path

from hours import views

app_name = "hours"

urlpatterns = [
    path("", views.my_hours, name="my_hours"),
]
