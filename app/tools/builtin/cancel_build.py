from pydantic import BaseModel

from app import builds
from app.tools.registry import Tool


class CancelInput(BaseModel):
    job_id: int


async def cancel_build(inp: CancelInput) -> dict[str, object]:
    job = await builds.cancel(inp.job_id)
    return {"job_id": job.id, "status": job.status, "stop_requested": job.cancel_requested}


TOOL = Tool(
    "cancel_build",
    "Stop a build job that is queued, paused or running; a running one is killed and its "
    "working copy is cleaned up.",
    CancelInput,
    "write",
    cancel_build,
)
