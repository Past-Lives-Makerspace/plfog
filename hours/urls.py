from django.urls import path

from hours import views

app_name = "hours"

urlpatterns = [
    path("", views.my_hours, name="my_hours"),
    path("save/", views.hours_save, name="save"),
    path("dates/", views.dates_add, name="dates_add"),
]
