import pytest

from app.agent.loop import AgentContext
from app.db.models import Base
from app.db.session import init_db, session_maker
from app.tools.builtin import default_registry
from app.tracing import Tracer


@pytest.fixture
async def tracer(tmp_path):
    init_db(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    from app.db.session import engine

    async with engine().begin() as c:
        await c.run_sync(Base.metadata.create_all)
    yield Tracer(session_maker())
    await engine().dispose()


@pytest.fixture
def make_ctx(tracer):
    def _make(llm, registry=None, **kw):
        return AgentContext(llm=llm, registry=registry or default_registry(), tracer=tracer, **kw)

    return _make
