"""One build job from the queue to a draft pull request."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.catalog import Product, get_catalog
from app.config import Settings
from app.db import session as db
from app.db.models import BuildJob, Spec
from worker import claude_runner, github, gitops
from worker.envclean import clean_env
from worker.protected import violations

log = logging.getLogger("jarvis.worker")
TEST_TIMEOUT_S = 900


class JobStop(Exception):
    """The job ends here, with this final status and reason."""

    def __init__(self, status: str, reason: str) -> None:
        super().__init__(reason)
        self.status, self.reason = status, reason


async def claim_next() -> BuildJob | None:
    """Take the oldest queued job and mark it running (one worker: no race to guard)."""
    async with db.session_maker()() as s:
        job = (
            await s.execute(
                select(BuildJob).where(BuildJob.status == "queued").order_by(BuildJob.id).limit(1)
            )
        ).scalar_one_or_none()
        if job is None:
            return None
        job.status, job.started_at, job.stop_reason = "running", datetime.now(UTC), None
        await s.commit()
        return job


async def recover_orphans(settings: Settings) -> int:
    """Jobs left 'running' by a worker that died: fail them and clean their copies."""
    async with db.session_maker()() as s:
        rows = list(
            (await s.execute(select(BuildJob).where(BuildJob.status == "running"))).scalars()
        )
        for j in rows:
            j.status, j.finished_at = "failed", datetime.now(UTC)
            j.stop_reason = "il worker si è fermato durante il lavoro"
        await s.commit()
    for j in rows:
        await _cleanup(settings, j.id, j.product)
    return len(rows)


def _workdir(settings: Settings, job_id: int) -> Path:
    return Path(settings.build_workdir).resolve() / f"job-{job_id}"


async def _cleanup(settings: Settings, job_id: int, product: str) -> None:
    try:
        (p,) = get_catalog().select(product)
        if p.local_path:
            await gitops.remove_worktree(Path(p.local_path), _workdir(settings, job_id))
    except Exception:
        log.exception("cleanup of job %s failed", job_id)


async def _finish(job_id: int, status: str, **fields) -> None:
    async with db.session_maker()() as s:
        job = await s.get(BuildJob, job_id)
        job.status, job.finished_at = status, datetime.now(UTC)
        if job.started_at:
            started = job.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            job.duration_s = int((datetime.now(UTC) - started).total_seconds())
        for k, v in fields.items():
            setattr(job, k, v)
        await s.commit()


async def _cancel_requested(job_id: int) -> bool:
    async with db.session_maker()() as s:
        job = await s.get(BuildJob, job_id)
        return bool(job and job.cancel_requested)


async def run_tests(product: Product, worktree: Path) -> tuple[bool | None, str]:
    """Run the product's test command (clean environment). (None, ...) when there is none."""
    if not product.test_command:
        return None, "nessun test_command nel catalogo"
    proc = await asyncio.create_subprocess_shell(
        product.test_command,
        cwd=str(worktree),
        env=clean_env({"PYTHONDONTWRITEBYTECODE": "1"}),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), TEST_TIMEOUT_S)
    except TimeoutError:
        await claude_runner.kill_tree(proc)
        return False, f"test oltre {TEST_TIMEOUT_S // 60} minuti: fermati"
    tail = "\n".join(out.decode("utf-8", errors="replace").strip().splitlines()[-15:])
    return proc.returncode == 0, tail


def _pr_body(spec: Spec, result: str, tests: tuple[bool | None, str], files: list[str]) -> str:
    verdict = {True: "✅ verdi", False: "❌ FALLITI", None: "non eseguiti"}[tests[0]]
    listed = "\n".join(f"- `{f}`" for f in files[:40]) + ("\n- …" if len(files) > 40 else "")
    return (
        f"Pull request in **bozza**, scritta da un agente Claude Code su richiesta di Jarvis "
        f"(specifica #{spec.id} v{spec.version}).\n\n## Riassunto\n{result[:1500] or '(nessuno)'}"
        f"\n\n## Test: {verdict}\n```\n{tests[1][-1200:]}\n```\n\n## File toccati ({len(files)})\n"
        f"{listed}\n\n## Da controllare\n- che le modifiche rispettino la specifica\n"
        "- che non ci siano file non richiesti\n\n## Specifica\n" + spec.text
    )


