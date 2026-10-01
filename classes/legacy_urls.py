"""Old class page addresses from the Drupal site that used to live at classes.pastlives.space.

Mounted at the site root from ``plfog/urls.py``, beside ``classes.urls`` under ``/classes/``.
"""

from django.urls import re_path

from classes import views

urlpatterns = [
    re_path(r"^class/(?P<alias>[^/]+)/?$", views.legacy_class_page, name="legacy_class_page"),
]
