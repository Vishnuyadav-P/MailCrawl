"""
The scan worker.

Each scan gets its own OS thread running its own event loop. This is forced by the
crawler, not a preference: src/crawler/crawler.py calls blocking work from inside
async functions — socket.getaddrinfo for every URL's SSRF check, lxml parsing of
pages up to 25MB, PDF extraction up to 150MB. Sharing the server's loop would stall
the SSE stream and every other request behind them.

Running on a thread nothing awaits has a second benefit that is a requirement here:
closing the browser tab cannot cancel the scan.
"""

import asyncio
import datetime
import threading
from typing import Optional

from src.crawler.crawler import AsyncCrawler
from src.models.scan import ScanConfig, ScanRecord
from src.processing.deduplicator import deduplicate_and_process_emails
from src.utils.logging import logger, create_scan_file_logger
from src.utils.urls import is_same_registered_domain
from web.jobs.adapters import EmailBatchAdapter, ProgressAdapter
from web.jobs.registry import JobState, ScanCancelled, registry
from web.services import store


def _log(job: JobState, status: str, message: str, pages: int = 0,
         sites: int = 0, emails: int = 0) -> None:
    """Appends a lifecycle row to the shared scan log and writes to per-scan log file."""
    store.append_scan_log(ScanRecord(
        scan_id=job.scan_id,
        search_name=job.search_name,
        target_domain=job.target_domain,
        started_at=(job.started_at or job.created_at).isoformat(timespec="seconds"),
        finished_at=job.finished_at.isoformat(timespec="seconds") if job.finished_at else "",
        duration_seconds=job.duration_seconds or 0.0,
        status=status,
        pages_scanned=pages,
        sites_discovered=sites,
        unique_emails=emails,
        resumed_from=job.resumed_from,
        message=message,
    ))
    scan_logger, log_path = create_scan_file_logger(job.scan_id, job.target_domain)
    scan_logger.info(f"[{status.upper()}] {message} | pages: {pages}, sites: {sites}, emails: {emails} (Log: {log_path})")


def _set_phase(job: JobState, phase: str, message: str) -> None:
    job.phase = phase
    job.emit("phase", {"phase": phase, "message": message})


def run_scan(job: JobState) -> None:
    """Entry point for the worker thread."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    job.started_at = datetime.datetime.now()
    job.status = "running"

    _log(job, "running", "Resumed from checkpoint" if job.resume else "Scan started")

    progress_adapter = ProgressAdapter(job)
    email_adapter = EmailBatchAdapter(job)

    crawler = AsyncCrawler(
        job.config,
        on_progress=progress_adapter,
        on_email_batch=email_adapter,
        email_batch_size=job.reveal_every,
        scan_id=job.scan_id,
        resume=job.resume,
    )
    job.crawler = crawler

    try:
        _set_phase(job, "crawl", "Crawling")

        try:
            raw_occs, stats, errors = loop.run_until_complete(crawler.crawl())
        except ScanCancelled as exc:
            _finish_incomplete(job, crawler, "cancelled", exc)
            return
        except Exception as exc:
            _finish_incomplete(job, crawler, "interrupted", exc)
            return

        job.stats = stats
        job.scan_errors = list(errors)
        progress_adapter.flush()
        job.emit("stats", stats.model_dump())

        # --- Deduplication --------------------------------------------------
        # Synchronous and DNS-heavy (one MX lookup per unique host). Already on the
        # worker thread, so it blocks nothing that matters.
        _set_phase(job, "dedup", "Deduplicating results")
        final_results = deduplicate_and_process_emails(
            occurrences=raw_occs,
            registered_domain=job.config.target_domain,
            only_target_domain=job.config.only_target_domain,
        )

        stats.unique_emails = len(final_results)
        stats.target_domain_emails = sum(
            1 for r in final_results
            if is_same_registered_domain(r.email, job.config.target_domain)
        )

        # --- Persist --------------------------------------------------------
        _set_phase(job, "persist", "Saving results")
        store.save_results(job.scan_id, final_results, stats, job.config, crawler.crawl_graph)
        store.clear_checkpoint(job.scan_id)

        job.results = final_results
        job.stats = stats
        job.mark_terminal("completed")

        _log(job, "completed",
             f"{stats.unique_emails} unique email(s) across {stats.pages_scanned} page(s)",
             pages=stats.pages_scanned, sites=stats.sites_discovered,
             emails=stats.unique_emails)

        job.emit("stats", stats.model_dump())
        job.emit("done", {
            "status": "completed",
            "result_count": len(final_results),
            "duration_seconds": job.duration_seconds,
            "results_url": f"/api/scans/{job.scan_id}/results",
        })

    except ScanCancelled as exc:
        # Cancelled during a post-crawl phase; the checkpoint from the crawl survives.
        _finish_incomplete(job, crawler, "cancelled", exc)

    except Exception as exc:
        logger.error(f"Scan '{job.scan_id}' failed after the crawl: {exc}")
        _finish_incomplete(job, crawler, "failed", exc)

    finally:
        try:
            loop.close()
        except Exception:
            pass


def _finish_incomplete(job: JobState, crawler: AsyncCrawler, status: str,
                       exc: BaseException) -> None:
    """
    Records a scan that stopped early.

    The crawler has already written a checkpoint on its way out, and the checkpoint is
    only cleared after results are persisted — so a post-crawl failure leaves it intact
    too. Every one of these states is resumable, and a resumed 'failed' scan re-runs
    the deduplication step.
    """
    stats = crawler.stats
    job.stats = stats
    job.error = {"type": type(exc).__name__, "message": str(exc)}
    job.mark_terminal(status)

    message = (
        "Cancelled by request" if status == "cancelled"
        else f"{type(exc).__name__}: {exc}"
    )

    _log(job, status, message,
         pages=stats.pages_scanned,
         sites=stats.sites_discovered,
         emails=len(crawler.unique_emails))

    logger.warning(
        f"Scan '{job.scan_id}' ended as '{status}' after {stats.pages_scanned} page(s). "
        f"Checkpoint retained for resume."
    )

    event = "cancelled" if status == "cancelled" else "failed"
    job.emit(event, {
        "status": status,
        "error": job.error,
        "pages_scanned": stats.pages_scanned,
        "resumable": True,
        "message": "Progress was checkpointed — resume it from the scan history.",
    })


def start_job(job: JobState, loop: asyncio.AbstractEventLoop) -> JobState:
    """Registers a job and launches its worker thread."""
    job._loop = loop
    registry.add(job)

    thread = threading.Thread(
        target=run_scan,
        args=(job,),
        name=f"scan-{job.scan_id}",
        daemon=False,
    )
    job._thread = thread
    thread.start()

    return job


def build_job(
    scan_id: str,
    search_name: str,
    config: ScanConfig,
    reveal_every: int,
    resume: bool = False,
    resumed_from: Optional[str] = None,
) -> JobState:
    return JobState(
        scan_id=scan_id,
        search_name=search_name,
        target_domain=config.target_domain,
        config=config,
        reveal_every=reveal_every,
        resume=resume,
        resumed_from=resumed_from,
    )
