from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.db.models import Run, Step


class Tracer:
    """Writes runs and steps to the database. Every model/tool call goes through it."""

    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self._maker = maker

    async def start_run(self, user_message: str, model: str) -> int:
        async with self._maker() as s:
            run = Run(user_message=user_message, model=model)
            s.add(run)
            await s.commit()
            return run.id

    async def add_step(
        self,
        run_id: int,
        idx: int,
        type: str,
        *,
        tool_name: str | None = None,
        input: Any = None,
        output: Any = None,
        error: str | None = None,
        duration_ms: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        async with self._maker() as s:
            s.add(
                Step(
                    run_id=run_id,
                    idx=idx,
                    type=type,
                    tool_name=tool_name,
                    input=input,
                    output=output,
                    error=error,
                    duration_ms=duration_ms,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            )
            await s.commit()

    async def finish_run(
        self, run_id: int, status: str, final_answer: str | None, in_tok: int, out_tok: int
    ) -> None:
        async with self._maker() as s:
            run = await s.get(Run, run_id)
            assert run is not None
            run.status, run.final_answer = status, final_answer
            run.input_tokens, run.output_tokens = in_tok, out_tok
            run.finished_at = datetime.now(UTC)
            await s.commit()

    async def get_run(self, run_id: int) -> Run | None:
        async with self._maker() as s:
            q = select(Run).where(Run.id == run_id).options(selectinload(Run.steps))
            return (await s.execute(q)).scalar_one_or_none()

    async def runs_since(self, since: datetime) -> list[Run]:
        async with self._maker() as s:
            q = select(Run).where(Run.started_at >= since).order_by(Run.id)
            return list((await s.execute(q)).scalars())

    async def list_runs(self, limit: int = 20) -> list[Run]:
        async with self._maker() as s:
            q = select(Run).order_by(Run.id.desc()).limit(limit).options(selectinload(Run.steps))
            return list((await s.execute(q)).scalars())
