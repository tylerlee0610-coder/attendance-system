from datetime import datetime, time, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import require_role
from app.models import CheckInRecord

router = APIRouter(prefix="/api")


@router.post("/checkin")
async def api_checkin(
    check_type: str = Body(..., embed=True),
    user: dict = Depends(require_role("employee")),
    session: AsyncSession = Depends(get_session),
):
    normalized = check_type.upper()
    if normalized not in {"IN", "OUT"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="check_type must be IN or OUT",
        )

    now = datetime.now()
    grace_end = (datetime.combine(now.date(), time(9, 0)) + timedelta(minutes=5)).time()
    is_late = normalized == "IN" and now.time() > grace_end

    record = CheckInRecord(
        user_id=user["user_id"], check_type=normalized, ts=now, is_late=is_late
    )
    session.add(record)
    await session.commit()

    return {
        "ok": True,
        "check_type": normalized,
        "ts": now.isoformat(),
        "is_late": is_late,
    }


@router.post("/manager/review/{id}")
async def api_manager_review(id: int, _: dict = Depends(require_role("manager"))):
    return {"ok": True, "review_id": id}


@router.post("/admin/users")
async def api_admin_users(_: dict = Depends(require_role("admin"))):
    return {"ok": True}
