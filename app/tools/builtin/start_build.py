from pydantic import BaseModel

from app import builds
from app.tools.registry import Tool


class StartInput(BaseModel):
    spec_id: int


async def describe(inp: StartInput) -> str:
    spec = await builds.get_spec(inp.spec_id)
    builds.buildable(spec.product)  # refused here already if the product is not enabled
    return (
        f"avvio del lavoro di costruzione su {spec.product}: «{spec.title}» "
        f"(specifica #{spec.id}, versione {spec.version}).\n\n{spec.text}\n\n"
        "Un agente Claude Code lavorerà su un ramo dedicato e aprirà una pull request in bozza. "
        "Non fa merge, deploy né push sul ramo principale."
    )


async def start_build(inp: StartInput) -> dict[str, object]:
    job = await builds.enqueue(inp.spec_id)
    return {"job_id": job.id, "status": job.status, "product": job.product, "spec_id": job.spec_id}


TOOL = Tool(
    "start_build",
    "Queue the build of an approved specification: a separate worker runs Claude Code on a "
    "dedicated branch and opens a DRAFT pull request. Needs the user's confirmation, which "
    "shows the whole spec. Never merges or deploys.",
    StartInput,
    "confirm",
    start_build,
    describe,
)
