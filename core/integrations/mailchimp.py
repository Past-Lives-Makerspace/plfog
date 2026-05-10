"""Mailchimp v3 REST client.

Reads credentials from ``core.models.SiteConfiguration``. All public methods
return ``bool`` and never raise — failures are logged, the caller decides what
to do. We deliberately do not pull in the official SDK; the v3 surface we use
is tiny and a bare ``requests`` client keeps tests fast and the dep tree small.

The single audience referenced by ``mailchimp_list_id`` is segmented via tags
(``class-registrant``, ``newsletter``, ``member``) so one Mailchimp list backs
every subscribe path the app cares about.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)


_DEFAULT_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class MailchimpConfig:
    """Resolved Mailchimp credentials. Use ``MailchimpClient.from_site_config()``."""

    api_key: str
    list_id: str

    @property
    def datacenter(self) -> str:
        """Mailchimp API keys end in ``-<datacenter>`` (e.g. ``abc123-us17``)."""
        if "-" not in self.api_key:
            raise ValueError("Mailchimp API key is malformed (missing datacenter suffix)")
        return self.api_key.rsplit("-", 1)[1]

    @property
    def base_url(self) -> str:
        return f"https://{self.datacenter}.api.mailchimp.com/3.0"


class MailchimpClient:
    """Minimal Mailchimp v3 client. Disabled when site config is missing."""

    def __init__(self, config: MailchimpConfig | None) -> None:
        self.config = config

    @classmethod
    def from_site_config(cls) -> MailchimpClient:
        """Build a client from the singleton ``SiteConfiguration``.

        Returns a disabled client when api_key or list_id is blank — callers
        check ``client.enabled`` (or rely on ``subscribe()`` returning False).
        """
        from core.models import SiteConfiguration

        site = SiteConfiguration.load()
        if not site.mailchimp_api_key or not site.mailchimp_list_id:
            return cls(config=None)
        return cls(
            config=MailchimpConfig(
                api_key=site.mailchimp_api_key,
                list_id=site.mailchimp_list_id,
            ),
        )

    @property
    def enabled(self) -> bool:
        return self.config is not None

    def subscribe(
        self,
        *,
        email: str,
        first_name: str = "",
        last_name: str = "",
        tags: list[str] | None = None,
    ) -> bool:
        """Add or update a subscriber in the configured audience.

        Uses the v3 ``PUT /lists/{id}/members/{md5(email)}`` upsert endpoint so
        the call is idempotent — re-subscribing an existing member succeeds and
        does not flip ``unsubscribed`` users back to ``subscribed``.

        Args:
            email: Subscriber email. Lowercased before hashing.
            first_name: Stored as the ``FNAME`` merge field.
            last_name: Stored as the ``LNAME`` merge field.
            tags: Optional list of tag names applied to the subscriber.

        Returns:
            True on 2xx, False otherwise (including disabled state, network
            errors, malformed API key, and 4xx/5xx responses).
        """
        if not self.enabled:
            return False
        if self.config is None:  # pragma: no cover - guarded by self.enabled
            return False

        try:
            datacenter_url = self.config.base_url
        except ValueError:
            logger.warning("Mailchimp subscribe skipped: malformed API key (no datacenter suffix)")
            return False

        subscriber_hash = hashlib.md5(email.lower().encode()).hexdigest()  # noqa: S324
        url = f"{datacenter_url}/lists/{self.config.list_id}/members/{subscriber_hash}"
        payload: dict[str, object] = {
            "email_address": email,
            "status_if_new": "subscribed",
            "merge_fields": {"FNAME": first_name, "LNAME": last_name},
        }
        if tags is not None:
            payload["tags"] = tags

        try:
            response = requests.put(
                url,
                json=payload,
                auth=("anystring", self.config.api_key),
                timeout=_DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            logger.warning("Mailchimp subscribe network error for %s: %s", email, exc)
            return False

        if response.ok:
            return True
        logger.warning(
            "Mailchimp subscribe failed for %s: %s %s",
            email,
            response.status_code,
            response.text[:300],
        )
        return False
