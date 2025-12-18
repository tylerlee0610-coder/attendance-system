from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.dependencies import require_role

templates = Jinja2Templates(directory="app/templates")

router = APIRouter()


@router.get("/admin/users", response_class=HTMLResponse)
async def show_users(request: Request, _: dict = Depends(require_role("admin"))):
    return templates.TemplateResponse("admin/users.html", {"request": request})
