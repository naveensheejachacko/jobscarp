"""Notification channel abstraction. EmailNotifier (via Gmail API, reusing the
same OAuth credentials as ingestion) is the default and only channel wired up
for v1 — adding Slack/Telegram later is one new BaseNotifier subclass.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.job import Job
from app.services.gmail import GmailClient

PRIORITY_EMOJI = {
    "HIGH": "\U0001f525",
    "STRONG": "\U0001f7e2",
    "CONSIDER": "\U0001f7e1",
    "LOW": "⚪",
    "SKIP": "\U0001f534",
}


def format_notification(job: Job) -> tuple[str, str]:
    """Returns (subject, body) in the exact shape specified for high-match alerts."""
    emoji = PRIORITY_EMOJI.get(job.priority.value if job.priority else "", "")
    subject = f"{emoji} NEW HIGH-MATCH JOB: {job.job_title} at {job.company} ({job.match_score}%)"

    salary = ""
    if job.salary_min is not None or job.salary_max is not None:
        salary = f"₹{job.salary_min:g}–{job.salary_max:g} LPA" if job.salary_min != job.salary_max else f"₹{job.salary_min:g} LPA"

    matched = [s for s in (job.required_skills or []) if s not in (job.missing_skills or [])]

    lines = [
        f"{emoji} NEW HIGH-MATCH JOB",
        "",
        f"Company: {job.company}",
        f"Role: {job.job_title}",
        f"Salary: {salary or 'not specified'}",
        f"Location: {job.location or 'not specified'}",
        f"Match: {job.match_score}%",
        "",
        "Why:",
        *([f"- {s}" for s in matched] if matched else ["- (see job_reason for details)"]),
        "",
        "Missing:",
        *([f"- {s}" for s in job.missing_skills] if job.missing_skills else ["- none noted"]),
        "",
        "Application:",
        job.application_message or "(not generated yet)",
        "",
        "Job:",
        job.job_url or "(no URL captured)",
    ]
    return subject, "\n".join(lines)


class BaseNotifier(ABC):
    @abstractmethod
    def notify(self, job: Job) -> None:
        raise NotImplementedError


class EmailNotifier(BaseNotifier):
    def __init__(self, gmail_client: GmailClient, to_address: str):
        self._gmail_client = gmail_client
        self._to_address = to_address

    def notify(self, job: Job) -> None:
        if not self._to_address:
            return
        subject, body = format_notification(job)
        self._gmail_client.send_email(self._to_address, subject, body)
