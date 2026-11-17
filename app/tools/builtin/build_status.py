from pydantic import BaseModel, Field

from app import builds
from app.tools.registry import Tool


class StatusInput(BaseModel):
    job_id: int | None = Field(None, description="one job; omit for the most recent ones")


async def build_status(inp: StatusInput) -> dict[str, object]:
    jobs = [await builds.get_job(inp.job_id)] if inp.job_id else await builds.list_jobs(8)
    return {
        "jobs": [
            {
                "id": j.id,
                "product": j.product,
                "status": j.status,
                "branch": j.branch,
                "pr_url": j.pr_url,
                "stop_reason": j.stop_reason,
                "duration_s": j.duration_s,
            }
            for j in jobs
        ]
    }


TOOL = Tool(
    "build_status",
    "State of the build jobs (queued, running, paused for quota, failed, cancelled, PR opened).",
    StatusInput,
    "read",
    build_status,
)