async def process(
    job: BuildJob,
    settings: Settings,
    *,
    claude_cmd: list[str] | None = None,
    remote_for: Callable[[str], str] = lambda repo: f"https://github.com/{repo}.git",
    open_pr: Callable[..., Awaitable[str]] = github.create_draft_pr,
    poll_s: float = 2.0,
) -> None:
    """Run one job. Always ends in a final status; never merges, deploys or pushes main."""
    started = time.monotonic()
    worktree = _workdir(settings, job.id)
    product_name = job.product
    try:
        async with db.session_maker()() as s:
            spec = await s.get(Spec, job.spec_id)
        (product,) = get_catalog().select(product_name)
        if not product.build_enabled or not product.local_path or not product.github_repo:
            raise JobStop("failed", "prodotto non abilitato ai lavori")
        if not settings.build_github_token:
            raise JobStop("failed", "BUILD_GITHUB_TOKEN non configurato")
        local = Path(product.local_path)
        if not (local / ".git").exists():
            raise JobStop("failed", f"{local} non è un clone git")
        if await _cancel_requested(job.id):
            raise JobStop("cancelled", "annullato prima dell'avvio")

        base_ref = await gitops.default_base(local)
        branch = gitops.branch_name(job.id, spec.title)
        gitops.assert_safe_branch(branch, base_ref)
        await gitops.prepare_worktree(local, worktree, branch, base_ref)
        async with db.session_maker()() as s:
            row = await s.get(BuildJob, job.id)
            row.branch = branch
            await s.commit()

        claude = claude_cmd or [settings.build_claude_bin]
        cmd = claude_runner.build_command(
            claude, product.test_command, settings.build_model, settings.build_max_budget_usd
        )
        prompt = claude_runner.build_prompt(product.name, spec.text, product.test_command)
        outcome = await claude_runner.run_claude(
            cmd, worktree, prompt,
            timeout_s=settings.build_timeout_minutes * 60,
            max_turns=settings.build_max_turns,
            should_cancel=lambda: _cancel_requested(job.id),
            poll_s=poll_s,
        )  # fmt: skip
        if outcome.kind == "cancelled":
            raise JobStop("cancelled", "fermato su richiesta")
        if outcome.kind == "timeout":
            raise JobStop(
                "failed", f"tempo massimo superato ({settings.build_timeout_minutes} min)"
            )
        if outcome.kind == "turns":
            raise JobStop(
                "failed", f"troppi passaggi dell'agente (oltre {settings.build_max_turns})"
            )
        if outcome.kind == "quota":
            # the copy stays: /resume continues from the work done so far
            await _finish(
                job.id, "paused_quota", stop_reason="limite di quota di Claude Code",
                log_summary=outcome.detail[:500],
            )  # fmt: skip
            return
        if outcome.kind == "error":
            raise JobStop("failed", f"Claude Code è fallito: {outcome.detail}")

        files = await gitops.changed_files(worktree, base_ref)
        if not files:
            raise JobStop("failed", "l'agente non ha modificato nessun file")
        blocked = violations(files, product.protected_paths)
        if blocked:  # before any commit or push
            raise JobStop("failed", "percorsi protetti modificati: " + ", ".join(blocked[:5]))

        tests = await run_tests(product, worktree)
        await gitops.commit(worktree, f"{spec.title}\n\nSpecifica #{spec.id} v{spec.version}")
        await gitops.push(
            worktree, branch, base_ref, remote_for(product.github_repo), settings.build_github_token
        )
        pr_url = await open_pr(
            settings.build_github_token, product.github_repo, f"[Jarvis] {spec.title}",
            branch, base_ref.split("/", 1)[-1], _pr_body(spec, outcome.result, tests, files),
        )  # fmt: skip
        verdict = {True: "test verdi", False: "TEST FALLITI", None: "test non eseguiti"}[tests[0]]
        summary = (
            f"{verdict} · {len(files)} file · {outcome.turns} passaggi\n\n{outcome.result[:1200]}"
        )
        await _finish(job.id, "pr_opened", pr_url=pr_url, log_summary=summary)
        await _cleanup(settings, job.id, product_name)
    except JobStop as stop:
        await _finish(job.id, stop.status, stop_reason=stop.reason)
        await _cleanup(settings, job.id, product_name)
    except Exception as e:
        log.exception("job %s failed", job.id)
        await _finish(job.id, "failed", stop_reason=f"{type(e).__name__}: {str(e)[:250]}")
        await _cleanup(settings, job.id, product_name)
    finally:
        log.info("job %s done in %.0fs", job.id, time.monotonic() - started)


async def run_forever(settings: Settings, poll_s: float = 5.0) -> None:
    await recover_orphans(settings)
    running: set[asyncio.Task] = set()
    while True:
        running = {t for t in running if not t.done()}
        while len(running) < max(1, settings.build_max_parallel):
            job = await claim_next()
            if job is None:
                break
            running.add(asyncio.create_task(process(job, settings)))
        await asyncio.sleep(poll_s)
