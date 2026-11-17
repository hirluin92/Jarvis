from datetime import date

from pydantic import BaseModel, Field

from app import opportunities
from app.tools.registry import Tool


class KitInput(BaseModel):
    opportunity_id: int
    kit: str = Field(
        min_length=100,
        max_length=2800,
        description="page structure and copy, signup form (with consent line), offer, ten "
        "interview questions, message drafts",
    )
    thresholds: dict[str, int] = Field(
        description="numbers that must be reached: signups, positive_replies, presales"
    )
    deadline: date = Field(description="YYYY-MM-DD, when the test ends")
    kill_criteria: str = Field(min_length=20, description="when to close it, written beforehand")


async def draft_validation_kit(inp: KitInput) -> dict[str, object]:
    plan = await opportunities.draft_kit(
        inp.opportunity_id, inp.kit, inp.thresholds, inp.deadline, inp.kill_criteria
    )
    return {
        "opportunity_id": inp.opportunity_id,
        "thresholds": plan.thresholds,
        "deadline": plan.deadline.isoformat(),
        "approved": plan.approved,
        "next": "call start_validation: the user sees the whole plan and approves it",
    }


TOOL = Tool(
    "draft_validation_kit",
    "Write the verification kit of an opportunity: it MUST include the numeric thresholds, the "
    "deadline and the closing criteria, decided before the test. It does not start anything: "
    "call start_validation right after, in the same turn.",
    KitInput,
    "write",
    draft_validation_kit,
)
