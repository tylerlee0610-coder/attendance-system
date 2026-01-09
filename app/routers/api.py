from datetime import datetime, time, timedelta

import csv
import os
import smtplib
import ssl
import logging
from io import StringIO
from pathlib import Path
import hashlib
from email.message import EmailMessage

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import desc, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import require_role, require_roles
from app.models import CheckInRecord, Department, LateAlert, LeaveApplication, ManualCheckRequest, User

router = APIRouter(prefix="/api")
logger = logging.getLogger("uvicorn.error")


async def _manager_dept_id(user: dict, session: AsyncSession) -> int | None:
    if user.get("role") != "manager":
        return None
    dept_id = await session.scalar(select(Department.id).where(Department.manager_id == user["user_id"]))
    if not dept_id:
        dept_id = await session.scalar(select(User.department_id).where(User.id == user["user_id"]))
    return dept_id


DEFAULT_LATE_START = time(9, 0)
DEFAULT_LATE_GRACE_MINUTES = 5


def _parse_hhmm(value: str | None) -> time | None:
    if not value:
        return None
    try:
        raw = value.strip()
        if not raw:
            return None
        parts = raw.split(":")
        if len(parts) != 2:
            return None
        hour = int(parts[0])
        minute = int(parts[1])
        return time(hour=hour, minute=minute)
    except Exception:
        return None


def _normalize_hhmm(value: str | None) -> str | None:
    parsed = _parse_hhmm(value)
    if not parsed:
        return None
    return parsed.strftime("%H:%M")


async def _late_rule_for_user_id(user_id: int, session: AsyncSession) -> tuple[time, int]:
    dept_id = await session.scalar(select(User.department_id).where(User.id == user_id))
    if not dept_id:
        return DEFAULT_LATE_START, DEFAULT_LATE_GRACE_MINUTES
    dept = await session.get(Department, dept_id)
    if not dept:
        return DEFAULT_LATE_START, DEFAULT_LATE_GRACE_MINUTES
    start_time = _parse_hhmm(dept.late_start_time) or DEFAULT_LATE_START
    grace_minutes = dept.late_grace_minutes if dept.late_grace_minutes is not None else DEFAULT_LATE_GRACE_MINUTES
    return start_time, int(grace_minutes)


def _smtp_config() -> tuple[str, int, str, str, str] | None:
    host = (os.getenv("SMTP_HOST") or "").strip()
    port_raw = (os.getenv("SMTP_PORT") or "587").strip()
    user = (os.getenv("SMTP_USER") or "").strip()
    password = (os.getenv("SMTP_PASS") or "").strip()
    sender = (os.getenv("SMTP_FROM") or "").strip() or user
    if not host or not user or not password or not sender:
        return None
    try:
        port = int(port_raw)
    except Exception:
        port = 587
    return host, port, user, password, sender


def _send_email_sync(to_addrs: list[str], subject: str, body: str) -> bool:
    if not to_addrs:
        return False
    config = _smtp_config()
    if not config:
        return False
    host, port, user, password, sender = config
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(to_addrs)
    msg["Subject"] = subject
    msg.set_content(body)
    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.starttls(context=context)
            server.login(user, password)
            server.send_message(msg)
    except Exception:
        logger.exception("late_alert_email_send_failed")
        return False
    logger.warning("late_alert_email_sent to=%s subject=%s", ",".join(to_addrs), subject)
    return True


async def _late_alert_recipients(user_id: int, session: AsyncSession) -> list[str]:
    user = await session.get(User, user_id)
    if not user:
        return []
    recipients = []
    if user.email:
        recipients.append(user.email)
    if user.department_id:
        dept = await session.get(Department, user.department_id)
        if dept and dept.manager_id and dept.manager_id != user_id:
            manager = await session.get(User, dept.manager_id)
            if manager and manager.email and manager.email not in recipients:
                recipients.append(manager.email)
    return recipients


