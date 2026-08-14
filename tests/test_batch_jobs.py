"""
Batch job registry, upload ceiling and concurrency caps.

These three used to be the same bug in different clothes: the validation and
SignalHire endpoints accepted unlimited work, held every result forever, and read
whole uploads into memory unchecked.
"""

import io
from datetime import datetime, timedelta

import pytest
from starlette.testclient import TestClient

from web import settings
from web.jobs.batch import BatchJob, BatchRegistry
from web.routes import signalhire as signalhire_routes
from web.routes import validate as validate_routes
from web.server import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean_registries():
    """Both registries are process-wide singletons, so a test must not leak into the next."""
    yield
    for registry in (validate_routes.validation_registry, signalhire_routes.signalhire_registry):
        for job in registry.all():
            registry.remove(job.job_id)


def _finished(job_id: str, *, expired: bool) -> BatchJob:
    job = BatchJob(job_id=job_id, kind="validation", label="x.csv")
    job.mark_terminal("completed")
    if expired:
        job.expires_at = datetime.now() - timedelta(seconds=1)
    return job


# --------------------------------------------------------------------------- #
# Eviction — the leak
# --------------------------------------------------------------------------- #

def test_evicts_finished_jobs_past_their_ttl() -> None:
    registry = BatchRegistry("validation", max_concurrent=5)
    registry.add(_finished("expired", expired=True))
    registry.add(_finished("fresh", expired=False))

    assert registry.evict_expired() == 1
    assert registry.get("expired") is None
    assert registry.get("fresh") is not None


def test_never_evicts_a_running_job() -> None:
    registry = BatchRegistry("validation", max_concurrent=5)
    running = BatchJob(job_id="running", kind="validation", label="x.csv")
    running.expires_at = datetime.now() - timedelta(hours=1)
    registry.add(running)

    assert registry.evict_expired() == 0
    assert registry.get("running") is not None


def test_cleanup_loop_evicts_both_batch_registries(monkeypatch) -> None:
    """Regression: cleanup only knew about the scan registry, so these two grew forever."""
    import asyncio

    from web.jobs import cleanup

    validate_routes.validation_registry.add(_finished("v", expired=True))
    signalhire_routes.signalhire_registry.add(_finished("s", expired=True))

    monkeypatch.setattr(settings, "CLEANUP_INTERVAL_SECONDS", 0)

    async def run_one_pass():
        task = asyncio.create_task(cleanup.cleanup_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run_one_pass())

    assert validate_routes.validation_registry.get("v") is None
    assert signalhire_routes.signalhire_registry.get("s") is None


# --------------------------------------------------------------------------- #
# Result accumulation
# --------------------------------------------------------------------------- #

def test_results_snapshot_is_isolated_from_later_appends() -> None:
    """
    The snapshot a reader holds must not change under it — that isolation is what
    lets the write path append without copying the whole accumulated list.
    """
    job = BatchJob(job_id="j", kind="validation", label="x.csv")
    job.extend([{"email": "a@example.com"}])

    snapshot = job.results
    job.extend([{"email": "b@example.com"}])

    assert len(snapshot) == 1
    assert job.processed == 2


def test_extend_reports_running_total() -> None:
    job = BatchJob(job_id="j", kind="validation", label="x.csv")

    assert job.extend([{"n": 1}, {"n": 2}]) == 2
    assert job.extend([{"n": 3}]) == 3


# --------------------------------------------------------------------------- #
# Concurrency caps
# --------------------------------------------------------------------------- #

def test_registry_capacity_tracks_only_running_jobs() -> None:
    registry = BatchRegistry("validation", max_concurrent=2)
    registry.add(BatchJob(job_id="a", kind="validation", label="a.csv"))
    assert registry.has_capacity()

    registry.add(BatchJob(job_id="b", kind="validation", label="b.csv"))
    assert not registry.has_capacity()

    # A job that ends frees its slot.
    registry.get("a").mark_terminal("completed")
    assert registry.has_capacity()


def test_validate_upload_refused_when_at_capacity(client, monkeypatch) -> None:
    for i in range(settings.MAX_CONCURRENT_VALIDATIONS):
        validate_routes.validation_registry.add(
            BatchJob(job_id=f"job-{i}", kind="validation", label="busy.csv")
        )

    response = client.post(
        "/api/validate_file",
        files={"file": ("emails.csv", b"a@example.com\n", "text/csv")},
    )

    assert response.status_code == 429


def test_signalhire_crawl_refused_when_at_capacity(client) -> None:
    for i in range(settings.MAX_CONCURRENT_SIGNALHIRE):
        signalhire_routes.signalhire_registry.add(
            BatchJob(job_id=f"job-{i}", kind="signalhire", label="busy")
        )

    response = client.post("/api/signalhire/crawl", json={"company": "example"})

    assert response.status_code == 429


# --------------------------------------------------------------------------- #
# Upload ceiling
# --------------------------------------------------------------------------- #

def test_oversized_upload_is_refused(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 1024)
    oversized = io.BytesIO(b"a@example.com,\n" * 500)

    response = client.post(
        "/api/validate_file",
        files={"file": ("emails.csv", oversized, "text/csv")},
    )

    assert response.status_code == 413
    assert "upload limit" in response.json()["detail"]


def test_upload_within_limit_is_accepted(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 1024 * 1024)

    # Stands in for the real worker so the test does no MX or SMTP lookups. It has
    # to close the stream the way the real one does, or the response never ends.
    async def finish_immediately(job, email_list):
        job.extend([{"email": email} for email in email_list])
        job.mark_terminal("completed")
        validate_routes._notify(job, None)

    monkeypatch.setattr(validate_routes, "run_validation_job", finish_immediately)

    response = client.post(
        "/api/validate_file",
        files={"file": ("emails.csv", b"someone@example.com\n", "text/csv")},
    )

    assert response.status_code == 200
    assert "someone@example.com" in response.text


def test_unsupported_extension_is_rejected_before_reading_body(client) -> None:
    response = client.post(
        "/api/validate_file",
        files={"file": ("payload.exe", b"nope", "application/octet-stream")},
    )

    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# Export ceilings
# --------------------------------------------------------------------------- #

def test_validate_export_rejects_oversized_payload(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "MAX_EXPORT_ROWS", 2)
    row = {
        "original_email": "a@example.com",
        "normalized_email": "a@example.com",
        "is_valid_syntax": True,
        "mx_status": "valid",
        "mailbox_status": "valid",
        "reason": "",
    }

    response = client.post("/api/validate_export", json={"results": [row] * 3, "format": "csv"})

    assert response.status_code == 413
