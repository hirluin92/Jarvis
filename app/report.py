from datetime import UTC, datetime, timedelta

from app.agent.loop import AgentContext, AgentResult, run_agent
from app.costs import estimate_cost, format_cost
from app.tracing import Tracer

REPORT_PROMPT = """Prepare the morning report on the state of my products.
Call site_status and github_activity without arguments (that covers every product in the \
catalog). Reply in Italian, at most 15 lines, in this order:
1. Anomalies first: unreachable or failing URLs, certificates flagged as expiring, failed CI \
workflows, and any source that errored or returned no data (say which one).
2. Then the numbers: commits, open pull requests, CI results, response times.
Do not mention things that are simply not applicable (for example no certificate for an http \
URL) and never list them as anomalies. Use only values returned by the tools: never estimate \
or invent a number. If a source is missing, say it is missing. \
If there is nothing to report, say so in one line."""


async def cost_summary(tracer: Tracer, now: datetime | None = None) -> str | None:
    """One line with the estimated spend of the last 24 hours, computed from traced tokens."""
    since = (now or datetime.now(UTC)) - timedelta(hours=24)
    runs = await tracer.runs_since(since)
    costs = [estimate_cost(r.model, r.input_tokens, r.output_tokens) for r in runs]
    known = [c for c in costs if c is not None]
    if not known:
        return None
    return f"Costi stimati ultime 24h: {format_cost(sum(known))} ({len(runs)} run)"


async def generate_report(ctx: AgentContext) -> AgentResult:
    result = await run_agent(REPORT_PROMPT, ctx)
    if result.status == "completed" and (line := await cost_summary(ctx.tracer)):
        result.answer = f"{result.answer}\n\n{line}"
    return result
