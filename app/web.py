import json
import secrets
from html import escape
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.costs import estimate_cost, format_cost

router = APIRouter()

PAGE = """<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Jarvis · {title}</title>
<style>
:root{{--bg:#fff;--fg:#1a1a1a;--mut:#666;--line:#ddd;--bad:#b00020;--ok:#1b7f3b}}
@media(prefers-color-scheme:dark){{:root{{--bg:#16181c;--fg:#e8e8e8;--mut:#9aa0a6;
--line:#333;--bad:#ff6b81;--ok:#5fd38d}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;margin:0;
padding:16px;max-width:1000px;margin-inline:auto}}
a{{color:inherit}} table{{border-collapse:collapse;width:100%}}
th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--mut);font-weight:600}} .mut{{color:var(--mut)}} .bad{{color:var(--bad)}}
.ok{{color:var(--ok)}} pre{{white-space:pre-wrap;word-break:break-word;margin:0;font-size:13px}}
.wrap{{overflow-x:auto}}
</style></head><body>{body}</body></html>"""

MAX_FIELD_CHARS = 4000


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(PAGE.format(title=escape(title), body=body))


def _status_class(status: str) -> str:
    return (
        "ok" if status == "completed" else "bad" if status in ("failed", "max_iterations") else ""
    )


def _dump(value: object) -> str:
    if value is None:
        return ""
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    if len(text) > MAX_FIELD_CHARS:
        text = text[:MAX_FIELD_CHARS] + "\n… (troncato)"
    return escape(text)


async def _form(request: Request) -> dict[str, str]:
    """Parse an urlencoded form body and check its CSRF token (no multipart dependency needed)."""
    data = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
    if not secrets.compare_digest(data.get("token", ""), request.app.state.csrf_token):
        raise HTTPException(403, "invalid token")
    return data


@router.get("/ui/memory", response_class=HTMLResponse)
async def memory_page(request: Request) -> HTMLResponse:
    token = request.app.state.csrf_token
    facts = await request.app.state.memory.list_active()
    rows = "".join(
        f"<tr><td>{m.id}</td><td>{escape(m.category)}</td><td>"
        f'<form method="post" action="/ui/memory/{m.id}/edit">'
        f'<input type="hidden" name="token" value="{token}">'
        f'<input name="text" value="{escape(m.text)}" size="60" maxlength="500">'
        f"<button>Salva</button></form></td><td>"
        f'<form method="post" action="/ui/memory/{m.id}/forget">'
        f'<input type="hidden" name="token" value="{token}">'
        f"<button>Dimentica</button></form></td></tr>"
        for m in facts
    )
    empty = "" if facts else "<p class='mut'>Nessun fatto salvato.</p>"
    body = (
        "<h1>Memoria</h1><p class='mut'>Fatti attivi. Si possono correggere o dimenticare; "
        "i segreti vengono rifiutati.</p>"
        f"<div class='wrap'><table><tr><th>#</th><th>Categoria</th><th>Testo</th><th></th></tr>"
        f"{rows}</table></div>{empty}"
    )
    return _page("Memoria", body)


@router.post("/ui/memory/{memory_id}/edit")
async def memory_edit(request: Request, memory_id: int) -> RedirectResponse:
    form = await _form(request)
    try:
        done = await request.app.state.memory.update_text(memory_id, form.get("text", ""))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not done:
        raise HTTPException(404, "fact not found")
    await request.app.state.tracer.record_action(
        "web", None, "memory_edit", "command", {"id": memory_id}, {"text": form["text"]}, None
    )
    return RedirectResponse("/ui/memory", status_code=303)


@router.post("/ui/memory/{memory_id}/forget")
async def memory_forget(request: Request, memory_id: int) -> RedirectResponse:
    await _form(request)
    if not await request.app.state.memory.forget(memory_id):
        raise HTTPException(404, "fact not found")
    await request.app.state.tracer.record_action(
        "web", None, "forget", "command", {"id": memory_id}, {"status": "forgotten"}, None
    )
    return RedirectResponse("/ui/memory", status_code=303)


@router.get("/ui/runs", response_class=HTMLResponse)
async def runs_page(request: Request, limit: int = 50) -> HTMLResponse:
    runs = await request.app.state.tracer.list_runs(min(limit, 200))
    rows = "".join(
        f'<tr><td><a href="/ui/runs/{r.id}">{r.id}</a></td>'
        f'<td class="{_status_class(r.status)}">{escape(r.status)}</td>'
        f'<td class="mut">{r.started_at:%Y-%m-%d %H:%M:%S}</td>'
        f"<td>{escape(r.user_message[:80])}</td>"
        f"<td>{r.input_tokens + r.output_tokens}</td>"
        f"<td>{format_cost(estimate_cost(r.model, r.input_tokens, r.output_tokens))}</td></tr>"
        for r in runs
    )
    body = (
        "<h1>Run</h1><div class='wrap'><table><tr><th>#</th><th>Stato</th><th>Inizio (UTC)</th>"
        f"<th>Richiesta</th><th>Token</th><th>Costo stimato</th></tr>{rows}</table></div>"
    )
    return _page("Run", body)


@router.get("/ui/runs/{run_id}", response_class=HTMLResponse)
async def run_page(request: Request, run_id: int) -> HTMLResponse:
    run = await request.app.state.tracer.get_run(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    cost = format_cost(estimate_cost(run.model, run.input_tokens, run.output_tokens))
    steps = "".join(
        f"<tr><td>{s.idx}</td><td>{escape(s.type)}"
        f"{' · ' + escape(s.tool_name) if s.tool_name else ''}</td>"
        f"<td>{s.duration_ms} ms</td><td>{s.input_tokens} / {s.output_tokens}</td>"
        f"<td><pre>{_dump(s.input)}</pre></td><td><pre>{_dump(s.output)}</pre></td>"
        f'<td class="bad">{escape(s.error or "")}</td></tr>'
        for s in run.steps
    )
    body = (
        '<p><a href="/ui/runs">← tutti i run</a></p>'
        f"<h1>Run {run.id} "
        f'<span class="{_status_class(run.status)}">{escape(run.status)}</span></h1>'
        f'<p class="mut">{escape(run.model)} · {run.input_tokens} in / {run.output_tokens} out '
        f"· costo stimato {cost}</p>"
        f"<h3>Richiesta</h3><pre>{escape(run.user_message)}</pre>"
        f"<h3>Risposta</h3><pre>{escape(run.final_answer or '')}</pre>"
        "<h3>Passaggi</h3><div class='wrap'><table><tr><th>#</th><th>Tipo</th><th>Durata</th>"
        f"<th>Token in/out</th><th>Input</th><th>Output</th><th>Errore</th></tr>{steps}"
        "</table></div>"
    )
    return _page(f"Run {run.id}", body)