async def _queue_late_alert(
    user_id: int,
    checkin_id: int | None,
    late_dt: datetime,
    session: AsyncSession,
    background_tasks: BackgroundTasks,
):
    exists_stmt = select(LateAlert.id).where(
        LateAlert.user_id == user_id,
        LateAlert.late_date == late_dt.date(),
    )
    exists_id = await session.scalar(exists_stmt)
    if exists_id:
        return
    recipients = await _late_alert_recipients(user_id, session)
    if not recipients:
        logger.warning("late_alert_skip_no_recipients user_id=%s", user_id)
        return
    if not _smtp_config():
        logger.warning("late_alert_skip_no_smtp_config user_id=%s", user_id)
        return
    session.add(LateAlert(user_id=user_id, checkin_id=checkin_id, late_date=late_dt.date()))
    subject = "Late alert"
    user = await session.get(User, user_id)
    display_name = user.name if user and user.name else f"ID {user_id}"
    display_username = user.username if user and user.username else ""
    name_suffix = f" ({display_username})" if display_username else ""
    body = f"Employee {display_name}{name_suffix} checked in late at {late_dt.isoformat()}."
    background_tasks.add_task(_send_email_sync, recipients, subject, body)


@router.post("/checkin")
async def api_checkin(
    background_tasks: BackgroundTasks,
    payload: dict = Body(...),
    user: dict = Depends(require_roles({"employee", "manager"})),
    session: AsyncSession = Depends(get_session),
):
    check_type = (payload.get("check_type") or "").upper()
    latitude = payload.get("latitude")
    longitude = payload.get("longitude")

    if check_type not in {"IN", "OUT"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="check_type must be IN or OUT",
        )

    now = datetime.now()
    late_start, grace_minutes = await _late_rule_for_user_id(user["user_id"], session)
    grace_end = (datetime.combine(now.date(), late_start) + timedelta(minutes=grace_minutes)).time()
    is_late = check_type == "IN" and now.time() > grace_end

    record = CheckInRecord(
        user_id=user["user_id"],
        check_type=check_type,
        ts=now,
        latitude=latitude,
        longitude=longitude,
        is_late=is_late,
    )
    session.add(record)
    await session.flush()
    if is_late and check_type == "IN":
        await _queue_late_alert(user["user_id"], record.id, now, session, background_tasks)
    await session.commit()

    return {
        "ok": True,
        "check_type": check_type,
        "ts": now.isoformat(),
        "is_late": is_late,
        "latitude": latitude,
        "longitude": longitude,
    }


@router.get("/records")
async def api_records(
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(require_roles({"employee", "manager"})),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(CheckInRecord)
        .where(CheckInRecord.user_id == user["user_id"])
        .order_by(desc(CheckInRecord.ts))
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id,
            "check_type": r.check_type,
            "ts": r.ts.isoformat(),
            "is_late": r.is_late,
            "latitude": r.latitude,
            "longitude": r.longitude,
        }
        for r in rows
    ]


@router.get("/manager/records")
async def api_manager_records(
    limit: int = Query(100, ge=1, le=500),
    user_id: int | None = Query(None, description="Filter by user id"),
    name: str | None = Query(None, description="Filter by name"),
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(require_roles({"manager", "admin"})),
):
    manager_dept = None
    if current["role"] == "manager":
        manager_dept = await _manager_dept_id(current, session)
        if not manager_dept:
            return []

    stmt = (
        select(CheckInRecord, User.username, User.name)
        .join(User, User.id == CheckInRecord.user_id)
        .order_by(desc(CheckInRecord.ts))
        .limit(limit)
    )
    if user_id:
        stmt = stmt.where(CheckInRecord.user_id == user_id)
    if name:
        stmt = stmt.where(User.name.ilike(f"%{name.strip()}%"))
    if manager_dept:
        stmt = stmt.where(User.department_id == manager_dept)

    rows = await session.execute(stmt)
    results = []
    for record, username, name in rows.all():
        results.append(
            {
                "id": record.id,
                "user_id": record.user_id,
                "username": username,
                "name": name,
                "check_type": record.check_type,
                "ts": record.ts.isoformat(),
                "is_late": record.is_late,
                "latitude": record.latitude,
                "longitude": record.longitude,
            }
        )
    return results


