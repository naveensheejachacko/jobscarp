from datetime import datetime

from app.models.job import Job, JobStatus, Priority, WorkMode
from app.services.sheets import HEADER_ROW, job_to_row, sync_jobs_to_sheet


class FakeSheetsClient:
    """In-memory stand-in for the Sheets API — no network calls."""

    def __init__(self, initial_rows: list[list[str]] | None = None):
        self.rows = initial_rows or [HEADER_ROW]

    def get_all_rows(self) -> list[list[str]]:
        return self.rows

    def write_all_rows(self, rows: list[list[str]]) -> None:
        self.rows = rows


def _job(id_, **overrides) -> Job:
    job = Job(
        id=id_,
        company=overrides.pop("company", "Acme"),
        job_title=overrides.pop("job_title", "Backend Engineer"),
        source=overrides.pop("source", "cutshort"),
        work_mode=WorkMode.REMOTE,
        status=JobStatus.NEW,
        discovered_at=datetime(2026, 1, 1),
    )
    for k, v in overrides.items():
        setattr(job, k, v)
    return job


def test_job_to_row_includes_priority_emoji():
    job = _job(1, match_score=95, priority=Priority.HIGH)
    row = job_to_row(job)
    assert row[0] == "1"
    assert "HIGH" in row[9]
    assert "\U0001f525" in row[9]


def test_sync_inserts_new_jobs():
    client = FakeSheetsClient()
    sync_jobs_to_sheet(client, [_job(1, match_score=90, priority=Priority.HIGH)])
    assert len(client.rows) == 2  # header + 1 row
    assert client.rows[1][0] == "1"


def test_sync_upserts_existing_job_instead_of_duplicating():
    client = FakeSheetsClient()
    sync_jobs_to_sheet(client, [_job(1, match_score=70, priority=Priority.CONSIDER)])
    assert len(client.rows) == 2

    # Same job id, score changed after re-analysis — must update, not add a row.
    sync_jobs_to_sheet(client, [_job(1, match_score=92, priority=Priority.HIGH)])
    assert len(client.rows) == 2
    assert client.rows[1][8] == "92"


def test_sync_preserves_rows_not_in_the_current_batch():
    client = FakeSheetsClient()
    sync_jobs_to_sheet(client, [_job(1, match_score=70, priority=Priority.CONSIDER)])
    sync_jobs_to_sheet(client, [_job(2, match_score=95, priority=Priority.HIGH)])
    ids_present = {row[0] for row in client.rows[1:]}
    assert ids_present == {"1", "2"}


def test_sync_sorts_by_match_score_descending():
    client = FakeSheetsClient()
    sync_jobs_to_sheet(
        client,
        [
            _job(1, match_score=65, priority=Priority.LOW),
            _job(2, match_score=95, priority=Priority.HIGH),
            _job(3, match_score=80, priority=Priority.STRONG),
        ],
    )
    scores_in_order = [row[8] for row in client.rows[1:]]
    assert scores_in_order == ["95", "80", "65"]


def test_sync_omits_skip_jobs_and_drops_old_skip_rows():
    client = FakeSheetsClient()
    sync_jobs_to_sheet(client, [_job(1, match_score=40, priority=Priority.SKIP)])
    assert client.rows == [HEADER_ROW]

    sync_jobs_to_sheet(client, [_job(2, match_score=88, priority=Priority.STRONG)])
    assert {row[0] for row in client.rows[1:]} == {"2"}

    # A later SKIP rescore must remove the row, not leave stale data.
    sync_jobs_to_sheet(
        client,
        [
            _job(2, match_score=30, priority=Priority.SKIP),
            _job(3, match_score=72, priority=Priority.CONSIDER),
        ],
    )
    ids_present = {row[0] for row in client.rows[1:]}
    assert ids_present == {"3"}
