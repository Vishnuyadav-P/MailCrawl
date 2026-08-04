"""
Background maintenance loop.
"""

import asyncio

from src.utils.logging import logger
from src.utils.urls import canonicalize_url, get_url_priority, is_same_registered_domain
from src.validation.domain_validator import check_domain_mx_records, check_host_ssrf
from web import settings
from web.jobs.registry import registry

# Caches that make a scan fast but must not outlive it. The DNS-backed two answer
# from the network, so a transient failure would otherwise pin a host to a stale
# verdict for the life of the process; the URL caches simply hold memory that the
# next scan of a different domain will never read.
_SCAN_CACHES = (
    check_domain_mx_records,
    check_host_ssrf,
    canonicalize_url,
    get_url_priority,
    is_same_registered_domain,
)


async def cleanup_loop() -> None:
    """
    Evicts finished jobs and drops per-scan caches once the server goes idle.

    Clearing only while idle keeps an in-flight scan's lookups warm — a scan is the
    whole reason those caches exist.
    """
    while True:
        try:
            await asyncio.sleep(settings.CLEANUP_INTERVAL_SECONDS)
            registry.evict_expired()

            if not registry.active():
                for cache in _SCAN_CACHES:
                    cache.cache_clear()

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"Cleanup loop error: {exc}")
