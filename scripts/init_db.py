import asyncio
import hashlib
import sys
from pathlib import Path

from sqlalchemy import select

# Ensure project root on path when running directly
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import AsyncSessionLocal, Base, engine
from app.models import CheckInRecord, Department, LeaveApplication, ManualCheckRequest, User


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def seed_users():
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(User))).scalars().all()
        existing = {u.username: u for u in rows}
        seeds = [
            ("emp", "emp123", "employee", "預設員工"),
            ("mgr", "mgr123", "manager", "預設主管"),
            ("admin", "admin123", "admin", "預設管理員"),
        ]
        for username, password, role, name in seeds:
            if username in existing:
                user = existing[username]
                # 補齊名稱欄位
                if not getattr(user, "name", None):
                    user.name = name
                continue
            session.add(
                User(
                    username=username,
                    password_hash=hash_password(password),
                    role=role,
                    name=name,
                )
            )
        await session.commit()


async def main():
    await create_tables()
    await seed_users()


if __name__ == "__main__":
    asyncio.run(main())
