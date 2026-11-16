from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_engine: AsyncEngine | None = None
_maker: async_sessionmaker[AsyncSession] | None = None


def init_db(url: str) -> None:
    global _engine, _maker
    _engine = create_async_engine(url)
    _maker = async_sessionmaker(_engine, expire_on_commit=False)


def session_maker() -> async_sessionmaker[AsyncSession]:
    assert _maker is not None, "init_db() not called"
    return _maker


def engine() -> AsyncEngine:
    assert _engine is not None, "init_db() not called"
    return _engine


async def get_session() -> AsyncIterator[AsyncSession]:
    async with session_maker()() as s:
        yield s
