from typing import Literal

from pydantic import BaseModel, Field

from app import opportunities
from app.tools.registry import Tool


class DecideInput(BaseModel):
    opportunity_id: int
    decision: Literal["go", "no_go", "kill"]
    reason: str = Field(min_length=10, max_length=800)
    results: dict[str, int] | None = Field(
        None, description="numbers observed so far: signups, positive_replies, presales"
    )


async def describe(inp: DecideInput) -> str:
    op = await opportunities.get(inp.opportunity_id)
    plan = await opportunities.plan_of(inp.opportunity_id)
    label = {"go": "COSTRUIRE", "no_go": "NON costruire", "kill": "CHIUDERE"}[inp.decision]
    report = opportunities.thresholds_report(plan, inp.results)
    return f"decisione su «{op.title}» (#{op.id}): {label}.\nMotivo: {inp.reason}\n{report}."


async def decide_opportunity(inp: DecideInput) -> dict[str, object]:
    return await opportunities.decide(inp.opportunity_id, inp.decision, inp.reason, inp.results)


TOOL = Tool(
    "decide_opportunity",
    "Register the decision on an opportunity: go (build), no_go (do not build) or kill (close). "
    "Needs the user's confirmation. A go never starts work by itself: at most it writes a DRAFT "
    "spec for phase 10, which still needs its own confirmation.",
    DecideInput,
    "confirm",
    decide_opportunity,
    describe,
)
