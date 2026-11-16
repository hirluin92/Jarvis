from app.agent.loop import AgentContext, AgentResult, run_agent

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


async def generate_report(ctx: AgentContext) -> AgentResult:
    return await run_agent(REPORT_PROMPT, ctx)
