from pydantic import BaseModel, Field

from app import opportunities
from app.tools.registry import Tool


class ScoreItem(BaseModel):
    value: int | None = Field(None, description="0-5, higher is better; null = unknown")
    why: str = ""
    evidence_ids: list[int] = Field(default_factory=list)


class ScoreInput(BaseModel):
    opportunity_id: int
    scores: dict[str, ScoreItem] = Field(
        description="criterion -> score. Criteria: " + ", ".join(opportunities.CRITERIA)
    )


async def score_opportunity(inp: ScoreInput) -> dict[str, object]:
    items = {k: v.model_dump() for k, v in inp.scores.items()}
    await opportunities.score(inp.opportunity_id, items)
    return await opportunities.summary(inp.opportunity_id)


TOOL = Tool(
    "score_opportunity",
    "Score criteria of an opportunity from 0 to 5 (5 = best for the project: big problem, "
    "urgent, people already pay, little competition, easy to reach, easy to build, good fit). "
    "A number REQUIRES a reason and the evidence_ids behind it; if the evidence is missing put "
    "value null (unknown): unknown is not zero and nothing is invented.",
    ScoreInput,
    "write",
    score_opportunity,
)
