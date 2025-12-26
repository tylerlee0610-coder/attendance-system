from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.dependencies import require_roles

templates = Jinja2Templates(directory="app/templates")

router = APIRouter()


@router.get("/checkin", response_class=HTMLResponse)
async def show_checkin(
    request: Request, _: dict = Depends(require_roles({"employee", "manager"}))
):
    return templates.TemplateResponse("employee/checkin.html", {"request": request})


@router.get("/records", response_class=HTMLResponse)
async def show_records(
    request: Request, _: dict = Depends(require_roles({"employee", "manager"}))
):
    return templates.TemplateResponse("employee/records.html", {"request": request})


@router.get("/alerts", response_class=HTMLResponse)
async def show_alerts(
    request: Request, _: dict = Depends(require_roles({"employee", "manager", "admin"}))
):
    return templates.TemplateResponse("alerts.html", {"request": request})


@router.get("/apply/manual", response_class=HTMLResponse)
async def show_manual_apply(
    request: Request, _: dict = Depends(require_roles({"employee", "manager"}))
):
    return templates.TemplateResponse("employee/manual.html", {"request": request})


@router.get("/leave/apply", response_class=HTMLResponse)
async def show_leave_apply(
    request: Request, _: dict = Depends(require_roles({"employee", "manager"}))
):
    return templates.TemplateResponse("employee/leave_apply.html", {"request": request})


@router.get("/leave/records", response_class=HTMLResponse)
async def show_leave_records(
    request: Request, _: dict = Depends(require_roles({"employee", "manager"}))
):
    return templates.TemplateResponse("employee/leave_records.html", {"request": request})
