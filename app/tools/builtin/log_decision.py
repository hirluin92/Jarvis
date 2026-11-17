from datetime import date

from pydantic import BaseModel, Field

from app import advisor
from app.tools.registry import Tool


class DecisionInput(BaseModel):
    product: str
    decision: str = Field(min_length=5, max_length=600)
    reasons: str = Field("", max_length=1200)
    alternatives: str = Field("", max_length=1200, description="the options that were discarded")
    review_on: date | None = Field(None, description="YYYY-MM-DD: when to look at it again")
    contradicts: list[int] = Field(
        default_factory=list, description="ids (from list_decisions) of decisions this one reverses"
    )


async def log_decision(inp: DecisionInput) -> dict[str, object]:
    saved, related = await advisor.log_decision(
        inp.product, inp.decision, inp.reasons, inp.alternatives, inp.review_on, inp.contradicts
    )
    out: dict[str, object] = advisor.decision_dict(saved)
    out["possibly_related_earlier_decisions"] = [advisor.decision_dict(d) for d in related]
    if inp.contradicts:
        out["warning"] = f"Questa decisione contraddice le decisioni {inp.contradicts}: dillo."
    elif related:
        out["warning"] = "Controlla se contraddice una decisione precedente e, se sì, dillo."
    return out


TOOL = Tool(
    "log_decision",
    "Save a decision with its reasons, the discarded alternatives and a review date. Call "
    "list_decisions first; if the new decision reverses an earlier one, pass its id in "
    "'contradicts' and tell the user. Only after the user clearly decided.",
    DecisionInput,
    "write",
    log_decision,
)
