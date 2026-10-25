from django.urls import path

from polls import views

app_name = "polls"

urlpatterns = [
    path("", views.polls_index, name="index"),
    path("<int:pk>/vote/", views.poll_vote, name="vote"),
    path("manage/", views.polls_admin, name="admin"),
    path("manage/new/", views.polls_admin_new, name="admin_new"),
    path("manage/<int:pk>/", views.polls_admin_poll, name="admin_poll"),
    path("manage/<int:pk>/close/", views.polls_admin_close, name="admin_close"),
]
