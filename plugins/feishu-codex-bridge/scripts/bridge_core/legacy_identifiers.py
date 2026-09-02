"""Exact pre-glossary identifiers used only for read-only quarantine or migration.

Nothing in this module grants producer authority. Current executable protocol
names live elsewhere and use Bridge, Dial, Page, Beeper, Responder, and Final
Callback exclusively.
"""

from __future__ import annotations


RETIRED_QUEUE_ROOT_NAME = "desktop-router"
RETIRED_SESSION_OWNER = "desktop-router"
RETIRED_PRODUCER_NAMESPACE = "experimental-gateway-v1"
RETIRED_BEEPER_HOST_FIELD = "host_id"
