"""Read-only views over jobs that have moved past NEW — i.e. the ones the user
has actually acted on (shortlisted, applied, interviewing, etc.) — and the
follow-up queue."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db, utcnow
from app.models.job import Job, JobStatus
from app.schemas.job import JobRead

router = APIRouter(prefix="/applications", tags=["applications"])

_ACTIONED_STATUSES = [
    JobStatus.SHORTLISTED,
    JobStatus.APPLY,
    JobStatus.APPLIED,
    JobStatus.INTERVIEW,
    JobStatus.OFFER,
    JobStatus.REJECTED,
]


@router.get("", response_model=list[JobRead])
def list_applications(db: Session = Depends(get_db)) -> list[Job]:
    return (
        db.query(Job)
        .filter(Job.status.in_(_ACTIONED_STATUSES))
        .order_by(Job.applied_at.desc().nullslast(), Job.discovered_at.desc())
        .all()
    )


@router.get("/follow-ups", response_model=list[JobRead])
def follow_ups_due(db: Session = Depends(get_db)) -> list[Job]:
    now = utcnow()
    return (
        db.query(Job)
        .filter(Job.follow_up_date.isnot(None), Job.follow_up_date <= now)
        .order_by(Job.follow_up_date.asc())
        .all()
    )
