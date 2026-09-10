"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import applications, health, jobs
from app.config import get_settings

logging.basicConfig(level=get_settings().log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.workers.job_processor import start_scheduler, stop_scheduler

    scheduler = start_scheduler()
    try:
        yield
    finally:
        stop_scheduler(scheduler)


app = FastAPI(
    title="Job Search Automation Assistant",
    description="Scores and prioritizes job-alert emails for manual review — never auto-applies.",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(jobs.router)
app.include_router(applications.router)
