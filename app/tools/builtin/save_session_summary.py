from datetime import date

from pydantic import BaseModel, Field

from app import advisor
from app.tools.registry import Tool


class SessionDecision(BaseModel):
    decision: str = Field(min_length=5, max_length=600)
    reasons: str = Field("", max_length=1200)
    alternatives: str = Field("", max_length=1200)
    review_on: date | None = None


class SummaryInput(BaseModel):
    product: str
    summary: str = Field(min_length=20, max_length=2500)
    decisions: list[SessionDecision] = Field(default_factory=list, max_length=6)


async def describe(inp: SummaryInput) -> str:
    lines = [
        f"salvataggio del riepilogo della sessione su {advisor.canonical(inp.product)}:",
        "",
        inp.summary,
    ]
    for d in inp.decisions:
        when = f" (riesame {d.review_on.isoformat()})" if d.review_on else ""
        lines.append(f"• Decisione: {d.decision}{when}")
    return "\n".join(lines)


async def save_session_summary(inp: SummaryInput) -> dict[str, object]:
    session = await advisor.save_summary(inp.product, inp.summary)
    saved = []
    for d in inp.decisions:
        row, _ = await advisor.log_decision(
            inp.product, d.decision, d.reasons, d.alternatives, d.review_on
        )
        saved.append(row.id)
    return {"session_id": session.id, "decision_ids": saved}


TOOL = Tool(
    "save_session_summary",
    "Save the end-of-session summary (and the decisions in it). Needs the user's confirmation, "
    "which shows the text: never save a summary without asking.",
    SummaryInput,
    "confirm",
    save_session_summary,
    describe,
)
