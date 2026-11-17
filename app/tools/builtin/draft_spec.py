from pydantic import BaseModel, Field

from app import builds
from app.tools.registry import Tool


class SpecInput(BaseModel):
    product: str
    title: str = Field(min_length=3, max_length=120)
    text: str = Field(min_length=40, max_length=builds.MAX_SPEC_CHARS)
    spec_id: int | None = Field(None, description="id of a draft to change; omit for a new one")


async def draft_spec(inp: SpecInput) -> dict[str, object]:
    saved = await builds.save_spec(inp.product, inp.title, inp.text, inp.spec_id)
    return {
        "spec_id": saved.id,
        "version": saved.version,
        "product": saved.product,
        "title": saved.title,
        "chars": len(saved.text),
        "next": "call start_build with this spec_id: the user sees the whole spec and approves",
    }


TOOL = Tool(
    "draft_spec",
    "Write or update the specification of something to build on a product (only products with "
    "builds enabled). The text must have these parts: Obiettivo, Incluso, Escluso, Criteri di "
    "accettazione, File probabilmente toccati; at most 3200 characters. If the request is "
    "ambiguous, ask the user at most three questions BEFORE calling this tool. Does not start "
    "any work: ALWAYS call start_build right after, in the same turn, with the spec_id: that "
    "is what shows the whole spec to the user for approval.",
    SpecInput,
    "write",
    draft_spec,
)
