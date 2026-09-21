import pytest
from django.core.exceptions import ValidationError
from core.models import SiteConfiguration

pytestmark = pytest.mark.django_db


def describe_site_configuration_member_agreement() -> None:
    def it_clean_requires_url_when_enforced() -> None:
        config = SiteConfiguration.load()
        config.member_agreement_required = True
        config.member_agreement_url = ""

        with pytest.raises(ValidationError) as exc_info:
            config.clean()

        assert "member_agreement_url" in exc_info.value.error_dict
        assert (
            exc_info.value.error_dict["member_agreement_url"][0].message
            == "Required when Member Agreement is enforced."
        )

    def it_clean_passes_when_valid() -> None:
        config = SiteConfiguration.load()
        config.member_agreement_required = True
        config.member_agreement_url = "https://example.com/agreement"
        config.clean()  # Should not raise

        config.member_agreement_required = False
        config.member_agreement_url = ""
        config.clean()  # Should not raise