@router.get("/manager/records/export")
async def api_manager_records_export(
    limit: int = Query(1000, ge=1, le=5000),
    user_id: int | None = Query(None, description="Filter by user id"),
    name: str | None = Query(None, description="Filter by name"),
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(require_roles({"manager", "admin"})),
):
    manager_dept = None
    if current["role"] == "manager":
        manager_dept = await _manager_dept_id(current, session)
        if not manager_dept:
            return Response(
                content="",
                media_type="text/csv",
                headers={"Content-Disposition": 'attachment; filename="checkin_records.csv"'},
            )

    stmt = (
        select(CheckInRecord, User.username, User.name)
        .join(User, User.id == CheckInRecord.user_id)
        .order_by(desc(CheckInRecord.ts))
        .limit(limit)
    )
    if user_id:
        stmt = stmt.where(CheckInRecord.user_id == user_id)
    if name:
        stmt = stmt.where(User.name.ilike(f"%{name.strip()}%"))
    if manager_dept:
        stmt = stmt.where(User.department_id == manager_dept)

    rows = await session.execute(stmt)
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["id", "name", "user_id", "username", "check_type", "ts", "is_late", "latitude", "longitude"]
    )
    for record, username, name in rows.all():
        writer.writerow(
            [
                record.id,
                name,
                record.user_id,
                username,
                record.check_type,
                record.ts.isoformat(),
                record.is_late,
                record.latitude,
                record.longitude,
            ]
        )
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="checkin_records.csv"'},
    )


@router.get("/alerts")
async def api_alerts(
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(require_roles({"employee", "manager", "admin"})),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(CheckInRecord, User.username, User.name)
        .join(User, User.id == CheckInRecord.user_id)
        .where(CheckInRecord.is_late.is_(True))
        .order_by(desc(CheckInRecord.ts))
        .limit(limit)
    )
    if user["role"] == "employee":
        stmt = stmt.where(CheckInRecord.user_id == user["user_id"])
    elif user["role"] == "manager":
        manager_dept = await _manager_dept_id(user, session)
        if not manager_dept:
            return []
        stmt = stmt.where(User.department_id == manager_dept)
    rows = await session.execute(stmt)
    results = []
    for record, username, name in rows.all():
        results.append(
            {
                "id": record.id,
                "user_id": record.user_id,
                "username": username,
                "name": name,
                "check_type": record.check_type,
                "ts": record.ts.isoformat(),
                "is_late": record.is_late,
                "latitude": record.latitude,
                "longitude": record.longitude,
            }
        )
    return results


