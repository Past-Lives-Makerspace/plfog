"""The ``class_published`` email now leads with the class's hero image.

The image is injected as a trusted, app-built SafeString
(:attr:`ClassOffering.email_hero_image_html`) into the curated copy fragment, so a class
with a photo shows it at the top of the "new class" email, and a class without one renders
no broken ``<img>``.
"""

from __future__ import annotations

import pytest

from classes.factories import ClassOfferingFactory
from core.events.registry import Channel
from core.events.templates import rendered_copy

pytestmark = pytest.mark.django_db


def describe_email_hero_image_html():
    def it_renders_an_img_when_the_class_has_a_hero():
        offering = ClassOfferingFactory()  # the factory gives every class a real hero image
        html = offering.email_hero_image_html
        assert html.startswith("<img ")
        assert offering.image.url in html
        assert f'alt="{offering.title}"' in html

    def it_is_empty_when_the_class_has_no_image():
        # image="", gallery=0, and the factory category carries no hero, so display_images is empty.
        offering = ClassOfferingFactory(image="", gallery=0)
        assert offering.email_hero_image_html == ""


def describe_class_published_email_body():
    def it_injects_the_hero_image_into_the_email_html():
        offering = ClassOfferingFactory(title="Forge Night")
        context = {
            "class_title": offering.title,
            "class_url": "https://pastlives.example/classes/forge-night/",
            "class_image_html": offering.email_hero_image_html,
        }
        rendered = rendered_copy("class_published", Channel.EMAIL, context)
        assert "<img " in rendered.body_html  # the SafeString passed through unescaped
        assert offering.image.url in rendered.body_html
        assert "just went live" in rendered.body_html

    def it_omits_the_image_when_the_class_has_none():
        offering = ClassOfferingFactory(image="", gallery=0, title="Forge Night")
        context = {
            "class_title": offering.title,
            "class_url": "https://pastlives.example/classes/forge-night/",
            "class_image_html": offering.email_hero_image_html,
        }
        rendered = rendered_copy("class_published", Channel.EMAIL, context)
        assert "<img " not in rendered.body_html
        assert "just went live" in rendered.body_html
