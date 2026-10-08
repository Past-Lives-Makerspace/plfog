from django.urls import path

from polls import views

app_name = "polls"

urlpatterns = [
    path("", views.polls_index, name="index"),
    path("<int:pk>/vote/", views.poll_vote, name="vote"),
]