@router.post("/manual-checkin")
async def api_manual_checkin(
    payload: dict = Body(...),
    user: dict = Depends(require_roles({"employee", "manager"})),
    session: AsyncSession = Depends(get_session),
):
    check_type = (payload.get("check_type") or "").upper()
    requested_ts = payload.get("requested_ts")
    reason = payload.get("reason")

    if check_type not in {"IN", "OUT"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="check_type must be IN or OUT",
        )
    if not requested_ts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="requested_ts is required (ISO datetime)",
        )
    try:
        req_dt = datetime.fromisoformat(requested_ts)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="requested_ts must be ISO datetime string",
        )

    # 每人每月最多 2 次成功/待審補卡（REJECT 不算）
    month_start = req_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if req_dt.month == 12:
        next_month = req_dt.replace(year=req_dt.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        next_month = req_dt.replace(month=req_dt.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
    count_stmt = (
        select(func.count())
        .select_from(ManualCheckRequest)
        .where(
            ManualCheckRequest.user_id == user["user_id"],
            ManualCheckRequest.requested_ts >= month_start,
            ManualCheckRequest.requested_ts < next_month,
            ManualCheckRequest.status != "REJECTED",
        )
    )
    current_count = await session.scalar(count_stmt)
    if current_count and current_count >= 2:
        raise HTTPException(status_code=400, detail="本月補卡次數已達上限 (2 次)")

    record = ManualCheckRequest(
        user_id=user["user_id"],
        check_type=check_type,
        requested_ts=req_dt,
        reason=reason,
        status="PENDING",
    )
    session.add(record)
    await session.commit()

    return {
        "ok": True,
        "id": record.id,
        "check_type": check_type,
        "requested_ts": req_dt.isoformat(),
        "status": record.status,
    }


@router.get("/manager/manual")
async def api_manager_manual_list(
    status_filter: str = Query("PENDING", description="PENDING/APPROVED/REJECTED"),
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(require_roles({"manager", "admin"})),
):
    manager_dept = None
    if current["role"] == "manager":
        manager_dept = await _manager_dept_id(current, session)
        if not manager_dept:
            return []

    stmt = (
        select(ManualCheckRequest, User.username, User.name)
        .join(User, User.id == ManualCheckRequest.user_id)
        .order_by(desc(ManualCheckRequest.created_at))
        .limit(limit)
    )
    if status_filter:
        stmt = stmt.where(ManualCheckRequest.status == status_filter)
    if manager_dept:
        stmt = stmt.where(User.department_id == manager_dept)
    rows = await session.execute(stmt)
    results = []
    for req, username, name in rows.all():
        results.append(
            {
                "id": req.id,
                "user_id": req.user_id,
                "username": username,
                "name": name,
                "check_type": req.check_type,
                "requested_ts": req.requested_ts.isoformat(),
                "reason": req.reason,
                "status": req.status,
            }
        )
    return results


@router.post("/manager/manual/{id}")
async def api_manager_manual_review(
    background_tasks: BackgroundTasks,
    id: int,
    payload: dict = Body(...),
    reviewer: dict = Depends(require_roles({"manager", "admin"})),
    session: AsyncSession = Depends(get_session),
):
    action = (payload.get("action") or "").upper()
    if action not in {"APPROVE", "REJECT"}:
        raise HTTPException(status_code=400, detail="action must be APPROVE or REJECT")

    req = await session.get(ManualCheckRequest, id)
    if not req:
        raise HTTPException(status_code=404, detail="request not found")
    if req.status != "PENDING":
        raise HTTPException(status_code=400, detail="already reviewed")

    req.status = "APPROVED" if action == "APPROVE" else "REJECTED"
    if action == "APPROVE":
        late_start, grace_minutes = await _late_rule_for_user_id(req.user_id, session)
        grace_end = (datetime.combine(req.requested_ts.date(), late_start) + timedelta(minutes=grace_minutes)).time()
        is_late = req.check_type == "IN" and req.requested_ts.time() > grace_end
        exists_stmt = select(CheckInRecord.id).where(
            CheckInRecord.user_id == req.user_id,
            CheckInRecord.check_type == req.check_type,
            CheckInRecord.ts == req.requested_ts,
        )
        exists_id = await session.scalar(exists_stmt)
        if not exists_id:
            record = CheckInRecord(
                user_id=req.user_id,
                check_type=req.check_type,
                ts=req.requested_ts,
                is_late=is_late,
            )
            session.add(record)
            await session.flush()
            if is_late and req.check_type == "IN":
                await _queue_late_alert(req.user_id, record.id, req.requested_ts, session, background_tasks)
    await session.commit()
    return {"ok": True, "id": id, "status": req.status}


UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)


@router.post("/leave/apply")
async def api_leave_apply(
    leave_type: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    reason: str | None = Form(None),
    attachment: UploadFile | None = File(None),
    user: dict = Depends(require_roles({"employee", "manager"})),
    session: AsyncSession = Depends(get_session),
):
    leave_type = leave_type.strip()
    if not leave_type:
        raise HTTPException(status_code=400, detail="leave_type is required")
    try:
        start_dt = datetime.fromisoformat(start_time)
        end_dt = datetime.fromisoformat(end_time)
    except Exception:
        raise HTTPException(status_code=400, detail="start_time/end_time must be ISO datetime")
    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="end_time must be after start_time")

    attachment_path = None
    if attachment and attachment.filename:
        safe_name = f"{int(datetime.now().timestamp())}_{attachment.filename}"
        dest = UPLOAD_DIR / safe_name
        with dest.open("wb") as f:
            f.write(await attachment.read())
        attachment_path = str(dest)

    record = LeaveApplication(
        user_id=user["user_id"],
        leave_type=leave_type,
        start_time=start_dt,
        end_time=end_dt,
        reason=reason,
        status="PENDING",
        attachment_path=attachment_path,
    )
    session.add(record)
    await session.commit()

    return {
        "ok": True,
        "id": record.id,
        "leave_type": leave_type,
        "start_time": start_dt.isoformat(),
        "end_time": end_dt.isoformat(),
        "status": record.status,
        "attachment_path": attachment_path,
    }


