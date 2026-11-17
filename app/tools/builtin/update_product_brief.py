from pydantic import BaseModel, Field

from app import advisor
from app.tools.registry import Tool


class UpdateBriefInput(BaseModel):
    product: str
    objective: str | None = Field(None, max_length=1500)
    customer: str | None = Field(None, max_length=1500)
    status: str | None = Field(None, max_length=1500)
    key_numbers: str | None = Field(None, max_length=1500)
    risks: str | None = Field(None, max_length=1500)
    next_moves: str | None = Field(None, max_length=1500)


async def update_product_brief(inp: UpdateBriefInput) -> dict[str, object]:
    fields = inp.model_dump(exclude={"product"})
    saved = await advisor.update_brief(inp.product, **fields)
    return advisor.brief_dict(saved)


TOOL = Tool(
    "update_product_brief",
    "Update only the given fields of a product's brief (only numbers and facts the user stated "
    "or a tool returned: never invent). Updates the date of the brief.",
    UpdateBriefInput,
    "write",
    update_product_brief,
)
