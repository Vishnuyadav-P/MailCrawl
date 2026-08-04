"""
Robots.txt parsing and compliance manager.

Rules are tracked per origin, because a crawl spans every reachable host under the
registered domain and 'careers.example.com/robots.txt' says nothing about what
'www.example.com' permits.
"""

from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from src.utils.config import Config
from src.utils.logging import logger

# Probed directly when robots.txt cannot be read. Its 'Sitemap:' lines are normally
# the authoritative pointer to a site's sitemaps, so when the file is unreachable the
# well-known locations have to be tried by hand instead.
FALLBACK_SITEMAP_PATHS = (
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap-index.xml",
    "/sitemap/sitemap.xml",
    "/sitemaps/sitemap.xml",
    "/wp-sitemap.xml",
)


class RobotsChecker:
    """
    Manages robots.txt rules for every host visited during a scan.

    robots.txt is always fetched and parsed regardless of `respect_rules`, because
    its 'Sitemap:' lines are a primary source of URLs. Only enforcement of the
    Disallow rules is optional.
    """
    def __init__(
        self,
        user_agent: str = Config.DEFAULT_USER_AGENT,
        respect_rules: bool = True
    ):
        self.user_agent = user_agent
        self.respect_rules = respect_rules
        self.parsers: dict[str, RobotFileParser | None] = {}
        self.sitemaps: list[str] = []
        self.ignored_rule_count = 0
        # Origins whose robots.txt could not be read, so their sitemaps were guessed
        self.unreadable_origins: set[str] = set()

    @staticmethod
    def parse_robots_body(body: str) -> RobotFileParser:
        """
        Parses robots.txt, tolerating blank lines inside a record.

        RobotFileParser ends a record at the first blank line, so a file that spaces
        its directives out —

            User-agent: *
            <blank>
            Disallow: /search/

        — leaves every Disallow orphaned and silently unenforced. Real sites do this
        (wipro.com among them). Dropping whitespace-only lines keeps each record
        intact; records still separate correctly because the parser also closes one
        when a new User-agent line follows directives.
        """
        parser = RobotFileParser()
        parser.parse([line for line in body.splitlines() if line.strip()])
        return parser

    @staticmethod
    def _origin_key(url: str) -> str:
        """Returns the lowercased 'scheme://host' a robots.txt applies to."""
        parsed = urlparse(url if "://" in url else f"https://{url}")
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"

    def register_fallback_sitemaps(self, base_url: str) -> int:
        """
        Queues the well-known sitemap locations for an origin, returning how many were new.

        Used when robots.txt is unreadable and cannot point at the real sitemap.
        """
        origin = self._origin_key(base_url)
        self.unreadable_origins.add(origin)

        added = 0
        for path in FALLBACK_SITEMAP_PATHS:
            candidate = f"{origin}{path}"
            if candidate not in self.sitemaps:
                self.sitemaps.append(candidate)
                added += 1

        return added

    async def fetch_and_parse(self, base_url: str, client: httpx.AsyncClient) -> None:
        """Fetches and parses robots.txt for the origin of base_url."""
        origin = self._origin_key(base_url)
        if origin in self.parsers:
            return

        # Recorded up front so a failed fetch is not retried for every URL on the host
        self.parsers[origin] = None

        robots_url = f"{origin}/robots.txt"
        try:
            resp = await client.get(robots_url, timeout=5.0, follow_redirects=True)

            if resp.status_code == 200:
                self.parsers[origin] = self.parse_robots_body(resp.text)

                # Collect sitemaps from robots.txt lines
                host_sitemaps = 0
                for line in resp.text.splitlines():
                    if line.lower().startswith("sitemap:"):
                        sitemap_url = line.split(":", 1)[1].strip()
                        if sitemap_url and sitemap_url not in self.sitemaps:
                            self.sitemaps.append(sitemap_url)
                            host_sitemaps += 1

                logger.info(
                    f"Successfully loaded robots.txt from '{robots_url}'. "
                    f"Sitemaps found: {host_sitemaps}"
                )

            elif resp.status_code in (401, 403):
                # A forbidden robots.txt hides the Sitemap: pointers rather than the
                # sitemap itself, which is usually served fine, so go straight to it.
                # Per RFC 9309 a 4xx robots.txt does not by itself forbid crawling.
                added = self.register_fallback_sitemaps(origin)
                logger.warning(
                    f"robots.txt at '{robots_url}' is forbidden (HTTP {resp.status_code}). "
                    f"Going directly to {added} well-known sitemap location(s) instead."
                )

            else:
                added = self.register_fallback_sitemaps(origin)
                logger.info(
                    f"No robots.txt at '{robots_url}' (HTTP {resp.status_code}). "
                    f"Trying {added} well-known sitemap location(s) instead."
                )

        except Exception as exc:
            # An unreachable robots.txt leaves the sitemap just as unknown as a 403 does
            added = self.register_fallback_sitemaps(origin)
            logger.warning(
                f"Could not retrieve robots.txt from '{robots_url}': {exc}. "
                f"Trying {added} well-known sitemap location(s) instead."
            )

    def can_fetch(self, url: str) -> bool:
        """Returns True if the URL may be crawled."""
        parser = self.parsers.get(self._origin_key(url))
        if not parser:
            return True

        try:
            allowed = parser.can_fetch(self.user_agent, url)
        except Exception:
            return True

        if allowed:
            return True

        if not self.respect_rules:
            self.ignored_rule_count += 1
            return True

        return False

    def crawl_delay_seconds(self, url: str) -> float:
        """
        Returns the Crawl-delay a host asks for, or 0.0.

        This is honoured even when Disallow rules are being ignored: the delay exists
        to protect the server from load, which is a separate concern from which paths
        the operator would rather were left alone.
        """
        parser = self.parsers.get(self._origin_key(url))
        if not parser:
            return 0.0

        try:
            delay = parser.crawl_delay(self.user_agent)
        except Exception:
            return 0.0

        try:
            return max(0.0, float(delay)) if delay is not None else 0.0
        except (TypeError, ValueError):
            return 0.0
