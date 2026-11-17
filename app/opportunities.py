"""Niche opportunities (phase 12): evidence, scores that need evidence, plans, decisions.

The rules that stop the model from inventing live here, in code: a criterion can only be scored if
it points at evidence of the same opportunity; what is not known stays unknown (it is not zero);
a validation plan needs its thresholds and its closing criteria before anyone can approve it.
"""

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select

from app import builds
from app.config import get_settings
from app.db import session as db
from app.db.models import Evidence, Opportunity, ValidationPlan

STATUSES = (
    "idea", "scouted", "shortlisted", "validating", "go", "no_go", "building", "launched", "killed",
)  # fmt: skip
OPEN_STATUSES = ("idea", "scouted", "shortlisted")  # not yet decided
KINDS = ("complaint", "competitor", "price", "trend", "other")
THRESHOLD_KEYS = ("signups", "positive_replies", "presales")
DECISIONS = {"go": "go", "no_go": "no_go", "kill": "killed"}
STALE_AFTER_DAYS = 365
MIN_EVIDENCE_FOR_RADAR = 3

# 0-5, higher is always better for the project (little competition = high, easy to build = high)
CRITERIA = {
    "size": ("Dimensione del problema", 2.0),
    "urgency": ("Urgenza", 2.0),
    "paying": ("Prova che si paga già", 3.0),
    "competition": ("Poca concorrenza", 1.5),
    "reach": ("Raggiungibilità", 2.0),
    "effort": ("Facilità di costruzione", 1.5),
    "fit": ("Adatto a Gianluca", 1.0),
}


def weights() -> dict[str, float]:
    w = {k: v[1] for k, v in CRITERIA.items()}
    raw = get_settings().score_weights
    if raw:
        try:
            w.update({k: float(v) for k, v in json.loads(raw).items() if k in w})
        except (ValueError, TypeError):
            pass  # a bad override must not break scoring: the defaults stay
    return w


def _now() -> datetime:
    return datetime.now(UTC)


async def get(opportunity_id: int) -> Opportunity:
    async with db.session_maker()() as s:
        op = await s.get(Opportunity, opportunity_id)
    if op is None:
        raise ValueError(f"Nessuna opportunità con id {opportunity_id}")
    return op


async def evidence_of(opportunity_id: int) -> list[Evidence]:
    async with db.session_maker()() as s:
        q = select(Evidence).where(Evidence.opportunity_id == opportunity_id).order_by(Evidence.id)
        return list((await s.execute(q)).scalars())


async def save(
    title: str,
    niche: str = "",
    problem: str = "",
    opportunity_id: int | None = None,
    product: str | None = None,
) -> Opportunity:
    async with db.session_maker()() as s:
        if opportunity_id is None:
            op = Opportunity(title=title, niche=niche, problem=problem, product=product)
            s.add(op)
        else:
            op = await s.get(Opportunity, opportunity_id)
            if op is None:
                raise ValueError(f"Nessuna opportunità con id {opportunity_id}")
            op.title, op.niche, op.problem = title, niche or op.niche, problem or op.problem
            op.product = product or op.product
            op.updated_at = _now()
        await s.commit()
        new_id = op.id
    return await get(new_id)


async def add_evidence(
    opportunity_id: int, kind: str, url: str, quote: str, fetched_on: date | None = None
) -> Evidence:
    """A piece of evidence needs a real web address and a quote: no quote, no evidence."""
    if kind not in KINDS:
        raise ValueError(f"tipo di prova non valido: {kind} (validi: {', '.join(KINDS)})")
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("la prova richiede un indirizzo web http o https")
    quote = quote.strip()
    if len(quote) < 20:
        raise ValueError("la citazione è troppo corta: servono le parole lette nella fonte")
    await get(opportunity_id)
    if any(e.url == url and e.quote == quote for e in await evidence_of(opportunity_id)):
        raise ValueError("questa prova è già presente")
    async with db.session_maker()() as s:
        row = Evidence(
            opportunity_id=opportunity_id, kind=kind, url=url, quote=quote[:600],
            fetched_on=fetched_on or _now().date(),
        )  # fmt: skip
        s.add(row)
        op = await s.get(Opportunity, opportunity_id)
        if op.status == "idea":
            op.status = "scouted"
        op.updated_at = _now()
        await s.commit()
        rid = row.id
    async with db.session_maker()() as s:
        return await s.get(Evidence, rid)  # type: ignore[return-value]


