import os

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from app.db import get_session
from app.routers import admin, api, auth, employee, manager

SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-secret-change-me")

app = FastAPI(title="Smart Attendance and Leave System")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

app.include_router(auth.router)
app.include_router(employee.router)
app.include_router(manager.router)
app.include_router(admin.router)
app.include_router(api.router)


@app.get("/health", response_class=JSONResponse)
async def health_check() -> dict:
    return {"status": "ok"}


@app.get("/health/db", response_class=JSONResponse)
async def health_db(session: AsyncSession = Depends(get_session)) -> dict:
    value = await session.scalar(text("SELECT 1"))
    return {"status": "ok", "db": value}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
