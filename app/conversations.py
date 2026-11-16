from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Conversation


def sanitize_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop a trailing tool call that never got its result (a confirmation lost in a restart),
    so the loaded history is valid for the model."""
    out = list(history)
    while out and out[-1].get("role") == "assistant":
        content = out[-1].get("content")
        if isinstance(content, list) and any(b.get("type") == "tool_use" for b in content):
            out.pop()
        else:
            break
    return out


class ConversationStore:
    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self._maker = maker

    async def load(self, conversation_id: str) -> tuple[list[dict[str, Any]], str]:
        async with self._maker() as s:
            row = await s.get(Conversation, conversation_id)
        if row is None:
            return [], ""
        return sanitize_history(row.history or []), row.summary or ""

    async def save(self, conversation_id: str, history: list[dict[str, Any]], summary: str) -> None:
        async with self._maker() as s:
            row = await s.get(Conversation, conversation_id)
            if row is None:
                row = Conversation(id=conversation_id)
                s.add(row)
            row.history, row.summary = list(history), summary
            row.updated_at = datetime.now(UTC)
            await s.commit()

    async def delete(self, conversation_id: str) -> None:
        async with self._maker() as s:
            await s.execute(delete(Conversation).where(Conversation.id == conversation_id))
            await s.commit()
