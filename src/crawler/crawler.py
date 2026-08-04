"""
Asynchronous web crawler orchestrator.
"""

import asyncio
import heapq
import time
from typing import Callable, Dict, List, Optional, Set

import httpx

from src.crawler.discovery import discover_seed_urls
from src.crawler.external_sources import scan_external_sources, select_external_sources
from src.crawler.playwright_fetcher import PlaywrightFetcher
from src.crawler.robots import RobotsChecker
from src.crawler.subdomains import discover_subdomain_origins
from src.crawler.wellknown_sources import collect_wellknown_emails
from src.extraction.context_extractor import extract_surrounding_context
from src.extraction.email_extractor import extract_emails_from_text, process_mailto_emails
from src.extraction.html_extractor import extract_from_html
from src.extraction.language import detect_language
from src.extraction.pdf_extractor import extract_emails_from_pdf_bytes
from src.models.email import EmailOccurrence
from src.models.scan import ScanConfig, ScanError, ScanProgress, ScanStats
from src.utils.config import Config
from src.utils.logging import logger
from src.utils.scan_store import load_checkpoint, save_checkpoint
from src.utils.urls import get_url_priority, normalize_domain_input
from src.validation.domain_validator import validate_url_ssrf


class AsyncCrawler:
    """Async crawler for web crawling, email extraction, and PDF handling."""

    def __init__(
        self,
        config: ScanConfig,
        on_progress: Optional[Callable[[ScanProgress], None]] = None,
        on_email_batch: Optional[Callable[[List[EmailOccurrence]], None]] = None,
        email_batch_size: int = 10,
        scan_id: Optional[str] = None,
        resume: bool = False
    ):
        self.config = config
        self.on_progress = on_progress
        self.on_email_batch = on_email_batch
        self.email_batch_size = max(1, email_batch_size)
        self.registered_domain, self.base_url = normalize_domain_input(config.target_domain)

        self.scan_id = scan_id
        self.resume = resume and scan_id is not None
        self.resumed_from_pages = 0

        self.visited_urls: Set[str] = set()
        self.queued_urls: Set[str] = set()

        self.raw_occurrences: List[EmailOccurrence] = []
        self.errors: List[ScanError] = []

        self.unique_emails: Set[str] = set()
        self._pending_email_batch: List[EmailOccurrence] = []

        self.site_origins: List[str] = []
        self.external_link_candidates: Set[str] = set()
        self.pdf_urls_to_process: Set[str] = set()
        # Compact event graph persisted with the scan; URLs are nodes, not a hard crawl limit.
        self.crawl_graph: Dict[str, Dict] = {"nodes": {}, "edges": []}

        # Restored from a checkpoint so a resumed scan does not redo finished phases
        self._completed_phases: Set[str] = set()
        self._pages_since_checkpoint = 0

        # Crawl frontier, a binary heap of (-priority, depth, url). Priority is negated
        # because heapq is a min-heap and the highest-scoring URL must come off first.
        # A heap is what makes an unbounded crawl viable: links surface continuously, so
        # a sorted list would have to be re-sorted after every batch — O(n log n) per
        # batch over a frontier that grows to the size of the site. Push and pop here
        # are O(log n) and the frontier is never re-scanned.
        #
        # The batch currently being fetched has already been popped off the heap, so a
        # checkpoint must put it back or those URLs are lost on resume.
        self._inflight: List[tuple[int, int, str]] = []
        self._queue: List[tuple[int, int, str]] = []

        self.stats = ScanStats(domain=self.registered_domain)
        self.robots_checker = RobotsChecker(respect_rules=config.respect_robots)
        self.playwright_fetcher: Optional[PlaywrightFetcher] = None

    async def crawl(self) -> tuple[List[EmailOccurrence], ScanStats, List[ScanError]]:
        """Executes the complete crawl job."""
        start_time_val = time.time()
        self.stats.start_time = time.strftime("%Y-%m-%d %H:%M:%S")

        # Initialize Playwright if requested or automatic
        if self.config.js_rendering in ("always", "automatic"):
            self.playwright_fetcher = PlaywrightFetcher()
            await self.playwright_fetcher.start()

        # Configure httpx client
        limits = httpx.Limits(
            max_keepalive_connections=self.config.concurrent_requests,
            max_connections=self.config.concurrent_requests * 2
        )
        timeout = httpx.Timeout(
            connect=float(self.config.timeout),
            read=float(self.config.timeout),
            write=float(self.config.timeout),
            pool=float(self.config.timeout)
        )
        headers = {"User-Agent": Config.DEFAULT_USER_AGENT}

        restored = self._restore_checkpoint()

        try:
            async with httpx.AsyncClient(limits=limits, timeout=timeout, headers=headers, follow_redirects=True) as client:
                self._queue = await self._prepare_queue(client, restored)
                queue = self._queue

                semaphore = asyncio.Semaphore(self.config.concurrent_requests)

                # 5. Main crawl loop
                while queue:
                    # Take the highest-priority URLs the concurrency limit allows.
                    # Links found while fetching are pushed straight back onto the
                    # heap, so a newly discovered /contact page outranks the rest of
                    # the frontier on the very next iteration.
                    batch_size = min(len(queue), self.config.concurrent_requests)
                    self._inflight = [heapq.heappop(queue) for _ in range(batch_size)]

                    tasks = [
                        self._process_url(
                            url=item[2],
                            depth=item[1],
                            client=client,
                            semaphore=semaphore,
                            queue=queue,
                            pdf_set=self.pdf_urls_to_process
                        )
                        for item in self._inflight
                    ]

                    await asyncio.gather(*tasks)
                    self._inflight = []

                    self._maybe_checkpoint(queue)

                # 6. Process PDFs if enabled. A resumed scan that already finished
                # this phase must not download every PDF a second time.
                if "pdfs" not in self._completed_phases:
                    if self.config.include_pdfs and self.pdf_urls_to_process:
                        await self._process_pdf_urls(
                            sorted(self.pdf_urls_to_process),
                            client,
                            asyncio.Semaphore(self.config.concurrent_requests),
                        )
                    self._mark_phase_complete("pdfs", queue)

                # 7. Scan off-domain company presences
                if "external" not in self._completed_phases:
                    if self.config.include_external_sources:
                        await self._scan_external_sources(client)
                    self._mark_phase_complete("external", queue)

                # Release any emails still short of a full batch
                self._flush_email_batch()

        except (KeyboardInterrupt, asyncio.CancelledError):
            # Persist progress before unwinding so the scan can be resumed
            self._write_checkpoint(self._queue)
            logger.warning(
                f"Scan interrupted after {self.stats.pages_scanned} page(s). "
                f"Progress was checkpointed and can be resumed."
            )
            raise
        except Exception:
            self._write_checkpoint(self._queue)
            logger.error(
                f"Scan failed after {self.stats.pages_scanned} page(s). "
                f"Progress was checkpointed and can be resumed."
            )
            raise
        finally:
            # Cleanup Playwright
            if self.playwright_fetcher:
                await self.playwright_fetcher.stop()

        finish_time_val = time.time()
        self.stats.finish_time = time.strftime("%Y-%m-%d %H:%M:%S")
        self.stats.scan_duration_seconds = round(finish_time_val - start_time_val, 2)
        self.stats.raw_email_occurrences = len(self.raw_occurrences)
        self.stats.error_count = len(self.errors)
        self.stats.robots_rules_ignored = self.robots_checker.ignored_rule_count

        return self.raw_occurrences, self.stats, self.errors

    async def _process_url(
        self,
        url: str,
        depth: int,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        queue: List[tuple[int, int, str]],
        pdf_set: Set[str]
    ) -> None:
        """Processes a single URL within semaphore limit."""
        if url in self.visited_urls:
            return

        self.visited_urls.add(url)

        # Check robots.txt
        if not self.robots_checker.can_fetch(url):
            self._graph_node(url, "page", "blocked")
            logger.info(f"URL '{url}' blocked by robots.txt")
            return

        # Check SSRF
        is_safe, ssrf_reason = validate_url_ssrf(url)
        if not is_safe:
            self._graph_node(url, "page", "blocked")
            self.errors.append(ScanError(url=url, error_type="SSRF Blocked", message=ssrf_reason))
            return

        async with semaphore:
            self.stats.pages_scanned += 1
            self._pages_since_checkpoint += 1
            self._notify_progress(
                f"Scanning {self.stats.pages_scanned} / {self.stats.pages_discovered}",
                current_url=url
            )

            # Crawl-delay is about server load, so it is honoured even when the
            # Disallow rules are being ignored.
            crawl_delay = self.robots_checker.crawl_delay_seconds(url)
            if crawl_delay > 0:
                await asyncio.sleep(min(crawl_delay, float(self.config.timeout)))

            try:
                resp = await client.get(url, timeout=float(self.config.timeout))
                if resp.status_code != 200:
                    self._graph_node(url, "page", "failed")
                    self.errors.append(ScanError(
                        url=url,
                        status_code=resp.status_code,
                        error_type="HTTP Error",
                        message=f"HTTP status code {resp.status_code}"
                    ))
                    return

                content_type = resp.headers.get("content-type", "").lower()
                if "pdf" in content_type:
                    pdf_set.add(url)
                    return

                if "html" not in content_type and "text" not in content_type:
                    return

                self.stats.html_pages += 1
                html_body = resp.text
                language = detect_language(html_body, dict(resp.headers))
                self._graph_node(url, "page", "crawled", language=language)

                # Extract content
                extracted = extract_from_html(html_body, url, self.registered_domain)

                # Fallback to Playwright if JS rendering is always or automatic with low text
                if (
                    self.playwright_fetcher
                    and (self.config.js_rendering == "always" or len(extracted.visible_text) < 200)
                ):
                    rendered_html = await self.playwright_fetcher.fetch_html(url, timeout_seconds=self.config.timeout)
                    if rendered_html:
                        extracted = extract_from_html(rendered_html, url, self.registered_domain)

                # Process mailto links
                mailto_occs = process_mailto_emails(extracted.mailto_emails, url, extracted.title)
                for occ in mailto_occs:
                    occ.context = extract_surrounding_context(extracted.visible_text, occ.email)
                for occ in mailto_occs:
                    occ.language = language
                self._record_occurrences(mailto_occs)

                # Process text emails
                text_occs = extract_emails_from_text(extracted.visible_text, url, extracted.title)
                for occ in text_occs:
                    occ.context = extract_surrounding_context(extracted.visible_text, occ.email)
                for occ in text_occs:
                    occ.language = language
                self._record_occurrences(text_occs)

                # Collect PDF links
                if self.config.include_pdfs:
                    pdf_set.update(extracted.pdf_links)

                # Collect off-domain links for the external source pass
                if self.config.include_external_sources:
                    self.external_link_candidates.update(extracted.external_links)

                # Enqueue internal links; max_depth of 0 means follow them all the
                # way down, which terminates because a site has finitely many URLs
                # and queued_urls never admits the same one twice.
                if self.config.max_depth <= 0 or depth < self.config.max_depth:
                    for link in extracted.internal_links:
                        if link not in self.visited_urls and link not in self.queued_urls:
                            self.queued_urls.add(link)
                            heapq.heappush(queue, (-get_url_priority(link), depth + 1, link))
                            self._graph_edge(url, link, "links_to")

                    self.stats.pages_discovered = len(self.queued_urls)

            except httpx.TimeoutException:
                self.errors.append(ScanError(url=url, error_type="Timeout", message="Request timed out"))
            except httpx.HTTPError as exc:
                self.errors.append(ScanError(url=url, error_type="HTTP Error", message=str(exc)))
            except Exception as exc:
                self.errors.append(ScanError(url=url, error_type="Crawler Error", message=str(exc)))

    def _graph_node(self, url: str, kind: str, status: str, **extra) -> None:
        node = self.crawl_graph["nodes"].setdefault(url, {"id": url, "type": kind, "status": status, "contacts": 0})
        node.update({"type": kind, "status": status, **extra})

    def _graph_edge(self, source: str, target: str, relation: str) -> None:
        edge = {"source": source, "target": target, "relation": relation}
        if edge not in self.crawl_graph["edges"]:
            self.crawl_graph["edges"].append(edge)

    async def _process_pdf_urls(
        self,
        pdf_urls: List[str],
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
    ) -> None:
        """Processes discovered PDFs concurrently while respecting the request limit."""
        if not pdf_urls:
            return

        pdf_total = len(pdf_urls)
        tasks = []
        for idx, pdf_url in enumerate(pdf_urls, start=1):
            async def run_pdf(pdf_url: str, idx: int, pdf_total: int) -> None:
                async with semaphore:
                    self._notify_progress(
                        f"Processing PDF {idx} / {pdf_total}",
                        pdf_processed=idx,
                        pdf_total=pdf_total,
                    )
                    await self._fetch_and_extract_pdf(
                        pdf_url,
                        client,
                        idx=idx,
                        total=pdf_total,
                        semaphore=semaphore,
                    )

            tasks.append(asyncio.create_task(run_pdf(pdf_url, idx, pdf_total)))

        await asyncio.gather(*tasks)

    async def _fetch_and_extract_pdf(
        self,
        pdf_url: str,
        client: httpx.AsyncClient,
        idx: Optional[int] = None,
        total: Optional[int] = None,
        semaphore: Optional[asyncio.Semaphore] = None,
    ) -> None:
        """Downloads PDF file and extracts emails."""
        is_safe, reason = validate_url_ssrf(pdf_url)
        if not is_safe:
            self.errors.append(ScanError(url=pdf_url, error_type="SSRF Blocked", message=reason))
            return

        try:
            resp = await client.get(pdf_url, timeout=float(self.config.timeout))
            if resp.status_code == 200:
                if len(resp.content) > Config.MAX_PDF_SIZE_BYTES:
                    self.errors.append(ScanError(
                        url=pdf_url,
                        error_type="File Size Limit",
                        message=f"PDF exceeded size limit ({len(resp.content)} bytes)"
                    ))
                    return

                self.stats.pdf_files += 1
                pdf_occs = extract_emails_from_pdf_bytes(resp.content, pdf_url, enable_ocr=self.config.enable_pdf_ocr, ocr_languages=self.config.ocr_languages)
                self._graph_node(pdf_url, "pdf", "crawled")
                self._record_occurrences(pdf_occs)
            else:
                self.errors.append(ScanError(
                    url=pdf_url,
                    status_code=resp.status_code,
                    error_type="HTTP Error",
                    message=f"HTTP status code {resp.status_code}"
                ))
        except Exception as exc:
            self.errors.append(ScanError(url=pdf_url, error_type="PDF Download Error", message=str(exc)))

    async def _prepare_queue(
        self,
        client: httpx.AsyncClient,
        restored: Optional[List[tuple[int, int, str]]]
    ) -> List[tuple[int, int, str]]:
        """
        Brings the crawl up to the point where the main loop can run.

        On a resumed scan the discovery phases are skipped entirely and the saved
        queue is returned, so the scan continues from exactly where it stopped.
        """
        if restored is not None:
            logger.info(
                f"Resuming scan '{self.scan_id}': {self.stats.pages_scanned} page(s) already "
                f"scanned, {len(restored)} URL(s) still queued, "
                f"{len(self.unique_emails)} unique email(s) recovered."
            )
            self._notify_progress(
                f"Resumed at page {self.stats.pages_scanned} — {len(restored)} URL(s) left"
            )
            return restored

        # 1. Enumerate every reachable site published under the registered domain
        if self.config.include_subdomains:
            self._notify_progress("Discovering sites on the domain...")
            self.site_origins = await discover_subdomain_origins(
                registered_domain=self.registered_domain,
                client=client,
                timeout=float(self.config.timeout),
                concurrency=self.config.concurrent_requests
            )
        else:
            self.site_origins = [self.base_url]

        self.stats.sites_discovered = len(self.site_origins)

        # 2. Fetch robots.txt for every site — rules do not carry across hosts
        self._notify_progress(f"Reading robots.txt for {len(self.site_origins)} site(s)...")
        await asyncio.gather(*[
            self.robots_checker.fetch_and_parse(origin, client)
            for origin in self.site_origins
        ])

        # 3. Standards-based contacts. Cheap and high-yield, so they run before
        # the crawl and reach the live feed almost immediately.
        if self.config.include_wellknown_sources:
            self._notify_progress("Reading security.txt, DNS and RDAP records...")
            wellknown_occs, wellknown_errors = await collect_wellknown_emails(
                registered_domain=self.registered_domain,
                origins=self.site_origins,
                client=client,
                timeout=float(self.config.timeout)
            )
            self.stats.wellknown_emails_found = len(wellknown_occs)
            self.errors.extend(wellknown_errors)
            self._record_occurrences(wellknown_occs)

        # 4. Discover seed URLs across every site
        self._notify_progress("Discovering URLs...")
        seed_items = await discover_seed_urls(
            base_urls=self.site_origins,
            registered_domain=self.registered_domain,
            client=client,
            known_sitemaps=self.robots_checker.sitemaps
        )

        # Build the frontier heap of (-priority, depth, url)
        queue: List[tuple[int, int, str]] = []
        for url, depth, priority in seed_items:
            if url not in self.queued_urls:
                self.queued_urls.add(url)
                queue.append((-priority, depth, url))

        # The domain's own page count is the crawl budget: every URL known for the
        # domain gets visited, and the total grows as new internal links surface.
        self.stats.pages_discovered = len(self.queued_urls)

        # Heapifying the whole seed set is O(n); pushing one at a time would be O(n log n).
        heapq.heapify(queue)

        # Discovery is expensive; save it before the first page is fetched so an
        # early interruption does not throw it away.
        self._write_checkpoint(queue)

        return queue

    # ----------------------------------------------------------------- #
    # Checkpointing
    # ----------------------------------------------------------------- #

    def _restore_checkpoint(self) -> Optional[List[tuple[int, int, str]]]:
        """
        Rebuilds crawler state from a saved checkpoint.

        Returns:
            Optional[List]: the pending queue when a checkpoint was loaded, else None.
        """
        if not self.resume or not self.scan_id:
            return None

        state = load_checkpoint(self.scan_id)
        if not state:
            logger.warning(f"No checkpoint found for scan '{self.scan_id}'; starting fresh.")
            return None

        try:
            self.visited_urls = set(state.get("visited_urls", []))
            self.queued_urls = set(state.get("queued_urls", []))
            self.site_origins = list(state.get("site_origins", []))
            self.external_link_candidates = set(state.get("external_link_candidates", []))
            self.pdf_urls_to_process = set(state.get("pdf_urls_to_process", []))
            self._completed_phases = set(state.get("completed_phases", []))

            self.raw_occurrences = [
                EmailOccurrence.model_validate(item) for item in state.get("raw_occurrences", [])
            ]
            self.errors = [
                ScanError.model_validate(item) for item in state.get("errors", [])
            ]
            self.stats = ScanStats.model_validate(state.get("stats", {}))
            self.unique_emails = {occ.email for occ in self.raw_occurrences}

            # Checkpoints store the human-readable (priority, depth, url); the heap
            # wants the priority negated. heapify restores the invariant in O(n),
            # which also makes the format forgiving of a hand-edited checkpoint.
            queue = [
                (-int(item[0]), int(item[1]), str(item[2]))
                for item in state.get("queue", [])
            ]
            heapq.heapify(queue)
        except Exception as exc:
            logger.error(
                f"Checkpoint for scan '{self.scan_id}' could not be restored ({exc}); starting fresh."
            )
            return None

        self.resumed_from_pages = self.stats.pages_scanned

        # Already-found emails are replayed so the live feed reflects the full set
        self._pending_email_batch = list(self.raw_occurrences)
        self._flush_email_batch()

        return queue

    def _checkpoint_state(self, queue: List[tuple[int, int, str]]) -> Dict:
        """Builds the serializable snapshot of everything needed to resume."""
        # An in-flight batch was popped off the heap but never finished, so it is
        # written back and its URLs are un-marked as visited to be retried. Order does
        # not matter — the restore path re-heapifies by priority.
        pending = [*self._inflight, *queue]
        inflight_urls = {item[2] for item in self._inflight}

        return {
            "scan_id": self.scan_id,
            "target_domain": self.config.target_domain,
            "config": self.config.model_dump(),
            "stats": self.stats.model_dump(),
            # Un-negate the priority so the checkpoint stays readable and matches the
            # score get_url_priority produced.
            "queue": [[-item[0], item[1], item[2]] for item in pending],
            "visited_urls": sorted(self.visited_urls - inflight_urls),
            "queued_urls": sorted(self.queued_urls),
            "site_origins": self.site_origins,
            "external_link_candidates": sorted(self.external_link_candidates),
            "pdf_urls_to_process": sorted(self.pdf_urls_to_process),
            "completed_phases": sorted(self._completed_phases),
            "raw_occurrences": [occ.model_dump() for occ in self.raw_occurrences],
            "errors": [err.model_dump() for err in self.errors],
        }

    def _write_checkpoint(self, queue: List[tuple[int, int, str]]) -> None:
        """Persists crawl state, if checkpointing is enabled for this scan."""
        if not self.scan_id or self.config.checkpoint_every_pages <= 0:
            return

        save_checkpoint(self.scan_id, self._checkpoint_state(queue))
        self._pages_since_checkpoint = 0

    def _maybe_checkpoint(self, queue: List[tuple[int, int, str]]) -> None:
        """Writes a checkpoint once enough pages have been scanned since the last one."""
        if not self.scan_id or self.config.checkpoint_every_pages <= 0:
            return

        if self._pages_since_checkpoint >= self.config.checkpoint_every_pages:
            self._write_checkpoint(queue)

    def _mark_phase_complete(self, phase: str, queue: List[tuple[int, int, str]]) -> None:
        """Records that a post-crawl phase finished, so a resume does not repeat it."""
        if phase in self._completed_phases:
            return

        self._completed_phases.add(phase)
        self._write_checkpoint(queue)

    async def _scan_external_sources(self, client: httpx.AsyncClient) -> None:
        """Scans off-domain company presences discovered while crawling."""
        source_urls = select_external_sources(
            candidate_urls=self.external_link_candidates,
            extra_urls=self.config.extra_source_urls
        )

        if not source_urls:
            return

        def report(idx: int, total: int, url: str) -> None:
            self._notify_progress(f"Scanning external source {idx} / {total}", current_url=url)

        occurrences, errors = await scan_external_sources(
            urls=source_urls,
            client=client,
            registered_domain=self.registered_domain,
            timeout=float(self.config.timeout),
            on_progress=report
        )

        self.stats.external_sources_scanned = len(source_urls)
        self.errors.extend(errors)
        self._record_occurrences(occurrences)

    def _record_occurrences(self, occurrences: List[EmailOccurrence]) -> None:
        for occurrence in occurrences:
            self._graph_node(occurrence.source_url, "pdf" if occurrence.source_type.startswith("pdf") else "page", "crawled")
            self.crawl_graph["nodes"][occurrence.source_url]["contacts"] += 1
            self._graph_node(f"email:{occurrence.email}", "email", "found")
            self._graph_edge(occurrence.source_url, f"email:{occurrence.email}", "produced_email")
        """Stores occurrences and queues newly seen addresses for the next batch release."""
        for occ in occurrences:
            self.raw_occurrences.append(occ)

            if occ.email in self.unique_emails:
                continue

            self.unique_emails.add(occ.email)
            self._pending_email_batch.append(occ)

        if len(self._pending_email_batch) >= self.email_batch_size:
            self._flush_email_batch()

    def _flush_email_batch(self) -> None:
        """Hands the pending batch of newly discovered emails to the consumer callback."""
        if not self._pending_email_batch:
            return

        batch = self._pending_email_batch
        self._pending_email_batch = []

        if self.on_email_batch:
            self.on_email_batch(batch)

    def _notify_progress(
        self,
        msg: str,
        current_url: str = "",
        pdf_processed: int = 0,
        pdf_total: int = 0
    ) -> None:
        """Triggers progress callback if set."""
        if self.on_progress:
            self.on_progress(ScanProgress(
                status_message=msg,
                pages_discovered=self.stats.pages_discovered,
                pages_scanned=self.stats.pages_scanned,
                current_url=current_url,
                raw_emails_found=len(self.raw_occurrences),
                unique_emails_found=len(self.unique_emails),
                pdf_processed_count=pdf_processed,
                pdf_total_count=pdf_total
            ))
