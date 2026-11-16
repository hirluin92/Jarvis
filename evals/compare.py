import json
from pathlib import Path
from typing import Any

SCORE_DROP = 0.1  # a rubric score falling by more than this counts as a regression


def compare(previous: dict[str, Any] | None, current: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Differences between the previous saved run and the current results, by scenario name."""
    out: dict[str, list[str]] = {"regressions": [], "fixed": [], "new": []}
    before = {r["name"]: r for r in previous["results"]} if previous else {}
    for r in current:
        p = before.get(r["name"])
        if p is None:
            if previous:
                out["new"].append(r["name"])
            continue
        if p["ok"] and not r["ok"]:
            out["regressions"].append(f"{r['name']}: PASS -> FAIL")
        elif not p["ok"] and r["ok"]:
            out["fixed"].append(r["name"])
        elif p.get("score") is not None and r.get("score") is not None:
            if r["score"] < p["score"] - SCORE_DROP:
                out["regressions"].append(
                    f"{r['name']}: score {p['score']:.2f} -> {r['score']:.2f}"
                )
    return out


def latest_result(results_dir: Path) -> dict[str, Any] | None:
    files = sorted(results_dir.glob("*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def save_result(results_dir: Path, stamp: str, model: str, results: list[dict[str, Any]]) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{stamp}.json"
    path.write_text(
        json.dumps({"created_at": stamp, "model": model, "results": results}, indent=2),
        encoding="utf-8",
    )
    return path
