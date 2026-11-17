from pydantic import BaseModel

from app import advisor
from app.tools.registry import Tool


class BriefInput(BaseModel):
    product: str


async def get_product_brief(inp: BriefInput) -> dict[str, object]:
    brief = await advisor.get_brief(inp.product)
    name = advisor.canonical(inp.product)
    if brief is None:
        return {
            "product": name,
            "exists": False,
            "note": "No brief yet: ask the user for the data.",
        }
    return {"exists": True, **advisor.brief_dict(brief)}


TOOL = Tool(
    "get_product_brief",
    "The structured summary of a product: objective, typical customer, status, key numbers, "
    "risks, next moves, with the date it was last updated. Check age_days before trusting numbers.",
    BriefInput,
    "read",
    get_product_brief,
)