async def score(opportunity_id: int, items: dict[str, dict[str, Any]]) -> Opportunity:
    """Save criteria scores. A number needs a reason and the ids of the evidence behind it;
    a null value records 'unknown'."""
    op = await get(opportunity_id)
    own = {e.id for e in await evidence_of(opportunity_id)}
    problems: list[str] = []
    detail = dict(op.score_detail or {})
    for name, item in items.items():
        if name not in CRITERIA:
            problems.append(f"{name}: criterio sconosciuto (validi: {', '.join(CRITERIA)})")
            continue
        value = item.get("value")
        ids = list(item.get("evidence_ids") or [])
        if value is None:
            detail[name] = {
                "value": None,
                "why": item.get("why", "sconosciuto"),
                "evidence_ids": [],
            }
            continue
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 5:
            problems.append(f"{name}: il voto è un numero intero da 0 a 5")
        elif not ids or not set(ids) <= own:
            problems.append(
                f"{name}: un voto senza prove di questa opportunità non è ammesso: "
                "metti 'sconosciuto' (value null) o aggiungi le prove"
            )
        elif not str(item.get("why", "")).strip():
            problems.append(f"{name}: manca il motivo del voto")
        else:
            detail[name] = {"value": value, "why": item["why"], "evidence_ids": ids}
    if problems:
        raise ValueError("punteggio rifiutato: " + "; ".join(problems))
    async with db.session_maker()() as s:
        row = await s.get(Opportunity, opportunity_id)
        row.score_detail = detail
        row.updated_at = _now()
        await s.commit()
    return await get(opportunity_id)


def total(op: Opportunity) -> tuple[float | None, float, list[str]]:
    """(score 0-100 over the criteria that are known, share of the weights known, unknown names)."""
    detail = op.score_detail or {}
    w = weights()
    known = {k: d["value"] for k, d in detail.items() if d.get("value") is not None and k in w}
    unknown = [k for k in CRITERIA if k not in known]
    known_w = sum(w[k] for k in known)
    if known_w == 0:
        return None, 0.0, unknown
    value = sum(w[k] * v / 5 for k, v in known.items()) / known_w * 100
    return round(value, 1), round(known_w / sum(w.values()), 2), unknown


def confidence(n_evidence: int, coverage: float) -> str:
    if n_evidence >= 5 and coverage >= 0.7:
        return "prove forti"
    if n_evidence >= MIN_EVIDENCE_FOR_RADAR:
        return "prove deboli"
    return "prove assenti"


def is_stale(e: Evidence, today: date | None = None) -> bool:
    return ((today or _now().date()) - e.fetched_on) > timedelta(days=STALE_AFTER_DAYS)


async def summary(opportunity_id: int) -> dict[str, Any]:
    op = await get(opportunity_id)
    ev = await evidence_of(opportunity_id)
    value, coverage, unknown = total(op)
    return {
        "id": op.id, "title": op.title, "niche": op.niche, "problem": op.problem,
        "status": op.status, "product": op.product, "decision_reason": op.decision_reason,
        "score": value, "score_coverage": coverage, "unknown_criteria": unknown,
        "confidence": confidence(len(ev), coverage),
        "score_detail": op.score_detail or {},
        "evidence": [
            {
                "id": e.id, "kind": e.kind, "url": e.url, "quote": e.quote,
                "read_on": e.fetched_on.isoformat(),
                "stale": is_stale(e),  # older than a year: say so
            }
            for e in ev
        ],
    }  # fmt: skip


async def list_all(status: str | None = None, limit: int = 20) -> list[Opportunity]:
    async with db.session_maker()() as s:
        q = select(Opportunity).order_by(Opportunity.id.desc()).limit(limit)
        if status:
            q = q.where(Opportunity.status == status)
        return list((await s.execute(q)).scalars())


async def shortlist(limit: int = 3) -> list[int]:
    """The best undecided opportunities that have enough evidence and a score, best first."""
    ranked: list[tuple[float, int]] = []
    for op in await list_all(limit=200):
        if op.status not in OPEN_STATUSES:
            continue
        value, coverage, _ = total(op)
        if value is None or len(await evidence_of(op.id)) < MIN_EVIDENCE_FOR_RADAR:
            continue
        ranked.append((value * (0.5 + 0.5 * coverage), op.id))
    ranked.sort(reverse=True)
    return [i for _, i in ranked[:limit]]


def card(data: dict[str, Any]) -> str:
    """The text of one opportunity as the radar shows it: everything comes from the database."""
    score = "n/d" if data["score"] is None else f"{data['score']:.0f}/100"
    lines = [
        f"#{data['id']} {data['title']}",
        f"Nicchia: {data['niche'] or 'n/d'}",
        f"Problema: {data['problem'] or 'n/d'}",
        f"Punteggio: {score} (criteri noti: {data['score_coverage']:.0%}) · {data['confidence']}",
    ]
    if data["unknown_criteria"]:
        names = ", ".join(CRITERIA[k][0] for k in data["unknown_criteria"])
        lines.append(f"Sconosciuto: {names}")
    stale = sum(1 for e in data["evidence"] if e["stale"])
    if stale:
        lines.append(f"Attenzione: {stale} prove hanno più di un anno [VECCHIA]")
    for e in data["evidence"][:4]:
        old = " [VECCHIA]" if e["stale"] else ""
        lines.append(f"- ({e['kind']}, {e['read_on']}){old} «{e['quote'][:160]}» {e['url']}")
    return "\n".join(lines)


