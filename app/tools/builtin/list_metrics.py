from pydantic import BaseModel, Field

from app import growth
from app.tools.registry import Tool


class ListMetricsInput(BaseModel):
    opportunity_id: int | None = None
    weeks: int = Field(4, ge=1, le=12)


async def list_metrics(inp: ListMetricsInput) -> dict[str, object]:
    rows = await growth.list_metrics(inp.opportunity_id, inp.weeks)
    return {
        "metrics": [
            {"week": m.week.isoformat(), "name": m.name, "value": m.value, "source": m.source,
             "opportunity_id": m.opportunity_id}
            for m in rows
        ]
    }  # fmt: skip


TOOL = Tool(
    "list_metrics",
    "The numbers of the last weeks with their source. Quote the source of every number you use.",
    ListMetricsInput,
    "read",
    list_metrics,
)
