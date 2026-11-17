from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app import advisor
from app.tools.registry import Tool


class ListDecisionsInput(BaseModel):
    product: str | None = Field(None, description="omit for all products")
    due_only: bool = Field(False, description="only decisions whose review date has come")


async def list_decisions(inp: ListDecisionsInput) -> dict[str, object]:
    due = datetime.now(UTC).date() if inp.due_only else None
    rows = await advisor.list_decisions(inp.product, due)
    return {"decisions": [advisor.decision_dict(d) for d in rows[:20]]}


TOOL = Tool(
    "list_decisions",
    "The registered decisions (newest first) with reasons, alternatives and review date. Use it "
    "before advising: a plan can contradict a decision already taken.",
    ListDecisionsInput,
    "read",
    list_decisions,
)
