import json

from app.db.models import Action


def format_action(a: Action) -> str:
    """One line per action: id, time, tool, permission, outcome and proof."""
    if a.error:
        outcome = f"ERRORE {a.error[:80]}"
    else:
        outcome = "ok " + json.dumps(a.output, ensure_ascii=False, default=str)[:140]
    return f"#{a.id} {a.created_at:%d/%m %H:%M} {a.tool_name} [{a.permission}] {outcome}"
