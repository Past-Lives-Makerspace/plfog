from django.urls import path

from hours import views

app_name = "hours"

urlpatterns = [
    path("", views.my_hours, name="my_hours"),
    path("save/", views.hours_save, name="save"),
    path("dates/", views.dates_add, name="dates_add"),
    path("days-off/", views.day_off_save, name="day_off_save"),
    path("days-off/preview/", views.day_off_preview, name="day_off_preview"),
    path("days-off/<int:pk>/delete/", views.day_off_delete, name="day_off_delete"),
]
