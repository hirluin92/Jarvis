"""Specs and build jobs (phase 10). Jarvis only writes rows: the worker does the work."""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import select

from app.catalog import Product, get_catalog
from app.db import session as db
from app.db.models import BuildJob, Spec

log = logging.getLogger("jarvis.builds")

MAX_SPEC_CHARS = 3200  # the whole spec must fit in one Telegram confirmation message
ACTIVE = ("queued", "running", "paused_quota")
NOTIFY_STATES = ("pr_opened", "failed", "cancelled", "paused_quota")


def buildable(product: str) -> Product:
    """The catalog product, only if the user enabled builds for it (build_enabled: true)."""
    (found,) = get_catalog().select(product)
    if not found.build_enabled:
        raise ValueError(
            f"Il prodotto {found.name!r} non è abilitato ai lavori (build_enabled nel catalogo)"
        )
    if not found.local_path or not found.github_repo:
        raise ValueError(f"Il prodotto {found.name!r} non ha local_path e github_repo nel catalogo")
    return found


async def save_spec(product: str, title: str, text: str, spec_id: int | None = None) -> Spec:
    found = buildable(product)
    async with db.session_maker()() as s:
        if spec_id is None:
            spec = Spec(product=found.name, title=title, text=text)
            s.add(spec)
        else:
            spec = await s.get(Spec, spec_id)
            if spec is None:
                raise ValueError(f"Nessuna specifica con id {spec_id}")
            spec.product, spec.title, spec.text = found.name, title, text
            spec.version, spec.status = spec.version + 1, "draft"  # a changed spec needs a new yes
            spec.updated_at = datetime.now(UTC)
        await s.commit()
        new_id = spec.id
    async with db.session_maker()() as s:  # read back: the proof is what is really stored
        saved = await s.get(Spec, new_id)
    if saved is None:
        raise RuntimeError(f"specifica {new_id} non trovata dopo il salvataggio")
    return saved


async def get_spec(spec_id: int) -> Spec:
    async with db.session_maker()() as s:
        spec = await s.get(Spec, spec_id)
    if spec is None:
        raise ValueError(f"Nessuna specifica con id {spec_id}")
    return spec


async def enqueue(spec_id: int) -> BuildJob:
    """Queue a build for a spec. Called only after the user confirmed on Telegram."""
    spec = await get_spec(spec_id)
    buildable(spec.product)  # re-checked at the moment of the yes, not only when drafted
    async with db.session_maker()() as s:
        busy = (
            (
                await s.execute(
                    select(BuildJob).where(BuildJob.spec_id == spec_id, BuildJob.status.in_(ACTIVE))
                )
            )
            .scalars()
            .first()
        )
        if busy is not None:
            raise ValueError(f"La specifica {spec_id} ha già il lavoro #{busy.id} ({busy.status})")
        row = await s.get(Spec, spec_id)
        row.status = "approved"
        job = BuildJob(spec_id=spec_id, product=spec.product)
        s.add(job)
        await s.commit()
        job_id = job.id
    return await get_job(job_id)


async def get_job(job_id: int) -> BuildJob:
    async with db.session_maker()() as s:
        job = await s.get(BuildJob, job_id)
    if job is None:
        raise ValueError(f"Nessun lavoro con id {job_id}")
    return job


async def list_jobs(limit: int = 10) -> list[BuildJob]:
    async with db.session_maker()() as s:
        q = select(BuildJob).order_by(BuildJob.id.desc()).limit(limit)
        return list((await s.execute(q)).scalars())


async def cancel(job_id: int) -> BuildJob:
    """Stop a build: a queued one is cancelled now, a running one is told to stop (the worker
    kills the process and cleans the working copy)."""
    async with db.session_maker()() as s:
        job = await s.get(BuildJob, job_id)
        if job is None:
            raise ValueError(f"Nessun lavoro con id {job_id}")
        if job.status not in ACTIVE:
            raise ValueError(f"Il lavoro #{job_id} è già finito ({job.status})")
        if job.status == "running":
            job.cancel_requested = True
        else:  # queued or paused: nothing is running, so it ends here
            job.status, job.finished_at = "cancelled", datetime.now(UTC)
        await s.commit()
    return await get_job(job_id)


async def resume(job_id: int) -> BuildJob:
    async with db.session_maker()() as s:
        job = await s.get(BuildJob, job_id)
        if job is None:
            raise ValueError(f"Nessun lavoro con id {job_id}")
        if job.status != "paused_quota":
            raise ValueError(f"Il lavoro #{job_id} non è in pausa per la quota ({job.status})")
        job.status, job.notified_status, job.cancel_requested = "queued", None, False
        await s.commit()
    return await get_job(job_id)


def format_job(job: BuildJob) -> str:
    parts = [f"#{job.id} {job.product} · {job.status}"]
    if job.branch:
        parts.append(f"ramo {job.branch}")
    if job.pr_url:
        parts.append(job.pr_url)
    if job.stop_reason:
        parts.append(f"motivo: {job.stop_reason}")
    return " · ".join(parts)


def message_for(job: BuildJob) -> str:
    head = {
        "pr_opened": f"✅ Lavoro #{job.id} su {job.product}: pull request in bozza pronta",
        "failed": f"❌ Lavoro #{job.id} su {job.product} fallito: {job.stop_reason or 'errore'}",
        "cancelled": f"⏹ Lavoro #{job.id} su {job.product} annullato",
        "paused_quota": (
            f"⏸ Lavoro #{job.id} su {job.product} in pausa: limite di quota di Claude Code. "
            f"Riprendi con /resume {job.id} quando la quota è tornata"
        ),
    }[job.status]
    lines = [head]
    if job.pr_url:
        lines.append(job.pr_url)
    if job.log_summary:
        lines.append(job.log_summary[:2500])
    return "\n\n".join(lines)


async def notify_changes(send: Callable[[str], Awaitable[None]]) -> int:
    """Tell the user about jobs that reached a state worth telling (once per state)."""
    async with db.session_maker()() as s:
        q = select(BuildJob).where(BuildJob.status.in_(NOTIFY_STATES)).order_by(BuildJob.id)
        todo = [j for j in (await s.execute(q)).scalars() if j.notified_status != j.status]
    sent = 0
    for job in todo:
        try:
            await send(message_for(job))
        except Exception:
            log.exception("build notification failed for job %s", job.id)
            continue
        async with db.session_maker()() as s:
            row = await s.get(BuildJob, job.id)
            if row is not None and row.status == job.status:
                row.notified_status = job.status
                await s.commit()
        sent += 1
    return sent