@router.get("/leave/mine")
async def api_leave_mine(
    status_filter: str | None = Query(None, description="PENDING/APPROVED/REJECTED"),
    limit: int = Query(50, ge=1, le=200),
    user_id: int | None = Query(None, description="Filter by user id (manager only)"),
    name: str | None = Query(None, description="Filter by name (manager only)"),
    user: dict = Depends(require_roles({"employee", "manager"})),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(LeaveApplication, User.username, User.name)
        .join(User, User.id == LeaveApplication.user_id)
        .order_by(desc(LeaveApplication.created_at))
        .limit(limit)
    )
    if user["role"] == "employee":
        stmt = stmt.where(LeaveApplication.user_id == user["user_id"])
    else:
        manager_dept = await _manager_dept_id(user, session)
        if not manager_dept:
            return []
        stmt = stmt.where(User.department_id == manager_dept)
        if user_id:
            stmt = stmt.where(LeaveApplication.user_id == user_id)
        if name:
            stmt = stmt.where(User.name.ilike(f"%{name.strip()}%"))
    if status_filter:
        stmt = stmt.where(LeaveApplication.status == status_filter)
    rows = (await session.execute(stmt)).all()
    results = []
    for leave, username, name_value in rows:
        results.append(
            {
                "id": leave.id,
                "user_id": leave.user_id,
                "username": username,
                "name": name_value,
                "leave_type": leave.leave_type,
                "start_time": leave.start_time.isoformat(),
                "end_time": leave.end_time.isoformat(),
                "reason": leave.reason,
                "status": leave.status,
                "reviewer_id": leave.reviewer_id,
            }
        )
    return results


@router.get("/manager/review")
async def api_manager_review_list(
    status_filter: str = Query("PENDING", description="PENDING/APPROVED/REJECTED"),
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    current: dict = Depends(require_roles({"manager", "admin"})),
):
    manager_dept = None
    if current["role"] == "manager":
        manager_dept = await _manager_dept_id(current, session)
        if not manager_dept:
            return []

    stmt = (
        select(LeaveApplication, User.username, User.name)
        .join(User, User.id == LeaveApplication.user_id)
        .order_by(desc(LeaveApplication.created_at))
        .limit(limit)
    )
    if status_filter:
        stmt = stmt.where(LeaveApplication.status == status_filter)
    if manager_dept:
        stmt = stmt.where(User.department_id == manager_dept)
    rows = await session.execute(stmt)
    results = []
    for leave, username, name in rows.all():
        results.append(
            {
                "id": leave.id,
                "user_id": leave.user_id,
                "username": username,
                "name": name,
                "leave_type": leave.leave_type,
                "start_time": leave.start_time.isoformat(),
                "end_time": leave.end_time.isoformat(),
                "reason": leave.reason,
                "status": leave.status,
                "reviewer_id": leave.reviewer_id,
            }
        )
    return results