# ---------- validation ----------


async def draft_kit(
    opportunity_id: int, kit: str, thresholds: dict[str, int], deadline: date, kill_criteria: str
) -> ValidationPlan:
    """The plan must carry thresholds and closing criteria before it can be approved."""
    await get(opportunity_id)
    clean = {k: int(v) for k, v in thresholds.items() if k in THRESHOLD_KEYS and int(v) >= 0}
    if not any(v > 0 for v in clean.values()):
        raise ValueError("servono soglie numeriche (iscrizioni, risposte positive o prevendite)")
    if deadline <= _now().date():
        raise ValueError("la scadenza della verifica deve essere nel futuro")
    if len(kill_criteria.strip()) < 20:
        raise ValueError("servono i criteri di chiusura, scritti prima di iniziare")
    async with db.session_maker()() as s:
        existing = (
            await s.execute(
                select(ValidationPlan).where(ValidationPlan.opportunity_id == opportunity_id)
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = ValidationPlan(opportunity_id=opportunity_id)
            s.add(existing)
        existing.kit, existing.thresholds = kit, clean
        existing.deadline, existing.kill_criteria, existing.approved = (
            deadline,
            kill_criteria,
            False,
        )
        op = await s.get(Opportunity, opportunity_id)
        if op.status in ("idea", "scouted"):
            op.status = "shortlisted"
        await s.commit()
    return await plan_of(opportunity_id)  # type: ignore[return-value]


async def plan_of(opportunity_id: int) -> ValidationPlan | None:
    async with db.session_maker()() as s:
        return (
            await s.execute(
                select(ValidationPlan).where(ValidationPlan.opportunity_id == opportunity_id)
            )
        ).scalar_one_or_none()


async def start_validation(opportunity_id: int) -> ValidationPlan:
    """Approve the plan and start the test. Called only after the user confirmed."""
    plan = await plan_of(opportunity_id)
    if plan is None:
        raise ValueError("manca il kit di verifica: scrivilo prima con draft_validation_kit")
    async with db.session_maker()() as s:
        row = await s.get(ValidationPlan, plan.id)
        row.approved = True
        op = await s.get(Opportunity, opportunity_id)
        op.status, op.updated_at = "validating", _now()
        await s.commit()
    return await plan_of(opportunity_id)  # type: ignore[return-value]


def thresholds_report(plan: ValidationPlan | None, results: dict[str, int] | None) -> str:
    if plan is None or not results:
        return "risultati non forniti"
    parts, reached = [], True
    for k, need in plan.thresholds.items():
        got = int(results.get(k, 0))
        ok = got >= need
        reached &= ok
        parts.append(f"{k}: {got}/{need} {'ok' if ok else 'NO'}")
    return (
        ("soglie raggiunte" if reached else "soglie NON raggiunte") + " (" + ", ".join(parts) + ")"
    )


async def decide(
    opportunity_id: int, decision: str, reason: str, results: dict[str, int] | None = None
) -> dict[str, Any]:
    """Register go / no_go / kill. A 'go' on an enabled product also writes a DRAFT spec."""
    if decision not in DECISIONS:
        raise ValueError(f"decisione non valida: {decision} (valide: {', '.join(DECISIONS)})")
    op = await get(opportunity_id)
    plan = await plan_of(opportunity_id)
    report = thresholds_report(plan, results)
    out: dict[str, Any] = {"opportunity_id": op.id, "decision": decision, "thresholds": report}
    async with db.session_maker()() as s:
        row = await s.get(Opportunity, opportunity_id)
        row.status = DECISIONS[decision]
        row.decision_reason = f"{reason} [{report}]"
        row.updated_at = _now()
        await s.commit()
    if decision == "go":
        out["next"] = (
            "specifica di costruzione: vedi draft_spec; il lavoro parte solo dopo la conferma"
        )
        if op.product:
            try:
                spec = await builds.save_spec(op.product, op.title[:120], spec_text(op, plan))
                out["draft_spec_id"] = spec.id  # a draft: nothing is queued
            except ValueError as e:
                out["draft_spec_error"] = str(e)
        else:
            out["next"] += ". Aggiungi il prodotto in config/products.yaml per scrivere la bozza"
    return out


def spec_text(op: Opportunity, plan: ValidationPlan | None) -> str:
    goal = f"Obiettivo: realizzare la prima versione di «{op.title}»"
    goal += f" per {op.niche}: {op.problem}" if op.niche or op.problem else "."
    need = f"soglie della verifica raggiunte ({plan.thresholds})" if plan else "da concordare"
    return (
        f"{goal}\nIncluso: il minimo che permetta di provare il valore ai primi clienti "
        "(da precisare con Gianluca prima di avviare).\n"
        "Escluso: tutto ciò che non serve alla prova."
        f"\nCriteri di accettazione: {need}.\nFile probabilmente toccati: da definire."
    )[: builds.MAX_SPEC_CHARS]
