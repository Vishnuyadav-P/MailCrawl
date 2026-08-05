"""
Results and exports.

Filtering happens here rather than in the browser so that what is on screen and what
is downloaded come from the same predicate and cannot drift apart. Exports are plain
GETs, which lets the frontend use ordinary download links instead of assembling blobs.
"""

import asyncio
import datetime
from typing import List, Optional, Tuple
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response, status

from src.export.csv_exporter import generate_csv_bytes
from src.export.excel_exporter import generate_excel_bytes
from src.models.email import CanonicalEmailResult, EmailOccurrence
from src.models.scan import ScanConfig, ScanError, ScanStats
from src.processing.deduplicator import deduplicate_and_process_emails
from web import settings
from web.jobs.registry import registry
from web.services import store
from web.services.filters import FilterSpec, apply_filters, build_facets

router = APIRouter(prefix="/api", tags=["results"])

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def _load_scan_payload(
    scan_id: str,
) -> Tuple[List[CanonicalEmailResult], Optional[ScanStats], Optional[ScanConfig], List[ScanError], str]:
    """
    Resolves a scan's results from memory (finished or in-progress), falling back to disk
    (saved results or checkpoint).
    """
    job = registry.get(scan_id)

    if job:
        if job.results is not None:
            return job.results, job.stats, job.config, job.scan_errors, job.target_domain

        # Dynamically process live occurrences for in-flight scan
        raw_occs = getattr(job.crawler, "raw_occurrences", []) if job.crawler else []
        results = await asyncio.to_thread(
            deduplicate_and_process_emails,
            occurrences=raw_occs,
            registered_domain=job.config.target_domain,
            only_target_domain=job.config.only_target_domain,
        )
        return results, job.stats or ScanStats(domain=job.target_domain), job.config, job.scan_errors, job.target_domain

    payload = await store.aload_results(scan_id)
    if payload:
        try:
            results = [CanonicalEmailResult.model_validate(item) for item in payload.get("results", [])]
            stats = ScanStats.model_validate(payload["stats"]) if payload.get("stats") else None
            config = ScanConfig.model_validate(payload["config"]) if payload.get("config") else None
            target_domain = config.target_domain if config else (stats.domain if stats else scan_id)
            return results, stats, config, [], target_domain
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Saved results for '{scan_id}' could not be read: {exc}",
            ) from exc

    checkpoint = await store.aload_checkpoint(scan_id)
    if checkpoint:
        try:
            config = ScanConfig.model_validate(checkpoint["config"]) if checkpoint.get("config") else None
            stats = ScanStats.model_validate(checkpoint["stats"]) if checkpoint.get("stats") else None
            target_domain = config.target_domain if config else (stats.domain if stats else scan_id)
            raw_items = checkpoint.get("raw_occurrences", [])
            raw_occs = [EmailOccurrence.model_validate(item) for item in raw_items]
            errors = [ScanError.model_validate(item) for item in checkpoint.get("errors", [])]
            results = await asyncio.to_thread(
                deduplicate_and_process_emails,
                occurrences=raw_occs,
                registered_domain=target_domain,
                only_target_domain=config.only_target_domain if config else True,
            )
            return results, stats, config, errors, target_domain
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Checkpoint for '{scan_id}' could not be read: {exc}",
            ) from exc

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"No saved results or checkpoint found for '{scan_id}'.",
    )


def _spec(q, email_type, validation_status, domain_match, min_confidence) -> FilterSpec:
    return FilterSpec.from_query(
        q=q,
        email_type=email_type,
        validation_status=validation_status,
        domain_match=domain_match,
        min_confidence=min_confidence,
    )


@router.get("/scans/{scan_id}/results")
async def get_results(
    scan_id: str,
    q: Optional[str] = None,
    email_type: Optional[str] = None,
    validation_status: Optional[str] = None,
    domain_match: Optional[str] = None,
    min_confidence: int = Query(default=0, ge=0, le=100),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=settings.DEFAULT_PAGE_SIZE, ge=1, le=settings.MAX_PAGE_SIZE),
):
    """Filtered, paged results plus the facet lists for the filter dropdowns."""
    results, stats, config, errors, target_domain = await _load_scan_payload(scan_id)

    spec = _spec(q, email_type, validation_status, domain_match, min_confidence)
    filtered = apply_filters(results, spec)

    return {
        "scan_id": scan_id,
        "target_domain": target_domain,
        "total": len(results),
        "filtered_total": len(filtered),
        "offset": offset,
        "limit": limit,
        # Facets come from the unfiltered set so the dropdowns do not collapse
        # as filters are applied.
        "facets": build_facets(results),
        "stats": stats.model_dump() if stats else None,
        "config": config.model_dump() if config else None,
        "errors": [e.model_dump() for e in errors],
        "results": [r.model_dump() for r in filtered[offset:offset + limit]],
    }


@router.get("/scans/{scan_id}/crawl-map")
async def get_crawl_map(scan_id: str):
    """Compact persisted crawl graph for an interactive client-side renderer."""
    job = registry.get(scan_id)
    if job and job.is_active():
        crawler_graph = getattr(job, "crawl_graph", None)
        return crawler_graph or {"nodes": [], "edges": []}
    payload = await store.aload_results(scan_id)
    if not payload:
        raise HTTPException(status_code=404, detail=f"No saved results found for '{scan_id}'.")
    graph = payload.get("crawl_graph") or {"nodes": {}, "edges": []}
    return {"nodes": list(graph.get("nodes", {}).values()) if isinstance(graph.get("nodes"), dict) else graph.get("nodes", []), "edges": graph.get("edges", [])}


def _attachment(filename: str) -> str:
    """Content-Disposition with an ASCII fallback plus RFC 5987 encoding."""
    ascii_name = filename.encode("ascii", "ignore").decode() or "export"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


@router.get("/scans/{scan_id}/export.csv")
async def export_csv(
    scan_id: str,
    q: Optional[str] = None,
    email_type: Optional[str] = None,
    validation_status: Optional[str] = None,
    domain_match: Optional[str] = None,
    min_confidence: int = Query(default=0, ge=0, le=100),
):
    results, _stats, _config, _errors, target_domain = await _load_scan_payload(scan_id)
    filtered = apply_filters(results, _spec(q, email_type, validation_status, domain_match, min_confidence))

    today = datetime.date.today().strftime("%Y-%m-%d")
    data = await asyncio.to_thread(generate_csv_bytes, filtered)

    return Response(
        content=data,
        media_type="text/csv",
        headers={"Content-Disposition": _attachment(f"{target_domain}_emails_{today}.csv")},
    )


@router.get("/scans/{scan_id}/export.xlsx")
async def export_xlsx(
    scan_id: str,
    q: Optional[str] = None,
    email_type: Optional[str] = None,
    validation_status: Optional[str] = None,
    domain_match: Optional[str] = None,
    min_confidence: int = Query(default=0, ge=0, le=100),
):
    results, stats, _config, errors, target_domain = await _load_scan_payload(scan_id)
    filtered = apply_filters(results, _spec(q, email_type, validation_status, domain_match, min_confidence))

    today = datetime.date.today().strftime("%Y-%m-%d")
    # openpyxl formats every cell in a loop, so this is genuinely CPU-bound.
    data = await asyncio.to_thread(
        generate_excel_bytes, filtered, stats or ScanStats(domain=target_domain), errors
    )

    return Response(
        content=data,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": _attachment(f"{target_domain}_emails_{today}.xlsx")},
    )
