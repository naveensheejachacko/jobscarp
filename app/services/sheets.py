"""Syncs Job rows to a Google Sheet for easy browsing/sharing — a read-mostly
view. Postgres remains the source of truth; this is a one-way export.

Rows are upserted by a hidden `job_id` column (the first column) so re-syncing
never creates duplicate rows, and the whole sheet is resorted by match score,
then salary, then discovery date on every sync.
"""
from __future__ import annotations

import logging
from typing import Protocol

from app.models.job import Job

logger = logging.getLogger(__name__)

HEADER_ROW = [
    "Job ID",  # hidden upsert key, column A
    "Date", "Company", "Role", "Source", "Location", "Salary", "Experience",
    "Match %", "Priority", "Matched Skills", "Missing Skills", "Reason",
    "Job URL", "Status", "Recruiter", "Applied Date", "Follow-up Date", "Notes",
]

PRIORITY_EMOJI = {
    "HIGH": "\U0001f525 HIGH",       # 🔥
    "STRONG": "\U0001f7e2 STRONG",   # 🟢
    "CONSIDER": "\U0001f7e1 CONSIDER",  # 🟡
    "LOW": "⚪ LOW",             # ⚪
    "SKIP": "\U0001f534 SKIP",       # 🔴
}


class SheetsClient(Protocol):
    """The subset of the Sheets API surface sheets.py needs — lets tests supply
    an in-memory fake instead of talking to Google."""

    def get_all_rows(self) -> list[list[str]]: ...
    def write_all_rows(self, rows: list[list[str]]) -> None: ...


def _salary_str(job: Job) -> str:
    if job.salary_min is None and job.salary_max is None:
        return ""
    if job.salary_min == job.salary_max:
        return f"{job.salary_min:g} {job.currency}"
    lo = f"{job.salary_min:g}" if job.salary_min is not None else "?"
    hi = f"{job.salary_max:g}" if job.salary_max is not None else "+"
    return f"{lo}-{hi} {job.currency}"


def _experience_str(job: Job) -> str:
    if job.experience_min is None and job.experience_max is None:
        return ""
    lo = f"{job.experience_min:g}" if job.experience_min is not None else "0"
    hi = f"{job.experience_max:g}" if job.experience_max is not None else "+"
    return f"{lo}-{hi} yrs"


def job_to_row(job: Job) -> list[str]:
    priority_label = PRIORITY_EMOJI.get(job.priority.value if job.priority else "", "")
    return [
        str(job.id),
        job.discovered_at.strftime("%Y-%m-%d") if job.discovered_at else "",
        job.company,
        job.job_title,
        job.source,
        job.location or "",
        _salary_str(job),
        _experience_str(job),
        str(job.match_score) if job.match_score is not None else "",
        priority_label,
        ", ".join(job.required_skills or []),
        ", ".join(job.missing_skills or []),
        job.match_reason or "",
        job.job_url or "",
        job.status.value,
        job.recruiter_name or job.recruiter_email or "",
        job.applied_at.strftime("%Y-%m-%d") if job.applied_at else "",
        job.follow_up_date.strftime("%Y-%m-%d") if job.follow_up_date else "",
        job.notes or "",
    ]


def _sort_key(row: list[str]) -> tuple:
    try:
        score = -int(row[8]) if row[8] else 0
    except ValueError:
        score = 0
    try:
        salary_num = -float(row[6].split("-")[-1].split()[0]) if row[6] else 0.0
    except (ValueError, IndexError):
        salary_num = 0.0
    date_str = row[1] or ""
    return (score, salary_num, date_str)


def sync_jobs_to_sheet(client: SheetsClient, jobs: list[Job]) -> None:
    """Full upsert-and-resort: builds the desired row set from `jobs`, merges it
    with whatever's already in the sheet keyed by Job ID, then rewrites the sheet."""
    existing_rows = client.get_all_rows()
    existing_by_id = {row[0]: row for row in existing_rows[1:] if row}  # skip header

    for job in jobs:
        existing_by_id[str(job.id)] = job_to_row(job)

    data_rows = sorted(existing_by_id.values(), key=_sort_key)
    client.write_all_rows([HEADER_ROW, *data_rows])
    logger.info("sheets.synced", extra={"row_count": len(data_rows)})


class GoogleSheetsClient:
    """Real Sheets API v4 implementation, used in production (not exercised in
    unit tests — those use an in-memory fake implementing the same Protocol)."""

    def __init__(self, credentials, spreadsheet_id: str, sheet_name: str | None = None):
        from googleapiclient.discovery import build

        self._service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        self._spreadsheet_id = spreadsheet_id
        # None = first tab. A missing "Jobs" tab used to fail the whole sync.
        self._sheet_name = sheet_name

    def _a1(self, cells: str) -> str:
        if self._sheet_name:
            return f"{self._sheet_name}!{cells}"
        return cells

    def get_all_rows(self) -> list[list[str]]:
        result = (
            self._service.spreadsheets()
            .values()
            .get(spreadsheetId=self._spreadsheet_id, range=self._a1("A:S"))
            .execute()
        )
        return result.get("values", [])

    def write_all_rows(self, rows: list[list[str]]) -> None:
        self._service.spreadsheets().values().clear(
            spreadsheetId=self._spreadsheet_id, range=self._a1("A:S")
        ).execute()
        self._service.spreadsheets().values().update(
            spreadsheetId=self._spreadsheet_id,
            range=self._a1("A1"),
            valueInputOption="RAW",
            body={"values": rows},
        ).execute()