@router.get("/admin/leave/approved")
async def api_admin_leave_approved(
    limit: int = Query(200, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_roles({"admin"})),
):
    stmt = (
        select(LeaveApplication, User.username)
        .join(User, User.id == LeaveApplication.user_id)
        .where(LeaveApplication.status == "APPROVED")
        .order_by(desc(LeaveApplication.updated_at))
        .limit(limit)
    )
    rows = await session.execute(stmt)
    results = []
    for leave, username in rows.all():
        results.append(
            {
                "id": leave.id,
                "user_id": leave.user_id,
                "username": username,
                "leave_type": leave.leave_type,
                "start_time": leave.start_time.isoformat(),
                "end_time": leave.end_time.isoformat(),
                "reason": leave.reason,
                "status": leave.status,
                "reviewer_id": leave.reviewer_id,
                "attachment_path": leave.attachment_path,
            }
        )
    return results


@router.post("/manager/review/{id}")
async def api_manager_review(
    id: int,
    payload: dict = Body(...),
    reviewer: dict = Depends(require_roles({"manager", "admin"})),
    session: AsyncSession = Depends(get_session),
):
    action = (payload.get("action") or "").upper()
    if action not in {"APPROVE", "REJECT"}:
        raise HTTPException(status_code=400, detail="action must be APPROVE or REJECT")

    leave = await session.get(LeaveApplication, id)
    if not leave:
        raise HTTPException(status_code=404, detail="leave not found")
    if leave.status != "PENDING":
        raise HTTPException(status_code=400, detail="leave already reviewed")

    leave.status = "APPROVED" if action == "APPROVE" else "REJECTED"
    leave.reviewer_id = reviewer["user_id"]
    leave.updated_at = datetime.now()
    await session.commit()

    return {"ok": True, "id": id, "status": leave.status}


@router.post("/admin/users")
async def api_admin_users_create(
    payload: dict = Body(...),
    _: dict = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    username = (payload.get("username") or "").strip()
    password = payload.get("password")
    role = (payload.get("role") or "").strip()
    name = (payload.get("name") or "").strip()
    email = (payload.get("email") or "").strip() or None
    if not username or not password or role not in {"employee", "manager", "admin"} or not name:
        raise HTTPException(status_code=400, detail="username, password, role, name required")
    if email and "@" not in email:
        raise HTTPException(status_code=400, detail="email is invalid")
    exists = await session.scalar(select(User).where(User.username == username))
    if exists:
        raise HTTPException(status_code=400, detail="username already exists")
    password_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
    user = User(username=username, password_hash=password_hash, role=role, name=name, email=email)
    session.add(user)
    await session.commit()
    return {"ok": True, "id": user.id, "username": username, "role": role, "name": name, "email": email}


@router.get("/admin/users")
async def api_admin_users_list(
    limit: int = Query(200, ge=1, le=500),
    _: dict = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(User).order_by(User.id).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "name": u.name,
            "email": u.email,
            "department_id": u.department_id,
            "created_at": u.created_at.isoformat(),
        }
        for u in rows
    ]


