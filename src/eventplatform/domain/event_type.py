"""The event-type catalogue.

Closed set on purpose. An open set would let a typo create a permanent new series in
every aggregate, and there is no way to tell a typo from a new product event after the
fact.
"""

from __future__ import annotations

from enum import StrEnum


class EventType(StrEnum):
    PAGEVIEW = "pageview"
    CLICK = "click"
    FORM_SUBMIT = "form_submit"
    CONVERSION = "conversion"
    EMAIL_OPEN = "email_open"
    EMAIL_CLICK = "email_click"
    AD_IMPRESSION = "ad_impression"
    AD_CLICK = "ad_click"
    REGISTRATION = "registration"
    DONATION = "donation"
    MEMBERSHIP = "membership"
    RENEWAL = "renewal"


class Channel(StrEnum):
    WEB = "web"
    EMAIL = "email"
    AD = "ad"
    SOCIAL = "social"
    SMS = "sms"
    OFFLINE = "offline"


EVENT_TYPES: frozenset[str] = frozenset(t.value for t in EventType)
CHANNELS: frozenset[str] = frozenset(c.value for c in Channel)
