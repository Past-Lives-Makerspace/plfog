import json
from pathlib import Path

import pytest


def describe_manifest_json():  # noqa: C901
    """Tests for PWA manifest.json file contents."""

    @pytest.fixture()
    def manifest():
        """Load the manifest.json file."""
        manifest_path = Path(__file__).parent.parent.parent / "static" / "manifest.json"
        with open(manifest_path) as f:
            return json.load(f)

    def it_is_valid_json(manifest):
        """Manifest should be parseable JSON."""
        assert isinstance(manifest, dict)

    def it_has_required_name_field(manifest):
        """Manifest must have a name field for PWA installation."""
        assert "name" in manifest
        assert manifest["name"] == "Past Lives Makerspace"

    def it_has_required_short_name_field(manifest):
        """Manifest must have a short_name for home screen display."""
        assert "short_name" in manifest
        assert manifest["short_name"] == "Past Lives"

    def it_has_required_start_url(manifest):
        """Manifest must have start_url set to root for PWA launch."""
        assert "start_url" in manifest
        assert manifest["start_url"] == "/"

    def it_has_required_display(manifest):
        """Manifest must have display set to standalone for native-like feel."""
        assert "display" in manifest
        assert manifest["display"] == "standalone"

    def it_has_required_theme_color(manifest):
        """Manifest must have theme_color matching brand navy."""
        assert "theme_color" in manifest
        assert manifest["theme_color"] == "#092E4C"

    def it_has_required_icons(manifest):
        """Manifest must have icons array with 3 icons for various sizes."""
        assert "icons" in manifest
        assert len(manifest["icons"]) == 3

    def it_has_maskable_icon(manifest):
        """At least one icon must have purpose 'maskable' for adaptive icons."""
        icons = manifest.get("icons", [])
        maskable_icons = [icon for icon in icons if icon.get("purpose") == "maskable"]
        assert len(maskable_icons) >= 1

    def it_has_192x192_icon(manifest):
        """Manifest should include a 192x192 icon for standard displays."""
        icons = manifest.get("icons", [])
        icon_192 = [icon for icon in icons if icon.get("sizes") == "192x192"]
        assert len(icon_192) >= 1

    def it_has_512x512_icon(manifest):
        """Manifest should include a 512x512 icon for high-res displays."""
        icons = manifest.get("icons", [])
        icon_512 = [icon for icon in icons if icon.get("sizes") == "512x512"]
        assert len(icon_512) >= 1

    def it_has_valid_icon_paths(manifest):
        """All icon paths should start with /static/."""
        icons = manifest.get("icons", [])
        for icon in icons:
            assert icon["src"].startswith("/static/")

    def it_has_background_color(manifest):
        """Manifest should have background_color for splash screen."""
        assert "background_color" in manifest
        assert manifest["background_color"] == "#092E4C"

    def it_has_scope(manifest):
        """Manifest should have scope set to root."""
        assert "scope" in manifest
        assert manifest["scope"] == "/"

    def it_has_description(manifest):
        """Manifest should have a description for app stores."""
        assert "description" in manifest
        assert "Past Lives Makerspace" in manifest["description"]
