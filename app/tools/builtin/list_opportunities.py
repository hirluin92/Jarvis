from pydantic import BaseModel, Field

from app import opportunities
from app.tools.registry import Tool


class ListInput(BaseModel):
    status: str | None = Field(None, description="filter, e.g. scouted, validating, go, killed")


async def list_opportunities(inp: ListInput) -> dict[str, object]:
    rows = await opportunities.list_all(inp.status)
    out = []
    for op in rows:
        value, coverage, _ = opportunities.total(op)
        out.append(
            {
                "id": op.id, "title": op.title, "status": op.status,
                "score": value, "score_coverage": coverage,
                "evidence": len(await opportunities.evidence_of(op.id)),
            }
        )  # fmt: skip
    return {"opportunities": out}


TOOL = Tool(
    "list_opportunities",
    "The opportunities under study (newest first) with status, score and number of evidence.",
    ListInput,
    "read",
    list_opportunities,
)
