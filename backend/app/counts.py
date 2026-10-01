from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import RequestStatus, VMRequest


def pending_approval_count(db: Session) -> int:
    return db.scalar(
        select(func.count())
        .select_from(VMRequest)
        .where(VMRequest.status == RequestStatus.pending_approval)
    )
