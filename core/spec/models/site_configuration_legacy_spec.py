import pytest
from core.models import SiteConfiguration


def describe_SiteConfiguration():
    def describe_legacy_cms_fields():
        def it_has_legacy_cms_sync_enabled_defaulting_to_false(db):
            config = SiteConfiguration.load()
            assert config.legacy_cms_sync_enabled is False

        def it_has_legacy_cms_last_synced_at_defaulting_to_none(db):
            config = SiteConfiguration.load()
            assert config.legacy_cms_last_synced_at is None
