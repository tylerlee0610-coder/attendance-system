from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.dependencies import require_roles

templates = Jinja2Templates(directory="app/templates")

router = APIRouter()


@router.get("/manager/records", response_class=HTMLResponse)
async def show_records(
    request: Request, _: dict = Depends(require_roles({"manager", "admin"}))
):
    return templates.TemplateResponse("manager/records.html", {"request": request})


@router.get("/manager/review", response_class=HTMLResponse)
async def show_review(
    request: Request, _: dict = Depends(require_roles({"manager", "admin"}))
):
    return templates.TemplateResponse("manager/review.html", {"request": request})


@router.get("/manager/manual", response_class=HTMLResponse)
async def show_manual_review(
    request: Request, _: dict = Depends(require_roles({"manager", "admin"}))
):
    return templates.TemplateResponse("manager/manual.html", {"request": request})