@router.patch("/admin/users/{user_id}")
async def api_admin_users_update_role(
    user_id: int,
    payload: dict = Body(...),
    admin: dict = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    role = (payload.get("role") or "").strip()
    if role not in {"employee", "manager", "admin"}:
        raise HTTPException(status_code=400, detail="role must be employee/manager/admin")
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    # 可選：避免自刪角色造成鎖死
    if user.id == admin["user_id"] and role != "admin":
        raise HTTPException(status_code=400, detail="cannot change own role away from admin")
    user.role = role
    await session.commit()
    return {"ok": True, "id": user.id, "role": user.role}


@router.delete("/admin/users/{user_id}")
async def api_admin_users_delete(
    user_id: int,
    admin: dict = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    if user_id == admin["user_id"]:
        raise HTTPException(status_code=400, detail="cannot delete self")
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    await session.delete(user)
    await session.commit()
    return {"ok": True, "deleted_id": user_id}


@router.post("/admin/departments")
async def api_admin_departments_create(
    payload: dict = Body(...),
    _: dict = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    name = (payload.get("name") or "").strip()
    manager_id = payload.get("manager_id")
    late_start_time = payload.get("late_start_time")
    late_grace_minutes = payload.get("late_grace_minutes")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    exists = await session.scalar(select(Department).where(Department.name == name))
    if exists:
        raise HTTPException(status_code=400, detail="department exists")
    if manager_id:
        manager = await session.get(User, manager_id)
        if not manager:
            raise HTTPException(status_code=404, detail="manager not found")
    normalized_start = None
    if late_start_time is not None:
        normalized_start = _normalize_hhmm(late_start_time)
        if not normalized_start:
            raise HTTPException(status_code=400, detail="late_start_time must be HH:MM")
    if late_grace_minutes is not None:
        try:
            late_grace_minutes = int(late_grace_minutes)
        except Exception:
            raise HTTPException(status_code=400, detail="late_grace_minutes must be integer")
        if late_grace_minutes < 0 or late_grace_minutes > 120:
            raise HTTPException(status_code=400, detail="late_grace_minutes out of range")
    dept = Department(name=name, manager_id=manager_id)
    if normalized_start is not None:
        dept.late_start_time = normalized_start
    if late_grace_minutes is not None:
        dept.late_grace_minutes = late_grace_minutes
    session.add(dept)
    await session.commit()
    return {"ok": True, "id": dept.id, "name": name, "manager_id": manager_id}


@router.get("/admin/departments")
async def api_admin_departments_list(
    limit: int = Query(200, ge=1, le=500),
    _: dict = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    depts = (await session.execute(select(Department).order_by(Department.id).limit(limit))).scalars().all()
    users = (await session.execute(select(User))).scalars().all()
    user_map = {u.id: u for u in users}
    members_by_dept = {}
    for u in users:
        if u.department_id:
            members_by_dept.setdefault(u.department_id, []).append(u)
    results = []
    for d in depts:
        mgr = user_map.get(d.manager_id) if d.manager_id else None
        members = members_by_dept.get(d.id, [])
        results.append(
            {
                "id": d.id,
                "name": d.name,
                "manager_id": d.manager_id,
                "manager_name": mgr.username if mgr else None,
                "late_start_time": d.late_start_time,
                "late_grace_minutes": d.late_grace_minutes,
                "members": [
                    {"id": m.id, "username": m.username, "role": m.role} for m in members
                ],
                "created_at": d.created_at.isoformat(),
            }
        )
    return results


@router.patch("/admin/departments/{dept_id}")
async def api_admin_departments_update(
    dept_id: int,
    payload: dict = Body(...),
    _: dict = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    dept = await session.get(Department, dept_id)
    if not dept:
        raise HTTPException(status_code=404, detail="department not found")
    if "late_start_time" in payload:
        normalized = _normalize_hhmm(payload.get("late_start_time"))
        if not normalized:
            raise HTTPException(status_code=400, detail="late_start_time must be HH:MM")
        dept.late_start_time = normalized
    if "late_grace_minutes" in payload:
        try:
            grace = int(payload.get("late_grace_minutes"))
        except Exception:
            raise HTTPException(status_code=400, detail="late_grace_minutes must be integer")
        if grace < 0 or grace > 120:
            raise HTTPException(status_code=400, detail="late_grace_minutes out of range")
        dept.late_grace_minutes = grace
    await session.commit()
    return {"ok": True, "id": dept.id, "late_start_time": dept.late_start_time, "late_grace_minutes": dept.late_grace_minutes}


@router.post("/admin/departments/{dept_id}/assign")
async def api_admin_departments_assign(
    dept_id: int,
    payload: dict = Body(...),
    _: dict = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    user_id = payload.get("user_id")
    set_manager = payload.get("set_manager", False)
    dept = await session.get(Department, dept_id)
    if not dept:
        raise HTTPException(status_code=404, detail="department not found")
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    user.department_id = dept_id
    if set_manager:
        dept.manager_id = user_id
    await session.commit()
    return {"ok": True, "dept_id": dept_id, "user_id": user_id, "manager_id": dept.manager_id}
