from pydantic import BaseModel, Field

from app import opportunities
from app.tools.registry import Tool


class OpportunityInput(BaseModel):
    title: str = Field(min_length=5, max_length=160)
    niche: str = Field("", max_length=200, description="the group of people or the sector")
    problem: str = Field("", max_length=800, description="the problem, in the words of the people")
    opportunity_id: int | None = Field(None, description="id of one to update; omit for a new one")
    product: str | None = Field(None, description="catalog name, once the product exists")


async def save_opportunity(inp: OpportunityInput) -> dict[str, object]:
    op = await opportunities.save(
        inp.title, inp.niche, inp.problem, inp.opportunity_id, inp.product
    )
    return {"opportunity_id": op.id, "title": op.title, "status": op.status}


TOOL = Tool(
    "save_opportunity",
    "Create or update a niche or product opportunity under study. It has no score and no "
    "evidence yet: add them with add_evidence and score_opportunity.",
    OpportunityInput,
    "write",
    save_opportunity,
)
