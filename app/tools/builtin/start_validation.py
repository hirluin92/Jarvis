from pydantic import BaseModel

from app import opportunities
from app.tools.registry import Tool


class StartInput(BaseModel):
    opportunity_id: int


async def describe(inp: StartInput) -> str:
    op = await opportunities.get(inp.opportunity_id)
    plan = await opportunities.plan_of(inp.opportunity_id)
    if plan is None:
        raise ValueError("manca il kit di verifica: scrivilo prima con draft_validation_kit")
    soglie = ", ".join(f"{k} {v}" for k, v in plan.thresholds.items())
    return (
        f"avvio della verifica di «{op.title}» (opportunità #{op.id}).\n\n{plan.kit}\n\n"
        f"Soglie da raggiungere: {soglie}. Scadenza: {plan.deadline.isoformat()}.\n"
        f"Si chiude se: {plan.kill_criteria}"
    )


async def start_validation(inp: StartInput) -> dict[str, object]:
    plan = await opportunities.start_validation(inp.opportunity_id)
    return {"opportunity_id": inp.opportunity_id, "status": "validating", "approved": plan.approved}


TOOL = Tool(
    "start_validation",
    "Approve and start the verification of an opportunity. Needs the user's confirmation, "
    "which shows the whole kit, the thresholds, the deadline and the closing criteria.",
    StartInput,
    "confirm",
    start_validation,
    describe,
)
