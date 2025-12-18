from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.dependencies import require_role

templates = Jinja2Templates(directory="app/templates")

router = APIRouter()


@router.get("/checkin", response_class=HTMLResponse)
async def show_checkin(request: Request, _: dict = Depends(require_role("employee"))):
    return templates.TemplateResponse("employee/checkin.html", {"request": request})
