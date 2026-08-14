import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.crawler.crawler import AsyncCrawler
from src.models.email import EmailOccurrence
from src.models.scan import ScanConfig
from web.jobs.registry import JobState, registry
from web.routes.results import _load_scan_payload, export_csv, export_xlsx


@pytest.mark.asyncio
async def test_pdf_checkpoint_syncing(tmp_path, monkeypatch):
    """Verifies that PDF processing tracks visited URLs and checkpoints progress correctly."""
    config = ScanConfig(target_domain="example.com", include_pdfs=True)
    crawler = AsyncCrawler(config=config, scan_id="test_scan_pdf_1")

    # Add mock pdf url
    pdf_url = "https://example.com/document.pdf"
    crawler.pdf_urls_to_process.add(pdf_url)

    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"%PDF-1.4 test content"
    mock_client.get.return_value = mock_resp

    monkeypatch.setattr("src.crawler.crawler.extract_emails_from_pdf_bytes", lambda *args, **kwargs: [
        EmailOccurrence(email="pdf_user@example.com", source_url=pdf_url, source_type="pdf")
    ])

    sem = asyncio.Semaphore(5)
    await crawler._process_pdf_urls([pdf_url], mock_client, sem)

    # Verify pdf_url was added to visited_urls and stats were updated
    assert pdf_url in crawler.visited_urls
    assert crawler.stats.pdf_files == 1
    assert any(occ.email == "pdf_user@example.com" for occ in crawler.raw_occurrences)

    # Verify running _process_pdf_urls again skips already visited PDF url
    mock_client.get.reset_mock()
    await crawler._process_pdf_urls([pdf_url], mock_client, sem)
    mock_client.get.assert_not_called()
    assert crawler.stats.pdf_files == 1


@pytest.mark.asyncio
async def test_download_listed_mails_whenever(tmp_path, monkeypatch):
    """Verifies that listed emails can be downloaded during active scan or from checkpoint."""
    config = ScanConfig(target_domain="example.com")
    job = JobState(
        scan_id="test_active_scan_1",
        search_name="Active Scan Test",
        target_domain="example.com",
        config=config,
        reveal_every=10,
        status="running",
    )
    crawler = AsyncCrawler(config=config, scan_id="test_active_scan_1")
    crawler.raw_occurrences.append(
        EmailOccurrence(email="live_contact@example.com", source_url="https://example.com/about", source_type="page")
    )
    job.crawler = crawler
    registry.add(job)

    try:
        # Load scan payload for active scan
        results, stats, loaded_config, errors, target_domain = await _load_scan_payload("test_active_scan_1")
        assert len(results) == 1
        assert results[0].email == "live_contact@example.com"
        assert target_domain == "example.com"

        # Export CSV during active scan
        response = await export_csv("test_active_scan_1")
        assert response.status_code == 200
        assert b"live_contact@example.com" in response.body

        # Export XLSX during active scan
        response_xlsx = await export_xlsx("test_active_scan_1")
        assert response_xlsx.status_code == 200
        assert len(response_xlsx.body) > 0

    finally:
        registry.remove("test_active_scan_1")


def test_per_scan_logging():
    """Verifies that a log file containing domain and timestamp is created for every scan."""
    from src.utils.logging import create_scan_file_logger
    scan_id = "2026-08-04_15-00-00__example-com"
    domain = "example.com"
    logger_instance, log_path = create_scan_file_logger(scan_id, domain)

    logger_instance.info("Test log entry for per-scan logger")
    assert log_path.exists()
    assert "example-com" in log_path.name
    assert "2026-08-04" in log_path.name
    content = log_path.read_text(encoding="utf-8")
    assert "Test log entry for per-scan logger" in content
