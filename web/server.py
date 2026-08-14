"""
FastAPI application.

Static files are mounted at "/" and must be registered LAST — Starlette matches
routes in order, so an earlier mount at the root would shadow every /api path.
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.models.scan import ScanConfig
from src.utils.logging import logger
from web import settings
from web.auth import BasicAuthMiddleware, check_auth_config
from web.jobs.cleanup import cleanup_loop
from web.jobs.registry import registry
from web.jobs.runner import build_job, start_job
from web.routes import history, results, scans, schedules, signalhire, stream, validate
from web.services import schedules as schedule_store
from web.services import store

STATIC_DIR = Path(__file__).parent / "static"

async def schedule_loop():
    """Dispatch due persistent schedules only while normal scan capacity exists."""
    while True:
        try:
            for row in await asyncio.to_thread(schedule_store.due):
                if not registry.has_capacity() or registry.active_for_domain(row["target_domain"]):
                    continue  # leave next_run_at due; a later tick safely retries it
                config = ScanConfig(target_domain=row["target_domain"], **row["scan_config"])
                name = row.get("search_name") or row["target_domain"]
                job = build_job(store.new_scan_id(name), name, config, 10)
                start_job(job, asyncio.get_running_loop())
                await asyncio.to_thread(schedule_store.mark_started, row["id"], job.scan_id)
        except Exception as exc:
            logger.warning(f"Schedule dispatch error: {exc}")
        await asyncio.sleep(30)



@asynccontextmanager
async def lifespan(app: FastAPI):
    """Starts the maintenance loop; lets in-flight scans checkpoint on shutdown."""
    task = asyncio.create_task(cleanup_loop())
    schedule_task = asyncio.create_task(schedule_loop())
    logger.info(
        f"Server ready. Max concurrent scans: {settings.MAX_CONCURRENT_SCANS}. "
        f"Job TTL: {settings.JOB_TTL_SECONDS}s."
    )

    try:
        yield
    finally:
        task.cancel()
        schedule_task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        try:
            await schedule_task
        except asyncio.CancelledError:
            pass

        # Each crawl checkpoints itself on the way out, so a Ctrl-C leaves every
        # in-flight scan resumable rather than lost.
        active = registry.active()
        if active:
            logger.info(f"Stopping {len(active)} in-flight scan(s); each will checkpoint.")
            await asyncio.to_thread(registry.shutdown)


def create_app() -> FastAPI:
    check_auth_config()

    app = FastAPI(
        title="MailCrawl",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Added before any route or mount so it covers all of them — including /docs,
    # /openapi.json and the static UI, which a route dependency would miss.
    app.add_middleware(BasicAuthMiddleware)

    app.include_router(scans.router)
    app.include_router(stream.router)
    app.include_router(results.router)
    app.include_router(history.router)
    app.include_router(schedules.router)
    app.include_router(validate.router)
    app.include_router(signalhire.router)

    # Registered last so it cannot shadow /api.
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app


app = create_app()
