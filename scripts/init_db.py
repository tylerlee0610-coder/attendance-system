import asyncio
import hashlib

from sqlalchemy import select

from app.db import AsyncSessionLocal, Base, engine
from app.models import CheckInRecord, User


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def seed_users():
    async with AsyncSessionLocal() as session:
        existing = {
            row[0]
            for row in (
                await session.execute(select(User.username))
            ).all()
        }
        seeds = [
            ("emp", "emp123", "employee"),
            ("mgr", "mgr123", "manager"),
            ("admin", "admin123", "admin"),
        ]
        for username, password, role in seeds:
            if username in existing:
                continue
            session.add(
                User(
                    username=username,
                    password_hash=hash_password(password),
                    role=role,
                )
            )
        await session.commit()


async def main():
    await create_tables()
    await seed_users()


if __name__ == "__main__":
    asyncio.run(main())
