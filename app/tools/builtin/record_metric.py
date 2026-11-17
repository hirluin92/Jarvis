from datetime import date

from pydantic import BaseModel, Field

from app import growth
from app.tools.registry import Tool


class MetricInput(BaseModel):
    name: str = Field(
        min_length=2,
        max_length=60,
        description="visits, signups, replies, calls, presales, revenue",
    )
    value: float
    source: str = Field(min_length=3, max_length=100, description="where the number comes from")
    opportunity_id: int | None = None
    day: date | None = Field(None, description="a day of the week it refers to; default today")


async def record_metric(inp: MetricInput) -> dict[str, object]:
    m = await growth.record_metric(inp.name, inp.value, inp.source, inp.opportunity_id, inp.day)
    return {"metric_id": m.id, "week": m.week.isoformat(), "name": m.name, "value": m.value}


TOOL = Tool(
    "record_metric",
    "Save a number of the week with where it comes from. Only numbers the user stated or a tool "
    "returned: never estimate. A number without a source is refused.",
    MetricInput,
    "write",
    record_metric,
)
