import hashlib

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import User

templates = Jinja2Templates(directory="app/templates")

router = APIRouter()

ROLE_HOME = {
    "employee": "/checkin",
    "manager": "/manager/dashboard",
    "admin": "/admin/users",
}


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    error = request.query_params.get("error")
    return templates.TemplateResponse("login.html", {"request": request, "error": error})


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    user = await session.scalar(select(User).where(User.username == username))
    if not user or user.password_hash != hash_password(password):
        return RedirectResponse("/login?error=invalid_credentials", status_code=303)

    role = user.role
    request.session["user_id"] = user.id
    request.session["role"] = role
    home = ROLE_HOME.get(role, "/login")
    return RedirectResponse(home, status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login?error=logged_out", status_code=303)
